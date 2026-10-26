"""Voice input: manager, device choice, GPU release, routes and the worker's audio helpers, against a stand-in worker process."""
import io
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
from gpu import Busy, GpuCoordinator
from speech import routes
from speech.manager import SpeechError, SpeechManager
from speech import worker

FAKE = ROOT / 'tests/fake_speech_worker.py'


def wav_bytes(seconds=1.0, rate=16000, channels=1, amplitude=8000, frequency=440):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = b''.join(struct.pack('<h', int(amplitude * math.sin(2 * math.pi * frequency * i / rate))) * channels for i in range(int(seconds * rate)))
        handle.writeframes(frames)
    return buffer.getvalue()


class Paths:
    def __init__(self, root):
        self.root = Path(root)
        self.models = self.root / 'Models'
        self.cache = self.root / 'UserData/Cache'
        self.settings = self.root / 'UserData/Settings'
        self.logs = self.root / 'UserData/Logs'


class Handler:
    def __init__(self, body=b'', headers=None):
        self.status = self.body = None
        self.rfile = io.BytesIO(body)
        self.headers = headers or {'Content-Length': str(len(body)), 'Content-Type': 'audio/wav'}

    def response_json(self, status, body):
        self.status, self.body = status, body


class SpeechCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = Paths(self.root)
        self.free, self.busy_modules = 12000, False
        self.manager = self.make()

    def make(self):
        return SpeechManager(self.paths, command=lambda folder, device: [sys.executable, str(FAKE), str(folder), device], free_vram=lambda: self.free, gpu_busy=lambda: self.busy_modules)

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def install(self, name='whisper-turbo'):
        folder = self.paths.models / 'Speech' / name
        folder.mkdir(parents=True, exist_ok=True)
        for file in ('config.json', 'preprocessor_config.json', 'tokenizer.json'):
            (folder / file).write_text('{}')
        (folder / 'model.safetensors').write_bytes(b'0' * 2_000_000)
        return folder


class AvailabilityTests(SpeechCase):
    def test_no_model_explains_where_to_get_one(self):
        status = self.manager.availability()
        self.assertFalse(status['available'])
        self.assertIn('Компоненты', status['reason'])

    def test_installed_models_are_listed_and_incomplete_ones_are_not_usable(self):
        self.install('whisper-small')
        broken = self.paths.models / 'Speech/whisper-turbo'
        broken.mkdir(parents=True)
        (broken / 'config.json').write_text('{}')
        models = {m['name']: m for m in self.manager.installed_models()}
        self.assertTrue(models['whisper-small']['ok'])
        self.assertFalse(models['whisper-turbo']['ok'])
        self.assertEqual(self.manager.chosen_model(), 'whisper-small')
        self.install('whisper-turbo')
        self.assertEqual(self.manager.chosen_model(), 'whisper-turbo')

    def test_settings_are_validated_and_saved(self):
        self.install()
        self.assertEqual(self.manager.settings()['language'], 'ru')
        self.manager.settings({'language': 'en', 'keep_alive': 15})
        self.assertEqual(self.manager.settings()['language'], 'en')
        for bad in ({'language': 'xx'}, {'device': 'tpu'}, {'keep_alive': 3}, {'model': 'whisper-small'}, {'surprise': 1}):
            with self.assertRaises(SpeechError):
                self.manager.settings(bad)


