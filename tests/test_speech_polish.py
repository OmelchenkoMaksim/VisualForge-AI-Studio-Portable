"""Enhanced voice input: the local Gemma edits the recognized text, with every safety net around it."""
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
sys.path.insert(0, str(ROOT / 'tests'))
from gpu import Busy, GpuCoordinator, GpuUnavailable
from speech import polish, routes
from speech.manager import SpeechManager
from test_speech import Handler, SpeechCase, wav_bytes


class FakeText:
    """What the Polisher needs of the Text manager."""

    def __init__(self, answer='Привет! Как дела?', available=True, error=None):
        self.answer, self.available, self.error = answer, available, error
        self.active = {}
        self.calls, self.started = [], 0
        self.gpu_claimed = []

    def availability(self):
        return dict(available=self.available, reason='' if self.available else 'Нет установленной текстовой модели. Скачайте её во вкладке «Компоненты».', models=[])

    def default_model(self):
        return 'gemma4:e4b'

    def begin(self, owner):
        if self.active:
            raise Busy('Text уже отвечает.')
        event = self.active[owner] = threading.Event()
        return event

    def end(self, owner):
        self.active.pop(owner, None)

    def start(self):
        self.started += 1

    def stream_chat(self, model, messages, think, cancel, options=None):
        self.calls.append(dict(model=model, messages=messages, think=think, options=options, active=dict(self.active)))
        if self.error:
            raise self.error
        for piece in (self.answer[:6], self.answer[6:]):
            yield {'message': {'content': piece}}


class FakeGpu:
    def __init__(self, busy=None):
        self.busy, self.claims = busy, []

    def claim(self, module, *args, **kwargs):
        self.claims.append(module)
        if self.busy:
            raise self.busy
        return []


class PolishTests(unittest.TestCase):
    def make(self, **keywords):
        self.text = FakeText(**keywords)
        self.gpu = FakeGpu()
        return polish.Polisher(self.text, self.gpu)

    def test_success_edits_the_text_and_uses_low_temperature(self):
        polisher = self.make(answer='Привет! Как дела?')
        self.assertEqual(polisher.polish('привет как дела'), ('Привет! Как дела?', ''))
        call = self.text.calls[0]
        self.assertEqual((call['model'], call['think']), ('gemma4:e4b', False))
        self.assertEqual(call['options']['temperature'], 0.2)
        self.assertEqual(call['messages'][0]['role'], 'system')
        self.assertEqual(call['messages'][-1], {'role': 'user', 'content': 'привет как дела'})
        self.assertIn(polish.OWNER, call['active'], 'the Text manager sees the edit as running (UI lock, Stop button)')
        self.assertEqual(self.text.active, {}, 'and it is always ended')
        self.assertEqual(self.gpu.claims, ['text'])

    def test_unavailable_busy_and_failing_gemma_fall_back_to_the_plain_text(self):
        text, note = self.make(available=False).polish('привет как дела')
        self.assertEqual(text, 'привет как дела')
        self.assertIn('Компоненты', note)
        polisher = self.make()
        polisher.gpu = FakeGpu(Busy('GPU занят Music.'))
        text, note = polisher.polish('привет как дела')
        self.assertEqual(text, 'привет как дела')
        self.assertIn('GPU занят Music', note)
        polisher = self.make()
        polisher.gpu = FakeGpu(GpuUnavailable('нет nvidia-smi'))
        self.assertEqual(polisher.polish('привет как дела')[0], 'привет как дела')
        polisher = self.make(error=ValueError('model failed to load'))
        text, note = polisher.polish('привет как дела')
        self.assertEqual(text, 'привет как дела')
        self.assertIn('не ответила', note)
        self.assertEqual(self.text.active, {}, 'ended even when the model failed')

    def test_text_busy_with_a_chat_answer_is_reported(self):
        polisher = self.make()
        self.text.active['chat'] = threading.Event()
        text, note = polisher.polish('привет как дела')
        self.assertEqual(text, 'привет как дела')
        self.assertIn('Text уже отвечает', note)

    def test_an_answer_that_is_not_the_same_sentence_is_rejected(self):
        for answer in ('Видеокарта нагревается, потому что транзисторы выделяют тепло при работе, а вентиляторы не всегда справляются.',
                       'Конечно! Вот исправленный текст с запятыми и точками для вас, пожалуйста пользуйтесь на здоровье и приятной работы.', ''):
            polisher = self.make(answer=answer)
            text, note = polisher.polish('почему нагревается видеокарта')
            self.assertEqual(text, 'почему нагревается видеокарта', answer)
            self.assertIn('слишком сильно', note)

    def test_light_edits_are_accepted_and_wrappers_are_removed(self):
        polisher = self.make(answer='```\n«Слушай, а получится сделать голосовой ввод?»\n```')
        self.assertEqual(polisher.polish('слушай а получится сделать голосовой ввод')[0], 'Слушай, а получится сделать голосовой ввод?')
        polisher = self.make(answer='Исправленный текст: Создай мелодию на 30 секунд.')
        self.assertEqual(polisher.polish('создай мелодию на тридцать секунд')[0], 'Создай мелодию на 30 секунд.')
        self.assertTrue(polish.acceptable('красная лиса на снегу', 'Красная лиса — на снегу.'))
        self.assertFalse(polish.acceptable('да', ''))

    def test_stop_during_the_edit_keeps_the_plain_text(self):
        polisher = self.make(answer='Привет! Как дела?')
        original = self.text.stream_chat

        def stopping(*arguments, **keywords):
            self.text.active[polish.OWNER].set()
            return original(*arguments, **keywords)
        self.text.stream_chat = stopping
        text, note = polisher.polish('привет как дела')
        self.assertEqual(text, 'привет как дела')
        self.assertIn('Остановлено', note)

    def test_very_long_and_empty_text_are_not_sent(self):
        polisher = self.make()
        self.assertEqual(polisher.polish('   '), ('   ', ''))
        text, note = polisher.polish('слово ' * 1000)
        self.assertEqual(text, 'слово ' * 1000)
        self.assertIn('слишком длинный', note)
        self.assertEqual(self.text.calls, [])

    def test_warm_loads_the_model_quietly(self):
        polisher = self.make()
        self.assertTrue(polisher.warm())
        self.assertEqual(self.text.calls[0]['options']['num_predict'], 4)
        busy = self.make()
        busy.gpu = FakeGpu(Busy('занят'))
        self.assertFalse(busy.warm())
        self.assertFalse(self.make(available=False).warm())

    def test_status_names_the_model(self):
        self.assertEqual(self.make().status(), dict(available=True, reason='', model='gemma4:e4b'))
        self.assertFalse(polish.Polisher(None, None).status()['available'])


