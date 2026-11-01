"""Bounded clarification decisions and browser flow, with fake Gemma and no model inference."""
from contextlib import nullcontext
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from prompt_clarification import Clarifications, fingerprint, question, selection  # noqa: E402
from engines.image_prompt_enhancer import PromptEnhancer  # noqa: E402
from engines import zimage_prompts as zimage  # noqa: E402
from video.video_prompt_enhancer import PromptEnhancer as VideoEnhancer  # noqa: E402
import test_image_prompt_manager as fixtures  # noqa: E402


class ProtocolTests(unittest.TestCase):
    def start(self):
        store = Clarifications()
        return store, store.start('original-input', fixtures.PROPOSAL)

    def test_multiple_numbers_and_zero_have_the_same_contract_as_the_browser(self):
        for answer in ('1, 3', '13', '1;3', '1 3', '113'):
            self.assertEqual(selection(answer, 3), [1, 3])
        self.assertEqual(selection('0', 3), [0])
        for answer in ('', '0,1', '9', '4', 'one', '1.3', '1-3', '1 и 3', {}, '1' * 41):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                selection(answer, 3)

    def test_compatible_multiple_choices_do_not_cause_another_question(self):
        store, first = self.start()
        with store.answer(first['clarification_token'], 'original-input', '13') as decision:
            self.assertEqual(decision['state'], 'ready')
            self.assertEqual(decision['clarification']['rounds'], 1)
            self.assertEqual(len(decision['choices']), 2)
        with self.assertRaises(ValueError), store.answer(first['clarification_token'], 'original-input', '1'):
            self.fail('replay')

    def test_conflict_is_scoped_and_the_second_answer_cannot_ask_a_third_question(self):
        for final in ('1', '2', '12', '121', '0'):
            store, first = self.start()
            with store.answer(first['clarification_token'], 'original-input', '123') as second:
                self.assertEqual(second['round'], 2)
                self.assertEqual(len(second['options']), 2, 'keep uncontested lighting without asking about it again')
            with store.answer(second['clarification_token'], 'original-input', final) as decision:
                self.assertEqual(decision['state'], 'ready')
                self.assertEqual(decision['clarification']['rounds'], 2)
                self.assertEqual(len(decision['choices']), 2)
                medium = next(choice for choice in decision['choices'] if choice['group'] == 'medium')
                self.assertEqual(medium['value'], fixtures.PROPOSAL['options'][0 if final in ('1', '121') else 1]['value'])
            self.assertFalse(store.pending)

    def test_zero_skips_all_further_questions(self):
        store, first = self.start()
        with store.answer(first['clarification_token'], 'original-input', '0') as decision:
            self.assertEqual(decision['state'], 'ready')
            self.assertEqual(decision['choices'], [])
        self.assertFalse(store.pending)

    def test_stale_inputs_and_bad_numbers_do_not_consume_a_valid_question(self):
        store, first = self.start()
        for identity, answer in [('changed-input', '1'), ('original-input', '8')]:
            with self.assertRaises(ValueError), store.answer(first['clarification_token'], identity, answer):
                self.fail('invalid continuation')
        self.assertIn(first['clarification_token'], store.pending)

    def test_reserved_continuation_refuses_concurrent_use_and_rolls_back_on_admission_failure(self):
        store, first = self.start()
        token = first['clarification_token']
        with self.assertRaisesRegex(RuntimeError, 'busy'), store.answer(token, 'original-input', '1'):
            with self.assertRaises(ValueError), store.answer(token, 'original-input', '1'):
                self.fail('concurrent use')
            raise RuntimeError('busy')
        with store.answer(token, 'original-input', '1') as decision:
            self.assertEqual(decision['state'], 'ready')

    def test_waiting_state_is_expiring_bounded_and_cancellable(self):
        clock = [0]
        store = Clarifications(clock=lambda:clock[0], ttl=10, limit=2)
        first = store.start('first', fixtures.PROPOSAL)
        self.assertRegex(first['clarification_token'], r'^[a-f0-9]{64}$')
        self.assertNotIn('value', first['options'][0], 'model instructions are not UI choices')
        store.start('second', fixtures.PROPOSAL)
        last = store.start('third', fixtures.PROPOSAL)
        self.assertEqual(len(store.pending), 2)
        self.assertNotIn(first['clarification_token'], store.pending)
        self.assertTrue(store.discard(last['clarification_token']))
        clock[0] = 10
        self.assertFalse(store.discard(last['clarification_token']))
        self.assertEqual(len(store.pending), 0)

    def test_image_and_control_fingerprints_are_bound_and_unambiguous(self):
        base = dict(prompt='fox', engine=zimage.ENGINE, mode='control', context={'control_type':'canny'}, binding={'seed':3}, images=[b'a', b'bc'])
        initial = fingerprint(**base)
        for field, value in [('prompt','cat'), ('mode','inpaint'), ('context',{'control_type':'gray'}),
                             ('binding',{'seed':4}), ('images',[b'ab', b'c'])]:
            self.assertNotEqual(initial, fingerprint(**{**base, field:value}))

    def test_malformed_or_duplicate_questions_are_rejected(self):
        for mutation in (dict(options=[]), dict(options=fixtures.PROPOSAL['options'] * 3), dict(question='x' * 401),
                         dict(options=[fixtures.PROPOSAL['options'][0]] * 2), dict(options=[{}, {}])):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                question({**fixtures.PROPOSAL, **mutation})


