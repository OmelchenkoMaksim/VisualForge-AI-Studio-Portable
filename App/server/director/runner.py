"""The Director's production line: from an approved plan to a finished film in the Video history.

Order of work (it keeps every model loaded as few times as it can): the voices speak their lines first and the music is composed next (a missing speech or music engine, or a refused
voice, shows up in the first minutes, not after hours of shooting), then keyframe pictures for all scenes (FLUX.3 Klein), then every scene (LTX 2.5, sound included), then the assembly with
transitions and grade, then the soundtrack (voices, the person's songs and the music under the picture's own sound) and the registration of the finished film in the Video history. The run holds the module "director" of the GPU coordinator for its whole length, so nothing else starts between two
steps; the Stop button stops it.

State is a plain dict, saved to `<runs>/<id>/run.json` after every change: the page reads it by polling, and a restart marks an unfinished run interrupted.
"""
from ui_strings import source as ui_text
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from director import assemble, media, soundtrack
from director import plan as plans
from generation_identity import begin as generation_begin, finish as generation_finish
from director import plan_rules as rules
from director.studio import DirectorCancelled, DirectorError
from video.video_delivery import DeliveryCancelled, DeliveryError, run_tool

RUN_ID = re.compile(r'[0-9a-f]{32}')
ATTEMPTS = 2
SEED_STEP = 7919
# What a run folder holds besides run.json. The assembled film and the filter graph files are rebuilt in a minute, so they never survive a run. The speech lines, the track, the
# keyframes and the links to the person's materials are what hours of work produced: a film that stopped keeps them so that it can be continued, and gives them back when it is done.
TRACKS = ('film.mp4', 'film-mix.mp4', 'assemble-graph.txt', 'mix-graph.txt', 'measure-graph.txt', 'music-graph.txt')
RESUMABLE = ('error', 'cancelled', 'interrupted')
SHORT_CLIP_TOLERANCE = 0.1           # a clip may be this many seconds shorter than its scene needs (the file's duration is rounded to frames)
SHORT_FILM_TOLERANCE = 0.5           # a finished film may be this many seconds shorter than planned before the person is told
KEEP_UNFINISHED_DAYS = 14            # an unfinished film is kept this long after it stopped; after that its files go (the scene clips stay in the Video history for the person to delete)


ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def keep_awake(on):
    """Ask Windows not to put the computer to sleep while a film is shot (`on`), or let it do so again. The request belongs to the calling thread and ends with it.

    A film takes hours, and a computer that sleeps in the middle of a scene stops the video worker and fails the job. The screen may still turn off. Nothing happens off Windows."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except (AttributeError, OSError, ImportError):
        pass


def run_leftovers(folder, keyframes=False):
    """The files of a run that only a continuation needs: speech lines, the track, the links to the materials (and the keyframes when `keyframes`)."""
    found = list(folder.glob('line-*.wav')) + list(folder.glob('material-*')) + list(folder.glob('music*.wav'))
    return found + list(folder.glob('keyframe-*.png')) if keyframes else found


def build_steps(plan, keyframes):
    """The steps of a run: every one is dict(id, kind, index, state, weight); the page words them from kind and index (the number of the voice for a speech step).

    `weight` is the step's estimated work in seconds (the same model as the plan screen's estimate): the bar follows the work done, not the number of steps, and the time left is
    reckoned from the weights and the time the finished steps really took."""
    voices = list(dict.fromkeys(line['voice'] for line in plan['speech']))
    clips = rules.clip_seconds([row['seconds'] for row in plan['scenes']])
    steps = [dict(id='speech-%d' % number, kind='speech', index=number, state='pending',
                  weight=rules.SPEECH_LOAD_SECONDS + rules.SPEECH_LINE_SECONDS * sum(1 for line in plan['speech'] if line['voice'] == number)) for number in voices]
    if plan['music']:
        steps.append(dict(id='music', kind='music', index=0, state='pending', weight=rules.MUSIC_TRACK_SECONDS * soundtrack.music_parts(plan['music'], plan['seconds'])))
    steps += [dict(id='keyframe-%d' % i, kind='keyframe', index=i, state='pending', weight=rules.KEYFRAME_SECONDS) for i, row in enumerate(plan['scenes'], 1) if row['keyframe'] and keyframes]
    steps += [dict(id='scene-%d' % i, kind='scene', index=i, state='pending', weight=rules.scene_seconds(clips[i - 1], plan['quality'])) for i in range(1, len(plan['scenes']) + 1)]
    steps.append(dict(id='assemble', kind='assemble', index=0, state='pending', weight=round(rules.ASSEMBLE_PER_FILM_SECOND * plan['seconds'] + 5)))
    steps.append(dict(id='mix', kind='mix', index=0, state='pending', weight=round(rules.MIX_PER_FILM_SECOND * plan['seconds'] + rules.MIX_FIXED_SECONDS)))
    return steps


def scene_prompt(plan, index):
    """The prompt of a scene with the film's style phrase in front of it (Gemma is asked to write it so; this makes it certain)."""
    phrase, prompt = plan['style']['phrase'], plan['scenes'][index]['prompt']
    return prompt if not phrase or prompt.startswith(phrase) else (phrase + ' ' + prompt)[:4900]


