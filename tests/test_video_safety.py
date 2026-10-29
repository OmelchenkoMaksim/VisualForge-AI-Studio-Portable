"""Targeted validation, persistence and owned-process checks without model inference."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from video.video_registry import validate
from video.video_service import Jobs
from video.owned_processes import OwnedJob
from video.video_manager import VideoManager
from portable_paths import from_server
from video.video_output import filename_base, copy_unique
from video.video_runtime import environment
from video.video_adapters import build_command


class VideoSafetyTests(unittest.TestCase):
    def setUp(self):
        parent = Path(tempfile.gettempdir()) / 'visualforge-tests'  # the system temp folder: ASCII, whatever folder the checkout lives in
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.tmp.name)
        self.args = argparse.Namespace(root=str(self.root / 'runtime'), models=str(self.root / 'models'), user=str(self.root / 'UserData'), state=str(self.root / 'state'))
        self.jobs = Jobs(self.args)

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_preserves_uint64(self):
        self.assertEqual(validate({'prompt': 'test', 'seed': '18446744073709551615'})['seed'], '18446744073709551615')
        for seed in ['18446744073709551616', -1, True, '1.0', '١٢', '1e3']:
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                validate({'prompt': 'test', 'seed': seed})

    def test_filename_uses_subject_and_copy_never_overwrites(self):
        base = filename_base('The small yellow toy robot gently waves one hand. Soft studio lighting.', 0)
        self.assertTrue(base.startswith('Small Yellow Toy Robot Gently Waves One Hand - '))
        self.assertTrue(filename_base('A red ball rolls slowly from left to right across a matte gray floor.', 0).startswith('Red Ball Rolls Slowly From Left To Right - '))
        source = self.root / 'source.mp4'
        source.write_bytes(b'new fixture')
        destination = self.root / 'results'
        destination.mkdir()
        (destination / (base + '.mp4')).write_bytes(b'existing fixture')
        saved = copy_unique(source, destination, base)
        self.assertEqual(saved.read_bytes(), b'new fixture')
        self.assertEqual((destination / (base + '.mp4')).read_bytes(), b'existing fixture')

    def test_unverified_modes_and_profiles_rejected(self):
        for extra in [{'mode': 'v2v'}, {'references': []}, {'frames': 240}, {'width': 1024}, {'width': True}, {'fps': 30}, {'quality': 'ultra'}, {'prompt': ' '}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate({'prompt': 'test', **extra})

    def test_long_profile_is_preview_t2v_only_and_builds_exact_frames(self):
        row = validate({'prompt': 'test', 'frames': 243, 'seed': '123'})
        folder = self.root / 'job'
        folder.mkdir()
        command = build_command(self.root / 'runtime', self.root / 'models', folder, row)
        self.assertEqual(command[command.index('--frames') + 1], '243')
        self.assertIn('--preview', command)
        for extra in [{'quality': 'high'}, {'quality': 'standard'}, {'mode': 'i2v', 'source_image': 'unused'}, {'mode': 'first_last', 'source_image': 'unused', 'end_image': 'unused'}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate({'prompt': 'test', 'frames': 243, **extra})

    def test_preparation_failure_releases_gpu_claim(self):
        ident = str(uuid.uuid4())
        row = dict(validate({'prompt': 'test'}), id=ident, state='queued', phase='queued', events=[], submitted_at=time.time(), folder=str(self.jobs.user / 'Cache/Video/Jobs' / ident))
        self.jobs.rows[ident] = row
        self.jobs.active = ident
        self.jobs.run(ident)
        self.assertEqual(row['state'], 'error')
        self.assertIsNone(self.jobs.active)

    def test_minute_characters_voice_validation(self):
        person = {'name': 'Robot', 'image': 'data:image/png;base64,unused', 'speech_text': 'Hello'}
        with self.assertRaises(ValueError):
            validate({'prompt': 'test', 'mode': 'characters', 'characters': [person]})
        person['voice_audio'] = 'data:audio/wav;base64,unused'
        row = validate({'prompt': 'test', 'mode': 'characters', 'frames': 1440, 'characters': [person]})
        folder = self.root / 'job'
        folder.mkdir()
        command = build_command(self.root / 'runtime', self.root / 'models', folder, row)
        self.assertTrue(any('video_pipeline.py' in s for s in command))
        self.assertEqual(row['frames'], 1440)
        with self.assertRaises(ValueError):
            validate({'prompt': 'test', 'mode': 'characters', 'characters': [person] * 4})

    def test_conditioning_requires_frames(self):
        for mode in ['i2v', 'first_last']:
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.jobs.check_request({'prompt': 'test', 'mode': mode})
        with self.assertRaises(ValueError):
            self.jobs.check_request({'prompt': 'test', 'mode': 'i2v', 'source_image': 'data:image/png;base64,YmFk'})
        self.assertIsNone(self.jobs.active)

    def test_external_absolute_output_preserved(self):
        selected = self.root / 'External - Кириллица'
        result = self.jobs.save_settings({'save_dir': str(selected)})
        self.assertEqual(Path(result['save_dir']), selected)
        self.assertEqual(json.loads(self.jobs.settings_path.read_text(encoding='utf-8'))['save_dir'], str(selected))

    def test_unicode_state_uses_ascii_compiler_cache_only(self):
        state = self.root / 'Кеш Video'
        models = self.root / 'Модели'
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'host-local')}):
            env = environment(self.root / 'Runtime', models, state)
        self.assertTrue(env['TORCHINDUCTOR_CACHE_DIR'].isascii())
        self.assertTrue(env['TRITON_CACHE_DIR'].isascii())
        self.assertTrue(env['CUDA_CACHE_PATH'].isascii())
        self.assertEqual(env['FREEVIDEO_MODEL_ROOT'], str(models))
        self.assertEqual(env['FREEVIDEO_HOME'], str(state))

    def test_internal_output_moves_with_userdata(self):
        self.jobs.save_settings({'save_dir': str(self.jobs.user / 'Results/Video')})
        stored = json.loads(self.jobs.settings_path.read_text())
        self.assertEqual(stored['save_dir'], 'Results/Video')
        other = self.root / 'Moved - Кириллица/UserData'
        (other / 'Settings').mkdir(parents=True)
        (other / 'Settings/video.settings.json').write_text(json.dumps(stored))
        args = argparse.Namespace(**{**vars(self.args), 'user': str(other)})
        self.assertEqual(Jobs(args).settings()['save_dir'], str(other / 'Results/Video'))

    def test_corrupt_history_preserved(self):
        path = self.jobs.history_path
        path.parent.mkdir(parents=True)
        path.write_text('{broken')
        restored = Jobs(self.args)
        restored.persist()
        self.assertFalse(restored.history_writable)
        self.assertEqual(path.read_text(), '{broken')

    def test_prompt_archive_does_not_modify_image(self):
        image = self.jobs.user / 'Prompts/favorites.json'
        image.parent.mkdir(parents=True)
        image.write_bytes(b'Image archive fixture')
        self.jobs.prompts({'action': 'add', 'prompt': 'video test'})
        self.assertEqual(image.read_bytes(), b'Image archive fixture')
        self.assertEqual(self.jobs.prompts()['entries'][0]['module'], 'video')

    def test_relative_history_is_confined(self):
        path = self.jobs.history_path
        path.parent.mkdir(parents=True)
        row = {'id': str(uuid.uuid4()), 'state': 'done', 'output_path': '../../escape.mp4'}
        path.write_text(json.dumps({'schema_version': 1, 'entries': [row]}))
        self.assertFalse(Jobs(self.args).history_writable)

    @unittest.skipUnless(os.name == 'nt', 'Windows ownership contract')
    def test_slow_busy_probe_keeps_the_service_and_the_last_answer(self):
        # Closing the job object kills the service and its running generation: a slow /active must never do that.
        paths = from_server()
        manager = VideoManager(paths)
        job = type('Job', (), {'closed': False, 'close': lambda self: setattr(self, 'closed', True)})()
        manager.process, manager.job = type('Alive', (), {'poll': lambda self: None})(), job
        answers = iter([{'busy': True}, OSError('timed out'), {'busy': False}])

        def call(route, data=None, timeout=15):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer
        manager.call = call
        self.assertEqual([manager.busy(), manager.busy(), manager.busy()], [True, True, False])
        self.assertFalse(job.closed)
        manager.process, manager.job = None, None

    def test_active_probe_answers_while_a_log_is_read(self):
        # /active (the busy probe) needs jobs.lock; reading a job's logs or decoding a picture must not hold it.
        ident = str(uuid.uuid4())
        folder = self.root / 'UserData/Cache/Video/Jobs' / ident
        folder.mkdir(parents=True)
        (folder / 'backend.log').write_text('line\n', encoding='utf-8')
        self.jobs.rows[ident] = dict(id=ident, state='running', submitted_at=time.time(), folder=str(folder), events=[])
        self.jobs.active = ident
        from http.server import ThreadingHTTPServer
        import threading
        import urllib.request
        from video.video_service import make_handler
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.jobs, 'secret', lambda: None))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def get(path):
            request = urllib.request.Request('http://127.0.0.1:%d%s' % (server.server_address[1], path), headers={'Authorization': 'Bearer secret'})
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read())
        original, gate = Path.read_text, threading.Event()

        def slow_read(path, *args, **kwargs):
            if path.suffix == '.log':
                gate.wait(5)  # a cold hard drive
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', slow_read):
            reader = threading.Thread(target=get, args=('/logs?id=' + ident,), daemon=True)
            reader.start()
            time.sleep(0.3)
            started = time.monotonic()
            self.assertTrue(get('/active')['busy'])
            self.assertLess(time.monotonic() - started, 1.0)
            gate.set()
            reader.join(5)

    def test_job_cleanup_leaves_unrelated_process_alive(self):
        control = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
        child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
        job = None
        try:
            job = OwnedJob(child)
            job.close()
            child.wait(timeout=5)
            self.assertIsNone(control.poll())
        finally:
            if job:
                job.close()
            for process in (child, control):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
