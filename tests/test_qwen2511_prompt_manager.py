"""Edit 2511 route/profile integration with fake Gemma/GPU/ComfyUI; no quality claim or inference."""
import base64
from contextlib import nullcontext
from io import BytesIO
import json
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import test_image_prompt_manager as fixture
from engines import qwen2511_prompts as qwen2511
from engines.image_preflight import graph_errors
from engines.image_prompt_enhancer import PromptEnhancer
from engines.image_registry import ENGINES, QWEN_LIGHTNING, QWEN_UNCENSORED
from job_history import JobHistory
import studio_server as server
from test_image_engines import object_info

EDIT = 'Change only the clock body to dark green. Keep the dial, brass bells, lettering, background and lighting unchanged.'
NEGATIVE = 'дубликаты часов, blur'


def directive(count):
    return EDIT if count < 2 else ('Keep the scene from Picture 1 and apply the pattern from Picture 2 to the clock body only. '
                                 + ('Place the cactus from Picture 3 on the right. ' if count == 3 else '')
                                 + 'Preserve the dial, lettering and surrounding objects.')


def payloads(count):
    return [dict(base64=base64.b64encode(fixture.png(color=(i * 60, 40, 80))).decode()) for i in range(count)]


class Qwen2511ManagerTests(unittest.TestCase):
    def setUp(self):
        activity = patch('video.gemma_task.operation', side_effect=lambda *args, **kwargs: nullcontext())
        activity.start()
        self.addCleanup(activity.stop)

    def manager(self, answers=(EDIT,)):
        text, gpu = fixture.Text(answers), Mock()
        return PromptEnhancer(text, gpu), text, gpu

    def test_each_mode_and_profile_has_researched_rules_and_correct_vision_count(self):
        self.assertEqual(set(qwen2511.MODE_RULES), set(ENGINES[qwen2511.ENGINE].modes))
        self.assertEqual(set(qwen2511.CLARIFICATION_RULES), set(qwen2511.MODE_RULES))
        systems = set()
        for profile in ('standard', 'lightning'):
            for mode, count in (('prompt', 0), ('single', 1), ('reference', 2), ('multi', 3)):
                with self.subTest(mode=mode, profile=profile):
                    manager, text, gpu = self.manager((directive(count),))
                    result = manager.enhance('Измени часы', qwen2511.ENGINE, mode, [fixture.png()] * count,
                                             dict(sampling_profile=profile, negative_prompt=NEGATIVE if profile == 'standard' else ''))
                    self.assertEqual(result['guide_version'], qwen2511.VERSION)
                    system = text.calls[0]['messages'][0]['content']
                    self.assertIn(qwen2511.MODE_RULES[mode], system)
                    self.assertIn(qwen2511.PROFILE_RULES[profile], system)
                    self.assertNotIn(system, systems)
                    systems.add(system)
                    self.assertEqual(len(text.calls[0]['messages'][1].get('images', [])), count)
                    self.assertFalse(text.calls[0]['think'])
                    gpu.claim.assert_called_once_with('text')
                    self.assertEqual(text.owners, [])

    def test_three_is_a_hard_source_limit_before_model_admission(self):
        for mode, count in (('prompt', 1), ('single', 0), ('reference', 3), ('multi', 2), ('multi', 4), ('multi', 10)):
            manager, text, gpu = self.manager()
            with self.subTest(mode=mode, count=count), self.assertRaises(ValueError):
                manager.enhance('Измени часы', qwen2511.ENGINE, mode, [fixture.png()] * count)
            self.assertEqual(text.calls, [])
            self.assertEqual(gpu.mock_calls, [])
        manager, text, gpu = self.manager()
        with self.assertRaises(ValueError):
            manager.enhance('Часы', qwen2511.ENGINE, 'prompt', context={'sampling_profile':'eight-step'})
        self.assertEqual(text.calls, [])
        self.assertEqual(gpu.mock_calls, [])

    def test_reference_numbers_are_complete_without_qwen21_tags_or_chat_tokens(self):
        for mode, count, bad in (
            ('multi', 3, directive(2)), ('reference', 2, directive(3)),
            ('reference', 2, directive(2).replace('Picture 2', '<image2>')),
            ('single', 1, EDIT + ' Use Picture 4.'), ('prompt', 0, fixture.FOX + ' Use Picture 1.'),
            ('single', 1, EDIT + ' <|vision_start|>'),
        ):
            with self.subTest(mode=mode, bad=bad):
                manager, text, _ = self.manager((bad, directive(count)))
                self.assertEqual(manager.enhance('Часы', qwen2511.ENGINE, mode, [fixture.png()] * count)['prompt'], directive(count))
                self.assertEqual(len(text.calls), 2)
        manager, text, _ = self.manager((directive(2), directive(2)))
        with self.assertRaises(ValueError):
            manager.enhance('Часы', qwen2511.ENGINE, 'multi', [fixture.png()] * 3)
        self.assertEqual(len(text.calls), 2)

    def test_final_russian_lettering_survives_but_removed_text_is_not_restored(self):
        good = 'Replace the sign lettering with "НОВОЕ", keeping the wooden frame, grain, position and lighting unchanged.'
        manager, text, _ = self.manager((good.replace('НОВОЕ', 'NEW'), good))
        result = manager.enhance('Замени «СТАРОЕ» на «НОВОЕ»', qwen2511.ENGINE, 'single', [fixture.png()])
        self.assertEqual(result['prompt'], good)
        self.assertEqual(len(text.calls), 2)
        literal = 'Create a poster with the exact title "Picture 4" in white letters, a dark green field and a small clock.'
        manager, _, _ = self.manager((literal,))
        self.assertEqual(manager.enhance('Плакат с надписью "Picture 4"', qwen2511.ENGINE, 'prompt')['prompt'], literal)


