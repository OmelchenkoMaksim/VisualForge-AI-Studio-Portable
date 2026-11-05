"""Qwen 2.1's real route/graphs with fake Gemma and GPU: routing evidence, not image quality."""
import base64
from contextlib import nullcontext
from io import BytesIO
import json
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import test_image_prompt_manager as fixture
from engines import qwen21_prompts as qwen21
from engines.image_preflight import engine_readiness, graph_errors
from engines.image_prompt_enhancer import PromptEnhancer
from engines.image_registry import ENGINES
from job_history import JobHistory
import studio_server as server
from test_image_engines import object_info


def caption(count=0):
    if count < 2:
        return fixture.FOX
    return ('Create a coherent forest composition using ' + ', '.join('<image%d>' % (i + 1) for i in range(count)) +
            ' in their assigned roles, retaining each supplied subject and harmonizing light, scale and perspective.')


def payloads(count):
    return [dict(base64=base64.b64encode(fixture.png(color=(i * 20, 40, 60))).decode()) for i in range(count)]


def transparent_png():
    image = Image.new('RGBA', (96, 64), (255, 0, 255, 0))
    image.putpixel((50, 30), (10, 20, 30, 255))
    output = BytesIO()
    image.save(output, 'PNG')
    return output.getvalue()


class Qwen21ManagerTests(unittest.TestCase):
    def setUp(self):
        activity = patch('video.gemma_task.operation', side_effect=lambda *args, **kwargs: nullcontext())
        activity.start()
        self.addCleanup(activity.stop)

    def manager(self, answers=(fixture.FOX,)):
        text, gpu = fixture.Text(answers), Mock()
        return PromptEnhancer(text, gpu), text, gpu

    def test_all_modes_use_their_own_guide_and_source_arity(self):
        self.assertEqual(set(qwen21.MODE_RULES), set(ENGINES[qwen21.ENGINE].modes))
        self.assertEqual(set(qwen21.CLARIFICATION_RULES), set(qwen21.MODE_RULES))
        for mode, count in (('prompt', 0), ('single', 1), ('reference', 2), ('multi', 10)):
            with self.subTest(mode=mode):
                manager, text, gpu = self.manager((caption(count),))
                result = manager.enhance('Нужная сцена', qwen21.ENGINE, mode, [fixture.png()] * count)
                self.assertEqual(result['prompt'], caption(count))
                self.assertEqual(result['guide_version'], qwen21.VERSION)
                self.assertIn(qwen21.MODE_RULES[mode], text.calls[0]['messages'][0]['content'])
                self.assertEqual(len(text.calls[0]['messages'][1].get('images', [])), count)
                self.assertFalse(text.calls[0]['think'])
                self.assertEqual(text.calls[0]['options']['temperature'], qwen21.TEMPERATURE)
                gpu.claim.assert_called_once_with('text')
                self.assertEqual(text.owners, [])
        for mode, count in (('prompt', 1), ('single', 0), ('reference', 1), ('multi', 2), ('multi', 11)):
            manager, text, gpu = self.manager()
            with self.subTest(mode=mode, count=count), self.assertRaises(ValueError):
                manager.enhance('Задумка', qwen21.ENGINE, mode, [fixture.png()] * count)
            self.assertEqual(text.calls, [])
            self.assertEqual(gpu.mock_calls, [])

    def test_missing_invented_wrong_case_and_single_image_tags_are_not_submitted(self):
        cases = [('reference', 2, caption(2).replace('<image2>', 'Image 2')),
                 ('reference', 2, caption(2).replace('<image2>', '<Image2>')),
                 ('multi', 3, caption(3) + ' Also use <image4>.'),
                 ('single', 1, fixture.FOX + ' Keep <image1>.'),
                 ('prompt', 0, fixture.FOX + ' Follow <image1>.')]
        for mode, count, bad in cases:
            with self.subTest(mode=mode, bad=bad):
                manager, text, _ = self.manager((bad, caption(count)))
                result = manager.enhance('Сцена', qwen21.ENGINE, mode, [fixture.png()] * count)
                self.assertEqual(result['prompt'], caption(count))
                self.assertEqual(len(text.calls), 2)
        manager, text, _ = self.manager((caption(3), caption(3)))
        with self.assertRaises(ValueError):
            manager.enhance('Два источника', qwen21.ENGINE, 'reference', [fixture.png()] * 2)
        self.assertEqual(len(text.calls), 2)

    def test_russian_lettering_removal_and_replacement_keep_only_final_text(self):
        good = 'Replace the sign lettering with "НОВОЕ", preserving the wooden frame, lighting and all other details.'
        manager, text, _ = self.manager((good.replace('НОВОЕ', 'NEW'), good))
        self.assertEqual(manager.enhance('Замени «СТАРОЕ» на «НОВОЕ»', qwen21.ENGINE, 'single', [fixture.png()])['prompt'], good)
        self.assertEqual(len(text.calls), 2)

    def test_explicit_alpha_survives_plain_and_ready_answers_but_glass_does_not_force_alpha(self):
        for request in ('Нарисуй лису на прозрачном фоне', 'a fox, RGBA', 'keep the alpha channel', 'Сохрани альфа-канал'):
            for interactive in (False, True):
                raw = json.dumps(dict(state='ready', prompt=fixture.FOX)) if interactive else fixture.FOX
                manager, _, _ = self.manager((raw,))
                method = manager.prepare if interactive else manager.enhance
                result = method(request, qwen21.ENGINE, 'prompt')['prompt']
                self.assertTrue(result.startswith('This is an RGBA image with transparency. '))
                self.assertTrue(result.endswith('The image has alpha channel and the background is transparent.'))
        manager, _, _ = self.manager()
        self.assertEqual(manager.enhance('A transparent glass cup on a table', qwen21.ENGINE, 'prompt')['prompt'], fixture.FOX)

    def test_alpha_cue_is_not_inferred_from_lettering_or_explicitly_opaque_requests(self):
        for request in ('A sign reading "RGBA"', 'A sign reading «transparent background»', 'A fox without transparency',
                        'A fox with no alpha channel', 'Лиса без прозрачного фона', 'Лиса на непрозрачном фоне'):
            with self.subTest(request=request):
                self.assertEqual(qwen21.rgba_prompt(fixture.FOX, request), fixture.FOX)
        wrapped = qwen21.rgba_prompt(fixture.FOX, 'RGBA')
        self.assertEqual(qwen21.rgba_prompt(wrapped, 'RGBA'), wrapped)

    def test_composition_alpha_wrapper_uses_the_human_intent_instead_of_an_invented_step_cue(self):
        candidate = 'A studio photograph of one red teapot on a seamless white background, with soft natural lighting.'
        step = 'Refine the teapot and output alpha transparency on a white background.'
        context = dict(composition_intent='Один красный чайник на чистом белом фоне.')
        result = PromptEnhancer._caption(candidate, step, qwen21.ENGINE, 'single', 1, context)
        self.assertEqual(result, candidate)
        context['composition_intent'] = 'Один красный чайник, настоящий прозрачный фон и альфа-канал PNG.'
        result = PromptEnhancer._caption(candidate, step, qwen21.ENGINE, 'single', 1, context)
        self.assertTrue(result.startswith('This is an RGBA image with transparency.'))

    def test_alpha_preview_hides_invisible_rgb_and_reports_the_original_channel(self):
        manager, text, _ = self.manager()
        manager.enhance('Сохрани объект', qwen21.ENGINE, 'single', [transparent_png()])
        user = text.calls[0]['messages'][1]
        self.assertEqual(json.loads(user['content'])['context']['source_alpha'],
                         [dict(image=1, transparent=True, alpha_range=[0, 255])])
        with Image.open(BytesIO(base64.b64decode(user['images'][0]))) as image:
            self.assertEqual(image.getpixel((0, 0)), (127, 127, 127))
            self.assertEqual(image.getpixel((50, 30)), (10, 20, 30))