class TranscribeTests(SpeechCase):
    def setUp(self):
        super().setUp()
        self.install()

    def test_transcribes_and_reuses_the_worker(self):
        first = self.manager.transcribe(wav_bytes(1.0), 'ru')
        self.assertEqual(first['text'], 'распознано 1.0 с (ru, cuda)')
        self.assertEqual((first['device'], first['model']), ('cuda', 'whisper-turbo'))
        process = self.manager.process
        second = self.manager.transcribe(wav_bytes(2.0), 'en')
        self.assertEqual(second['text'], 'распознано 2.0 с (en, cuda)')
        self.assertIs(self.manager.process, process)
        self.assertTrue(self.manager.status()['running'])

    def test_bad_recordings_are_refused(self):
        for bad in (b'', b'not a wav' * 200, b'RIFF' + b'\0' * 2000):
            with self.assertRaises(SpeechError):
                self.manager.transcribe(bad, 'ru')
        with self.assertRaises(SpeechError):
            self.manager.transcribe(wav_bytes(1.0), 'klingon')

    def test_without_a_model_it_says_so(self):
        for folder in (self.paths.models / 'Speech/whisper-turbo').iterdir():
            folder.unlink()
        with self.assertRaises(SpeechError) as raised:
            self.manager.transcribe(wav_bytes(1.0), 'ru')
        self.assertIn('Компоненты', str(raised.exception))

    def test_recordings_are_recognized_one_at_a_time_and_a_final_waits_its_turn(self):
        results = {}
        thread = threading.Thread(target=lambda: results.update(first=self.manager.transcribe(wav_bytes(4.0), 'ru')))
        thread.start()
        deadline = 0
        while not self.manager.busy() and deadline < 100:
            threading.Event().wait(0.05)
            deadline += 1
        second = self.manager.transcribe(wav_bytes(1.0), 'ru')   # used to be refused (409) and the whole dictation was lost
        self.assertEqual(second['text'], 'распознано 1.0 с (ru, cuda)')
        thread.join(20)
        self.assertIn('first', results)

    def test_a_final_that_waits_too_long_is_refused_politely(self):
        import speech.manager as module
        original = module.FINAL_WAIT
        module.FINAL_WAIT = 0.2
        try:
            thread = threading.Thread(target=lambda: self.manager.transcribe(wav_bytes(4.0), 'ru'))
            thread.start()
            for _ in range(100):
                if self.manager.busy():
                    break
                threading.Event().wait(0.05)
            with self.assertRaises(Busy):
                self.manager.transcribe(wav_bytes(1.0), 'ru')
            thread.join(20)
        finally:
            module.FINAL_WAIT = original

    def test_a_crashing_worker_is_reported_and_replaced(self):
        import os
        os.environ['FAKE_SPEECH_CRASH_ON_LONG'] = '1'
        try:
            with self.assertRaises(SpeechError) as raised:
                self.manager.transcribe(wav_bytes(6.0), 'ru')
            self.assertIn('прервано', str(raised.exception))
        finally:
            del os.environ['FAKE_SPEECH_CRASH_ON_LONG']
        self.assertFalse(self.manager.alive())
        self.assertEqual(self.manager.transcribe(wav_bytes(1.0), 'ru')['text'], 'распознано 1.0 с (ru, cuda)')

    def test_a_worker_that_cannot_start_is_reported(self):
        import os
        os.environ['FAKE_SPEECH_FAIL_START'] = '1'
        try:
            with self.assertRaises(SpeechError):
                self.manager.transcribe(wav_bytes(1.0), 'ru')
        finally:
            del os.environ['FAKE_SPEECH_FAIL_START']
        self.assertFalse(self.manager.alive())

    def test_partial_preview_never_loads_a_model_and_only_uses_a_gpu_worker(self):
        self.assertEqual(self.manager.transcribe(wav_bytes(1.0), 'ru', partial=True), {'text': None, 'skipped': True})
        self.assertFalse(self.manager.alive(), 'a preview must not start the worker')
        self.manager.transcribe(wav_bytes(1.0), 'ru')
        self.assertEqual(self.manager.device, 'cuda')
        self.assertEqual(self.manager.transcribe(wav_bytes(1.5), 'ru', partial=True)['text'], 'распознано 1.5 с (ru, cuda)')
        self.manager.stop()
        self.free = 100
        self.manager.transcribe(wav_bytes(1.0), 'ru')
        self.assertEqual(self.manager.device, 'cpu')
        self.assertEqual(self.manager.transcribe(wav_bytes(1.0), 'ru', partial=True), {'text': None, 'skipped': True}, 'processor workers are too slow for a preview')

    def test_partial_is_skipped_while_another_recognition_runs(self):
        self.manager.transcribe(wav_bytes(1.0), 'ru')
        results = {}
        thread = threading.Thread(target=lambda: results.update(final=self.manager.transcribe(wav_bytes(4.0), 'ru')))
        thread.start()
        for _ in range(100):
            if self.manager.busy():
                break
            threading.Event().wait(0.05)
        self.assertEqual(self.manager.transcribe(wav_bytes(1.0), 'ru', partial=True), {'text': None, 'skipped': True})
        thread.join(20)
        self.assertIn('final', results)

    def test_partial_route_flag(self):
        handler = Handler(wav_bytes(1.0))
        routes.transcribe(handler, self.manager, '?lang=ru&partial=1')
        self.assertEqual((handler.status, handler.body), (200, {'text': None, 'skipped': True}))