class ManagerEnhanceTests(SpeechCase):
    def setUp(self):
        super().setUp()
        self.install()

    def test_enhance_runs_after_the_recognition_lock_is_released(self):
        seen = {}

        class Polisher:
            def status(inner):
                return dict(available=True, reason='', model='gemma4:e4b')

            def polish(inner, text, language):
                seen['busy'] = self.manager.busy()   # the coordinator must not see the voice worker as working
                seen['text'] = text
                return 'Улучшенный текст.', ''

            def warm(inner):
                seen['warm'] = True
        self.manager.polisher = Polisher()
        answer = self.manager.transcribe(wav_bytes(1.0), 'ru', enhance=True)
        self.assertEqual(seen, {'busy': False, 'text': 'распознано 1.0 с (ru, cuda)'})
        self.assertEqual((answer['text'], answer['enhanced'], answer['plain']), ('Улучшенный текст.', True, 'распознано 1.0 с (ru, cuda)'))
        self.assertTrue(self.manager.prepare_enhance())

    def test_enhance_is_skipped_for_previews_and_when_not_asked_and_reports_notes(self):
        calls = []

        class Polisher:
            def status(inner):
                return dict(available=True, reason='', model='m')

            def polish(inner, text, language):
                calls.append(text)
                return text, 'GPU занят Music.'
        self.manager.polisher = Polisher()
        self.manager.transcribe(wav_bytes(1.0), 'ru')
        self.manager.transcribe(wav_bytes(1.0), 'ru', partial=True)
        self.assertEqual(calls, [])
        answer = self.manager.transcribe(wav_bytes(1.0), 'ru', enhance=True)
        self.assertEqual((answer['enhanced'], answer['enhance_note']), (False, 'GPU занят Music.'))
        self.assertEqual(answer['text'], 'распознано 1.0 с (ru, cuda)')

    def test_without_a_polisher_enhance_just_returns_the_recognition(self):
        answer = self.manager.transcribe(wav_bytes(1.0), 'ru', enhance=True)
        self.assertNotIn('enhanced', answer)
        self.assertFalse(self.manager.availability()['enhance']['available'])
        self.assertFalse(self.manager.prepare_enhance())

    def test_availability_and_routes_carry_the_enhance_state(self):
        self.manager.polisher = polish.Polisher(FakeText(), FakeGpu())
        self.assertEqual(self.manager.availability()['enhance'], dict(available=True, reason='', model='gemma4:e4b'))
        handler = Handler(wav_bytes(1.0))
        routes.transcribe(handler, self.manager, '?lang=ru&enhance=1')
        self.assertEqual(handler.status, 200)
        self.assertIn('enhance_note', handler.body)
        handler = Handler()
        routes.handle(handler, self.manager, 'prepare', {'enhance': True})
        self.assertEqual(handler.body, {'preparing': True})

    def test_the_coordinator_frees_the_voice_worker_before_gemma_claims_the_card(self):
        self.manager.transcribe(wav_bytes(1.0), 'ru')
        self.assertTrue(self.manager.alive())
        coordinator = GpuCoordinator(probes={}, releasers={'speech': self.manager.release_gpu})
        self.assertEqual(coordinator.claim('text'), ['speech'])
        self.assertFalse(self.manager.alive())


if __name__ == '__main__':
    unittest.main()
