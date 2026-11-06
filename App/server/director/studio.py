"""The Director's hands: it starts the studio's own jobs the way the page does (over the loopback bridge) and waits for them.

Going through the bridge's routes instead of calling the managers keeps every rule of those routes: disabled components, the GPU claim, file names, the history
entries, the event identities. The requests carry the run's token (`X-VisualForge-Run`), which makes the bridge treat the Director as the holder of the card
instead of as a rival (see `GpuCoordinator.acting_for`). Registering a finished film and removing scene clips are calls into the Video service itself; those
never go through a route a page could reach.
"""
from ui_strings import source as ui_text
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from director import plan_rules as rules

HEADER = 'X-VisualForge-Run'
BUSY_WAIT_SECONDS = 600
TERMINAL = ('done', 'error', 'cancelled')
# The speech engines of the Voiceover module in the order the Director prefers them: VoxCPM2 speaks at 48 kHz, Qwen3-TTS needs far less of the card.
# VISUALFORGE_DIRECTOR_SPEECH_ENGINE names one of them to use instead (for tests and comparisons).
SPEECH_ENGINES = ('voxcpm2', 'qwen3-tts-12hz-0.6b-base')
CHAPTER_TITLE = re.compile(r'(\d+)\s*$')


class DirectorError(Exception):
    """A step of the film failed; the message is meant for the person."""


class StudioBusy(DirectorError):
    """The studio answered 409: another module holds the card for the moment."""


class DirectorCancelled(Exception):
    """The person stopped the film."""


def data_url(raw, mime='image/png'):
    return 'data:%s;base64,%s' % (mime, base64.b64encode(raw).decode('ascii'))