class DeviceTests(SpeechCase):
    def setUp(self):
        super().setUp()
        self.install()

    def test_gpu_only_when_free_memory_suffices_and_nobody_works(self):
        self.free = 12000
        self.assertEqual(self.manager.transcribe(wav_bytes(), 'ru')['device'], 'cuda')
        self.manager.stop()
        self.free = 2000
        self.assertEqual(self.manager.transcribe(wav_bytes(), 'ru')['device'], 'cpu')
        self.manager.stop()
        self.free, self.busy_modules = 12000, True
        self.assertEqual(self.manager.transcribe(wav_bytes(), 'ru')['device'], 'cpu')

    def test_forced_device_and_unreadable_memory(self):
        self.manager.settings({'device': 'cpu'})
        self.assertEqual(self.manager.transcribe(wav_bytes(), 'ru')['device'], 'cpu')
        self.manager.settings({'device': 'auto'})
        self.manager.free_vram = lambda: (_ for _ in ()).throw(OSError('no nvidia-smi'))
        self.assertEqual(self.manager.transcribe(wav_bytes(), 'ru')['device'], 'cpu')

    def test_release_gpu_stops_a_gpu_worker_but_not_a_processor_one(self):
        self.manager.transcribe(wav_bytes(), 'ru')
        self.assertEqual(self.manager.device, 'cuda')
        self.manager.release_gpu()
        self.assertFalse(self.manager.alive())
        self.free = 100
        self.manager.transcribe(wav_bytes(), 'ru')
        self.assertEqual(self.manager.device, 'cpu')
        self.manager.release_gpu()
        self.assertTrue(self.manager.alive())

    def test_the_coordinator_releases_speech_before_another_module_starts(self):
        self.manager.transcribe(wav_bytes(), 'ru')
        coordinator = GpuCoordinator(probes={}, releasers={'speech': self.manager.release_gpu})
        self.assertEqual(coordinator.claim('music'), ['speech'])
        self.assertFalse(self.manager.alive())
        self.assertEqual(coordinator.snapshot(), {'busy': False, 'modules': [], 'labels': []}, 'voice input never locks the interface')

    def test_changing_the_model_setting_restarts_the_worker(self):
        self.install('whisper-small')
        self.manager.transcribe(wav_bytes(), 'ru')
        self.manager.settings({'model': 'whisper-small'})
        self.assertFalse(self.manager.alive())
        self.assertEqual(self.manager.transcribe(wav_bytes(), 'ru')['model'], 'whisper-small')

    def test_prepare_loads_the_model_in_the_background(self):
        self.assertTrue(self.manager.prepare())
        for _ in range(100):
            if self.manager.alive():
                break
            threading.Event().wait(0.05)
        self.assertTrue(self.manager.alive())
        self.assertFalse(self.manager.prepare(), 'already loaded')


class PunctuationTests(SpeechCase):
    """Punctuation and capitals for Russian speech (RUPunct) are restored around Latin words that must stay as they are."""

    @staticmethod
    def fake_model(text):
        # Fake RUPunct: capitalize the first word, mark the two trigger words, otherwise end with a period.
        words = text.split()
        labels = []
        for number, word in enumerate(words):
            case = 'UPPER' if number == 0 else 'LOWER'
            mark = 'COMMA' if word == 'пожалуйста' else 'QUESTION' if word == 'вопрос' else 'PERIOD' if number == len(words) - 1 else 'O'
            labels.append((word, case + '_' + mark))
        return labels

    def test_russian_runs_get_signs_and_latin_words_stay_untouched(self):
        result = worker.punctuate_text('привет расскажи пожалуйста как дела', self.fake_model)
        self.assertEqual(result, 'Привет расскажи пожалуйста, как дела.')
        result = worker.punctuate_text('доктор пеппер вертолет летает madness madness im crazy', self.fake_model)
        self.assertEqual(result, 'Доктор пеппер вертолет летает madness madness im crazy', result)
        self.assertTrue(result.endswith('madness madness im crazy'), 'Latin words are never changed or capitalized in the middle of a sentence')

    def test_a_russian_run_after_latin_words_continues_the_sentence(self):
        result = worker.punctuate_text('открой файл readme и покажи содержимое', self.fake_model)
        self.assertEqual(result, 'Открой файл readme и покажи содержимое.', result)

    def test_well_punctuated_text_is_left_alone(self):
        good = 'Привет, расскажи, пожалуйста, как работает видеокарта и почему она нагревается?'
        self.assertTrue(worker.is_punctuated(good))
        self.assertFalse(worker.is_punctuated('привет расскажи как работает видеокарта и почему она нагревается'))
        self.assertTrue(worker.is_punctuated('Да'), 'very short phrases are not touched')

    def test_nothing_changes_when_the_model_disagrees_or_there_is_no_russian(self):
        self.assertEqual(worker.punctuate_text('привет мир как дела', lambda text: [('привет', 'UPPER_O')]), 'привет мир как дела')
        self.assertEqual(worker.punctuate_text('нужно 30 секунд', self.fake_model), 'Нужно 30 секунд.', 'numbers stay, they do not split the Russian words')
        self.assertEqual(worker.punctuate_text('just english words here', self.fake_model), 'just english words here')

    def test_a_new_sentence_always_starts_with_a_capital(self):
        def model(text):
            return [(word, 'LOWER_QUESTION' if word == 'картинки' else 'LOWER_O') for word in text.split()]
        self.assertEqual(worker.punctuate_text('слушай нужна картинки раз получилось', model), 'Слушай нужна картинки? Раз получилось.')

    def test_label_rendering(self):
        self.assertEqual(worker.apply_label('слово', 'LOWER_O'), 'слово')
        self.assertEqual(worker.apply_label('слово', 'UPPER_COMMA'), 'Слово,')
        self.assertEqual(worker.apply_label('нато', 'UPPER_TOTAL_PERIOD'), 'НАТО.')
        self.assertEqual(worker.apply_label('да', 'LOWER_QUESTIONVOSKL'), 'да?!')

    def test_manager_passes_the_punctuation_model_only_when_installed_and_enabled(self):
        self.install()
        self.assertIsNone(self.manager.punct_dir())
        self.assertFalse(self.manager.availability()['punctuation_installed'])
        folder = self.paths.models / 'Speech/rupunct-big'
        folder.mkdir(parents=True)
        for name in ('config.json', 'pytorch_model.bin', 'tokenizer.json', 'vocab.txt'):
            (folder / name).write_text('x')
        self.assertEqual(self.manager.punct_dir(), folder)
        self.assertTrue(self.manager.availability()['punctuation_installed'])
        self.manager.settings({'punctuation': False})
        self.assertIsNone(self.manager.punct_dir())
        with self.assertRaises(SpeechError):
            self.manager.settings({'punctuation': 'yes'})