class DirectorManager:
    def __init__(self, root, cache, ffmpeg, ffprobe, brain, studio, gpu, submission_lock=None, token=None):
        self.root, self.cache = Path(root), Path(cache)
        self.ffmpeg, self.ffprobe, self.brain, self.studio, self.gpu = ffmpeg, ffprobe, brain, studio, gpu
        self.lock = submission_lock or threading.RLock()
        self.token = token or secrets.token_urlsafe(24)           # the one `Studio` sends with its requests
        self.run, self.thread, self.cancel = None, None, threading.Event()
        self.planning = None            # where the plan being written is (a plan of fifty scenes takes minutes); the page asks for it while it waits
        (self.root / 'runs').mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)
        media.prune_drafts(self.cache)
        self._settle_old_runs()

    # --- the page's questions -------------------------------------------------------------------------------------------------------------
    def acting(self, header):
        """True for a request that carries the token of the run in progress: it is the Director's own call, not a rival's."""
        return bool(header) and self.busy() and secrets.compare_digest(str(header), self.token)

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def limits(self):
        return dict(min_seconds=rules.MIN_SECONDS, max_seconds=rules.MAX_SECONDS, min_scenes=rules.MIN_SCENES, scenes_per_minute=rules.SCENES_PER_MINUTE,
                    max_video_seconds=rules.MAX_VIDEO_SECONDS, max_video_bytes=rules.MAX_VIDEO_BYTES, max_photo_bytes=rules.MAX_PHOTO_BYTES,
                    max_song_seconds=rules.MAX_SONG_SECONDS, max_song_bytes=rules.MAX_SONG_BYTES, max_voice_bytes=rules.MAX_VOICE_BYTES,
                    voice_sample_seconds=rules.VOICE_SAMPLE_SECONDS, min_voice_seconds=rules.MIN_VOICE_SECONDS, groups=[dict(row) for row in rules.GROUPS])

    def profile(self, engines):
        """The entry of the engine list ("Режиссёр"): it needs Gemma for the plan and the LTX engine for the scenes; `engines` is the Video service's list."""
        ltx = next((row for row in engines if row['id'] == 'ltx25-distilled'), None)
        gemma = self.brain.status() if self.brain is not None else dict(available=False, reason=ui_text('director.engine_needs_gemma'))
        reason = '' if gemma['available'] else (gemma['reason'] or ui_text('director.engine_needs_gemma'))
        if not reason and not (ltx and ltx['available']):
            reason = ui_text('director.engine_needs_ltx')
        return dict(id='director', name=ui_text('director.engine_list_name'), virtual='director', available=not reason, reason=reason, modes=['director'], qualities=['standard'], frames=[],
                    resolutions=[], fps=rules.FPS, rating=None, limits=self.limits(), requirements=ui_text('director.engine_list_requirements'), limitations=ui_text('director.engine_list_limitations'),
                    missing=[])

    def planning_state(self):
        """Where the plan being written has come to: dict(stage, first, last, total) with a stage of look, plan, outline or scenes, or an empty stage when no plan is being written."""
        return self.planning or dict(stage='')

    def draft(self, draft_id):
        """The attachments of a draft without their file names, and the running time that suits them."""
        items = self._draft(draft_id).items()
        return dict(items=[self._public(row) for row in items], advice=rules.advise(items))

    def _draft(self, draft_id):
        return media.open_draft(self.cache, draft_id, self.ffmpeg, self.ffprobe)

    def attach_photo(self, draft_id, raw, name, group):
        return self._added(draft_id, self._draft(draft_id).add_photo(raw, name, group))

    def attach_video(self, draft_id, upload, name, group):
        return self._added(draft_id, self._draft(draft_id).add_video(upload, name, group))

    def attach_song(self, draft_id, upload, name, group):
        return self._added(draft_id, self._draft(draft_id).add_song(upload, name, group))

    def attach_voice(self, draft_id, upload, name, group):
        return self._added(draft_id, self._draft(draft_id).add_voice(upload, name, group))

    def _added(self, draft_id, item):
        return dict(self.draft(draft_id), item=self._public(item))

    def detach(self, draft_id, number):
        self._draft(draft_id).remove(number)
        return self.draft(draft_id)

    def label(self, draft_id, number, text):
        self._draft(draft_id).set_label(number, text)
        return self.draft(draft_id)

    def media_file(self, draft_id, number):
        """(path, content type) of an attachment, so that the page can play or show it."""
        draft = self._draft(draft_id)
        if not isinstance(number, int) or isinstance(number, bool):
            raise ValueError(ui_text('director.media_unknown_attachment'))
        path = draft.path(number)
        kind = {'.png': 'image/png', '.mp4': 'video/mp4', '.m4a': 'audio/mp4', '.wav': 'audio/wav'}.get(path.suffix.lower())
        if kind is None or not path.is_file():
            raise ValueError(ui_text('director.media_unknown_attachment'))
        return path, kind

    @staticmethod
    def _public(item):
        return {key: value for key, value in item.items() if key != 'file'}

    def make_plan(self, draft_id, request, language):
        """The plan Gemma writes for `request` (prompt, seconds, scenes, optional orientation and quality) from the materials of the draft."""
        if self.brain is None:
            raise ValueError(ui_text('director.manager_gemma_missing'))
        if self.busy():
            raise ValueError(ui_text('director.manager_film_in_progress'))
        request = self._request(request)
        self.brain.progress = lambda state: setattr(self, 'planning', state)
        try:
            plan = self.brain.make_plan(self._draft(draft_id), request, self.studio.capabilities(), 'English' if language == 'English' else 'Russian')
        finally:
            self.planning, self.brain.progress = None, None
        keyframes = sum(1 for row in plan['scenes'] if row['keyframe'])
        plan['estimate_minutes'] = rules.estimate_minutes(plan['seconds'], plan['quality'], keyframes, bool(plan['music']), len(plan['speech']), len(plan['scenes']),
                                                          lengths=[row['seconds'] for row in plan['scenes']], voices=len({line['voice'] for line in plan['speech']}),
                                                          music_tracks=soundtrack.music_parts(plan['music'], plan['seconds']) if plan['music'] else 0)
        return plan

    @staticmethod
    def _request(data):
        prompt = data.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 5000:
            raise ValueError(ui_text('director.manager_prompt_needed'))
        seconds, scenes = data.get('seconds'), data.get('scenes')
        rules.check_request(seconds, scenes)
        orientation = rules.pick(data.get('orientation'), rules.ORIENTATIONS)
        quality = rules.pick(data.get('quality'), rules.QUALITIES)
        return dict(prompt=prompt.strip(), seconds=seconds, scenes=scenes, orientation=orientation, quality=quality, ultra=data.get('ultra') is True,
                    language='English' if data.get('language') == 'English' else 'Russian', speech_language=plans.idea_language(prompt))

    # --- a run --------------------------------------------------------------------------------------------------------------------------------
    def _check_disk(self, seconds):
        """ValueError for the person when the drive of the Director's files or the drive of the video scratch folder (the system's temporary folder) has less room than a film of `seconds` needs."""
        needed = rules.disk_needed_bytes(seconds)
        for place in dict.fromkeys((str(self.root), tempfile.gettempdir())):
            try:
                free = shutil.disk_usage(place).free
            except OSError:
                continue                                       # a drive that cannot be asked is not a reason to refuse
            if free < needed:
                raise ValueError(ui_text('director.manager_disk_space_low', Path(place).anchor or place, round(free / 1024 ** 3, 1), round(needed / 1024 ** 3, 1)))

    def start(self, draft_id, plan, request):
        """Check the (possibly edited) plan once more, take the GPU for the whole film and start the production thread."""
        request = self._request(request)
        draft = self._draft(draft_id)
        caps = self.studio.capabilities()
        raw = plan if isinstance(plan, dict) else {}
        if isinstance(raw.get('speech'), list) and raw['speech'] and not caps.get('speech'):
            raise ValueError(ui_text('director.manager_speech_engine_missing'))           # the plan has lines but the engine is gone: say so, instead of shooting a silent film
        # The plan on the screen may have been edited: it goes through the same checks as Gemma's answer, with its own format and quality as the choice.
        plan = plans.normalize(raw, dict(request, orientation=rules.pick(raw.get('orientation'), rules.ORIENTATIONS) or request['orientation'],
                                         quality=rules.pick(raw.get('quality'), rules.QUALITIES) or request['quality']), caps, plans.Materials(draft.items()))
        self._check_disk(plan['seconds'])
        os.utime(draft.folder)                     # a draft that a film is made of is in use: the weekly clean-up must not take it
        with self.lock:
            if self.busy():
                raise ValueError(ui_text('director.manager_film_in_progress'))
            self.gpu.claim('director')
            ident = uuid.uuid4().hex
            folder = self.root / 'runs' / ident
            folder.mkdir(parents=True)
            files, materials = self._keep(draft, plan, folder)
            self.cancel = threading.Event()
            # `artifacts` is what the finished steps made (the scene clips are Video history entries, the lines and the track are files of the run folder): with the steps' states it is all that
            # a continuation needs. `seed` is kept so that a continued film draws its remaining scenes from the same series.
            self.run = dict(id=ident, state='running', phase='', progress=0.0, plan=plan, request=request, error='', result=None, started_at=time.time(), finished_at=None, notes=[],
                            steps=build_steps(plan, caps['keyframes']), draft=draft_id, generation=generation_begin('DirectorRun'), materials=materials,
                            speech_engines=(caps.get('speech') or {}).get('engines', []), artifacts=dict(speech={}, scenes={}, music=None, spare=[]), seed=secrets.randbelow(2 ** 31), resumed=0)
            self._save()
            self.thread = threading.Thread(target=self._produce, args=(self.run, files, folder, self.cancel), daemon=True)
            self.thread.start()
            return self.status(ident)

    # --- continuing a film that stopped -----------------------------------------------------------------------------------------------------
    def resume(self, ident):
        """Continue a film that failed, was stopped or was interrupted by a closed studio: the finished steps (voices, keyframes, scene clips, the track) are kept, the others are done again.

        A film takes hours and one scene that fails twice used to throw all of them away."""
        folder = self._run_folder(ident)
        caps = self.studio.capabilities()            # asked of the bridge before the lock is taken (as `start` does): the bridge's own handlers may need that lock to answer
        with self.lock:
            if self.busy():
                raise ValueError(ui_text('director.manager_film_in_progress'))
            run = self.run if self.run is not None and self.run['id'] == ident else self._load(folder)
            if run is None or run.get('state') not in RESUMABLE or run.get('discarded') or not isinstance(run.get('plan'), dict):
                raise ValueError(ui_text('director.manager_cannot_resume'))
            files = self._materials(folder, run['plan'])
            self._check_disk(run['plan']['seconds'])
            if run['plan']['speech'] and not caps.get('speech'):
                raise ValueError(ui_text('director.manager_speech_engine_missing'))
            self.gpu.claim('director')
            run.setdefault('artifacts', dict(speech={}, scenes={}, music=None, spare=[]))
            run.setdefault('seed', secrets.randbelow(2 ** 31))
            for step in run['steps']:
                if step['state'] != 'done':
                    step['state'] = 'pending'
            run.update(state='running', error='', phase='', result=None, finished_at=None, resumed=run.get('resumed', 0) + 1, generation=generation_begin('DirectorRun'),
                       speech_engines=(caps.get('speech') or {}).get('engines', []))
            if any(step['state'] == 'done' for step in run['steps']) and ui_text('director.run_note_resumed') not in run['notes']:
                run['notes'].append(ui_text('director.run_note_resumed'))
            self.run, self.cancel = run, threading.Event()
            self._save()
            self.thread = threading.Thread(target=self._produce, args=(self.run, files, folder, self.cancel), daemon=True)
            self.thread.start()
            return self.status(ident)

    def discard(self, ident):
        """Give up a film that stopped: its scene clips leave the Video history, the files of the run go, the record stays (it shows that the film was given up)."""
        folder = self._run_folder(ident)
        with self.lock:
            if self.busy():
                raise ValueError(ui_text('director.manager_film_in_progress'))
            run = self.run if self.run is not None and self.run['id'] == ident else self._load(folder)
            if run is None or run.get('state') not in RESUMABLE:
                raise ValueError(ui_text('director.manager_cannot_resume'))
            artifacts = run.get('artifacts') or {}
            clips = [row['id'] for row in (artifacts.get('scenes') or {}).values()] + list(artifacts.get('spare', []))
            run.update(discarded=True, artifacts=dict(speech={}, scenes={}, music=None, spare=[]))
            if self.run is not None and self.run['id'] == ident:
                self.run = run
            self._write(folder, run)                 # given up first, so that nobody can continue it while its files are being removed
        for clip in clips:                           # a call into the Video service for each clip: not under the lock that every module's start waits for
            self.studio.forget_clip(clip)
        for path in run_leftovers(folder, keyframes=True):
            path.unlink(missing_ok=True)
        return self.status(ident)

    def _run_folder(self, ident):
        if not isinstance(ident, str) or not RUN_ID.fullmatch(ident) or not (self.root / 'runs' / ident / 'run.json').is_file():
            raise ValueError(ui_text('director.manager_no_such_run'))
        return self.root / 'runs' / ident

    @staticmethod
    def _load(folder):
        try:
            return json.loads((folder / 'run.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _materials(folder, plan):
        """{number: path} of the links to the materials that a film kept in its run folder; ValueError when one is gone (the person's own files are never touched, but the links may have been cleaned)."""
        wanted = {number for row in plan['scenes'] for number in row['references']} | {line['voice'] for line in plan['speech']} | {cue['song'] for cue in plan['songs']}
        files = {number: next(iter(sorted(folder.glob('material-%d.*' % number))), None) for number in sorted(wanted)}
        if any(path is None or not path.is_file() for path in files.values()):
            raise ValueError(ui_text('director.manager_resume_materials_gone'))
        return files

    @staticmethod
    def _keep(draft, plan, folder):
        """The files a film is made from, linked into its own folder, and what the page and the soundtrack need to know about them: ({number: path}, {number: facts}).

        The draft belongs to the page, which may remove a picture or a voice while the film is being shot (a film takes hours); a hard link keeps the data alive for the run
        however the draft changes (a copy where the file system cannot link), and the run folder gives them back when it is cleaned."""
        wanted = {number for row in plan['scenes'] for number in row['references']} | {line['voice'] for line in plan['speech']} | {cue['song'] for cue in plan['songs']}
        items = {row['number']: row for row in draft.items()}
        files, materials = {}, {}
        for number in sorted(wanted):
            source = draft.path(number)
            target = folder / ('material-%d%s' % (number, source.suffix))
            try:
                os.link(source, target)
            except OSError:
                shutil.copyfile(source, target)
            files[number] = target
            row = items[number]
            materials[str(number)] = dict(name=row['name'], label=row.get('label', ''), kind=row['kind'], group=row['group'], mean_db=(row.get('facts') or {}).get('mean_db'))
        return files, materials

    def status(self, ident=None):
        run = self.run
        if run is None or (ident and run['id'] != ident):
            if ident and RUN_ID.fullmatch(ident) and (self.root / 'runs' / ident / 'run.json').is_file():
                return self._recorded(json.loads((self.root / 'runs' / ident / 'run.json').read_text(encoding='utf-8')))
            # After a restart the page asks for "the latest film" and finds nothing in memory: an unfinished film of the last days that can still be continued is the answer.
            latest = self._latest_unfinished() if run is None and not ident else None
            return self._recorded(latest) if latest else dict(state='none')
        view = dict(run)
        view['steps'] = [dict(step) for step in run['steps']]
        view['elapsed_sec'] = round((run['finished_at'] or time.time()) - run['started_at'])
        view['eta_seconds'] = self._eta(run)
        view['resumable'] = self._can_resume(run)
        return view

    @staticmethod
    def _can_resume(row):
        """Whether the page may offer "continue": the film stopped before it was done and has not been given up. The files themselves are checked when it is asked for."""
        return row.get('state') in RESUMABLE and not row.get('discarded') and isinstance(row.get('plan'), dict)

    def _recorded(self, row):
        return dict(row, resumable=self._can_resume(row))

    def _latest_unfinished(self):
        """The run record of the most recent film that stopped before it was done and has not been given up, or None."""
        newest, found = 0.0, None
        for path in (self.root / 'runs').glob('*/run.json'):
            try:
                stamp = path.stat().st_mtime
                if stamp > newest and time.time() - stamp < KEEP_UNFINISHED_DAYS * 86400:
                    row = json.loads(path.read_text(encoding='utf-8'))
                    if row.get('state') in RESUMABLE and not row.get('discarded') and isinstance(row.get('plan'), dict):
                        newest, found = stamp, row
            except (OSError, ValueError):
                continue
        return found

    def cancel_run(self, ident):
        if self.run is None or self.run['id'] != ident:
            raise ValueError(ui_text('director.manager_no_such_run'))
        self.cancel.set()
        return dict(cancelled=self.busy())

    def stop(self):
        """The global Stop button: stop the film in progress, if there is one."""
        if self.busy():
            self.cancel.set()
            return True
        return False

    def _settle_old_runs(self):
        """A run that was running when the studio closed is interrupted (it can be continued); one that stopped more than `KEEP_UNFINISHED_DAYS` ago gives its files back."""
        for path in (self.root / 'runs').glob('*/run.json'):
            try:
                row = json.loads(path.read_text(encoding='utf-8'))
                if row.get('state') == 'running':
                    row.update(state='interrupted', error=ui_text('director.manager_film_was_interrupted'))
                    path.write_text(json.dumps(row, ensure_ascii=False), encoding='utf-8')
                if row.get('state') in RESUMABLE and not row.get('discarded') and time.time() - path.stat().st_mtime > KEEP_UNFINISHED_DAYS * 86400:
                    row.update(discarded=True)
                    path.write_text(json.dumps(row, ensure_ascii=False), encoding='utf-8')
                if row.get('discarded'):             # given up by the person, or by age: whatever a stop in the middle of the removal left is removed now
                    for leftover in run_leftovers(path.parent, keyframes=True):
                        leftover.unlink(missing_ok=True)
            except (OSError, ValueError):
                continue

    @staticmethod
    def _write(folder, run):
        """run.json is replaced as a whole, so that a power loss leaves the previous complete record and not half of a new one."""
        path = folder / 'run.json'
        temporary = path.with_name('run.json.tmp')
        temporary.write_text(json.dumps(run, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)

    def _save(self):
        self._write(self.root / 'runs' / self.run['id'], self.run)

    def _step(self, run, step_id, state=None, phase=None):
        for step in run['steps']:
            if step['id'] == step_id and state:
                step['state'] = state
                if state == 'running':
                    step['started'] = time.time()
                elif state == 'done':
                    step['finished'] = time.time()
        if phase is not None:
            run['phase'] = phase if isinstance(phase, str) else ''
        finished = sum(step.get('weight', 1) for step in run['steps'] if step['state'] in ('done', 'skipped'))
        run['progress'] = round(finished / (sum(step.get('weight', 1) for step in run['steps']) or 1), 3)
        self._save()

    @staticmethod
    def _eta(run):
        """The seconds left, or None when the film is not being shot. A step counts by its estimate times how much longer or shorter than estimated the finished steps of its own kind really took
        (a keyframe is quick once the picture model is loaded, a scene is not: one ratio for all would tell nonsense): the median of the last seven, leaving out the first of the kind because it
        reads its model from the disk and takes two or more times as long as the others. A kind with no such measure counts by its estimate; a step in progress counts what remains of it."""
        if run.get('state') != 'running':
            return None
        steps = run['steps']
        ratios = {}
        for kind in {step['kind'] for step in steps}:
            done = sorted((step['finished'], (step['finished'] - step['started']) / step['weight']) for step in steps
                          if step['kind'] == kind and step['state'] == 'done' and step.get('started') and step.get('finished') and step.get('weight'))
            usable = sorted(ratio for _, ratio in done[1:][-7:])
            ratios[kind] = (usable[len(usable) // 2] if len(usable) % 2 else (usable[len(usable) // 2 - 1] + usable[len(usable) // 2]) / 2) if usable else 1.0
        left = 0.0
        for step in steps:
            work = step.get('weight', 1) * ratios[step['kind']]
            if step['state'] == 'pending':
                left += work
            elif step['state'] == 'running':
                left += max(work - (time.time() - step.get('started', time.time())), 0.1 * work)
        return round(left)

    # --- the production line ----------------------------------------------------------------------------------------------------------------
    def _produce(self, run, files, folder, cancel):
        film_id = None
        keep_awake(True)
        try:
            plan = run['plan']
            scenes = plan['scenes']
            orientation = plan['orientation']
            size = rules.QUALITIES[plan['quality']][orientation]
            spoken = self._speech(run, files, folder, cancel, plan)
            track = self._music(run, cancel, plan, folder)            # before the hours of shooting: a music engine that fails shows it now, and a track is safe in the run folder
            starts = self._keyframes(run, files, folder, cancel, plan)
            lengths = assemble.clip_lengths(scenes)
            clips = []
            for index in range(len(scenes)):
                step = 'scene-%d' % (index + 1)
                kept = self._kept_clip(run, index, lengths[index])
                if kept is None:
                    self._step(run, step, 'running', '')
                    kept = self._scene(run, cancel, plan, index, lengths[index], starts.get(index), run['seed'] + index * SEED_STEP, step)
                    self._step(run, step, 'done')
                clips.append(kept)
            self._step(run, 'assemble', 'running', '')
            film = folder / 'film.mp4'
            built = assemble.assemble(self.ffmpeg, self.ffprobe, clips, plan, size, film, cancel, lambda fraction: self._step(run, 'assemble', phase='%d%%' % round(fraction * 100)))
            if cancel.is_set():
                raise DirectorCancelled()
            if built['seconds'] < plan['seconds'] - SHORT_FILM_TOLERANCE:       # a clip shorter than its scene shortens the film silently: the person is told
                run['notes'].append(ui_text('director.run_note_film_is_shorter', round(built['seconds'], 1), plan['seconds']))
            self._step(run, 'assemble', 'done', '')
            final = self._sound(run, files, folder, cancel, plan, film, track, spoken)
            row = self.studio.register(dict(path=str(final), prompt=run['request']['prompt'], scenes=[scene_prompt(plan, i) for i in range(len(scenes))], quality=plan['quality'],
                                            title=plan['title'], run=run['id'], name_prompt=plan['title'] if plan['title'].isascii() else scenes[0]['prompt'],
                                            log='\n'.join(json.dumps(step, ensure_ascii=False) for step in run['steps'])))
            film_id = row['id']
            artifacts = run['artifacts']
            for clip in [shot['id'] for shot in artifacts['scenes'].values()] + artifacts['spare']:
                self.studio.forget_clip(clip)
            run.update(state='done', result=dict(id=film_id), phase='')
        except (DirectorCancelled, DeliveryCancelled):
            run.update(state='cancelled', phase='')
        except (DirectorError, DeliveryError, ValueError) as error:
            run.update(state='error', error=str(error)[:1500], phase='')
        except Exception as error:
            run.update(state='error', error=ui_text('director.manager_unexpected_error', type(error).__name__, str(error)[:300]), phase='')
        finally:
            keep_awake(False)
            run['finished_at'] = time.time()
            generation_finish(run.get('generation'), state={'done': 'done', 'cancelled': 'cancelled'}.get(run['state'], 'error'))
            for step in run['steps']:
                if step['state'] == 'running':
                    step['state'] = 'failed' if run['state'] == 'error' else 'skipped'
            for name in TRACKS:                     # the Video history holds the film (a copy in the save folder); the run folder keeps its keyframes and its log
                (folder / name).unlink(missing_ok=True)
            if run['state'] == 'done':              # a film that stopped keeps what it made (lines, track, links to the materials) so that it can be continued; a finished one gives it back
                for leftover in run_leftovers(folder):
                    leftover.unlink(missing_ok=True)
            try:
                self._save()
            except OSError:
                pass

    def _kept_clip(self, run, index, length):
        """The clip of scene `index + 1` that an earlier try of this film already shot, or None when there is none that can be used.

        The step must be done, the file must still exist (the person may have deleted the clip from the Video history) and must be as long as the scene needs: a shorter clip would
        shorten the film without any error. A clip that cannot be used is remembered in `spare` so that it leaves the Video history with the others when the film is done."""
        step = next(row for row in run['steps'] if row['id'] == 'scene-%d' % (index + 1))
        shot = run['artifacts']['scenes'].get(str(index))
        if step['state'] != 'done' or shot is None:
            return None
        try:
            if media.probe(self.ffprobe, shot['path'])['seconds'] >= length - SHORT_CLIP_TOLERANCE:
                return Path(shot['path'])
        except (ValueError, OSError):
            pass
        run['artifacts']['spare'].append(shot['id'])
        del run['artifacts']['scenes'][str(index)]
        return None

    def _keyframes(self, run, files, folder, cancel, plan):
        """A start picture for every scene: composed by Klein from its pictures when the plan asks for it, else the scene's first attached picture, else none.

        A keyframe that an earlier try of this film made is read back from the run folder."""
        starts = {}
        wanted = {step['index']: step for step in run['steps'] if step['kind'] == 'keyframe'}
        for index, row in enumerate(plan['scenes']):
            made = folder / ('keyframe-%d.png' % (index + 1))
            if index + 1 in wanted and wanted[index + 1]['state'] == 'done' and made.is_file():
                starts[index] = made.read_bytes()
                continue
            photos = [files[number].read_bytes() for number in row['references']]
            if index + 1 in wanted:
                step = 'keyframe-%d' % (index + 1)
                self._step(run, step, 'running', '')
                # The plan names pictures by their numbers in the draft; the picture model counts the ones it is given, in the order in which the shape of the film is kept (see
                # `media.frame_pictures`). A picture drawn from words alone has no photo to take its look from: the film's style phrase gives it.
                order, pictures, note = media.frame_pictures(photos, plan['orientation'])
                words = plans.renumber_pictures(row['keyframe'], [row['references'][i] for i in order])
                if not photos and plan['style']['phrase']:
                    words = plan['style']['phrase'] + '. ' + words
                if note:
                    words = words.rstrip(' .') + '. ' + note
                png = self.studio.keyframe(words[:1500], pictures, plan['orientation'], cancel,
                                           lambda state, step=step: self._step(run, step, phase=state.get('state') if isinstance(state, dict) else ''))
                (folder / ('keyframe-%d.png' % (index + 1))).write_bytes(png)
                starts[index] = png
                self._step(run, step, 'done')
            elif photos:
                starts[index] = photos[0]
            if cancel.is_set():
                raise DirectorCancelled()
        return starts

    def _scene(self, run, cancel, plan, index, length, start, seed, step):
        """Shoot scene `index + 1` (the second try uses another seed). The clip is recorded in the run as soon as it exists, so that a film which stops later can be continued."""
        job = rules.scene_job(length, plan['quality'], plan['orientation'], start is not None, seed)
        problem = None
        for attempt in range(ATTEMPTS):
            try:
                shot = self.studio.shoot(dict(job, seed=str(seed + attempt)), scene_prompt(plan, index), start, cancel, lambda phase: self._step(run, step, phase=phase))
                run['artifacts']['scenes'][str(index)] = dict(id=shot['id'], path=str(shot['path']))
                return Path(shot['path'])
            except DirectorCancelled:
                raise
            except DirectorError as error:
                problem = error
                if cancel.is_set():
                    raise DirectorCancelled() from None
        raise DirectorError(ui_text('director.manager_scene_failed', index + 1, problem))

    def _music(self, run, cancel, plan, folder):
        """The generated track, downloaded to the run folder: dict(path, mean_db) with the average level the mix needs, or None when the plan has none.

        A film longer than the engine's longest track gets several tracks of the same style, crossfaded into one file (a loop of one track repeats the same minutes and cuts at the seam).
        The tracks an earlier try already downloaded are kept, so that a film which stopped in the fourth track does not compose the first three again."""
        if not plan['music']:
            return None
        music, path, made = plan['music'], folder / 'music.wav', run['artifacts']['music']
        if made and next(step for step in run['steps'] if step['id'] == 'music')['state'] == 'done' and path.is_file():
            return dict(path=path, mean_db=made['mean_db'])            # an earlier try of this film already composed it
        self._step(run, 'music', 'running', '')
        count, length = soundtrack.music_parts(music, plan['seconds']), plan['seconds'] - music['start']
        parts = [folder / ('music-%d.wav' % number) for number in range(1, count + 1)]
        try:
            for number, part in enumerate(parts, 1):
                if part.is_file():
                    continue
                label = '' if count == 1 else '%d/%d ' % (number, count)
                ident = self.studio.compose(music, cancel, lambda phase, label=label: self._step(run, 'music', phase=label + (phase or '')))
                part.write_bytes(self.studio.track(ident))
            if count == 1:
                os.replace(parts[0], path)
            else:
                soundtrack.crossfaded(self.ffmpeg, parts, length, path, cancel)
        except (DirectorCancelled, DeliveryCancelled):
            raise
        except (DirectorError, DeliveryError) as error:
            raise DirectorError(ui_text('director.manager_music_failed', error)) from None
        for part in parts:
            part.unlink(missing_ok=True)
        mean_db = media.mean_level(media.levels(self.ffmpeg, path, min(length, music['seconds'] * count)))
        run['artifacts']['music'] = dict(mean_db=mean_db)
        self._step(run, 'music', 'done')
        return dict(path=path, mean_db=mean_db)

    def _speech(self, run, files, folder, cancel, plan):
        """Speak the lines of the plan, one Voiceover job for each voice and language: {index of the line: dict(path, seconds)} of the lines cut out of the jobs' audio."""
        if not plan['speech']:
            return {}
        spoken, jobs = {}, {}
        for index, line in enumerate(plan['speech']):
            jobs.setdefault(line['voice'], {}).setdefault(line['language'], []).append(index)
        for voice, languages in jobs.items():
            step = 'speech-%d' % voice
            lines = [index for indexes in languages.values() for index in indexes]
            made = run['artifacts']['speech']
            if next(row for row in run['steps'] if row['id'] == step)['state'] == 'done' and all(str(i) in made and (folder / ('line-%d.wav' % i)).is_file() for i in lines):
                spoken.update({i: dict(path=folder / ('line-%d.wav' % i), seconds=made[str(i)]['seconds']) for i in lines})        # an earlier try of this film already said them
                continue
            self._step(run, step, 'running', '')
            item = run['materials'][str(voice)]
            for language, indexes in languages.items():
                result = self._say(run, step, files[voice].read_bytes(), item['label'] or item['name'], [plan['speech'][i]['text'] for i in indexes], language, cancel)
                try:
                    for position, index in enumerate(indexes):
                        start, end = result['spans'][position]
                        target = folder / ('line-%d.wav' % index)
                        self._cut_line(result['path'], start, end, target, cancel)
                        spoken[index] = dict(path=target, seconds=round(end - start, 3))
                        made[str(index)] = dict(seconds=spoken[index]['seconds'])
                    if result['flagged']:
                        run['notes'].append(ui_text('director.run_note_speech_flagged', len(result['flagged'])))
                finally:
                    self.studio.forget_speech(result['job'])
            self._step(run, step, 'done', '')
        return spoken

    def _say(self, run, step, sample, name, lines, language, cancel):
        """One speech job: the engines are tried in the order of preference and the next one takes over when one fails (a note says so); a stop or the last failure ends it."""
        engines = run['speech_engines']
        for position, engine in enumerate(engines):
            try:
                return self.studio.speak(engine, sample, name, lines, language, cancel, lambda phase, step=step: self._step(run, step, phase=phase))
            except DirectorCancelled:
                raise
            except DirectorError as error:
                if position + 1 == len(engines):
                    raise DirectorError(ui_text('director.manager_speech_failed', error)) from None
                run['notes'].append(ui_text('director.run_note_speech_engine_changed', engine, engines[position + 1]))
                self._step(run, step, phase='')

    def _cut_line(self, source, start, end, target, cancel):
        """One line out of a job's audio, with a few milliseconds of fade at both ends so that the cut cannot click."""
        length = max(0.1, end - start)
        run_tool([str(self.ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-ss', '%.3f' % start, '-t', '%.3f' % length, '-i', str(source),
                  '-af', 'afade=t=in:st=0:d=0.02,afade=t=out:st=%.3f:d=0.04' % max(0.0, length - 0.04), '-ac', '1', str(target)], 120, cancel)

    def _sound(self, run, files, folder, cancel, plan, film, track, spoken):
        """Write the film again with its soundtrack: the lines placed in their scenes, the person's songs and the generated track under them. -> the path of that film."""
        self._step(run, 'mix', 'running', '')
        seconds = media.probe(self.ffprobe, film)['seconds']
        cues, sources = [], {}
        for cue in plan['songs']:
            sources[len(cues)] = (files[cue['song']], run['materials'][str(cue['song'])]['mean_db'])
            cues.append(cue)
        if plan['music'] and track:
            sources[len(cues)] = (track['path'], track['mean_db'])
            cues.append(dict(soundtrack.generated_cue(plan['music'], seconds), loop=False))          # the track is made long enough (crossfaded tracks), never looped
        scene_starts = soundtrack.scene_starts(plan['scenes'])
        order = sorted(spoken)
        placed = soundtrack.place_lines([(scene_starts[plan['speech'][i]['scene'] - 1] + plan['speech'][i]['delay'], spoken[i]['seconds']) for i in order], seconds)
        voices = []
        for index, where in zip(order, placed):
            if where is None:
                run['notes'].append(ui_text('director.run_note_line_dropped', index + 1))
                continue
            voices.append(dict(path=spoken[index]['path'], start=where[0], tempo=where[1], gain_db=plan['speech'][index]['gain_db']))
        beds = soundtrack.beds_for(cues, sources)
        final = folder / 'film-mix.mp4'
        film_level = media.mean_level(media.levels(self.ffmpeg, film, seconds))
        # Native-only films need the same final loudness stage, but no bed attenuation when nothing is laid over their sound.
        native = soundtrack.native_gain(film_level) if beds or voices else 0.0
        # What was put where, kept in the run's log: the mix cannot be listened to from here, but a wrong place or level can be read.
        run['sound'] = dict(film_seconds=seconds, scenes_level_db=film_level, scenes_gain_db=native,
                            beds=[dict(start=bed['start'], seconds=bed['seconds'], offset=bed['offset'], gain_db=bed['gain_db'], loop=bool(bed.get('loop'))) for bed in beds],
                            lines=[dict(start=voice['start'], tempo=voice['tempo'], seconds=spoken[index]['seconds']) for voice, index in zip(voices, [i for i, where in zip(order, placed) if where])])
        run['sound']['mix'] = soundtrack.mixdown(self.ffmpeg, self.ffprobe, film, final, beds, voices, native_db=native, cancel=cancel,
                                                 progress=lambda fraction: self._step(run, 'mix', phase='%d%%' % round(fraction * 100)))
        if cancel.is_set():
            raise DirectorCancelled()
        self._step(run, 'mix', 'done', '')
        return final
