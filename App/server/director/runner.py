"""The Director's production line: from an approved plan to a finished film in the Video history.

Order of work (it keeps every model loaded as few times as it can): the voices speak their lines first (a missing speech engine or a refused voice shows up in
the first minutes, not after an hour of shooting), then keyframe pictures for all scenes (FLUX.3 Klein), then every scene (LTX 2.5, sound included), then the music,
then the assembly with transitions and grade, then the soundtrack (voices, the person's songs and the music under the picture's own sound) and the registration
of the finished film in the Video history. The run holds the module "director" of the GPU coordinator for its whole length, so nothing else starts between two
steps; the Stop button stops it.

State is a plain dict, saved to `<runs>/<id>/run.json` after every change: the page reads it by polling, and a restart marks an unfinished run interrupted.
"""
from ui_strings import source as ui_text
import json
import os
import re
import secrets
import shutil
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
TRACKS = ('film.mp4', 'film-mix.mp4', 'music.wav')


def build_steps(plan, keyframes):
    """The steps of a run: every one is dict(id, kind, index, state); the page words them from kind and index (the number of the voice for a speech step)."""
    voices = list(dict.fromkeys(line['voice'] for line in plan['speech']))
    steps = [dict(id='speech-%d' % number, kind='speech', index=number, state='pending') for number in voices]
    steps += [dict(id='keyframe-%d' % i, kind='keyframe', index=i, state='pending') for i, row in enumerate(plan['scenes'], 1) if row['keyframe'] and keyframes]
    steps += [dict(id='scene-%d' % i, kind='scene', index=i, state='pending') for i in range(1, len(plan['scenes']) + 1)]
    if plan['music']:
        steps.append(dict(id='music', kind='music', index=0, state='pending'))
    steps.append(dict(id='assemble', kind='assemble', index=0, state='pending'))
    steps.append(dict(id='mix', kind='mix', index=0, state='pending'))
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
        plan = self.brain.make_plan(self._draft(draft_id), request, self.studio.capabilities(), 'English' if language == 'English' else 'Russian')
        keyframes = sum(1 for row in plan['scenes'] if row['keyframe'])
        plan['estimate_minutes'] = rules.estimate_minutes(plan['seconds'], plan['quality'], keyframes, bool(plan['music']), len(plan['speech']), len(plan['scenes']))
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
            self.run = dict(id=ident, state='running', phase='', progress=0.0, plan=plan, request=request, error='', result=None, started_at=time.time(), finished_at=None, notes=[],
                            steps=build_steps(plan, caps['keyframes']), draft=draft_id, generation=generation_begin('DirectorRun'), materials=materials,
                            speech_engines=(caps.get('speech') or {}).get('engines', []))
            self._save()
            self.thread = threading.Thread(target=self._produce, args=(self.run, files, folder, self.cancel), daemon=True)
            self.thread.start()
            return self.status(ident)

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
                return json.loads((self.root / 'runs' / ident / 'run.json').read_text(encoding='utf-8'))
            return dict(state='none')
        view = dict(run)
        view['steps'] = [dict(step) for step in run['steps']]
        view['elapsed_sec'] = round((run['finished_at'] or time.time()) - run['started_at'])
        return view

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
        for path in (self.root / 'runs').glob('*/run.json'):
            try:
                row = json.loads(path.read_text(encoding='utf-8'))
                if row.get('state') == 'running':
                    row.update(state='interrupted', error=ui_text('director.manager_film_was_interrupted'))
                    path.write_text(json.dumps(row, ensure_ascii=False), encoding='utf-8')
            except (OSError, ValueError):
                continue

    def _save(self):
        run = self.run
        path = self.root / 'runs' / run['id'] / 'run.json'
        temporary = path.with_name('run.json.tmp')
        temporary.write_text(json.dumps(run, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)

    def _step(self, run, step_id, state=None, phase=None):
        for step in run['steps']:
            if step['id'] == step_id and state:
                step['state'] = state
        if phase is not None:
            run['phase'] = phase if isinstance(phase, str) else ''
        done = sum(1 for step in run['steps'] if step['state'] in ('done', 'skipped'))
        run['progress'] = round(done / len(run['steps']), 3)
        self._save()

    # --- the production line ----------------------------------------------------------------------------------------------------------------
    def _produce(self, run, files, folder, cancel):
        scene_ids, film_id = [], None
        try:
            plan = run['plan']
            scenes = plan['scenes']
            orientation = plan['orientation']
            size = rules.QUALITIES[plan['quality']][orientation]
            spoken = self._speech(run, files, folder, cancel, plan)
            starts = self._keyframes(run, files, folder, cancel, plan)
            lengths = assemble.clip_lengths(scenes)
            seed = secrets.randbelow(2 ** 31)
            clips = []
            for index in range(len(scenes)):
                step = 'scene-%d' % (index + 1)
                self._step(run, step, 'running', '')
                clips.append(self._scene(run, cancel, plan, index, lengths[index], starts.get(index), seed + index * SEED_STEP, step, scene_ids))
                self._step(run, step, 'done')
            track = self._music(run, cancel, plan, folder)
            self._step(run, 'assemble', 'running', '')
            film = folder / 'film.mp4'
            assemble.assemble(self.ffmpeg, self.ffprobe, clips, plan, size, film, cancel, lambda fraction: self._step(run, 'assemble', phase='%d%%' % round(fraction * 100)))
            if cancel.is_set():
                raise DirectorCancelled()
            self._step(run, 'assemble', 'done', '')
            final = self._sound(run, files, folder, cancel, plan, film, track, spoken)
            row = self.studio.register(dict(path=str(final), prompt=run['request']['prompt'], scenes=[scene_prompt(plan, i) for i in range(len(scenes))], quality=plan['quality'],
                                            title=plan['title'], run=run['id'], name_prompt=plan['title'] if plan['title'].isascii() else scenes[0]['prompt'],
                                            log='\n'.join(json.dumps(step, ensure_ascii=False) for step in run['steps'])))
            film_id = row['id']
            for ident in scene_ids:
                self.studio.forget_clip(ident)
            run.update(state='done', result=dict(id=film_id), phase='')
        except (DirectorCancelled, DeliveryCancelled):
            run.update(state='cancelled', phase='')
        except (DirectorError, DeliveryError, ValueError) as error:
            run.update(state='error', error=str(error)[:1500], phase='')
        except Exception as error:
            run.update(state='error', error=ui_text('director.manager_unexpected_error', type(error).__name__, str(error)[:300]), phase='')
        finally:
            run['finished_at'] = time.time()
            generation_finish(run.get('generation'), state={'done': 'done', 'cancelled': 'cancelled'}.get(run['state'], 'error'))
            for step in run['steps']:
                if step['state'] == 'running':
                    step['state'] = 'failed' if run['state'] == 'error' else 'skipped'
            for name in TRACKS:                     # the Video history holds the film (a copy in the save folder); the run folder keeps its keyframes and its log
                (folder / name).unlink(missing_ok=True)
            for leftover in list(folder.glob('line-*.wav')) + list(folder.glob('material-*')):
                leftover.unlink(missing_ok=True)
            try:
                self._save()
            except OSError:
                pass

    def _keyframes(self, run, files, folder, cancel, plan):
        """A start picture for every scene: composed by Klein from its pictures when the plan asks for it, else the scene's first attached picture, else none."""
        starts = {}
        wanted = {step['index'] for step in run['steps'] if step['kind'] == 'keyframe'}
        for index, row in enumerate(plan['scenes']):
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

    def _scene(self, run, cancel, plan, index, length, start, seed, step, scene_ids):
        job = rules.scene_job(length, plan['quality'], plan['orientation'], start is not None, seed)
        problem = None
        for attempt in range(ATTEMPTS):
            try:
                shot = self.studio.shoot(dict(job, seed=str(seed + attempt)), scene_prompt(plan, index), start, cancel, lambda phase: self._step(run, step, phase=phase))
                scene_ids.append(shot['id'])
                return Path(shot['path'])
            except DirectorCancelled:
                raise
            except DirectorError as error:
                problem = error
                if cancel.is_set():
                    raise DirectorCancelled() from None
        raise DirectorError(ui_text('director.manager_scene_failed', index + 1, problem))

    def _music(self, run, cancel, plan, folder):
        """The generated track, downloaded to the run folder: dict(path, mean_db) with the average level the mix needs, or None when the plan has none."""
        if not plan['music']:
            return None
        self._step(run, 'music', 'running', '')
        try:
            ident = self.studio.compose(plan['music'], cancel, lambda phase: self._step(run, 'music', phase=phase))
            path = folder / 'music.wav'
            path.write_bytes(self.studio.track(ident))
        except DirectorCancelled:
            raise
        except DirectorError as error:
            raise DirectorError(ui_text('director.manager_music_failed', error)) from None
        self._step(run, 'music', 'done')
        return dict(path=path, mean_db=media.mean_level(media.levels(self.ffmpeg, path, plan['music']['seconds'])))

    def _speech(self, run, files, folder, cancel, plan):
        """Speak the lines of the plan, one Voiceover job for each voice and language: {index of the line: dict(path, seconds)} of the lines cut out of the jobs' audio."""
        if not plan['speech']:
            return {}
        spoken, jobs = {}, {}
        for index, line in enumerate(plan['speech']):
            jobs.setdefault(line['voice'], {}).setdefault(line['language'], []).append(index)
        for voice, languages in jobs.items():
            step = 'speech-%d' % voice
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
            cues.append(soundtrack.generated_cue(plan['music'], seconds))
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
