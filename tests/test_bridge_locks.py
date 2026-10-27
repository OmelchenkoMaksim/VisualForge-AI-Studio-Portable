"""The real bridge on throw-away state, with the native folder dialog replaced by one that stays open: Stop and the other
POSTs must not wait behind it (they once queued behind the GPU submission lock for as long as the dialog was open)."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

DEV = Path(__file__).resolve().parents[1]
LAUNCHER = r'''
import sys, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'App/server'))
import server
open_dialog = threading.Event()
def choose_save_dir(initial=None):
    open_dialog.wait(8)   # the person has not picked a folder yet
    return None
server.choose_save_dir = choose_save_dir
server.ThreadingHTTPServer(('127.0.0.1', server.PORT), server.Handler).serve_forever(poll_interval=0.25)
'''


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class BridgeLocks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='vf-locks-'))
        shutil.copytree(DEV / 'App', cls.root / 'App', ignore=shutil.ignore_patterns('__pycache__'))
        (cls.root / 'UserData/Settings').mkdir(parents=True)
        (cls.root / 'launch.py').write_text(LAUNCHER, encoding='utf-8')
        cls.port = free_port()
        ports = sorted({free_port() for _ in range(6)} - {cls.port})
        env = {**os.environ, 'VISUALFORGE_PORTABLE_ROOT': str(cls.root), 'QWEN_UI_PORT': str(cls.port), 'QWEN_COMFY_PORT': str(ports[0]),
               'VISUALFORGE_VIDEO_PORT': str(ports[1]), 'VISUALFORGE_TEXT_PORT': str(ports[2]), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONIOENCODING': 'utf-8'}
        cls.stream = (cls.root / 'bridge.log').open('wb')
        cls.process = subprocess.Popen([sys.executable, '-u', str(cls.root / 'launch.py')], cwd=str(cls.root), env=env, stdout=cls.stream, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                cls.call('/health')
                return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError('bridge did not start')

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
    def call(cls, path, data=None, timeout=20):
        request = urllib.request.Request('http://127.0.0.1:%d%s' % (cls.port, path), data=None if data is None else json.dumps(data).encode('utf-8'),
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b'{}')

    def timed(self, path, data=None):
        started = time.monotonic()
        status, answer = self.call(path, data)
        return status, answer, time.monotonic() - started

    def test_open_folder_dialog_blocks_nothing_else(self):
        self.call('/api/busy')  # no ComfyUI here: the bridge remembers the refused connection (2 s on Windows) for 10 s
        dialog = threading.Thread(target=self.call, args=('/api/choose-save-dir', {}), daemon=True)
        dialog.start()
        time.sleep(0.5)
        status, answer, seconds = self.timed('/api/stop-generation', {})
        self.assertEqual(status, 200, answer)
        self.assertLess(seconds, 1.5)                       # was: until the dialog closed
        status, answer, seconds = self.timed('/api/favorites', {'action': 'add', 'prompt': 'кот на крыше'})
        self.assertEqual((status, answer.get('added')), (200, True))
        self.assertLess(seconds, 1.5)
        status, answer, _ = self.timed('/api/choose-save-dir', {})    # a second dialog is refused, not stacked
        self.assertEqual(status, 409, answer)
        self.assertIn('уже открыто', answer['error'])
        status, _, seconds = self.timed('/api/busy')
        self.assertEqual(status, 200)
        self.assertLess(seconds, 1.5)
        dialog.join(15)


if __name__ == '__main__':
    unittest.main()