class RouteTests(SpeechCase):
    def test_routes(self):
        self.install()
        handler = Handler()
        routes.handle(handler, self.manager, 'availability', None)
        self.assertEqual((handler.status, handler.body['available']), (200, True))
        routes.handle(handler, self.manager, 'settings', {'language': 'en'})
        self.assertEqual(handler.body['language'], 'en')
        routes.handle(handler, self.manager, 'settings', {'language': 'zz'})
        self.assertEqual(handler.status, 400)
        routes.handle(handler, self.manager, 'bogus', {})
        self.assertEqual(handler.status, 404)
        routes.handle(handler, None, 'availability', None)
        self.assertEqual(handler.status, 503)
        routes.handle(handler, self.manager, 'unload', {})
        self.assertEqual(handler.body, {'unloaded': True})

    def test_transcribe_route(self):
        self.install()
        audio = wav_bytes(1.5)
        handler = Handler(audio)
        routes.transcribe(handler, self.manager, '?lang=en')
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.body['text'], 'распознано 1.5 с (en, cuda)')
        for headers, body, expected in (({'Content-Length': '0', 'Content-Type': 'audio/wav'}, b'', 413), ({'Content-Length': '5000', 'Content-Type': 'text/plain'}, b'x' * 5000, 415),
                                        ({'Content-Length': '2000', 'Content-Type': 'audio/wav'}, b'x' * 2000, 400)):
            handler = Handler(body, headers)
            routes.transcribe(handler, self.manager, '')
            self.assertEqual(handler.status, expected)
        handler = Handler(audio)
        routes.transcribe(handler, None, '')
        self.assertEqual(handler.status, 503)


class WorkerAudioTests(unittest.TestCase):
    def setUp(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest('numpy is not installed in this interpreter')
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def path(self, data):
        path = Path(self.temp.name) / 'a.wav'
        path.write_bytes(data)
        return str(path)

    def test_resampling_stereo_and_levels(self):
        samples, level = worker.read_wav(self.path(wav_bytes(1.0, rate=48000, channels=2, amplitude=8000)))
        self.assertEqual(len(samples), 16000)
        self.assertGreater(level, -20)
        _, quiet = worker.read_wav(self.path(wav_bytes(1.0, amplitude=3)))
        self.assertLess(quiet, -55, 'near silence is recognised as silence and never sent to the model')

    def test_bad_files(self):
        with self.assertRaises(Exception):
            worker.read_wav(self.path(b'not audio'))
        eight_bit = io.BytesIO()
        with wave.open(eight_bit, 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(1)
            handle.setframerate(16000)
            handle.writeframes(b'\x80' * 16000)
        with self.assertRaises(ValueError):
            worker.read_wav(self.path(eight_bit.getvalue()))


if __name__ == '__main__':
    unittest.main()
