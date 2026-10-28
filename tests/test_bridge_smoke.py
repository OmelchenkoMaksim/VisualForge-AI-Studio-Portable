"""The real bridge process on throw-away state: every route family must answer, and nothing may crash a request thread.

server.py cannot be unit-tested piece by piece (it builds its managers on import), and a route that still called a changed
function signature once went unnoticed. This starts the actual server.py from a copy of App and walks the routes."""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

DEV = Path(__file__).resolve().parents[1]
TEXT_MODEL_ABSENT_OK = True


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class BridgeSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='vf-bridge-'))
        shutil.copytree(DEV / 'App', cls.root / 'App', ignore=shutil.ignore_patterns('__pycache__'))
        (cls.root / 'UserData/Settings').mkdir(parents=True)
        cls.port = free_port()
        ports = {free_port() for _ in range(6)} - {cls.port}
        env = {**os.environ, 'VISUALFORGE_PORTABLE_ROOT': str(cls.root), 'QWEN_UI_PORT': str(cls.port), 'QWEN_COMFY_PORT': str(sorted(ports)[0]),
               'VISUALFORGE_VIDEO_PORT': str(sorted(ports)[1]), 'VISUALFORGE_TEXT_PORT': str(sorted(ports)[2]), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONIOENCODING': 'utf-8'}
        cls.log = cls.root / 'bridge.log'
        cls.stream = cls.log.open('wb')
        cls.process = subprocess.Popen([sys.executable, '-u', str(cls.root / 'App/server/server.py')], cwd=str(cls.root), env=env, stdout=cls.stream, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                cls.call('/health')
                return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError('bridge did not start:\n' + cls.log.read_text(encoding='utf-8', errors='replace')[-2000:])

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try:
            cls.process.wait(10)
        except subprocess.TimeoutExpired:
            cls.process.kill()
        cls.stream.close()
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def call(cls, path, data=None, timeout=30):
        request = urllib.request.Request('http://127.0.0.1:%d%s' % (cls.port, path), data=None if data is None else json.dumps(data).encode('utf-8'),
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def json(self, path, data=None):
        status, raw = self.call(path, data)
        return status, json.loads(raw or b'{}')

    def test_server_module_imports_without_the_script_folder_on_sys_path(self):
        # The installed launcher starts the bridge with the embedded Python, which does not put the script's folder on
        # sys.path (isolated mode reproduces that); the other tests here use the system Python and cannot see such a failure.
        code = "import runpy, sys; runpy.run_path(sys.argv[1], run_name='probe')"
        done = subprocess.run([sys.executable, '-I', '-c', code, str(self.root / 'App/server/server.py')], capture_output=True, text=True, timeout=120,
                              env={**os.environ, 'VISUALFORGE_PORTABLE_ROOT': str(self.root), 'PYTHONIOENCODING': 'utf-8'})
        self.assertEqual(done.returncode, 0, done.stderr[-1500:])

    def test_voiceover_routes_answer_through_the_bridge(self):
        status, availability = self.json('/api/voiceover/availability')
        self.assertEqual(status, 200)
        self.assertFalse(availability['available'], 'the throw-away root has no model or runtime')
        status, voices = self.json('/api/voiceover/voices')
        self.assertEqual((status, voices['voices'], voices['builtin']), (200, [], []))
        self.assertEqual((voices['defaults']['state'], voices['defaults']['ready'], voices['defaults']['wanted']), ('idle', 0, 8))
        self.assertEqual(self.json('/api/voiceover/history'), (200, {'entries': []}))
        self.assertEqual(self.json('/api/voiceover/settings')[0], 200)
        status, book = self.json('/api/voiceover/prepare', {'title': 'Тест', 'text': 'Глава 1\n\nПервый абзац.\n\nГлава 2\n\nВторой абзац.'})
        self.assertEqual(status, 200)
        self.assertEqual([row['title'] for row in book['chapters']], ['Глава 1', 'Глава 2'])
        self.assertEqual(self.json('/api/voiceover/prepare', {'text': '   '})[0], 400)
        self.assertEqual(self.json('/api/voiceover/generate', {'book_id': 'bad'})[0], 400)
        self.assertEqual(self.json('/api/voiceover/generate', {'book_id': book['book_id'], 'voice_id': 'b' * 32})[0], 503)
        self.assertEqual(self.json('/api/voiceover/unknown')[0], 404)

    def test_launch_readiness_is_reported_by_the_page_for_its_own_launch(self):
        self.assertEqual(self.json('/api/launch-ready?launch=abcdefgh12345678'), (200, {'ready': False, 'ok': False}))
        self.assertEqual(self.json('/api/launch-ready', {'launch': '../x', 'ok': True})[0], 400)
        self.assertEqual(self.json('/api/launch-ready', {'launch': 'abcdefgh12345678', 'ok': True}), (200, {'ready': True}))
        self.assertEqual(self.json('/api/launch-ready?launch=abcdefgh12345678'), (200, {'ready': True, 'ok': True}))
        self.assertEqual(self.json('/api/launch-ready?launch=otherlaunch00000')[1]['ready'], False)

    def test_unified_event_history_records_only_authorized_microphone_events(self):
        code, started = self.json('/api/event-history/session', {'action':'start'})
        self.assertEqual(code, 200)
        record = started['generation']
        self.assertEqual(record['type'], 'MicrophoneSession')
        self.assertEqual(len(record['id']), 64)
        code, page = self.json('/api/event-history?type=MicrophoneSession')
        self.assertEqual(code, 200)
        self.assertTrue(any(row['generation']['id'] == record['id'] and row['state'] == 'running' for row in page['entries']))
        code, stopped = self.json('/api/event-history/session', {'action':'finish', 'id':record['id'], 'state':'done'})
        self.assertEqual(code, 200)
        self.assertIsNotNone(stopped['generation']['duration_sec'])
        self.assertNotIn(record['id'], self.log.read_text(encoding='utf-8', errors='replace'), 'technical session IDs must stay out of the existing launcher/HTTP log')
        self.assertEqual(self.json('/api/event-history?type=invalid')[0], 400)
        self.assertEqual(self.json('/api/event-history/session', {'action':'start', 'type':'Text'})[0], 400)
        self.assertEqual(self.json('/api/event-history/session', {'action':'finish', 'id':'f'*64})[0], 400)

    def test_every_asset_linked_from_the_page_is_served(self):
        # The bridge serves only an explicit STATIC list; a stylesheet or script added to index.html but not to that list would 404 silently.
        page = (self.root / 'App/frontend/index.html').read_text(encoding='utf-8')
        assets = sorted(set(re.findall(r'(?:href|src)="(/[^"/]+\.(?:css|js))"', page)))
        self.assertIn('/theme.css', assets)
        self.assertEqual({path: self.call(path)[0] for path in assets}, {path: 200 for path in assets})

    def test_every_route_family_answers_and_no_request_thread_crashes(self):
        reads = ['/api/hardware', '/hardware.js', '/hardware.css', '/model-requirements.js', '/health', '/api/busy', '/api/settings', '/api/history', '/api/favorites', '/api/video/availability', '/api/video/history',
                 '/api/music/availability', '/api/music/history', '/api/music/active', '/api/music/settings', '/api/text/availability',
                 '/api/text/status', '/api/text/chats', '/api/music/status?id=none', '/api/video/status?id=none', '/api/components', '/api/speech/availability']
        results = {path: self.call(path)[0] for path in reads}
        # Whatever the module's answer is (200, 400 or 503 when its runtime is not installed), it must be an answer, never a dropped connection.
        self.assertTrue(all(status in (200, 400, 404, 409, 502, 503) for status in results.values()), results)
        self.assertEqual(results['/health'], 200)
        self.assertEqual(results['/api/hardware'], 200)
        hardware = self.json('/api/hardware')[1]
        self.assertIn('cpu', hardware['hardware'])
        self.assertIn('video:ltx25-distilled', hardware['models'])
        self.assertIn(hardware['models']['video:ltx25-distilled']['status'], ('green','yellow','red'))
        self.assertEqual(results['/hardware.js'], 200)
        self.assertEqual(results['/hardware.css'], 200)
        self.assertEqual(results['/model-requirements.js'], 200)
        self.assertEqual(hardware['models']['video:ltx25-distilled']['profile']['storage']['model_bytes'], 41162142158)
        self.assertEqual(self.json('/api/busy'), (200, {'busy': False, 'modules': [], 'labels': []}))
        self.assertEqual(results['/api/music/history'], 200)
        self.assertEqual(results['/api/music/active'], 200)
        self.assertEqual(results['/api/text/chats'], 200)
        self.assertEqual(results['/api/components'], 200)
        modules = self.json('/api/components')[1]['modules']
        self.assertEqual([m['id'] for m in modules], ['core', 'image', 'video', 'music', 'text', 'speech', 'voiceover'])

    def test_posts_of_every_module_are_routed(self):
        self.assertEqual(self.json('/api/stop-generation', {}), (200, {'stopped': []}))
        status, chat = self.json('/api/text/chats', {'think': False})
        self.assertEqual(status, 200)
        self.assertEqual(self.json('/api/text/chat-rename', {'id': chat['id'], 'title': 'Smoke'})[1]['title'], 'Smoke')
        self.assertEqual(self.json('/api/text/chat-delete', {'id': chat['id']}), (200, {'deleted': True}))
        for path, body in (('/api/music/generate', {'caption': 'Piano'}), ('/api/video/generate', {'engine': 'nope'}), ('/api/generate', {}),
                           ('/api/music/cancel', {'id': 'x', 'token': 'y'}), ('/api/cancel', {'prompt_id': 'x'}),
                           ('/api/history/delete', {'id': 'x'}), ('/api/music/delete', {'id': 'x'}), ('/api/video/delete', {'id': 'x'})):
            self.assertIn(self.call(path, body)[0], (400, 403, 409, 502, 503), path)
        self.assertEqual(self.json('/api/components/cancel', {}), (200, {'cancelled': False}))
        status, answer = self.json('/api/speech/availability')
        self.assertEqual((status, answer['available']), (200, False))
        self.assertEqual(answer['enhance']['available'], False, 'no Text model in this throw-away root')
        self.assertIn('Компоненты', answer['reason'])
        self.assertEqual(self.json('/api/speech/unload', {}), (200, {'unloaded': True}))
        self.assertEqual(self.json('/api/speech/settings', {'language': 'xx'})[0], 400)
        request = urllib.request.Request('http://127.0.0.1:%d/api/speech/transcribe' % self.port, data=b'RIFF' + b'\x00' * 4000, headers={'Content-Type': 'audio/wav'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                code = response.status
        except urllib.error.HTTPError as error:
            code = error.code
        self.assertEqual(code, 400)
        self.assertEqual(self.call('/api/components/install', {'models': ['no.such.model']})[0], 400)
        self.assertEqual(self.call('/api/nothing')[0], 404)
        self.assertEqual(self.call('/api/nothing', {})[0], 404)

    def test_the_bridge_log_has_no_unhandled_exception(self):
        self.call('/api/music/history')
        self.call('/api/video/availability')
        time.sleep(0.5)
        text = self.log.read_text(encoding='utf-8', errors='replace')
        self.assertNotIn('Traceback', text)
        self.assertNotIn('Exception occurred during processing', text)


if __name__ == '__main__':
    unittest.main()
