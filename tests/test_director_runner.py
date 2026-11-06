"""The production line with a fake studio: the order of the steps, retries, a stop, a failure, speech, songs, and the film that comes out (real FFmpeg, no model, no GPU)."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image  # noqa: E402

from director import media, plan, runner, soundtrack  # noqa: E402
from director.studio import DirectorCancelled, DirectorError  # noqa: E402
from gpu_coordinator import Busy  # noqa: E402
from video.video_delivery import DeliveryCancelled, DeliveryError, ffmpeg_paths  # noqa: E402

RUNTIME = Path(os.environ.get('VISUALFORGE_VIDEO_RUNTIME') or ROOT / 'Runtime/Video')
FFMPEG, FFPROBE = ffmpeg_paths(RUNTIME)
CAPS = dict(keyframes=True, music=[dict(id='ace-step-15-turbo', modes=['instrumental', 'song'], max_duration=180, about='Songs.')], speech=None)
SPEECH_CAPS = dict(CAPS, speech=dict(engine='voxcpm2', engines=['voxcpm2'], languages=['Russian', 'English']))
TWO_ENGINES = dict(CAPS, speech=dict(engine='voxcpm2', engines=['voxcpm2', 'qwen3-tts-12hz-0.6b-base'], languages=['Russian', 'English']))
PROMPT = 'Photorealistic western film, warm light. Wide shot of a red car on a desert road, a woman in a hat drives; tires hiss on sand; wind and a distant guitar.'
REQUEST = dict(prompt='Road trip', seconds=10, scenes=2, orientation='landscape', quality='draft')
PHOTO = dict(number=1, kind='photo', group='hero')


def plan_needed(seconds):
    from director import plan_rules
    return plan_rules.disk_needed_bytes(seconds)


def png():
    buffer = io.BytesIO()
    Image.new('RGB', (300, 200), (180, 60, 30)).save(buffer, 'PNG')
    return buffer.getvalue()


def sound(path, frequency, seconds, level=0.25):
    subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'sine=frequency=%d:duration=%s:sample_rate=48000' % (frequency, seconds),
                    '-af', 'volume=%s' % level, '-ac', '2', str(path)], check=True)


def noise(path, seconds):
    subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'anoisesrc=color=pink:amplitude=0.3:duration=%s:sample_rate=44100' % seconds,
                    '-ac', '1', str(path)], check=True)


class FakeStudio:
    """Answers like the studio but makes the clips itself; `calls` is the order of what the Director asked for."""

    def __init__(self, work, caps=CAPS):
        self.work, self.caps, self.calls, self.fail = Path(work), caps, [], {}
        self.hold = None            # an Event the next shot waits for (to test a stop)
        self.hold_speech = None     # the same for the next speech job
        self.on_speak = None        # something to do at the first speech job (the person removes a material while the film is shot)
        self.fail_speech = False
        self.fail_music = False
        self.fail_engines = set()   # engines whose speech jobs fail (the first engine may break while the second works)
        self.line_seconds = None    # a fixed length for every spoken line; else 0.05 s a character, at least a second
        self.flagged = []
        self.jobs = 0
        self.keyframe_photos = []   # the pictures of every keyframe job, in the order they were given

    def capabilities(self):
        return self.caps

    def keyframe(self, prompt, photos, orientation, cancel, progress=None):
        self.calls.append(('keyframe', prompt, len(photos), orientation))
        self.keyframe_photos.append(photos)
        return png()

    def shoot(self, job, prompt, start_png, cancel, progress=None):
        self.calls.append(('shoot', job['frames'], job['mode'], job['quality'], (job['width'], job['height']), start_png is not None, job['seed']))
        if self.hold is not None:
            self.hold.set()
            cancel.wait(10)
            raise DirectorCancelled()
        index = sum(1 for call in self.calls if call[0] == 'shoot')
        if self.fail.get(index):
            self.fail[index] -= 1
            raise DirectorError('the engine refused')
        path = self.work / ('scene-%d.mp4' % len(self.calls))
        seconds = job['frames'] / 24
        subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=%dx%d:rate=24:duration=%s' % (job['width'], job['height'], seconds),
                        '-f', 'lavfi', '-i', 'sine=frequency=330:duration=%s' % seconds, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(path)], check=True)
        return dict(id='clip-%d' % index, path=str(path))

    def compose(self, music, cancel, progress=None):
        self.calls.append(('compose', music['engine'], music['seconds']))
        if self.fail_music:
            raise DirectorError('the music engine is broken')
        return 'music-1'

    def track(self, ident):
        self.calls.append(('track', ident))
        path = self.work / 'track.wav'
        sound(path, 2500, 12)
        return path.read_bytes()

    def speak(self, engine, sample, name, lines, language, cancel, progress=None):
        """One WAV with a tone for every line (a different pitch for every job), a second and a half of silence between them, and the spans the studio would report."""
        self.jobs += 1
        self.calls.append(('speak', engine, name, tuple(lines), language, len(sample) > 0))
        if self.on_speak is not None:
            self.on_speak()
            self.on_speak = None
        if self.hold_speech is not None:
            self.hold_speech.set()
            cancel.wait(10)
            raise DirectorCancelled()
        if self.fail_speech or engine in self.fail_engines:
            raise DirectorError('the voice engine fell over')
        spans, clock, parts = {}, 0.0, []
        for index, line in enumerate(lines):
            seconds = self.line_seconds or max(1.0, 0.05 * len(line))
            spans[index] = (round(clock, 3), round(clock + seconds, 3))
            parts.append(('sine=frequency=%d:duration=%s:sample_rate=24000,volume=0.4' % (500 + 700 * self.jobs, seconds), seconds))
            clock += seconds + 1.5
        path = self.work / ('speech-%d.wav' % self.jobs)
        graph = ';'.join('%s[l%d];anullsrc=r=24000:cl=mono,atrim=duration=1.5[g%d]' % (source, i, i) for i, (source, _) in enumerate(parts))
        graph += ';' + ''.join('[l%d][g%d]' % (i, i) for i in range(len(parts))) + 'concat=n=%d:v=0:a=1' % (2 * len(parts))
        subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-filter_complex', graph, str(path)], check=True)
        return dict(job='job-%d' % self.jobs, path=str(path), spans=spans, flagged=self.flagged)

    def forget_speech(self, ident):
        self.calls.append(('forget_speech', ident))

    def register(self, film):
        self.calls.append(('register', Path(film['path']).name, len(film['scenes'])))
        self.film = film
        shutil.copyfile(film['path'], self.work / 'registered.mp4')                      # the run folder keeps no copy after the film is registered: look at it here
        result = subprocess.run([str(FFPROBE), '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', film['path']], capture_output=True, text=True, check=True)
        self.film_streams = json.loads(result.stdout)['streams']
        return dict(id='film-1')

    def forget_clip(self, ident):
        self.calls.append(('forget', ident))


class FakeBrain:
    def __init__(self, available=True):
        self.available = available
        self.requests = []

    def status(self):
        return dict(available=self.available, reason='' if self.available else 'no model', model='gemma-test')

    def make_plan(self, draft, request, capabilities, language):
        self.requests.append((dict(request), language))
        return plan_for(request, capabilities)


class FakeGpu:
    def __init__(self):
        self.claims, self.error = [], None

    def claim(self, module, *args):
        if self.error:
            raise self.error
        self.claims.append(module)


def scene(seconds, **values):
    return dict(dict(seconds=seconds, prompt=PROMPT, references=[1], keyframe=None, transition='fade'), **values)


def plan_for(request, caps, items=(PHOTO,), **changes):
    raw = dict(title='Road', summary='Two scenes.', orientation='landscape', quality='draft', style=dict(phrase='35mm film', warmth=0.2, contrast=1.05, saturation=1.0),
               music=dict(engine='ace-step-15-turbo', mode='instrumental', caption='western guitar, slow', gain_db=-16),
               scenes=[scene(request['seconds'] / 2, keyframe='A woman by a red car'), scene(request['seconds'] / 2)])
    raw.update(changes)
    return plan.normalize(raw, request, caps, plan.Materials(items))


@unittest.skipUnless(FFMPEG.is_file() and FFPROBE.is_file(), 'bundled FFmpeg not found')
class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.studio, self.gpu = FakeStudio(self.work), FakeGpu()
        self.manager = runner.DirectorManager(self.work / 'Director', self.work / 'cache', FFMPEG, FFPROBE, FakeBrain(), self.studio, self.gpu)
        self.draft_id = media.new_draft_id()
        self.draft = media.open_draft(self.manager.cache, self.draft_id, FFMPEG, FFPROBE)
        self.draft.add_photo(png(), 'car.png')

    def film(self, wait=90, caps=None, items=(PHOTO,), **changes):
        self.manager.start(self.draft_id, plan_for(REQUEST, caps or self.studio.caps, items, **changes), REQUEST)
        self.manager.thread.join(wait)
        return self.manager.status()

    def add_voices(self):
        """A narrator (number 2) and a character (number 3) in the draft; returns the items for `plan_for`."""
        sample = self.work / 'sample.wav'
        noise(sample, 8)
        self.draft.add_voice(shutil.copyfile(sample, self.work / 'upload-1.part'), 'narrator.wav', 'narrator')
        self.draft.add_voice(shutil.copyfile(sample, self.work / 'upload-2.part'), 'hero.wav', 'character')
        self.draft.set_label(3, 'Alexey')
        return [PHOTO] + [dict(row) for row in self.draft.items() if row['kind'] == 'voice']

    def add_song(self, seconds=30):
        path = self.work / 'song.wav'
        sound(path, 1000, seconds)
        self.draft.add_song(shutil.copyfile(path, self.work / 'upload-3.part'), 'song.wav')
        return [dict(row) for row in self.draft.items() if row['kind'] == 'audio']

    # --- a film with music ----------------------------------------------------------------------------------------------------------------
    def test_a_film_is_made_in_the_order_that_loads_each_model_once(self):
        status = self.film()
        self.assertEqual((status['state'], status['result']), ('done', dict(id='film-1')), status.get('error'))
        kinds = [call[0] for call in self.studio.calls]
        self.assertEqual(kinds, ['compose', 'track', 'keyframe', 'shoot', 'shoot', 'register', 'forget', 'forget'])
        self.assertEqual(self.studio.calls[2][2:], (1, 'landscape'))                      # the keyframe is composed from the scene's picture
        first, second = self.studio.calls[3], self.studio.calls[4]
        self.assertEqual((first[2], second[2]), ('i2v', 'i2v'))                            # the keyframe, and the first attached picture, are start frames
        self.assertEqual((first[3], first[4]), ('preview', (768, 512)))
        self.assertEqual(self.studio.calls[0][1:], ('ace-step-15-turbo', 10))             # the track is composed before the shooting begins
        self.assertEqual(self.studio.calls[5][1], 'film-mix.mp4')                        # the film that is registered is the one with the music under it
        self.assertEqual(self.studio.calls[6:], [('forget', 'clip-1'), ('forget', 'clip-2')])
        self.assertEqual(self.gpu.claims, ['director'])
        self.assertTrue(all(step['state'] == 'done' for step in status['steps']))
        self.assertEqual([step['id'] for step in status['steps']], ['music', 'keyframe-1', 'scene-1', 'scene-2', 'assemble', 'mix'])
        self.assertEqual((status['progress'], status['notes']), (1.0, []))
        self.assertTrue(self.studio.film['scenes'][0].startswith('35mm film'))             # the style phrase opens every scene prompt

    def test_the_film_file_is_exactly_as_long_as_planned(self):
        self.film()
        path = Path(self.studio.film['path'])
        self.assertEqual(path.parent.parent.name, 'runs')
        self.assertEqual(sorted(child.name for child in path.parent.iterdir()), ['keyframe-1.png', 'run.json'])      # registered, so the run keeps its keyframe and its log only
        streams = self.studio.film_streams
        video = next(s for s in streams if s['codec_type'] == 'video')
        self.assertEqual((int(video['nb_read_frames']), video['width'], video['height']), (240, 768, 512))
        audio = next(s for s in streams if s['codec_type'] == 'audio')
        self.assertEqual((audio['codec_name'], audio['sample_rate']), ('aac', '48000'))

    def test_the_generated_track_is_heard_under_the_scenes_own_sound(self):
        self.film()
        registered = self.work / 'registered.mp4'
        from test_director_soundtrack import band
        self.assertGreater(band(registered, 2500, 1.0, 3.0), -55)                         # the track (2500 Hz, a bed under the scenes) is in the film
        self.assertGreater(band(registered, 330, 1.0, 3.0), -40)                          # and so is the sound the scenes came with (330 Hz)

    def test_a_film_without_music_or_keyframes_skips_those_steps(self):
        self.studio.caps = dict(keyframes=False, music=[], speech=None)
        status = self.film(music=None)
        self.assertEqual(status['state'], 'done')
        self.assertEqual([call[0] for call in self.studio.calls], ['shoot', 'shoot', 'register', 'forget', 'forget'])
        self.assertEqual(self.studio.calls[2][1], 'film-mix.mp4')                          # native-only films still pass the final loudness stage
        self.assertEqual(status['result'], dict(id='film-1'))
        self.assertEqual([step['id'] for step in status['steps']], ['scene-1', 'scene-2', 'assemble', 'mix'])
        self.assertEqual(status['sound']['scenes_gain_db'], 0.0)
        loudness = status['sound']['mix']['loudness']
        self.assertIsNotNone(loudness['before_lufs'])
        # The synthetic assembly is already loud enough to hit the -6 dB attenuation bound; mastering must report the achieved level rather than promise the target.
        self.assertAlmostEqual(loudness['after_lufs'] - loudness['before_lufs'], loudness['makeup_gain_db'], delta=0.5)
        self.assertLessEqual(loudness['after_true_peak_db'], 0.0)
        saved = json.loads((self.manager.root / 'runs' / status['id'] / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['sound']['mix']['loudness'], loudness)

    # --- speech ---------------------------------------------------------------------------------------------------------------------------
    def speech_plan(self, items):
        lines = [dict(voice=2, scene=1, text='Это начало пути.', language='Russian', delay=0.5), dict(voice=3, scene=2, text='Поехали!', language='Russian', delay=0.3)]
        return dict(speech=lines, music=None, items=items)

    def test_voices_speak_first_and_every_line_lands_in_its_scene(self):
        self.studio.caps = SPEECH_CAPS
        items = self.add_voices()
        status = self.film(caps=SPEECH_CAPS, **self.speech_plan(items))
        self.assertEqual(status['state'], 'done', status.get('error'))
        order = [call[0] for call in self.studio.calls]
        self.assertEqual(order, ['speak', 'forget_speech', 'speak', 'forget_speech', 'keyframe', 'shoot', 'shoot', 'register', 'forget', 'forget'])        # the voices are tried first
        speak = [call for call in self.studio.calls if call[0] == 'speak']
        self.assertEqual(speak[0][1:], ('voxcpm2', 'narrator.wav', ('Это начало пути.',), 'Russian', True))
        self.assertEqual(speak[1][1:], ('voxcpm2', 'Alexey', ('Поехали!',), 'Russian', True))          # a voice is called by the name the person gave it
        self.assertEqual([step['id'] for step in status['steps']], ['speech-2', 'speech-3', 'keyframe-1', 'scene-1', 'scene-2', 'assemble', 'mix'])
        self.assertTrue(all(step['state'] == 'done' for step in status['steps']))
        from test_director_soundtrack import band
        registered = self.work / 'registered.mp4'
        self.assertGreater(band(registered, 1200, 0.7, 0.6), -35)                         # the first job's tone, in the first scene, 0.5 s after it begins
        self.assertGreater(band(registered, 1200, 0.7, 0.6), band(registered, 1200, 5.4, 0.6) + 20)           # and not in the second
        self.assertGreater(band(registered, 1900, 5.4, 0.6), -35)                         # the second job's tone, in the second scene (5 s + 0.3 s)
        self.assertGreater(band(registered, 1900, 5.4, 0.6), band(registered, 1900, 0.7, 0.6) + 20)

    def test_lines_of_one_voice_are_one_job(self):
        self.studio.caps = SPEECH_CAPS
        items = self.add_voices()
        lines = [dict(voice=2, scene=1, text='Первая фраза.'), dict(voice=2, scene=2, text='Вторая фраза.'), dict(voice=2, scene=2, text='Третья фраза.', language='English')]
        status = self.film(caps=SPEECH_CAPS, speech=lines, music=None, items=items)
        self.assertEqual(status['state'], 'done', status.get('error'))
        speak = [call for call in self.studio.calls if call[0] == 'speak']
        self.assertEqual([(call[3], call[4]) for call in speak], [(('Первая фраза.', 'Вторая фраза.'), 'Russian'), (('Третья фраза.',), 'English')])      # one job per voice and language
        self.assertEqual([step['id'] for step in status['steps'] if step['kind'] == 'speech'], ['speech-2'])

    def test_a_line_that_does_not_fit_is_left_out_and_the_film_says_so(self):
        self.studio.caps = SPEECH_CAPS
        self.studio.line_seconds = 40                                                      # far longer than the film
        items = self.add_voices()
        status = self.film(caps=SPEECH_CAPS, **self.speech_plan(items))
        self.assertEqual(status['state'], 'done', status.get('error'))
        self.assertEqual(len(status['notes']), 2)
        self.assertEqual(self.studio.calls[-3][1], 'film-mix.mp4')                          # no lines left, but the native sound still gets mastered
        self.assertEqual([step['state'] for step in status['steps'] if step['kind'] == 'mix'], ['done'])

    def test_lines_the_listener_doubted_are_reported(self):
        self.studio.caps = SPEECH_CAPS
        self.studio.flagged = [dict(key='a'), dict(key='b')]
        status = self.film(caps=SPEECH_CAPS, **self.speech_plan(self.add_voices()))
        self.assertEqual(status['state'], 'done')
        self.assertEqual(len(status['notes']), 2)                                           # one note for each of the two jobs

    def test_a_voice_that_fails_stops_the_film_before_any_shooting(self):
        self.studio.caps = SPEECH_CAPS
        self.studio.fail_speech = True
        status = self.film(caps=SPEECH_CAPS, **self.speech_plan(self.add_voices()))
        self.assertEqual(status['state'], 'error')
        self.assertIn('the voice engine fell over', status['error'])
        self.assertEqual([call[0] for call in self.studio.calls], ['speak'])                  # nothing was shot
        states = {step['id']: step['state'] for step in status['steps']}
        self.assertEqual((states['speech-2'], states['scene-1']), ('failed', 'pending'))
        self.assertFalse(self.manager.busy())

    def test_a_second_engine_takes_over_when_the_first_one_fails(self):
        self.studio.caps = TWO_ENGINES
        self.studio.fail_engines = {'voxcpm2'}
        status = self.film(caps=TWO_ENGINES, **self.speech_plan(self.add_voices()))
        self.assertEqual(status['state'], 'done', status.get('error'))
        self.assertEqual([(call[1]) for call in self.studio.calls if call[0] == 'speak'], ['voxcpm2', 'qwen3-tts-12hz-0.6b-base', 'voxcpm2', 'qwen3-tts-12hz-0.6b-base'])      # for each voice
        self.assertEqual(len(status['notes']), 2)
        self.assertIn('voxcpm2', status['notes'][0])
        self.assertIn('qwen3-tts-12hz-0.6b-base', status['notes'][0])

    def test_when_every_engine_fails_the_film_stops_before_any_shooting(self):
        self.studio.caps = TWO_ENGINES
        self.studio.fail_engines = {'voxcpm2', 'qwen3-tts-12hz-0.6b-base'}
        status = self.film(caps=TWO_ENGINES, **self.speech_plan(self.add_voices()))
        self.assertEqual(status['state'], 'error')
        self.assertIn('the voice engine fell over', status['error'])
        self.assertEqual([call[0] for call in self.studio.calls], ['speak', 'speak'])
        self.assertEqual(len(status['notes']), 1)                                                   # the change of engine was noted, then the last failure ended the film

    def test_a_stop_during_the_speech_ends_the_film(self):
        self.studio.caps = SPEECH_CAPS
        self.studio.hold_speech = threading.Event()
        self.manager.start(self.draft_id, plan_for(REQUEST, SPEECH_CAPS, **self.speech_plan(self.add_voices())), REQUEST)
        self.assertTrue(self.studio.hold_speech.wait(30))
        self.assertTrue(self.manager.stop())
        self.manager.thread.join(30)
        self.assertEqual(self.manager.status()['state'], 'cancelled')
        self.assertEqual([call[0] for call in self.studio.calls], ['speak'])

    def test_a_plan_with_lines_is_refused_when_the_speech_engine_has_gone(self):
        items = self.add_voices()
        spoken = plan_for(REQUEST, SPEECH_CAPS, **self.speech_plan(items))
        self.assertEqual(len(spoken['speech']), 2)
        self.studio.caps = CAPS                                                            # the engine was removed between the plan and the start
        with self.assertRaises(ValueError):
            self.manager.start(self.draft_id, spoken, REQUEST)
        self.assertIsNone(self.manager.thread)
        self.assertEqual(self.gpu.claims, [])

    def test_a_picture_or_a_voice_removed_during_the_film_does_not_break_it(self):
        self.studio.caps = SPEECH_CAPS
        items = self.add_voices()
        self.studio.on_speak = lambda: (self.draft.remove(3), self.draft.remove(1))        # the page removes the second voice and the picture while the first voice speaks
        status = self.film(caps=SPEECH_CAPS, **self.speech_plan(items))
        self.assertEqual(status['state'], 'done', status.get('error'))
        speak = [call for call in self.studio.calls if call[0] == 'speak']
        self.assertEqual([call[2] for call in speak], ['narrator.wav', 'Alexey'])                  # the voice that was removed still spoke: the film keeps its own links
        self.assertEqual(next(call for call in self.studio.calls if call[0] == 'keyframe')[2], 1)       # and so was the picture that was removed still used
        self.assertEqual({key: row['name'] for key, row in status['materials'].items()}, {'1': 'car.png', '2': 'narrator.wav', '3': 'hero.wav'})
        self.assertEqual(sorted(child.name for child in Path(self.manager.root, 'runs', status['id']).iterdir()), ['keyframe-1.png', 'run.json'])      # the links are given back

    # --- songs ----------------------------------------------------------------------------------------------------------------------------
    def test_a_song_of_the_person_is_laid_under_the_film_with_the_generated_track_after_it(self):
        song = self.add_song(30)
        cue = dict(song=2, offset=3.0, start=0.0, seconds=5.0, gain_db=-6.0, fade_in=0.2, fade_out=0.5)
        status = self.film(items=[PHOTO] + song, songs=[cue], music=dict(engine='ace-step-15-turbo', mode='instrumental', caption='western guitar, slow', start=5, gain_db=-12))
        self.assertEqual(status['state'], 'done', status.get('error'))
        self.assertEqual([call[0] for call in self.studio.calls], ['compose', 'track', 'keyframe', 'shoot', 'shoot', 'register', 'forget', 'forget'])
        self.assertEqual(self.studio.calls[0][1:], ('ace-step-15-turbo', 10))             # the generated track is asked for the whole film; the plan says where it begins
        from test_director_soundtrack import band
        registered = self.work / 'registered.mp4'
        self.assertGreater(band(registered, 1000, 1.0, 3.0), -40)                         # the song fills the first five seconds
        self.assertGreater(band(registered, 1000, 1.0, 3.0), band(registered, 1000, 6.5, 2.5) + 20)
        self.assertGreater(band(registered, 2500, 6.5, 2.5), band(registered, 2500, 0.5, 3.0) + 20)               # and the generated track the rest

    def test_songs_alone_need_no_generated_music(self):
        song = self.add_song(12)
        status = self.film(items=[PHOTO] + song, songs=[dict(song=2, offset=0, start=0, seconds=10, gain_db=-8)], music=None)
        self.assertEqual(status['state'], 'done', status.get('error'))
        self.assertEqual([step['id'] for step in status['steps']], ['keyframe-1', 'scene-1', 'scene-2', 'assemble', 'mix'])
        self.assertNotIn('compose', [call[0] for call in self.studio.calls])

    # --- failures and stops -----------------------------------------------------------------------------------------------------------------
    def test_a_scene_that_fails_once_is_shot_again_with_another_seed(self):
        self.studio.fail = {1: 1}
        status = self.film()
        self.assertEqual(status['state'], 'done', status.get('error'))
        seeds = [call[6] for call in self.studio.calls if call[0] == 'shoot']
        self.assertEqual(len(seeds), 3)
        self.assertNotEqual(seeds[0], seeds[1])

    def test_a_scene_that_fails_twice_stops_the_film_with_a_message(self):
        self.studio.fail = {2: 1, 3: 1}                    # the second and the third shot of the film: scene 2 and its second try
        status = self.film()
        self.assertEqual(status['state'], 'error')
        self.assertIn('2', status['error'])
        self.assertEqual([step['state'] for step in status['steps'] if step['id'] == 'scene-2'], ['failed'])
        self.assertFalse(self.manager.busy())

    def test_a_stop_ends_the_film_and_the_slot_is_free_again(self):
        self.studio.hold = threading.Event()
        self.manager.start(self.draft_id, plan_for(REQUEST, CAPS), REQUEST)
        self.assertTrue(self.studio.hold.wait(30))
        self.assertTrue(self.manager.acting(self.manager.token))
        self.assertFalse(self.manager.acting('not the token'))
        self.assertTrue(self.manager.stop())
        self.manager.thread.join(30)
        self.assertEqual(self.manager.status()['state'], 'cancelled')
        self.assertFalse(self.manager.acting(self.manager.token))                    # nobody is acting for a film that ended
        self.assertFalse(self.manager.stop())

    def test_a_mix_that_fails_or_is_stopped_ends_the_film_the_right_way(self):
        with mock.patch.object(soundtrack, 'mixdown', side_effect=DeliveryError('the mix broke')):
            status = self.film()
        self.assertEqual((status['state'], status['error']), ('error', 'the mix broke'))
        self.assertEqual([step['state'] for step in status['steps'] if step['id'] == 'mix'], ['failed'])
        self.assertFalse((Path(self.manager.root) / 'runs' / status['id'] / 'film.mp4').exists())     # no half-made film is left in the run folder
        with mock.patch.object(soundtrack, 'mixdown', side_effect=DeliveryCancelled('stopped')):
            self.assertEqual(self.film()['state'], 'cancelled')

    def test_an_edited_plan_is_checked_again_and_a_busy_card_starts_nothing(self):
        broken = plan_for(REQUEST, CAPS)
        broken['scenes'] = broken['scenes'][:1]
        with self.assertRaises(ValueError):
            self.manager.start(self.draft_id, broken, REQUEST)
        self.assertIsNone(self.manager.thread)
        self.gpu.error = Busy('image works')
        with self.assertRaises(Busy):
            self.manager.start(self.draft_id, plan_for(REQUEST, CAPS), REQUEST)
        self.assertIsNone(self.manager.thread)
        with self.assertRaises(ValueError):
            self.manager.start('../x', plan_for(REQUEST, CAPS), REQUEST)

    def test_a_second_film_cannot_start_while_one_is_running(self):
        self.studio.hold = threading.Event()
        self.manager.start(self.draft_id, plan_for(REQUEST, CAPS), REQUEST)
        self.assertTrue(self.studio.hold.wait(30))
        with self.assertRaises(ValueError):
            self.manager.start(self.draft_id, plan_for(REQUEST, CAPS), REQUEST)
        self.manager.stop()
        self.manager.thread.join(30)

    def test_a_film_keeps_its_draft_from_the_weekly_clean_up(self):
        stamp = time.time() - 9 * 86400
        os.utime(self.draft.folder, (stamp, stamp))
        self.manager.start(self.draft_id, plan_for(REQUEST, CAPS), REQUEST)
        self.manager.thread.join(90)
        self.assertGreater(self.draft.folder.stat().st_mtime, time.time() - 3600)
        self.assertEqual(media.prune_drafts(self.manager.cache, 7), 0)

    def test_a_run_left_running_by_a_closed_studio_is_marked_interrupted(self):
        folder = self.work / 'Director' / 'runs' / ('a' * 32)
        folder.mkdir(parents=True)
        (folder / 'run.json').write_text(json.dumps(dict(id='a' * 32, state='running', steps=[])), encoding='utf-8')
        runner.DirectorManager(self.work / 'Director', self.work / 'cache', FFMPEG, FFPROBE, None, self.studio, self.gpu)
        self.assertEqual(json.loads((folder / 'run.json').read_text(encoding='utf-8'))['state'], 'interrupted')
        self.assertEqual(self.manager.status('a' * 32)['state'], 'interrupted')
        self.assertEqual(self.manager.status('b' * 32)['state'], 'none')

    # --- continuing a film that stopped ---------------------------------------------------------------------------------------------------
    FOUR = dict(seconds=60, scenes=4)             # below a minute a film has exactly two scenes

    def four_scenes(self, caps=None, items=(PHOTO,), **changes):
        """A plan of four scenes (a minute) with a keyframe for the first, so that every kind of step exists."""
        request = dict(REQUEST, **self.FOUR)
        raw = dict(scenes=[scene(15, keyframe='A woman by a red car'), scene(15), scene(15), scene(15)], **changes)
        return plan_for(request, caps or self.studio.caps, items, **raw), request

    def run_film(self, **changes):
        plan_, request = self.four_scenes(**changes)
        self.manager.start(self.draft_id, plan_, request)
        self.manager.thread.join(90)
        return self.manager.status()

    def test_a_film_that_failed_in_the_third_scene_is_continued_without_shooting_the_first_two_again(self):
        self.studio.fail = {3: 1, 4: 1}                        # the third scene and its second try
        failed = self.run_film()
        self.assertEqual(failed['state'], 'error')
        self.assertTrue(failed['resumable'])
        self.assertEqual([step['state'] for step in failed['steps'] if step['kind'] == 'scene'], ['done', 'done', 'failed', 'pending'])
        self.assertEqual(sorted(failed['artifacts']['scenes']), ['0', '1'])                  # the clips of the two finished scenes are recorded
        folder = Path(self.manager.root, 'runs', failed['id'])
        self.assertTrue((folder / 'keyframe-1.png').is_file() and list(folder.glob('material-*')))            # what hours of work made stays in the run folder
        mark = len(self.studio.calls)
        resumed = self.manager.resume(failed['id'])
        self.assertEqual(resumed['state'], 'running')
        self.manager.thread.join(90)
        status = self.manager.status()
        self.assertEqual((status['state'], status['result']), ('done', dict(id='film-1')), status.get('error'))
        again = [call[0] for call in self.studio.calls[mark:]]
        self.assertEqual(again, ['shoot', 'shoot', 'register', 'forget', 'forget', 'forget', 'forget'])        # scenes 3 and 4 only: the keyframe and the track were made before the failure
        self.assertEqual({call[1] for call in self.studio.calls[mark:] if call[0] == 'forget'}, {'clip-1', 'clip-2', 'clip-5', 'clip-6'})      # the clips of both tries leave the history
        self.assertEqual(status['resumed'], 1)
        self.assertIn('не снимались', ' '.join(status['notes']))
        self.assertTrue(all(step['state'] == 'done' for step in status['steps']))
        self.assertEqual(sorted(child.name for child in folder.iterdir()), ['keyframe-1.png', 'run.json'])                    # the links are given back once the film is done
        self.assertEqual(self.gpu.claims, ['director', 'director'])

    def test_a_film_stopped_by_the_person_is_continued_from_the_step_it_stopped_in(self):
        self.studio.hold = threading.Event()
        plan_, request = self.four_scenes()
        self.manager.start(self.draft_id, plan_, request)
        self.assertTrue(self.studio.hold.wait(30))
        self.assertTrue(self.manager.stop())
        self.manager.thread.join(30)
        stopped = self.manager.status()
        self.assertEqual((stopped['state'], stopped['resumable']), ('cancelled', True))
        self.assertEqual([step['state'] for step in stopped['steps'] if step['kind'] == 'keyframe'], ['done'])
        self.studio.hold = None
        mark = len(self.studio.calls)
        self.manager.resume(stopped['id'])
        self.manager.thread.join(90)
        self.assertEqual(self.manager.status()['state'], 'done', self.manager.status().get('error'))
        again = [call[0] for call in self.studio.calls[mark:]]
        self.assertEqual((again.count('shoot'), again.count('keyframe')), (4, 0))                      # nothing had been shot: all four scenes now, and the keyframe is kept

    def test_a_film_interrupted_by_a_closed_studio_is_continued_by_a_new_manager(self):
        self.studio.fail = {2: 1, 3: 1}
        failed = self.run_film()
        self.assertEqual(failed['state'], 'error')
        path = Path(self.manager.root, 'runs', failed['id'], 'run.json')
        saved = json.loads(path.read_text(encoding='utf-8'))
        saved.update(state='running', error='')                                                       # the studio was closed in the middle of the work
        path.write_text(json.dumps(saved), encoding='utf-8')
        mark = len(self.studio.calls)
        fresh = runner.DirectorManager(self.work / 'Director', self.work / 'cache', FFMPEG, FFPROBE, FakeBrain(), self.studio, self.gpu)
        latest = fresh.status()                                                                        # the page asks for "the latest film" after the restart
        self.assertEqual((latest['id'], latest['state'], latest['resumable']), (failed['id'], 'interrupted', True))
        fresh.resume(failed['id'])
        fresh.thread.join(90)
        self.assertEqual(fresh.status()['state'], 'done', fresh.status().get('error'))
        self.assertEqual([call[0] for call in self.studio.calls[mark:]].count('shoot'), 3)             # scene 1 was kept; 2, 3 and 4 are shot

    def test_a_clip_that_is_gone_or_too_short_is_shot_again(self):
        self.studio.fail = {3: 1, 4: 1}
        failed = self.run_film()
        clips = failed['artifacts']['scenes']
        Path(clips['0']['path']).unlink()                                                              # the person deleted the first clip from the Video history
        cut = Path(clips['1']['path'])
        subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=768x512:rate=24:duration=0.5', '-pix_fmt', 'yuv420p',
                        str(cut.with_name('cut.mp4'))], check=True)
        os.replace(cut.with_name('cut.mp4'), cut)                                                      # and the second one is cut off
        mark = len(self.studio.calls)
        self.manager.resume(failed['id'])
        self.manager.thread.join(90)
        status = self.manager.status()
        self.assertEqual(status['state'], 'done', status.get('error'))
        again = self.studio.calls[mark:]
        self.assertEqual([call[0] for call in again].count('shoot'), 4)                                # all four scenes were shot again
        self.assertEqual({call[1] for call in again if call[0] == 'forget'} >= {'clip-1', 'clip-2'}, True)      # the two unusable clips leave the history with the rest
        self.assertEqual(len([call for call in again if call[0] == 'forget']), 6)

    def test_voices_the_keyframe_and_the_track_are_not_made_again(self):
        self.studio.caps = SPEECH_CAPS
        items = self.add_voices()
        speech = [dict(voice=2, scene=1, text='Это начало пути.'), dict(voice=3, scene=2, text='Поехали!')]
        music = dict(engine='ace-step-15-turbo', mode='instrumental', caption='slow guitar', gain_db=-16)
        with mock.patch.object(soundtrack, 'mixdown', side_effect=DeliveryError('the mix broke')):         # everything is made, the last step breaks
            failed = self.run_film(caps=SPEECH_CAPS, items=items, speech=speech, music=music)
        self.assertEqual((failed['state'], failed['error']), ('error', 'the mix broke'))
        self.assertEqual(sorted(failed['artifacts']['speech']), ['0', '1'])
        self.assertEqual(sorted(failed['artifacts']['scenes']), ['0', '1', '2', '3'])
        self.assertIsNotNone(failed['artifacts']['music'])
        mark = len(self.studio.calls)
        self.manager.resume(failed['id'])
        self.manager.thread.join(90)
        status = self.manager.status()
        self.assertEqual(status['state'], 'done', status.get('error'))
        self.assertEqual([call[0] for call in self.studio.calls[mark:]], ['register', 'forget', 'forget', 'forget', 'forget'])         # only the assembly and the mix were done again
        self.assertTrue(all(step['state'] == 'done' for step in status['steps']))

    SHORT_TRACKS = dict(keyframes=True, music=[dict(id='ace-step-15-turbo', modes=['instrumental', 'song'], max_duration=30, about='Songs.')], speech=None)

    def test_a_film_longer_than_the_longest_track_gets_several_tracks_crossfaded_not_a_loop(self):
        self.studio.caps = self.SHORT_TRACKS
        status = self.run_film(caps=self.SHORT_TRACKS)
        self.assertEqual(status['state'], 'done', status.get('error'))
        kinds = [call[0] for call in self.studio.calls]
        self.assertEqual((kinds.count('compose'), kinds.count('track')), (3, 3))                       # 3 x 30 - 2 x 4 = 82 s cover the film of 60 s
        self.assertEqual(next(call for call in self.studio.calls if call[0] == 'compose')[2], 30)
        self.assertEqual(sorted(child.name for child in Path(self.manager.root, 'runs', status['id']).iterdir()), ['keyframe-1.png', 'run.json'])      # the part files are gone
        self.assertEqual([row['loop'] for row in status['sound']['beds']], [False])                  # one bed, not looped: the seams are crossfades

    def test_a_film_that_stopped_in_the_second_track_keeps_the_first_one(self):
        self.studio.caps = self.SHORT_TRACKS
        original = self.studio.track
        broken = []

        def flaky(ident):
            studio = self.studio
            if len([call for call in studio.calls if call[0] == 'track']) == 1 and not broken:
                broken.append(1)
                studio.calls.append(('track', ident))
                raise DirectorError('the music engine fell over')
            return original(ident)
        with mock.patch.object(self.studio, 'track', flaky):
            failed = self.run_film(caps=self.SHORT_TRACKS)
        self.assertEqual(failed['state'], 'error')
        self.assertIn('music engine', failed['error'])
        folder = Path(self.manager.root, 'runs', failed['id'])
        self.assertEqual(sorted(child.name for child in folder.glob('music*')), ['music-1.wav'])      # the first track is kept
        mark = len(self.studio.calls)
        self.manager.resume(failed['id'])
        self.manager.thread.join(90)
        self.assertEqual(self.manager.status()['state'], 'done', self.manager.status().get('error'))
        self.assertEqual([call[0] for call in self.studio.calls[mark:]].count('compose'), 2)           # tracks 2 and 3 only: track 1 was downloaded before the failure

    def test_a_music_engine_that_is_broken_stops_the_film_in_the_first_seconds_not_after_the_shooting(self):
        self.studio.fail_music = True
        status = self.run_film()
        self.assertEqual(status['state'], 'error')
        self.assertIn('the music engine is broken', status['error'])
        self.assertEqual([call[0] for call in self.studio.calls], ['compose'])                          # no keyframe, no scene was shot
        self.assertTrue(status['resumable'])
        self.studio.fail_music = False
        self.manager.resume(status['id'])
        self.manager.thread.join(90)
        self.assertEqual(self.manager.status()['state'], 'done', self.manager.status().get('error'))      # the engine is repaired: the same film goes on

    def test_a_finished_film_or_an_unknown_one_cannot_be_continued(self):
        done = self.film()
        self.assertEqual(done['state'], 'done')
        self.assertFalse(done['resumable'])
        with self.assertRaises(ValueError):
            self.manager.resume(done['id'])
        with self.assertRaises(ValueError):
            self.manager.discard(done['id'])
        with self.assertRaises(ValueError):
            self.manager.resume('f' * 32)
        with self.assertRaises(ValueError):
            self.manager.resume('../x')

    def test_a_film_that_is_given_up_leaves_the_history_and_is_no_longer_offered(self):
        self.studio.fail = {2: 1, 3: 1}
        failed = self.film()
        self.assertEqual(failed['state'], 'error')
        mark = len(self.studio.calls)
        gone = self.manager.discard(failed['id'])
        self.assertTrue(gone['discarded'] and not gone['resumable'])
        self.assertEqual([call[0] for call in self.studio.calls[mark:]], ['forget'])                  # the one finished scene clip leaves the Video history
        folder = Path(self.manager.root, 'runs', failed['id'])
        self.assertEqual(sorted(child.name for child in folder.iterdir()), ['run.json'])
        with self.assertRaises(ValueError):
            self.manager.resume(failed['id'])
        self.assertEqual(self.manager.status()['state'], 'error')                                      # the record stays; it is no longer offered
        self.assertIsNone(runner.DirectorManager(self.work / 'Director', self.work / 'cache', FFMPEG, FFPROBE, FakeBrain(), self.studio, self.gpu)._latest_unfinished())

    def test_materials_that_were_cleaned_make_a_message_and_old_unfinished_films_give_their_files_back(self):
        self.studio.fail = {2: 1, 3: 1}
        failed = self.film()
        folder = Path(self.manager.root, 'runs', failed['id'])
        links = list(folder.glob('material-*'))
        for link in links:
            link.unlink()
        with self.assertRaises(ValueError) as caught:
            self.manager.resume(failed['id'])
        self.assertIn('материал', str(caught.exception))
        self.assertFalse(self.manager.busy())
        stamp = time.time() - 15 * 86400
        (folder / 'line-0.wav').write_bytes(b'x')
        os.utime(folder / 'run.json', (stamp, stamp))
        runner.DirectorManager(self.work / 'Director', self.work / 'cache', FFMPEG, FFPROBE, FakeBrain(), self.studio, self.gpu)
        self.assertEqual(sorted(child.name for child in folder.iterdir()), ['run.json'])               # the keyframe and the line are gone after the fortnight
        self.assertTrue(json.loads((folder / 'run.json').read_text(encoding='utf-8'))['discarded'])

    def test_a_film_with_a_shortened_clip_says_so(self):
        original = FakeStudio.shoot

        def short_shoot(studio, job, prompt, start_png, cancel, progress=None):
            return original(studio, dict(job, frames=job['frames'] // 2), prompt, start_png, cancel, progress)
        with mock.patch.object(FakeStudio, 'shoot', short_shoot):
            status = self.film()
        self.assertEqual(status['state'], 'done', status.get('error'))
        self.assertTrue(any('короче плана' in note for note in status['notes']), status['notes'])

    # --- the page's questions -------------------------------------------------------------------------------------------------------------
    def test_the_steps_carry_their_estimated_work_and_the_time_left_follows_what_the_finished_ones_took(self):
        steps = runner.build_steps(plan_for(dict(REQUEST, seconds=60, scenes=4), CAPS, scenes=[scene(15, keyframe='A woman'), scene(15), scene(15), scene(15)]), True)
        self.assertTrue(all(step['weight'] > 0 for step in steps))
        scenes = [step for step in steps if step['kind'] == 'scene']
        self.assertEqual(len({step['weight'] for step in scenes[:3]}), 1)                              # the same length, the same windows, the same work
        self.assertGreater(scenes[0]['weight'], 110)                                                  # a scene pays its setup and every window it is made of
        now = time.time()
        for step in steps:
            step['state'] = 'pending'
        run = dict(state='running', steps=steps)
        self.assertEqual(runner.DirectorManager._eta(run), round(sum(step['weight'] for step in steps)))      # nothing measured yet: the estimate
        self.assertIsNone(runner.DirectorManager._eta(dict(run, state='done')))
        slow = [step for step in steps if step['kind'] == 'scene'][:3]

        def took(*times):                                                                             # the three scenes finished one after the other, each after `times` times its estimate
            for index, (step, factor) in enumerate(zip(slow, times)):
                finished = now - 100 + 30 * index
                step.update(state='done', started=finished - factor * step['weight'], finished=finished)
        took(2, 2, 2)                                                                                 # three scenes that took twice as long as estimated
        left = [step for step in steps if step['state'] == 'pending']
        scenes_left = sum(step['weight'] for step in left if step['kind'] == 'scene')
        others_left = sum(step['weight'] for step in left if step['kind'] != 'scene')
        self.assertAlmostEqual(runner.DirectorManager._eta(run), scenes_left * 2 + others_left, delta=(scenes_left + others_left) * 0.02)      # the scenes by what scenes took, the rest by their estimates
        # the first scene of a film is slow because the video model is read from the disk: it is left out, so that one slow step does not stretch the rest
        took(10, 1, 1)
        self.assertAlmostEqual(runner.DirectorManager._eta(run), scenes_left + others_left, delta=(scenes_left + others_left) * 0.02)
        took(2, 2, 2)
        pending = scenes_left * 2 + others_left
        running = next(step for step in steps if step['state'] == 'pending')
        running.update(state='running', started=now - 30)
        self.assertLess(runner.DirectorManager._eta(run), pending)                                    # a step in progress has done some of its work

    def test_the_bar_follows_the_work_done_and_the_time_left_is_in_the_status_of_a_running_film(self):
        self.studio.hold = threading.Event()
        self.manager.start(self.draft_id, plan_for(REQUEST, CAPS), REQUEST)
        self.assertTrue(self.studio.hold.wait(30))
        status = self.manager.status()
        self.assertEqual(status['state'], 'running')
        self.assertGreaterEqual(status['eta_seconds'], 0)                                              # (a fake studio is instant, so the finished keyframe makes the measured ratio tiny)
        self.assertGreater(status['progress'], 0)                                                     # the keyframe is done: its share of the work, not one step of six
        self.assertLess(status['progress'], 0.5)
        self.manager.stop()
        self.manager.thread.join(30)
        self.assertIsNone(self.manager.status()['eta_seconds'])

    def test_a_film_is_refused_while_the_disk_is_too_full_and_the_check_scales_with_the_film(self):
        from collections import namedtuple
        usage = namedtuple('usage', 'total used free')
        needed = plan_needed(REQUEST['seconds'])
        self.assertLess(plan_needed(60), plan_needed(600))
        with mock.patch.object(shutil, 'disk_usage', return_value=usage(10 ** 12, 10 ** 12 - needed + 1, needed - 1)):
            with self.assertRaises(ValueError) as caught:
                self.manager.start(self.draft_id, plan_for(REQUEST, CAPS), REQUEST)
        self.assertIn('мало места', str(caught.exception))
        self.assertIsNone(self.manager.thread)                                                        # nothing was started and the card was not taken
        self.assertEqual(self.gpu.claims, [])
        with mock.patch.object(shutil, 'disk_usage', return_value=usage(10 ** 12, 0, 10 ** 12)):
            self.assertEqual(self.film()['state'], 'done')
        with mock.patch.object(shutil, 'disk_usage', side_effect=OSError('unreadable')):             # a drive that cannot be asked is no reason to refuse
            self.assertEqual(self.film()['state'], 'done')

    def test_the_computer_is_kept_awake_for_the_length_of_the_film_and_not_longer(self):
        with mock.patch.object(runner, 'keep_awake') as awake:
            self.assertEqual(self.film()['state'], 'done')
        self.assertEqual([call.args[0] for call in awake.call_args_list], [True, False])
        with mock.patch.object(runner, 'keep_awake') as awake:
            self.studio.fail = {3: 1, 4: 1}                                                              # the first film shot twice: this one's first scene and its second try
            self.assertEqual(self.film()['state'], 'error')
        self.assertEqual([call.args[0] for call in awake.call_args_list], [True, False])                  # a film that fails lets the computer sleep again
        runner.keep_awake(True)                                                                           # the real call: harmless, whatever the system
        runner.keep_awake(False)

    def test_the_page_can_ask_where_the_plan_has_come_to_while_it_is_written(self):
        seen = []

        class SlowBrain(FakeBrain):
            progress = None

            def make_plan(inner, draft, request, capabilities, language):
                inner.progress(dict(stage='outline', first=1, last=20, total=50))
                seen.append(self.manager.planning_state())
                return plan_for(request, capabilities)
        self.manager.brain = SlowBrain()
        self.assertEqual(self.manager.planning_state(), dict(stage=''))
        self.manager.make_plan(self.draft_id, dict(REQUEST), 'Russian')
        self.assertEqual(seen, [dict(stage='outline', first=1, last=20, total=50)])
        self.assertEqual(self.manager.planning_state(), dict(stage=''))                                  # nothing is being written any more
        self.assertIsNone(self.manager.brain.progress)

    def test_the_engine_entry_says_what_is_missing(self):
        ltx = [dict(id='ltx25-distilled', available=True)]
        self.assertTrue(self.manager.profile(ltx)['available'])
        self.assertIn('LTX', self.manager.profile([dict(id='ltx25-distilled', available=False)])['reason'] + 'LTX')
        self.assertFalse(self.manager.profile([dict(id='ltx25-distilled', available=False)])['available'])
        manager = runner.DirectorManager(self.work / 'Director', self.work / 'cache', FFMPEG, FFPROBE, FakeBrain(False), self.studio, self.gpu)
        self.assertEqual(manager.profile(ltx)['reason'], 'no model')
        limits = self.manager.profile(ltx)['limits']
        self.assertEqual((limits['max_seconds'], limits['max_song_seconds'], limits['voice_sample_seconds']), (900, 3600, 30))
        self.assertEqual([(row['id'], row['limit']) for row in limits['groups'] if row['media'] == 'voice'], [('narrator', 3), ('character', 10)])
        with self.assertRaises(ValueError):
            runner.DirectorManager(self.work / 'Director', self.work / 'cache', FFMPEG, FFPROBE, None, self.studio, self.gpu).make_plan(self.draft_id, dict(REQUEST), 'Russian')

    # --- the Ultra toggle -------------------------------------------------------------------------------------------------------------------
    def test_the_ultra_flag_travels_from_the_page_to_the_brain(self):
        for sent, expected in ((True, True), (False, False), ('yes', False), (1, False), (None, False)):
            with self.subTest(sent=sent):
                self.manager.make_plan(self.draft_id, dict(REQUEST, ultra=sent), 'English')
                self.assertIs(self.manager.brain.requests[-1][0]['ultra'], expected)
        self.manager.make_plan(self.draft_id, dict(REQUEST), 'English')
        self.assertIs(self.manager.brain.requests[-1][0]['ultra'], False)                    # a request without the flag is a normal one

    def test_an_ultra_film_gets_a_keyframe_for_every_scene_and_the_style_for_a_picture_drawn_from_words(self):
        request = dict(REQUEST, ultra=True)
        plain = [scene(5.0, references=[]), scene(5.0, references=[])]
        self.manager.start(self.draft_id, plan_for(request, self.studio.caps, scenes=plain), request)
        self.manager.thread.join(90)
        status = self.manager.status()
        self.assertEqual(status['state'], 'done', status.get('error'))
        self.assertTrue(status['plan']['ultra'] and status['request']['ultra'])
        self.assertEqual([step['id'] for step in status['steps']], ['music', 'keyframe-1', 'keyframe-2', 'scene-1', 'scene-2', 'assemble', 'mix'])
        keyframes = [call for call in self.studio.calls if call[0] == 'keyframe']
        self.assertEqual([call[2] for call in keyframes], [0, 0])                                  # no photos: both were drawn from the words
        self.assertTrue(all(call[1].startswith('35mm film. ') for call in keyframes))               # and the film's look was put in front of them
        self.assertEqual([call[2] for call in self.studio.calls if call[0] == 'shoot'], ['i2v', 'i2v'])

    def test_the_picture_model_is_asked_in_its_own_numbers(self):
        """The plan says "picture 2" for the second picture of the draft; Klein is given the pictures of the scene in order and counts them from one."""
        self.draft.add_photo(png(), 'harbour.png')
        items = (PHOTO, dict(number=2, kind='photo', group='place'))
        words = 'The woman from picture 1 walks along the harbour of picture 2 at dawn'
        self.manager.start(self.draft_id, plan_for(REQUEST, self.studio.caps, items, scenes=[scene(5.0, references=[2, 1], keyframe=words), scene(5.0, references=[1])]), REQUEST)
        self.manager.thread.join(90)
        sent = [call for call in self.studio.calls if call[0] == 'keyframe']
        self.assertEqual(sent[0][1:], ('The woman from picture 2 walks along the harbour of picture 1 at dawn', 2, 'landscape'))        # the harbour is the scene's first picture now
        self.assertEqual(self.manager.status()['plan']['scenes'][0]['keyframe'], words)                                                  # and the plan shows what it showed

    def add_tall_photo(self):
        buffer = io.BytesIO()
        Image.new('RGB', (200, 300), (30, 60, 180)).save(buffer, 'PNG')
        self.draft.add_photo(buffer.getvalue(), 'tall.png')              # number 2: a hero who is taller than wide

    def test_the_pictures_of_a_keyframe_are_given_in_the_shape_of_the_film(self):
        """Klein draws on the proportions of the first picture: for a landscape film the wide picture of the scene goes first, and the words follow the new order."""
        self.add_tall_photo()
        items = (dict(PHOTO, group='place'), dict(number=2, kind='photo', group='hero'))
        words = 'The woman from picture 2 stands in the harbour of picture 1'
        self.manager.start(self.draft_id, plan_for(REQUEST, self.studio.caps, items, scenes=[scene(5.0, references=[2, 1], keyframe=words), scene(5.0, references=[1])]), REQUEST)
        self.manager.thread.join(90)
        sent = [call for call in self.studio.calls if call[0] == 'keyframe'][0]
        self.assertEqual((sent[1], sent[2]), (words, 2))                           # picture 2 is still the second one the model gets, picture 1 the first
        self.assertEqual([Image.open(io.BytesIO(raw)).size for raw in self.studio.keyframe_photos[0]], [(300, 200), (200, 300)])

    def test_a_tall_photo_alone_goes_to_the_picture_model_on_a_wide_canvas(self):
        self.add_tall_photo()
        items = (PHOTO, dict(number=2, kind='photo', group='hero'))
        words = 'The woman walks along the harbour at dawn'
        self.manager.start(self.draft_id, plan_for(REQUEST, self.studio.caps, items, scenes=[scene(5.0, references=[2], keyframe=words), scene(5.0, references=[1])]), REQUEST)
        self.manager.thread.join(90)
        sent = [call for call in self.studio.calls if call[0] == 'keyframe'][0]
        self.assertTrue(sent[1].startswith(words + '. ') and sent[1].endswith(media.PAD_NOTE['landscape']), sent[1])
        self.assertEqual([Image.open(io.BytesIO(raw)).size for raw in self.studio.keyframe_photos[0]], [media.FRAME_CANVAS['landscape']])

    def test_a_keyframe_made_of_photos_keeps_its_own_words(self):
        request = dict(REQUEST, ultra=True)
        self.manager.start(self.draft_id, plan_for(request, self.studio.caps), request)
        self.manager.thread.join(90)
        words = [call[1] for call in self.studio.calls if call[0] == 'keyframe']
        self.assertEqual(words[0], 'A woman by a red car')                                         # Gemma's own keyframe: a photo already gives the look
        self.assertEqual(words[1], 'Show the person from picture 1. Photorealistic western film, warm light.')

    def test_planning_goes_through_the_brain_and_adds_an_estimate(self):
        result = self.manager.make_plan(self.draft_id, dict(REQUEST), 'English')
        self.assertEqual(len(result['scenes']), 2)
        self.assertGreater(result['estimate_minutes'], 1)
        with self.assertRaises(ValueError):
            self.manager.make_plan(self.draft_id, dict(REQUEST, scenes=3), 'English')
        with self.assertRaises(ValueError):
            self.manager.make_plan(self.draft_id, dict(REQUEST, prompt=' '), 'English')

    def test_the_draft_is_shown_without_paths_and_with_the_advice_on_running_time(self):
        shown = self.manager.draft(self.draft_id)
        self.assertEqual([row['number'] for row in shown['items']], [1])
        self.assertNotIn('file', shown['items'][0])
        self.assertEqual(shown['advice']['reasons'][0]['code'], 'baseline')
        song = self.add_song(100)
        self.assertEqual(self.manager.draft(self.draft_id)['advice']['ideal'], 100)
        added = self.manager.label(self.draft_id, song[0]['number'], 'Main song')
        self.assertEqual([row['label'] for row in added['items']], ['', 'Main song'])
        removed = self.manager.detach(self.draft_id, song[0]['number'])
        self.assertEqual([row['number'] for row in removed['items']], [1])
        self.assertEqual(removed['advice']['ideal'], 30)

    def test_an_attachment_can_be_played_back_by_its_number_only(self):
        song = self.add_song(5)
        path, kind = self.manager.media_file(self.draft_id, song[0]['number'])
        self.assertEqual((path.suffix, kind), ('.m4a', 'audio/mp4'))
        self.assertEqual(self.manager.media_file(self.draft_id, 1)[1], 'image/png')
        for number in (99, 'x', True, None, -1):
            with self.subTest(number=number), self.assertRaises(ValueError):
                self.manager.media_file(self.draft_id, number)
        with self.assertRaises(ValueError):
            self.manager.media_file('../..', 1)


class StepTests(unittest.TestCase):
    def test_steps_follow_the_plan(self):
        full = plan_for(REQUEST, CAPS)
        self.assertEqual([step['id'] for step in runner.build_steps(full, True)], ['music', 'keyframe-1', 'scene-1', 'scene-2', 'assemble', 'mix'])
        self.assertEqual([step['id'] for step in runner.build_steps(full, False)], ['music', 'scene-1', 'scene-2', 'assemble', 'mix'])
        silent = plan_for(REQUEST, CAPS, music=None)
        self.assertEqual([step['id'] for step in runner.build_steps(silent, False)], ['scene-1', 'scene-2', 'assemble', 'mix'])
        self.assertEqual(runner.scene_prompt(full, 0)[:12], '35mm film Ph')
        self.assertEqual(runner.scene_prompt(dict(full, style=dict(phrase='')), 1), PROMPT)

    def test_every_voice_that_speaks_has_a_step_in_front_and_songs_have_a_mix_but_no_track(self):
        items = [PHOTO, dict(number=2, kind='voice', group='narrator', seconds=9.0), dict(number=5, kind='voice', group='character', seconds=9.0), dict(number=7, kind='audio', group='song', seconds=60.0)]
        lines = [dict(voice=5, scene=1, text='Привет, мир.'), dict(voice=2, scene=2, text='Пока, мир.'), dict(voice=5, scene=2, text='Ещё слово.')]
        both = plan_for(REQUEST, SPEECH_CAPS, items, speech=lines, music=None)
        self.assertEqual([step['id'] for step in runner.build_steps(both, False)], ['speech-5', 'speech-2', 'scene-1', 'scene-2', 'assemble', 'mix'])        # in the order of their first line
        song = plan_for(REQUEST, CAPS, items, music=None, songs=[dict(song=7, offset=0, start=0, seconds=8)])
        self.assertEqual([step['id'] for step in runner.build_steps(song, False)], ['scene-1', 'scene-2', 'assemble', 'mix'])


if __name__ == '__main__':
    unittest.main()
