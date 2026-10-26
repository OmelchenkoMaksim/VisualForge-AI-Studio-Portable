"""Focused safety checks for Music; no GPU inference or Image suite."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from music.registry import validate
from music.storage import publish, resolve_path, stored_path
from music.manager import MusicManager
from music.routes import handle
from gpu import GpuCoordinator

class MusicTests(unittest.TestCase):
    def test_request_preserves_song_and_seed(self):
        row = validate({'caption': 'Acoustic folk', 'mode': 'song', 'lyrics': 'Тихая река', 'seed': '0'})
        self.assertEqual((row['lyrics'], row['seed']), ('Тихая река', 0))
        self.assertTrue(row['planner'])

    def test_request_rejects_missing_lyrics_and_oversize(self):
        for data in ({'caption':'test','mode':'song'}, {'caption':'test','duration':181},
                     {'caption':'test','seed':2**32}, {'caption':'test','planner':'false'}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate(data)

    def test_portable_and_external_output_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'Studio'
            root.mkdir()
            internal = root / 'UserData/Results/Music'
            external = Path(temporary) / 'External'
            self.assertEqual(stored_path(internal, root), 'UserData/Results/Music')
            self.assertEqual(resolve_path(stored_path(external, root), root), external)
            with self.assertRaises(ValueError):
                resolve_path('../escape', root)

    def test_publish_never_overwrites_or_deletes_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.wav'
            source.write_bytes(b'original waveform bytes')
            row = {'id':'a'*32,'caption':'Test music','seed':42,'mode':'instrumental'}
            output = publish(source, root/'Results', row, root)
            output.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):
                publish(source, root/'Results', row, root)
            self.assertEqual(output.read_bytes(), b'keep')
            self.assertEqual(json.loads(output.with_suffix('.json').read_text())['seed'], 42)

    def test_cancel_rejects_other_job_and_wrong_token(self):
        manager = MusicManager.__new__(MusicManager)
        import threading
        manager.lock = threading.RLock()
        manager.active = {'id':'our-job','token':'secret','state':'running'}
        manager.owned = Mock()
        manager.process = Mock()
        for data in ({'id':'other','token':'secret'},{'id':'our-job','token':'wrong'}):
            with self.assertRaises(ValueError):
                manager.cancel(data)
        manager.owned.close.assert_not_called()
        manager.cancel({'id':'our-job','token':'secret'})
        manager.owned.close.assert_called_once()

    def coordinator(self, **busy):
        release = Mock()
        return GpuCoordinator({name: (lambda state=state: state) for name, state in ({'image': False, 'video': False, 'text': False} | busy).items()},
                              dict(image=release, text=release), lambda: 16000), release

    def test_music_admission_preserves_busy_image(self):
        handler, manager = Mock(), Mock()
        manager.busy.return_value = False
        manager.availability.return_value = {'available':True}
        gpu, release = self.coordinator(image=True)
        handle(handler, manager, gpu, 'generate', {'caption':'Piano'}, '')
        self.assertEqual(handler.response_json.call_args.args[0], 409)
        self.assertIn('Image', handler.response_json.call_args.args[1]['error'])
        manager.submit.assert_not_called()
        release.assert_not_called()

    def test_music_admission_preserves_busy_video_and_text(self):
        for busy in ('video', 'text'):
            with self.subTest(busy=busy):
                handler, manager = Mock(), Mock()
                manager.busy.return_value = False
                manager.availability.return_value = {'available':True}
                gpu, release = self.coordinator(**{busy: True})
                handle(handler, manager, gpu, 'generate', {'caption':'Piano'}, '')
                self.assertEqual(handler.response_json.call_args.args[0], 409)
                manager.submit.assert_not_called()
                release.assert_not_called()

    def test_music_start_frees_the_idle_modules_and_submits(self):
        handler, manager = Mock(), Mock()
        manager.busy.return_value = False
        manager.availability.return_value = {'available':True}
        manager.submit.return_value = {'id':'job'}
        gpu, release = self.coordinator()
        handle(handler, manager, gpu, 'generate', {'caption':'Piano'}, '')
        self.assertEqual(handler.response_json.call_args.args[:2], (200, {'id':'job'}))
        self.assertEqual(release.call_count, 2)

    def test_music_refuses_when_it_is_already_running(self):
        handler, manager = Mock(), Mock()
        manager.busy.return_value = True
        gpu, release = self.coordinator()
        handle(handler, manager, gpu, 'generate', {'caption':'Piano'}, '')
        self.assertEqual(handler.response_json.call_args.args[0], 409)
        manager.submit.assert_not_called()
        release.assert_not_called()

if __name__ == '__main__':
    unittest.main()
