"""The Director's HTTP hands against a stand-in studio: what it sends for each kind of job, how it waits, and how a refusal or a stop is understood."""
import base64
import json
import sys
import threading
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))

from director.studio import DirectorCancelled, DirectorError, Studio, StudioBusy  # noqa: E402


class StandIn:
    """A studio that records every request and answers from a table of canned replies (a list is consumed one answer per request)."""

    def __init__(self):
        self.replies, self.requests = {}, []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def handle_any(self):
                body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                data = json.loads(body) if body else None
                owner.requests.append((self.command, self.path, data, self.headers.get('X-VisualForge-Run')))
                reply = owner.replies.get(self.path.split('?')[0], (200, {}))
                if isinstance(reply, list):
                    reply = reply.pop(0) if len(reply) > 1 else reply[0]
                code, value = reply
                raw = value if isinstance(value, bytes) else json.dumps(value).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            do_GET = do_POST = handle_any

            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def paths(self):
        return [path.split('?')[0] for _, path, _, _ in self.requests]


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.standin = StandIn()
        self.addCleanup(self.standin.close)
        self.video = SimpleNamespace(calls=[], call=lambda route, data=None, timeout=15: self.video.calls.append((route, data)) or dict(id='film-1', deleted=True))
        self.studio = Studio(self.standin.port, 'run-token', self.video, pause=lambda seconds: None)
        self.cancel = threading.Event()

    def test_every_request_carries_the_token_and_a_refusal_keeps_the_studios_words(self):
        self.standin.replies['/api/x'] = (400, dict(error='Нет такой модели'))
        with self.assertRaises(DirectorError) as caught:
            self.studio.request('/api/x', dict(a=1))
        self.assertEqual(str(caught.exception), 'Нет такой модели')
        self.assertEqual(self.standin.requests[0][3], 'run-token')
        self.standin.replies['/api/y'] = (409, dict(error='GPU busy'))
        with self.assertRaises(StudioBusy):
            self.studio.request('/api/y', dict())
        with self.assertRaises(DirectorError):
            Studio(1, 'token', self.video, pause=lambda seconds: None).request('/api/z')                        # nothing listens there

    def test_a_busy_card_is_waited_out_and_then_the_job_starts(self):
        self.standin.replies['/api/video/generate'] = [(409, dict(error='GPU busy')), (409, dict(error='GPU busy')), (200, dict(id='job-1'))]
        self.assertEqual(self.studio.start('/api/video/generate', dict())['id'], 'job-1')
        self.assertEqual(self.standin.paths().count('/api/video/generate'), 3)

    def test_a_scene_is_started_with_its_start_picture_and_waited_for(self):
        self.standin.replies['/api/video/generate'] = (200, dict(id='job-1', cancel_token='secret'))
        self.standin.replies['/api/video/status'] = [(200, dict(state='running', phase='Сцена 1')), (200, dict(state='done', saved_path='C:/clips/a.mp4'))]
        phases = []
        shot = self.studio.shoot(dict(engine='ltx25-distilled', frames=49), 'A fox runs.', b'\x89PNG', self.cancel, phases.append)
        self.assertEqual(shot, dict(id='job-1', path='C:/clips/a.mp4'))
        sent = next(data for _, path, data, _ in self.standin.requests if path == '/api/video/generate')
        self.assertEqual((sent['prompt'], sent['frames']), ('A fox runs.', 49))
        self.assertTrue(sent['source_image'].startswith('data:image/png;base64,'))
        self.assertEqual(phases, ['Сцена 1', None])
        failed = self.standin.replies['/api/video/status'] = (200, dict(state='error', error='out of memory'))
        with self.assertRaises(DirectorError) as caught:
            self.studio.shoot(dict(), 'A fox runs.', None, self.cancel)
        self.assertEqual(str(caught.exception), 'out of memory')
        self.assertIsNotNone(failed)

    def test_a_stop_cancels_the_job_in_the_studio_and_raises(self):
        self.standin.replies['/api/video/generate'] = (200, dict(id='job-1', cancel_token='secret'))
        self.standin.replies['/api/video/status'] = [(200, dict(state='running')), (200, dict(state='cancelled'))]
        self.cancel.set()
        with self.assertRaises(DirectorCancelled):
            self.studio.shoot(dict(), 'A fox runs.', None, self.cancel)
        self.assertIn(('POST', '/api/video/cancel', dict(id='job-1', cancel_token='secret'), 'run-token'), self.standin.requests)

    def test_a_keyframe_uses_the_klein_mode_that_fits_the_number_of_pictures(self):
        self.standin.replies['/api/generate'] = (200, dict(prompt_id='img-1', cancel_token='t'))
        self.standin.replies['/api/status'] = (200, dict(state='done'))
        self.standin.replies['/api/result'] = (200, b'PNGDATA')
        modes = {}
        for count in (0, 1, 2, 3, 5, 12):
            self.standin.requests.clear()
            self.assertEqual(self.studio.keyframe('A woman by a car', [b'p%d' % i for i in range(count)], 'portrait', self.cancel), b'PNGDATA')
            sent = next(data for _, path, data, _ in self.standin.requests if path == '/api/generate')
            modes[count] = (sent['mode'], 'image1' in sent, 'image2' in sent, len(sent.get('extra_images', [])), sent['canvas'])
        self.assertEqual(modes[0], ('prompt', False, False, 0, 'portrait'))
        self.assertEqual(modes[1], ('single', True, False, 0, 'portrait'))
        self.assertEqual(modes[2], ('reference', True, True, 0, 'portrait'))
        self.assertEqual(modes[3], ('multi', True, True, 1, 'portrait'))
        self.assertEqual(modes[5], ('multi', True, True, 3, 'portrait'))
        self.assertEqual(modes[12], ('multi', True, True, 8, 'portrait'))                      # the studio takes ten pictures at most
        self.assertEqual(base64.b64decode(sent['image1']['base64']), b'p0')

    def test_music_is_composed_and_its_audio_fetched(self):
        self.standin.replies['/api/music/generate'] = (200, dict(id='m' * 32, token='t'))
        self.standin.replies['/api/music/status'] = (200, dict(state='done'))
        self.standin.replies['/api/music/result'] = (200, b'RIFFWAVE')
        music = dict(engine='ace-step-15-turbo', caption='western guitar', lyrics='', mode='instrumental', seconds=30, language='en', gain_db=-16.0, loop=False)
        self.assertEqual(self.studio.compose(music, self.cancel), 'm' * 32)
        sent = next(data for _, path, data, _ in self.standin.requests if path == '/api/music/generate')
        self.assertEqual((sent['duration'], sent['mode'], sent['engine']), (30, 'instrumental', 'ace-step-15-turbo'))
        self.assertEqual(self.studio.track('m' * 32), b'RIFFWAVE')
        self.assertIn(('GET', '/api/music/result?id=' + 'm' * 32, None, 'run-token'), self.standin.requests)

    def speech_replies(self, state='done'):
        self.standin.replies['/api/voiceover/voice-add'] = (200, dict(id='v' * 32, name='Narrator', duration=12.0))
        self.standin.replies['/api/voiceover/prepare'] = (200, dict(book_id='b' * 32))
        self.standin.replies['/api/voiceover/generate'] = (200, dict(id='j' * 32, token='secret'))
        self.standin.replies['/api/voiceover/voice-delete'] = (200, dict(deleted=True))
        self.standin.replies['/api/voiceover/delete'] = (200, dict(deleted=True))
        self.standin.replies['/api/voiceover/status'] = [(200, dict(state='running', phase='Синтез')), (200, dict(
            state=state, saved_path='C:/out/voiceover.wav', error='the engine fell over', flagged=[dict(key='k')],
            chapters=[dict(title='Chapter 2', start=4.0, end=6.5), dict(title='Chapter 1', start=0.0, end=2.5)]))]

    def test_lines_are_spoken_by_a_voice_that_exists_only_for_the_job(self):
        self.speech_replies()
        phases = []
        result = self.studio.speak('voxcpm2', b'RIFFsample', 'Alexey', ['First line.', 'Second line.'], 'Russian', self.cancel, phases.append)
        self.assertEqual(result, dict(job='j' * 32, path='C:/out/voiceover.wav', spans={0: (0.0, 2.5), 1: (4.0, 6.5)}, flagged=[dict(key='k')]))
        sent = {path: data for _, path, data, _ in self.standin.requests if data is not None}
        self.assertEqual(sent['/api/voiceover/voice-add']['name'], 'Alexey')
        self.assertIs(sent['/api/voiceover/voice-add']['consent'], True)                              # the person confirmed the right to the voice on the page
        self.assertEqual(base64.b64decode(sent['/api/voiceover/voice-add']['audio'].split(',', 1)[1]), b'RIFFsample')
        self.assertEqual(sent['/api/voiceover/prepare']['text'], 'Chapter 1\nFirst line.\n\nChapter 2\nSecond line.')     # every line is a chapter: the file says where each begins
        self.assertEqual({key: sent['/api/voiceover/generate'][key] for key in ('engine', 'language', 'format', 'book_id', 'voice_id')},
                         dict(engine='voxcpm2', language='Russian', format='wav', book_id='b' * 32, voice_id='v' * 32))
        self.assertEqual(phases, ['Синтез', None])
        self.assertEqual(self.standin.paths()[-1], '/api/voiceover/voice-delete')                      # the sample does not stay in the library
        self.assertEqual(sent['/api/voiceover/voice-delete'], dict(id='v' * 32))
        self.assertTrue(all(token == 'run-token' for _, _, _, token in self.standin.requests))
        self.studio.forget_speech('j' * 32)
        self.assertEqual(self.standin.requests[-1][1:3], ('/api/voiceover/delete', dict(id='j' * 32)))

    def test_a_failed_job_still_removes_the_voice_and_says_why(self):
        self.speech_replies('error')
        with self.assertRaises(DirectorError) as caught:
            self.studio.speak('qwen3-tts-12hz-0.6b-base', b'x', 'Voice', ['Only line.'], 'English', self.cancel)
        self.assertEqual(str(caught.exception), 'the engine fell over')
        self.assertIn('/api/voiceover/voice-delete', self.standin.paths())
        self.assertEqual(self.standin.requests[-1][1:3], ('/api/voiceover/delete', dict(id='j' * 32)))             # and the failed job does not stay in the person's Voiceover history

    def test_a_stop_cancels_the_job_and_removes_the_voice(self):
        self.speech_replies('cancelled')
        self.cancel.set()
        with self.assertRaises(DirectorCancelled):
            self.studio.speak('voxcpm2', b'x', 'Voice', ['Only line.'], 'English', self.cancel)
        self.assertIn(('POST', '/api/voiceover/cancel', dict(id='j' * 32, token='secret'), 'run-token'), self.standin.requests)
        self.assertIn('/api/voiceover/voice-delete', self.standin.paths())
        self.assertIn('/api/voiceover/delete', self.standin.paths())

    def test_a_job_that_is_not_in_the_history_yet_is_forgotten_a_moment_later(self):
        self.standin.replies['/api/voiceover/delete'] = [(400, dict(error='still running')), (200, dict(deleted=True))]
        self.studio.forget_speech('j' * 32)
        self.assertEqual(self.standin.paths().count('/api/voiceover/delete'), 2)
        self.standin.replies['/api/voiceover/delete'] = (400, dict(error='no such job'))
        self.studio.forget_speech('j' * 32)                                                            # a job that cannot be deleted never fails a film
        self.assertEqual(self.standin.paths().count('/api/voiceover/delete'), 4)

    def test_a_file_that_lacks_a_line_is_not_accepted(self):
        self.speech_replies()
        with self.assertRaises(DirectorError):
            self.studio.speak('voxcpm2', b'x', 'Voice', ['First.', 'Second.', 'Third.'], 'Russian', self.cancel)               # the stand-in's file has two chapters

    def test_capabilities_read_what_is_installed_and_ready(self):
        self.standin.replies['/api/preflight'] = (200, dict(engines={'flux3-klein-int8': dict(ready=True)}))
        self.standin.replies['/api/music/availability'] = (200, dict(engines=[dict(id='ace-step-15-turbo', available=True, max_duration=180), dict(id='minimax-music3-int8', available=False),
                                                                              dict(id='stable-audio-3-medium', available=True, max_duration=120)]))
        found = self.studio.capabilities()
        self.assertTrue(found['keyframes'])
        self.assertEqual([row['id'] for row in found['music']], ['ace-step-15-turbo', 'stable-audio-3-medium'])
        self.assertEqual(found['music'][1]['modes'], ['audio'])
        self.standin.replies['/api/preflight'] = (502, dict(error='ComfyUI is down'))
        self.assertFalse(self.studio.capabilities()['keyframes'])

    def test_the_speech_engine_is_the_best_installed_one(self):
        def engines(*rows):
            self.standin.replies['/api/voiceover/availability'] = (200, dict(engines=list(rows)))
        qwen, voxcpm = dict(id='qwen3-tts-12hz-0.6b-base', available=True, languages=['Russian', 'English', 'Klingon']), dict(id='voxcpm2', available=True, languages=['Russian', 'Korean'])
        engines(qwen, voxcpm)
        self.assertEqual(self.studio.capabilities()['speech'], dict(engine='voxcpm2', engines=['voxcpm2', 'qwen3-tts-12hz-0.6b-base'], languages=['Russian', 'Korean']))
        engines(qwen, dict(voxcpm, available=False))
        self.assertEqual(self.studio.capabilities()['speech'], dict(engine='qwen3-tts-12hz-0.6b-base', engines=['qwen3-tts-12hz-0.6b-base'], languages=['Russian', 'English']))      # a language the Director does not know is not offered
        engines(dict(qwen, available=False))
        self.assertIsNone(self.studio.capabilities()['speech'])
        self.standin.replies['/api/voiceover/availability'] = (503, dict(error='no Voiceover'))
        self.assertIsNone(self.studio.capabilities()['speech'])
        engines(qwen, voxcpm)
        with unittest.mock.patch.dict('os.environ', {'VISUALFORGE_DIRECTOR_SPEECH_ENGINE': 'qwen3-tts-12hz-0.6b-base'}):
            self.assertEqual(self.studio.capabilities()['speech']['engines'], ['qwen3-tts-12hz-0.6b-base'])

    def test_a_film_is_registered_and_a_clip_forgotten_through_the_video_service(self):
        self.assertEqual(self.studio.register(dict(path='x')), dict(id='film-1', deleted=True))
        self.studio.forget_clip('clip-1')
        self.assertEqual(self.video.calls, [('/register', dict(path='x')), ('/delete', dict(id='clip-1'))])

    def test_a_job_that_never_ends_is_stopped(self):
        self.standin.replies['/api/video/status'] = (200, dict(state='running'))
        stops = []
        with self.assertRaises(DirectorError):
            self.studio.wait(lambda: self.studio.request('/api/video/status'), self.cancel, stops.append, limit=-1)
        self.assertEqual(len(stops), 1)


if __name__ == '__main__':
    unittest.main()