class Qwen21BridgeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.BridgeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        server.engine_readiness.return_value['modes'] = list(qwen21.MODE_RULES)

    def generate(self, **data):
        return self.fixture.generate(engine=qwen21.ENGINE, **data)

    def test_all_modes_submit_final_caption_once_and_keep_native_rgba_and_2k(self):
        for mode, count in (('prompt', 0), ('single', 1), ('reference', 2), ('multi', 3)):
            with self.subTest(mode=mode):
                raws = payloads(count)
                data = dict(mode=mode, expand=True, native_megapixels=4)
                if count:
                    data['image1'] = raws[0]
                if count > 1:
                    data['image2'] = raws[1]
                if count > 2:
                    data['extra_images'] = raws[2:]
                self.fixture.text.answers = iter((caption(count),))
                status, result = self.generate(**data)
                self.assertEqual(status, 200, result)
                graph = self.fixture.graphs[-1]
                self.assertEqual(graph['40']['inputs']['prompt'], caption(count))
                self.assertEqual(graph['40']['inputs']['resolution'], 2048)
                self.assertNotIn('26', graph)
                self.assertNotIn('23', graph)
                self.assertEqual(result['expansion'], 'off')
                self.assertEqual(result['prompt_enhancement']['guide_version'], qwen21.VERSION)
                self.assertEqual(result['reference_count'], count)
                self.assertEqual(graph_errors(graph, object_info(ENGINES[qwen21.ENGINE])), [])
                for index in range(count):
                    joined = str(31 + index) if index < 2 else str(310 + index - 2)
                    self.assertEqual(graph[joined]['class_type'], 'JoinImageWithAlpha')

    def test_ten_sources_reach_vision_and_rgba_encoder_in_the_same_order(self):
        raws = payloads(10)
        self.fixture.text.answers = iter((caption(10),))
        server.upload_image.side_effect = ['ref%d.png' % i for i in range(1, 11)]
        status, result = self.generate(mode='multi', image1=raws[0], image2=raws[1], extra_images=raws[2:])
        self.assertEqual(status, 200, result)
        call = self.fixture.text.calls[0]
        self.assertGreaterEqual(call['options']['num_ctx'], 19456)
        for index, encoded in enumerate(call['messages'][1]['images']):
            with Image.open(BytesIO(base64.b64decode(encoded))) as image:
                self.assertEqual(image.getpixel((0, 0)), (index * 20, 40, 60))
        graph = self.fixture.graphs[-1]
        for index in range(10):
            loader = str(11 + index) if index < 2 else str(110 + index - 2)
            joined = str(31 + index) if index < 2 else str(310 + index - 2)
            self.assertEqual(graph[loader]['inputs']['image'], 'ref%d.png' % (index + 1))
            self.assertEqual(graph[joined]['inputs'], dict(image=[loader, 0], alpha=[loader, 1]))
            self.assertEqual(graph['40']['inputs']['images.image_' + str(index + 1)], [joined, 0])

    def test_legacy_expand_true_does_not_block_managed_alpha_or_load_native_pe(self):
        status, result = self.generate(prompt='Нарисуй лису на прозрачном фоне', expand=True)
        self.assertEqual(status, 200, result)
        self.assertIn('RGBA image with transparency', self.fixture.graphs[0]['40']['inputs']['prompt'])
        self.assertNotIn('26', self.fixture.graphs[0])
        self.assertEqual(len(self.fixture.text.calls), 1)
        status, _ = self.generate(expand='yes')
        self.assertEqual(status, 400)
        self.assertEqual(len(self.fixture.text.calls), 1, 'legacy flag types are still validated before Gemma')

    def test_original_alpha_bytes_are_uploaded_while_gemma_sees_a_matte_preview(self):
        raw = transparent_png()
        payload = dict(base64=base64.b64encode(raw).decode())
        status, result = self.generate(mode='single', image1=payload, prompt='Удали фон, сделай прозрачный PNG')
        self.assertEqual(status, 200, result)
        server.upload_image.assert_called_once_with(payload)
        context = json.loads(self.fixture.text.calls[0]['messages'][1]['content'])['context']
        self.assertTrue(context['source_alpha'][0]['transparent'])
        self.assertIn('RGBA image with transparency', self.fixture.graphs[0]['40']['inputs']['prompt'])

    def test_ready_uses_one_call_and_conflicts_stop_at_two_questions_with_history(self):
        self.fixture.text.answers = iter((json.dumps(dict(state='ready', prompt=fixture.FOX)),))
        status, result = self.generate(clarify=True)
        self.assertEqual(status, 200, result)
        self.assertIn('prompt_id', result)
        self.assertEqual(len(self.fixture.text.calls), 1)
        self.assertIn(qwen21.CLARIFICATION_RULES['prompt'], self.fixture.text.calls[0]['messages'][0]['content'])
        self.fixture.text.answers = iter((json.dumps(fixture.PROPOSAL), fixture.choice_caption(2, 1)))
        _, first = self.generate(clarify=True)
        _, second = self.generate(clarify=True, clarification_token=first['clarification_token'], clarification_answer='123')
        self.assertEqual(second['round'], 2)
        status, result = self.generate(clarify=True, clarification_token=second['clarification_token'], clarification_answer='12')
        self.assertEqual(status, 200, result)
        info = result['prompt_enhancement']['clarification']
        self.assertEqual(info['version'], qwen21.CLARIFICATION_VERSION)
        self.assertEqual(info['rounds'], 2)
        store = JobHistory(self.fixture.root / 'history.json')
        store.record(self.fixture.ident, 'done', server.JOB_META[self.fixture.ident])
        self.assertEqual(store.get(self.fixture.ident)['prompt_enhancement'], result['prompt_enhancement'])

    def test_changed_alpha_source_or_pixel_budget_invalidates_waiting_choices(self):
        for change in ('source', 'budget'):
            with self.subTest(change=change):
                data = dict(mode='single', image1=payloads(1)[0], clarify=True, native_megapixels=1)
                self.fixture.text.answers = iter((json.dumps(fixture.PROPOSAL),))
                _, first = self.generate(**data)
                if change == 'source':
                    data['image1'] = dict(base64=base64.b64encode(transparent_png()).decode())
                else:
                    data['native_megapixels'] = 4
                status, _ = self.generate(**data, clarification_token=first['clarification_token'], clarification_answer='1')
                self.assertEqual(status, 400)
                self.assertEqual(self.fixture.graphs, [])

    def test_preflight_does_not_inspect_unused_native_pe_nodes_or_components(self):
        engine = ENGINES[qwen21.ENGINE]
        row = engine_readiness(engine, object_info(engine), disabled_components=['image.model.qwen21-pe-i2i', 'image.model.qwen21-pe-t2i'])
        self.assertTrue(row['ready'], row)
        self.assertTrue(row['prompt_manager'])
        self.assertFalse(row['expansion'])
        self.assertEqual(row['expansion_reasons'], {})
        self.assertEqual(row['expansion_available_modes'], [])


if __name__ == '__main__':
    unittest.main()