class ManagerTests(unittest.TestCase):
    def setUp(self):
        activity = patch('video.gemma_task.operation', side_effect=lambda *args, **kwargs: nullcontext())
        activity.start(); self.addCleanup(activity.stop)

    def manager(self, answers):
        text, gpu = fixtures.Text(answers), Mock()
        return PromptEnhancer(text, gpu), text, gpu

    def test_each_mode_decides_with_its_own_rules_and_actual_sources(self):
        for mode in zimage.MODE_RULES:
            manager, text, _ = self.manager((json.dumps(dict(state='ready', prompt=fixtures.FOX)),))
            images = [] if mode == 'prompt' else [fixtures.png(), fixtures.png(mask=True)] if mode == 'inpaint' else [fixtures.png()]
            result = manager.prepare('Лиса в хвойном лесу', zimage.ENGINE, mode, images, {'control_type':'canny'}, language='English')
            self.assertEqual(result['prompt'], fixtures.FOX)
            self.assertEqual(len(text.calls), 1)
            self.assertIn(zimage.CLARIFICATION_RULES[mode], text.calls[0]['messages'][0]['content'])
            self.assertIn('UI language: English', text.calls[0]['messages'][0]['content'])
            self.assertEqual(text.owners, [])

    def test_final_caption_receives_only_agreed_preferences_and_keeps_russian_choice_text(self):
        proposal = copy.deepcopy(fixtures.PROPOSAL)
        proposal['options'][0]['value'] = 'The postcard heading reads "ЛЕС".'
        caption = 'A watercolor winter postcard with the heading "ЛЕС" in large handwritten Cyrillic letters.'
        manager, text, gpu = self.manager((json.dumps(proposal), caption))
        first = manager.prepare('Открытка с названием', zimage.ENGINE, 'prompt')
        self.assertEqual(text.owners, [])
        self.assertEqual(len(gpu.claim.call_args_list), 1)
        result = manager.prepare('Открытка с названием', zimage.ENGINE, 'prompt', token=first['clarification_token'], answer='1')
        self.assertEqual(result['prompt'], caption)
        context = json.loads(text.calls[-1]['messages'][1]['content'])['context']
        self.assertEqual(context['clarification_choices'], [proposal['options'][0]['value']])
        self.assertNotIn(proposal['options'][1]['value'], text.calls[-1]['messages'][1]['content'])

    def test_malformed_decision_is_retried_internally_without_counting_a_question(self):
        manager, text, _ = self.manager(('not JSON', json.dumps(fixtures.PROPOSAL)))
        result = manager.prepare('Выбери вид сцены', zimage.ENGINE, 'prompt')
        self.assertEqual(result['round'], 1)
        self.assertEqual(len(text.calls), 2)
        self.assertEqual(text.owners, [])

    def test_cancellation_during_decision_leaves_no_waiting_session(self):
        manager, text, _ = self.manager((json.dumps(fixtures.PROPOSAL),))
        text.on_answer = text.cancel.set
        with self.assertRaises(ValueError):
            manager.prepare('Лиса', zimage.ENGINE, 'prompt')
        self.assertEqual(text.owners, [])
        self.assertEqual(len(manager.clarifications.pending), 0)

    def test_video_scaffold_cannot_start_inference_or_advertise_readiness(self):
        text, gpu = Mock(), Mock()
        manager = VideoEnhancer(text, gpu)
        self.assertEqual(manager.clarification_status(), dict(available=False, state='template', max_questions=2))
        with self.assertRaises(NotImplementedError):
            manager.prepare_clarification('fox', 'wan22', 'prompt')
        self.assertEqual(text.mock_calls, [])
        self.assertEqual(gpu.mock_calls, [])


@unittest.skipUnless(shutil.which('node'), 'Node is not installed')
class BrowserTests(unittest.TestCase):
    def test_numbered_browser_flow_and_hard_question_limit(self):
        script = Path(__file__).with_name('image_clarification_test.js')
        result = subprocess.run([shutil.which('node'), str(script)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('image clarification ok', result.stdout)


if __name__ == '__main__':
    unittest.main()
