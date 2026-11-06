"""Engine selection and conservative admission checks; no inference."""
from pathlib import Path
import sys
import threading
import unittest
import tempfile
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from music.music_registry import ENGINE, MINIMAX, validate
from music.music_manager import MusicManager


class MiniMaxMusicTests(unittest.TestCase):
    def test_float_resampling_avoids_clipping_and_applies_constant_gain_with_clean_edges(self):
        import numpy as np
        from music.minimax_worker import save_minimax_audio
        ffmpeg = Path(__file__).resolve().parents[1]/'Runtime/Video/ffmpeg/bin/ffmpeg.exe'
        with tempfile.TemporaryDirectory() as temporary:
            for rate, level in ((44100, 1.4), (32000, .2)):
                signal = level * np.cos(2*np.pi*1000*np.arange(rate)/rate)
                stereo = np.column_stack((signal, signal*.5))
                target = Path(temporary)/f'minimax-{rate}.wav'
                delivery = save_minimax_audio(stereo, rate, target, ffmpeg)
                with wave.open(str(target)) as audio:
                    self.assertEqual((audio.getframerate(), audio.getnchannels(), audio.getnframes()), (32000, 2, 32000))
                    pcm = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').reshape(-1, 2)
                self.assertLess(np.abs(pcm.astype(float)).max(), 32767)
                self.assertLess(abs(int(pcm[0, 0])), 2)
                self.assertLess(abs(int(pcm[-1, 0])), 10)
                scale = 10**(delivery['gain_db']/20)
                self.assertLess(abs(pcm[16000, 0]/32768 - level*scale), .005)
                self.assertLess(abs(pcm[16000, 1]/32768 - .5*level*scale), .005)
                self.assertEqual(delivery['gain_db'] == 0, level < 1)

    def test_minimax_preserves_lyrics_seed_and_uses_own_planning(self):
        request = validate(dict(engine=MINIMAX['id'],caption='Gentle folk',mode='song',
                                lyrics='[Verse]\nТихий свет',seed=0,duration=10,planner=True))
        self.assertEqual(request['engine'], MINIMAX['id'])
        self.assertEqual(request['lyrics'], '[Verse]\nТихий свет')
        self.assertEqual(request['seed'], 0)
        self.assertFalse(request['planner'])

    def test_initial_duration_limit_does_not_reduce_ace_limit(self):
        self.assertEqual(validate(dict(caption='Piano',duration=180))['engine'], ENGINE['id'])
        with self.assertRaises(ValueError):
            validate(dict(engine=MINIMAX['id'],caption='Piano',duration=61))
        with self.assertRaises(ValueError):
            validate(dict(engine='unknown',caption='Piano'))

    def test_selected_missing_backend_cannot_fall_back_to_ace(self):
        manager = MusicManager.__new__(MusicManager)
        manager.lock = threading.RLock()
        manager.active = None
        manager.closing = False
        with patch.object(manager,'availability',return_value={'available':False,'reason':'MiniMax missing'}) as available:
            with self.assertRaisesRegex(ValueError,'MiniMax missing'):
                manager.submit(dict(engine=MINIMAX['id'],caption='Piano',duration=10))
        available.assert_called_once_with(MINIMAX['id'])

    def test_backend_process_selection_keeps_ace_isolated(self):
        manager = MusicManager.__new__(MusicManager)
        manager.root, manager.models = Path('ACE Runtime'), Path('ACE Models')
        self.assertEqual(manager.engine_paths(ENGINE['id']), (manager.root,manager.models,'music_worker.py'))
        manager.paths = type('Paths',(),{'root':Path('Portable'),'models':Path('Portable/Models')})()
        with patch.dict('os.environ',{'VISUALFORGE_MINIMAX_RUNTIME':str(Path('Native Comfy').resolve()),
                                     'VISUALFORGE_MINIMAX_MODELS':str(Path('MiniMax Models').resolve())}):
            root,models,worker = manager.engine_paths(MINIMAX['id'])
        self.assertEqual(worker,'minimax_worker.py')
        self.assertNotEqual(root,manager.root)
        self.assertNotEqual(models,manager.models)


if __name__ == '__main__':
    unittest.main()