class Qwen2511BridgeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.BridgeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.text.answers = iter((EDIT,))
        server.engine_readiness.return_value.update(modes=list(qwen2511.MODE_RULES), quality_steps={'low':40, 'fast':4},
                                                   quality_reasons={}, community_lora_available=True)

    def generate(self, **data):
        return self.fixture.generate(engine=qwen2511.ENGINE, **data)

    def test_all_modes_keep_standard_lightning_and_negative_conditioning(self):
        for quality in ('low', 'fast'):
            for mode, count in (('prompt', 0), ('single', 1), ('reference', 2), ('multi', 3)):
                with self.subTest(mode=mode, quality=quality):
                    inputs = payloads(count)
                    data = dict(mode=mode, quality=quality, negative_prompt=NEGATIVE if quality == 'low' else '')
                    if count:
                        data['image1'] = inputs[0]
                    if count > 1:
                        data['image2'] = inputs[1]
                    if count > 2:
                        data['extra_images'] = inputs[2:]
                    self.fixture.text.answers = iter((fixture.FOX if mode == 'prompt' else directive(count),))
                    before = len(self.fixture.text.calls)
                    status, result = self.generate(**data)
                    self.assertEqual(status, 200, result)
                    self.assertEqual(len(self.fixture.text.calls), before + 1)
                    graph = self.fixture.graphs[-1]
                    self.assertEqual(graph['151']['class_type'], 'TextEncodeQwenImageEditPlus')
                    self.assertEqual(graph['149']['inputs']['prompt'], data['negative_prompt'])
                    self.assertEqual((graph['169']['inputs']['steps'], graph['169']['inputs']['cfg']), (40, 4) if quality == 'low' else (4, 1))
                    self.assertEqual('171' in graph, quality == 'fast')
                    self.assertNotIn('170', graph)
                    self.assertEqual(graph_errors(graph, object_info(ENGINES[qwen2511.ENGINE])), [])
                    self.assertEqual(result['reference_count'], count)
                    self.assertEqual(result['prompt_enhancement']['guide_version'], qwen2511.VERSION)
                    context = json.loads(self.fixture.text.calls[-1]['messages'][1]['content'])['context']
                    self.assertEqual(context['negative_prompt'], data['negative_prompt'])
                    self.assertEqual(context['sampling_profile'], 'standard' if quality == 'low' else 'lightning')

    def test_prompt_ignores_unrelated_uploads_and_replaces_one_generated_canvas(self):
        status, result = self.generate(image1={'base64':'unused broken data'}, image2={'base64':'also unused'})
        self.assertEqual(status, 200, result)
        self.assertNotIn('images', self.fixture.text.calls[0]['messages'][1])
        server.upload_image.assert_called_once()
        raw = base64.b64decode(server.upload_image.call_args.args[0]['base64'])
        with Image.open(BytesIO(raw)) as image:
            self.assertEqual(len(image.getcolors()), 1)
        text = self.fixture.graphs[-1]['151']['inputs']['prompt']
        self.assertEqual(text.count('The input is only a completely blank neutral canvas'), 1)
        self.assertIn(EDIT, text)

    def test_three_pictures_reach_gemma_and_both_encoder_branches_in_order(self):
        inputs = payloads(3)
        self.fixture.text.answers = iter((directive(3),))
        server.upload_image.side_effect = ['one.png', 'two.png', 'three.png']
        status, result = self.generate(mode='multi', image1=inputs[0], image2=inputs[1], extra_images=inputs[2:])
        self.assertEqual(status, 200, result)
        pictures = self.fixture.text.calls[0]['messages'][1]['images']
        for index, encoded in enumerate(pictures):
            with Image.open(BytesIO(base64.b64decode(encoded))) as image:
                self.assertEqual(image.getpixel((0, 0)), (index * 60, 40, 80))
        graph = self.fixture.graphs[0]
        self.assertEqual([graph[node]['inputs']['image'] for node in ('41', '83', '84')], ['one.png', 'two.png', 'three.png'])
        for node in ('151', '149'):
            self.assertEqual([graph[node]['inputs']['image' + str(i)] for i in (1, 2, 3)], [['160', 0], ['83', 0], ['84', 0]])

    def test_invalid_lightning_negative_or_fourth_input_fails_before_gemma(self):
        for data in (dict(quality='fast', negative_prompt=NEGATIVE),
                     dict(mode='multi', image1=payloads(1)[0], image2=payloads(2)[1], extra_images=payloads(2))):
            status, _ = self.generate(**data)
            self.assertEqual(status, 400)
            self.assertEqual(self.fixture.text.calls, [])
            self.assertEqual(self.fixture.graphs, [])

    def test_waiting_choices_are_bound_to_negative_adapter_and_profile(self):
        for changed in ('negative', 'adapter', 'quality'):
            with self.subTest(changed=changed):
                data = dict(clarify=True, negative_prompt='blur', quality='low', use_uncensored_lora=False)
                self.fixture.text.answers = iter((json.dumps(fixture.PROPOSAL),))
                _, first = self.generate(**data)
                if changed == 'negative':
                    data['negative_prompt'] = 'duplicate clocks'
                elif changed == 'adapter':
                    data['use_uncensored_lora'] = True
                else:
                    data.update(quality='fast', negative_prompt='')
                status, _ = self.generate(**data, clarification_token=first['clarification_token'], clarification_answer='1')
                self.assertEqual(status, 400)
                self.assertEqual(self.fixture.graphs, [])

    def test_ready_is_one_call_and_conflict_has_no_third_question_with_saved_provenance(self):
        self.fixture.text.answers = iter((json.dumps(dict(state='ready', prompt=fixture.FOX)),))
        status, result = self.generate(clarify=True)
        self.assertEqual(status, 200, result)
        self.assertEqual(len(self.fixture.text.calls), 1)
        self.assertIn(qwen2511.CLARIFICATION_RULES['prompt'], self.fixture.text.calls[0]['messages'][0]['content'])
        self.fixture.text.answers = iter((json.dumps(fixture.PROPOSAL), fixture.choice_caption(2, 1)))
        _, first = self.generate(clarify=True, negative_prompt=NEGATIVE)
        _, second = self.generate(clarify=True, negative_prompt=NEGATIVE, clarification_token=first['clarification_token'], clarification_answer='123')
        self.assertEqual(second['round'], 2)
        status, result = self.generate(clarify=True, negative_prompt=NEGATIVE, clarification_token=second['clarification_token'], clarification_answer='12')
        self.assertEqual(status, 200, result)
        info = result['prompt_enhancement']['clarification']
        self.assertEqual((info['version'], info['rounds']), (qwen2511.CLARIFICATION_VERSION, 2))
        self.assertEqual(self.fixture.graphs[-1]['149']['inputs']['prompt'], NEGATIVE)
        store = JobHistory(self.fixture.root / 'history.json')
        store.record(self.fixture.ident, 'done', server.JOB_META[self.fixture.ident])
        self.assertEqual(store.get(self.fixture.ident)['prompt_enhancement'], result['prompt_enhancement'])

    def test_variation_uses_single_vision_without_interactive_questions_and_keeps_adapters(self):
        self.fixture.text.answers = iter((EDIT,))
        status, result = self.generate(mode='prompt', operation='variation', image1=payloads(1)[0],
                                       quality='fast', use_uncensored_lora=True, original_prompt='Изначальная идея')
        self.assertEqual(status, 200, result)
        self.assertEqual((result['selected_mode'], result['mode'], result['reference_count']), ('prompt', 'single', 1))
        self.assertEqual(len(self.fixture.text.calls[0]['messages'][1]['images']), 1)
        graph = self.fixture.graphs[0]
        self.assertEqual(graph['151']['inputs']['prompt'], EDIT)
        self.assertEqual(graph['171']['inputs']['lora_name'], QWEN_LIGHTNING)
        self.assertEqual(graph['170']['inputs']['lora_name'], QWEN_UNCENSORED)
        self.assertEqual(graph['171']['inputs']['model'], ['170', 0])
        self.assertEqual(server.JOB_META[self.fixture.ident]['original_prompt'], 'Изначальная идея')


if __name__ == '__main__':
    unittest.main()