class Studio:
    def __init__(self, port, token, video, pause=time.sleep):
        self.base = 'http://127.0.0.1:%d' % port
        self.token, self.video, self.pause = token, video, pause
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # --- plain calls -------------------------------------------------------------------------------------------------------------------------
    def request(self, path, data=None, timeout=120, binary=False):
        headers = {HEADER: self.token}
        body = None
        if data is not None:
            body, headers['Content-Type'] = json.dumps(data).encode('utf-8'), 'application/json'
        try:
            with self.opener.open(urllib.request.Request(self.base + path, data=body, headers=headers), timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read()).get('error') or ''
            except ValueError:
                message = ''
            failure = StudioBusy if error.code == 409 else DirectorError
            raise failure(message or ui_text('director.studio_request_failed', path, error.code)) from None
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise DirectorError(ui_text('director.studio_not_answering', path)) from error
        return raw if binary else json.loads(raw)

    def start(self, path, data, timeout=300):
        """POST a job; a 409 (another module holds the card for a moment) is waited out for up to ten minutes."""
        deadline = time.monotonic() + BUSY_WAIT_SECONDS
        while True:
            try:
                return self.request(path, data, timeout=timeout)
            except StudioBusy:
                if time.monotonic() > deadline:
                    raise
                self.pause(5)

    # The default-None form keeps the callback optional without the explicit union rejected by the IDE's SDK-less inspection.
    def wait(self, poll: Callable[[], dict], cancel, stop: Callable[[dict], object], progress: Callable[[dict], object] = None, limit=4 * 3600, interval=3):
        """Poll `poll()` (a status dict with `state`) until it is terminal; `stop(row)` is called once when `cancel` is set."""
        started, stopped = time.monotonic(), False
        while True:
            row = poll()
            if progress:
                progress(row)
            if row.get('state') in TERMINAL:
                return row
            if cancel.is_set() and not stopped:
                stopped = True
                stop(row)
            if time.monotonic() - started > limit:
                stop(row)
                raise DirectorError(ui_text('director.studio_job_too_slow'))
            self.pause(interval)

    # --- what can be done -------------------------------------------------------------------------------------------------------------------
    def capabilities(self):
        """What the planner may use now: keyframe pictures when ComfyUI and FLUX.3 Klein are ready, every installed music engine and the speech engine for voices."""
        found = dict(keyframes=False, music=[], speech=None)
        try:
            klein = self.request('/api/preflight', timeout=30)['engines'].get('flux3-klein-int8') or {}
            found['keyframes'] = bool(klein.get('ready'))
        except (DirectorError, KeyError, AttributeError):
            pass
        try:
            ready = {row['id']: row for row in self.request('/api/voiceover/availability', timeout=30).get('engines', []) if row.get('available')}
            wanted = os.environ.get('VISUALFORGE_DIRECTOR_SPEECH_ENGINE')
            engines = [name for name in ((wanted,) if wanted else SPEECH_ENGINES) if name in ready]
            if engines:               # the first is tried first; the others take over a line that it could not speak
                found['speech'] = dict(engine=engines[0], engines=engines, languages=[name for name in ready[engines[0]].get('languages', []) if name in rules.SPEECH_LANGUAGES])
        except (DirectorError, KeyError, AttributeError, TypeError):
            pass
        try:
            for engine in self.request('/api/music/availability', timeout=30).get('engines', []):
                if engine.get('available') and engine['id'] in MUSIC_ABOUT:
                    found['music'].append(dict(id=engine['id'], modes=MUSIC_ABOUT[engine['id']][0], max_duration=engine.get('max_duration') or MUSIC_ABOUT[engine['id']][1],
                                               about=MUSIC_ABOUT[engine['id']][2]))
        except (DirectorError, KeyError, AttributeError, TypeError):
            pass
        return found

    # --- the jobs ------------------------------------------------------------------------------------------------------------------------------
    def keyframe(self, prompt, photos, orientation, cancel, progress=None):
        """One picture from FLUX.3 Klein: composed from the given photos (PNG bytes), or drawn from the text when there are none. -> PNG bytes."""
        mode = ('prompt', 'single', 'reference', 'multi')[min(len(photos), 3)]
        payload = dict(engine='flux3-klein-int8', mode=mode, prompt=prompt, quality='low', canvas=orientation)
        if photos:
            payload['image1'] = dict(base64=base64.b64encode(photos[0]).decode('ascii'))
        if len(photos) > 1:
            payload['image2'] = dict(base64=base64.b64encode(photos[1]).decode('ascii'))
        if len(photos) > 2:
            payload['extra_images'] = [dict(base64=base64.b64encode(raw).decode('ascii')) for raw in photos[2:10]]
        answer = self.start('/api/generate', payload)
        ident, token = answer['prompt_id'], answer.get('cancel_token')
        row = self.wait(lambda: self.request('/api/status?id=' + ident, timeout=60), cancel,
                        lambda _row: self.request('/api/cancel', dict(prompt_id=ident, cancel_token=token), timeout=30), progress, limit=1800)
        if cancel.is_set():
            raise DirectorCancelled()
        if row.get('state') != 'done':
            raise DirectorError(row.get('error') or ui_text('director.studio_keyframe_failed'))
        return self.request('/api/result?id=%s&index=0' % ident, timeout=120, binary=True)

    def shoot(self, job, prompt, start_png, cancel, progress=None):
        """One Video job of the studio; -> dict(id, path) of the finished clip (a normal entry of the Video history)."""
        payload = dict(job, prompt=prompt)
        if start_png:
            payload['source_image'] = data_url(start_png)
        answer = self.start('/api/video/generate', payload)
        ident, token = answer['id'], answer['cancel_token']
        row = self.wait(lambda: self.request('/api/video/status?id=' + ident, timeout=60), cancel,
                        lambda _row: self.request('/api/video/cancel', dict(id=ident, cancel_token=token), timeout=30),
                        (lambda current: progress(current.get('phase'))) if progress else None)
        if cancel.is_set():
            raise DirectorCancelled()
        if row.get('state') != 'done' or not row.get('saved_path'):
            raise DirectorError(row.get('error') or ui_text('director.studio_scene_failed'))
        return dict(id=ident, path=row['saved_path'])

    def compose(self, music, cancel, progress=None):
        """One track from the music engine; -> the Music history id (`track` fetches its audio)."""
        payload = dict(engine=music['engine'], caption=music['caption'], lyrics=music['lyrics'], mode=music['mode'], duration=music['seconds'], language=music['language'])
        answer = self.start('/api/music/generate', payload)
        ident, token = answer['id'], answer.get('token')
        row = self.wait(lambda: self.request('/api/music/status?id=' + ident, timeout=60), cancel,
                        lambda _row: self.request('/api/music/cancel', dict(id=ident, token=token), timeout=30),
                        (lambda current: progress(current.get('phase'))) if progress else None)
        if cancel.is_set():
            raise DirectorCancelled()
        if row.get('state') != 'done':
            raise DirectorError(row.get('error') or ui_text('director.studio_music_failed'))
        return ident

    def _video_service(self):
        """Make sure the Video service is running. It starts with the first Video job; a film that is continued after a restart of the studio with nothing but its assembly left has had no
        job, and its registration found nobody to talk to (seen on 2026-11-06 after forty scenes of a film of ten minutes were shot)."""
        start = getattr(self.video, 'start', None)
        if start is not None:
            try:
                start()
            except ValueError as error:
                raise DirectorError(str(error)) from None

    def register(self, film):
        """The assembled film becomes an entry of the Video history (a call into the Video service, not a page route)."""
        try:
            self._video_service()
            return self.video.call('/register', film, timeout=600)
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise DirectorError(ui_text('director.studio_register_failed', error)) from None

    def track(self, ident):
        """The audio of a finished Music job as WAV bytes (the same file the Music page plays)."""
        return self.request('/api/music/result?id=' + ident, timeout=300, binary=True)

    def speak(self, engine, sample, name, lines, language, cancel, progress=None):
        """Speak `lines` (texts) with the voice in `sample` (WAV bytes) through the Voiceover module; -> dict(job, path, spans={line index from 0: (start, end)}, flagged).

        The sample becomes a voice of the Voiceover library for the length of the job (the person confirmed on the Director page that they may use it) and is removed
        afterwards. The lines are chapters of one text, so one job loads the model once and the finished file says where every line begins and ends. The module
        listens to what it made and repeats a line that came out wrong; `flagged` lists those that still did."""
        voice = self.request('/api/voiceover/voice-add', dict(name=(name or 'Director')[:60], consent=True, audio=data_url(sample, 'audio/wav')), timeout=120)
        ident, row = None, {}                          # the job's id and its last status: bound before the try, so that nothing after the finally can meet an unbound name
        try:
            text = '\n\n'.join('Chapter %d\n%s' % (index, line) for index, line in enumerate(lines, 1))
            book = self.request('/api/voiceover/prepare', dict(text=text, title='Director'), timeout=120)
            answer = self.start('/api/voiceover/generate', dict(engine=engine, book_id=book['book_id'], voice_id=voice['id'], language=language, format='wav'))
            ident, token = answer['id'], answer.get('token')
            row = self.wait(lambda: self.request('/api/voiceover/status?id=' + ident, timeout=60), cancel,
                            lambda _row: self.request('/api/voiceover/cancel', dict(id=ident, token=token), timeout=30),
                            (lambda current: progress(current.get('phase'))) if progress else None, limit=3600)
        finally:
            try:
                self.request('/api/voiceover/voice-delete', dict(id=voice['id']), timeout=60)
            except DirectorError:
                pass            # a sample left in the library is harmless and the person can delete it there
        if cancel.is_set() or row.get('state') != 'done' or not row.get('saved_path'):
            self.forget_speech(ident)                 # a stopped or failed job is of no use to anybody: it must not stay in the person's Voiceover history
            if cancel.is_set():
                raise DirectorCancelled()
            raise DirectorError(row.get('error') or ui_text('director.studio_speech_failed'))
        spans = {}
        for chapter in row.get('chapters', []):
            found = CHAPTER_TITLE.search(str(chapter.get('title')))
            if found:
                spans[int(found.group(1)) - 1] = (float(chapter['start']), float(chapter['end']))
        if sorted(spans) != list(range(len(lines))):
            raise DirectorError(ui_text('director.studio_speech_incomplete'))
        return dict(job=ident, path=row['saved_path'], spans=spans, flagged=row.get('flagged', []))

    def forget_speech(self, ident):
        """Remove a finished speech job from the Voiceover history with its file (the lines are cut out of it already). A job enters the history a moment after its
        state turns terminal, so the first try may be too early: it is repeated once."""
        for attempt in range(2):
            try:
                self.request('/api/voiceover/delete', dict(id=ident), timeout=60)
                return
            except DirectorError:
                if attempt == 0:
                    self.pause(2)
    def forget_clip(self, ident):
        """Remove a finished clip from the Video history with its file (the scene clips are only material of the film)."""
        try:
            self._video_service()
            self.video.call('/delete', dict(id=ident), timeout=120)
        except (urllib.error.URLError, OSError, ValueError, DirectorError):
            pass            # a leftover scene clip is harmless; it never fails a finished film


# What each installed music engine is good for, told to the planner; the first entry is what a mode list may contain.
MUSIC_ABOUT = {
    'ace-step-15-turbo': (['instrumental', 'song'], 180, 'Songs with vocals in many languages, or instrumentals. Describe genre, instruments, mood, tempo and the voice in English; for a song write sparse lyrics.'),
    'minimax-music3-int8': (['instrumental', 'song'], 60, 'Songs or instrumentals up to a minute. Describe genre, mood, instruments and the voice in English.'),
    'stable-audio-3-medium': (['audio'], 120, 'Music or sound ambience without a singing voice. Describe genre, instruments, mood and energy in English.'),
}
