"""The Director's HTTP side with a fake request handler: what it answers for each route and each kind of mistake."""
import io
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from director import media, plan_rules, routes, runner  # noqa: E402
from gpu_coordinator import Busy  # noqa: E402
from test_director_media import SONG_GRAPH, make_sound, speech_graph  # noqa: E402
from test_director_runner import CAPS, FFMPEG, FFPROBE, FakeBrain, FakeGpu, FakeStudio, png  # noqa: E402


class Handler:
    """What the bridge's request handler offers a route: a body to read, headers to look at, and the two ways to answer (JSON, or a file with byte ranges)."""

    def __init__(self, body=b'', length=None, headers=None):
        self.rfile = io.BytesIO(body)
        self.headers = {'Content-Length': str(len(body) if length is None else length), **(headers or {})}
        self.answer, self.status, self.sent, self.wfile = None, None, {}, io.BytesIO()

    def response_json(self, code, data):
        self.answer = (code, data)

    def send_response(self, code):
        self.status = code

    def send_header(self, name, value):
        self.sent[name] = value

    def end_headers(self):
        pass


class RoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sounds = Path(tempfile.mkdtemp(prefix='vf-director-routes-'))
        cls.song, cls.voice = cls.sounds / 'song.wav', cls.sounds / 'voice.wav'
        make_sound(cls.song, SONG_GRAPH)
        make_sound(cls.voice, speech_graph(8))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.sounds, ignore_errors=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        work = Path(self.temp.name)
        self.gpu, self.brain = FakeGpu(), FakeBrain()
        self.manager = runner.DirectorManager(work / 'Director', work / 'cache', FFMPEG, FFPROBE, self.brain, FakeStudio(work, CAPS), self.gpu)
        self.uploads = work / 'uploads'
        self.draft = media.new_draft_id()

    def post(self, route, **data):
        handler = Handler()
        routes.handle_post(handler, self.manager, route, data)
        return handler.answer

    def get(self, route, query='', headers=None):
        handler = Handler(headers=headers)
        routes.handle_get(handler, self.manager, route, query)
        return handler

    def attach(self, group, body, draft=None, name='x.png', length=None, extra=''):
        handler = Handler(body, length)
        routes.attach(handler, self.manager, '?draft=%s&group=%s&name=%s%s' % (draft or self.draft, group, name, extra), self.uploads)
        return handler.answer

    # --- attaching ------------------------------------------------------------------------------------------------------------------------
    def test_a_photo_is_attached_listed_and_detached(self):
        code, answer = self.attach('place', png(), name='my%20car.png')
        item = answer['item']
        self.assertEqual((code, item['number'], item['name'], item['kind'], item['group']), (200, 1, 'my car.png', 'photo', 'place'))
        self.assertNotIn('file', item)                                               # the path of the stored file never leaves the server
        self.assertEqual([row['number'] for row in answer['items']], [1])
        self.assertEqual(answer['advice']['reasons'][0]['code'], 'baseline')          # every change of the materials brings the advice on running time with it
        self.assertEqual([row['number'] for row in self.get('draft', '?id=' + self.draft).answer[1]['items']], [1])
        code, left = self.post('detach', draft=self.draft, number=1)
        self.assertEqual((code, left['items']), (200, []))
        self.assertEqual(self.post('detach', draft=self.draft, number='x')[0], 400)

    def test_the_older_query_with_a_kind_still_names_the_first_group_of_that_kind(self):
        handler = Handler(png())
        routes.attach(handler, self.manager, '?draft=%s&kind=photo&name=x.png' % self.draft, self.uploads)
        self.assertEqual((handler.answer[0], handler.answer[1]['item']['group']), (200, 'hero'))

    def test_a_video_streams_to_disk_and_leaves_no_upload_behind(self):
        from test_director_media import make_clip
        clip = Path(self.temp.name) / 'clip.mkv'
        make_clip(clip, seconds=2)
        code, answer = self.attach('video', clip.read_bytes(), name='style.mkv')
        self.assertEqual((code, answer['item']['kind'], answer['item']['width']), (200, 'video', 640))
        self.assertEqual(list(self.uploads.glob('*')), [])

    def test_a_song_streams_to_disk_is_converted_and_leaves_no_upload_behind(self):
        code, answer = self.attach('song', self.song.read_bytes(), name='my%20song.wav')
        item = answer['item']
        self.assertEqual((code, item['kind'], item['group'], item['name']), (200, 'audio', 'song', 'my song.wav'))
        self.assertAlmostEqual(item['seconds'], 40.0, delta=0.3)
        self.assertEqual(answer['advice']['ideal'], 40)                                # the song is best heard whole
        self.assertEqual(list(self.uploads.glob('*')), [])

    def test_a_voice_needs_the_confirmation_of_the_right_to_it(self):
        code, answer = self.attach('narrator', self.voice.read_bytes(), name='v.wav')
        self.assertEqual(code, 400)
        self.assertIn('голос', answer['error'])
        self.assertEqual(self.manager.draft(self.draft)['items'], [])
        self.assertEqual(list(self.uploads.glob('*')), [])                            # refused before the body was read
        code, answer = self.attach('narrator', self.voice.read_bytes(), name='v.wav', extra='&consent=1')
        self.assertEqual((code, answer['item']['kind'], answer['item']['group']), (200, 'voice', 'narrator'))
        self.assertEqual(self.attach('character', self.voice.read_bytes(), extra='&consent=0')[0], 400)       # only an exact yes counts

    def test_a_group_that_is_full_takes_no_more(self):
        for _ in range(plan_rules.LIMIT_OF['narrator']):
            self.assertEqual(self.attach('narrator', self.voice.read_bytes(), extra='&consent=1')[0], 200)
        code, answer = self.attach('narrator', self.voice.read_bytes(), extra='&consent=1')
        self.assertEqual(code, 400)
        self.assertIn(str(plan_rules.LIMIT_OF['narrator']), answer['error'])
        self.assertEqual(self.attach('character', self.voice.read_bytes(), extra='&consent=1')[0], 200)      # the characters have their own ten
        self.assertEqual(list(self.uploads.glob('*')), [])

    def test_mistakes_are_answered_before_anything_big_is_read(self):
        self.assertEqual(self.attach('hero', png(), draft='../../x')[0], 400)
        self.assertEqual(self.attach('movie', png())[0], 400)                         # no such group
        self.assertEqual(self.attach('hero', b'', length=0)[0], 400)
        self.assertEqual(self.attach('hero', b'not a picture')[0], 400)
        self.assertEqual(self.attach('video', b'x' * 100, length=100)[0], 400)           # not a video: refused after the conversion, nothing kept
        self.assertEqual(self.attach('song', b'x' * 100, length=100)[0], 400)            # nor a song
        self.assertEqual(self.attach('song', self.voice.read_bytes(), name='v.wav', length=plan_rules.MAX_SONG_BYTES + 1)[0], 400)       # a body over the limit
        self.assertEqual(self.attach('hero', self.song.read_bytes())[0], 400)            # sound is not a picture
        self.assertEqual(self.manager.draft(self.draft)['items'], [])
        self.assertEqual(list(self.uploads.glob('*')), [])
        handler = Handler()
        routes.attach(handler, None, '?draft=1', self.uploads)
        self.assertEqual(handler.answer[0], 503)

    # --- names and playback -----------------------------------------------------------------------------------------------------------------
    def test_an_attachment_is_named_through_a_route(self):
        self.attach('hero', png())
        code, answer = self.post('label', draft=self.draft, number=1, text='  Алексей  ')
        self.assertEqual((code, answer['items'][0]['label']), (200, 'Алексей'))
        self.assertEqual(self.post('label', draft=self.draft, number=9, text='x')[0], 400)
        self.assertEqual(self.post('label', draft='nope', number=1, text='x')[0], 400)

    def test_an_attachment_is_played_with_byte_ranges_by_its_number(self):
        self.attach('song', self.song.read_bytes())
        whole = self.get('media', '?draft=%s&number=1' % self.draft)
        self.assertEqual((whole.status, whole.sent['Content-Type']), (200, 'audio/mp4'))
        size = int(whole.sent['Content-Length'])
        self.assertEqual(len(whole.wfile.getvalue()), size)
        part = self.get('media', '?draft=%s&number=1' % self.draft, headers={'Range': 'bytes=0-99'})
        self.assertEqual((part.status, len(part.wfile.getvalue()), part.sent['Content-Range'].startswith('bytes 0-99/')), (206, 100, True))
        self.assertEqual(self.get('media', '?draft=%s&number=1' % self.draft, headers={'Range': 'bytes=999999999-'}).answer[0], 416)
        self.assertEqual(self.get('media', '?draft=%s&number=7' % self.draft).answer[0], 404)
        self.assertEqual(self.get('media', '?draft=../..&number=1').answer[0], 404)
        self.assertEqual(self.get('media', '?draft=%s&number=abc' % self.draft).answer[0], 404)

    # --- planning and shooting --------------------------------------------------------------------------------------------------------------
    def test_planning_starting_and_stopping_go_through_the_manager(self):
        request = dict(draft=self.draft, prompt='A road trip', seconds=10, scenes=2, orientation='landscape', quality='draft')
        code, plan = self.post('plan', **request)
        self.assertEqual((code, len(plan['scenes'])), (200, 2))
        self.assertEqual(self.post('plan', **dict(request, scenes=5))[0], 400)
        self.assertEqual(self.post('plan', **dict(request, prompt=''))[0], 400)
        self.assertEqual(self.post('cancel', id='a' * 32)[0], 400)
        self.assertEqual(self.post('nothing')[0], 404)
        code, run = self.post('start', **dict(request, plan=plan))
        self.assertEqual((code, run['state']), (200, 'running'))
        self.manager.thread.join(60)
        handler = Handler()
        routes.handle_get(handler, self.manager, 'status', '?id=' + run['id'])
        self.assertEqual(handler.answer[1]['state'], 'done')
        routes.handle_get(handler, self.manager, 'status', '')
        self.assertEqual(handler.answer[1]['id'], run['id'])
        routes.handle_get(handler, self.manager, 'elsewhere', '')
        self.assertEqual(handler.answer[0], 404)

    def test_the_ultra_toggle_of_the_page_reaches_the_planner(self):
        for flag in (True, False):
            self.post('plan', draft=self.draft, prompt='A road trip', seconds=10, scenes=2, ultra=flag)
        self.post('plan', draft=self.draft, prompt='A road trip', seconds=10, scenes=2)
        self.assertEqual([request['ultra'] for request, language in self.brain.requests], [True, False, False])

    def test_the_language_of_the_page_reaches_the_planner(self):
        self.post('plan', draft=self.draft, prompt='A road trip', seconds=10, scenes=2, language='English')
        self.post('plan', draft=self.draft, prompt='A road trip', seconds=10, scenes=2, language='Klingon')
        self.assertEqual([(request['language'], language) for request, language in self.brain.requests], [('English', 'English'), ('Russian', 'Russian')])

    def test_a_busy_card_is_a_409_and_a_missing_module_a_503(self):
        self.gpu.error = Busy('video works')
        plan = self.post('plan', draft=self.draft, prompt='x y', seconds=10, scenes=2)[1]
        self.assertEqual(self.post('start', draft=self.draft, prompt='x y', seconds=10, scenes=2, plan=plan)[0], 409)
        handler = Handler()
        routes.handle_post(handler, None, 'plan', {})
        self.assertEqual(handler.answer[0], 503)
        handler = Handler()
        routes.handle_get(handler, None, 'media', '')
        self.assertEqual(handler.answer[0], 503)

    def test_the_routes_the_module_describes_are_the_ones_the_bridge_hands_to_it(self):
        documented = set(re.findall(r'(?:GET|POST)\s+/api/director/(\w+)', (ROOT / 'App/server/director/routes.py').read_text(encoding='utf-8')))
        self.assertEqual(documented, {'draft', 'status', 'planning', 'media', 'attach', 'detach', 'label', 'plan', 'start', 'cancel', 'resume', 'discard'})
        bridge = (ROOT / 'App/server/studio_server.py').read_text(encoding='utf-8-sig')
        for fragment in ("u.path.startswith('/api/director/')", "route == '/api/director/attach'", "route.startswith('/api/director/')"):
            self.assertIn(fragment, bridge)                                            # GET goes to `handle_get`, the raw upload to `attach`, every other POST to `handle_post`
        for route in documented - {'attach'}:
            with self.subTest(route=route):
                handler = Handler()
                if route in ('draft', 'status', 'planning', 'media'):
                    routes.handle_get(handler, self.manager, route, '?id=' + self.draft if route == 'draft' else '')
                else:
                    routes.handle_post(handler, self.manager, route, {})
                self.assertNotEqual((handler.answer or (200,))[0], 500)


if __name__ == '__main__':
    unittest.main()
