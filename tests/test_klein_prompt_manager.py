"""CPU integration of Klein's researched modes; fake Gemma/ComfyUI, never model inference."""
import base64
from contextlib import nullcontext
import copy
from io import BytesIO
import json
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import test_image_prompt_manager as fixture
from engines import klein_prompts as klein
from engines.image_prompt_enhancer import PromptEnhancer
from engines.image_registry import ENGINES
from job_history import JobHistory
import studio_server as server

BOXES = [dict(id='red', desc='red sphere', bbox=[100, 100, 400, 400]),
         dict(id='blue', desc='blue cube', bbox=[600, 600, 900, 900])]
LAYOUT = ('A clean studio composition with a red sphere <red> in the upper-left foreground '
          'and a blue cube <blue> in the lower right, evenly lit against a neutral background.')


def payloads(count):
    return [{'base64':base64.b64encode(fixture.png(color=(index * 20, 40, 60))).decode()} for index in range(count)]


class KleinManagerTests(unittest.TestCase):
    def setUp(self):
        activity = patch('video.gemma_task.operation', side_effect=lambda *args, **kwargs: nullcontext())
        activity.start()
        self.addCleanup(activity.stop)

    def manager(self, answers=(fixture.FOX,)):
        text, gpu = fixture.Text(answers), Mock()
        return PromptEnhancer(text, gpu), text, gpu

    def test_modes_have_distinct_researched_guides_and_expected_sources(self):
        self.assertEqual(set(klein.MODE_RULES), set(ENGINES[klein.ENGINE].modes))
        self.assertEqual(set(klein.CLARIFICATION_RULES), set(klein.MODE_RULES))
        systems = set()
        for mode, count in (('prompt', 0), ('single', 1), ('reference', 2), ('multi', 10), ('layout', 0)):
            with self.subTest(mode=mode):
                answer = LAYOUT if mode == 'layout' else fixture.FOX
                manager, text, gpu = self.manager((answer,))
                result = manager.enhance('Нужная сцена', klein.ENGINE, mode, [fixture.png()] * count,
                                         {'layout_boxes':copy.deepcopy(BOXES)} if mode == 'layout' else {})
                self.assertEqual(result['guide_version'], klein.VERSION)
                self.assertEqual(result['prompt'], answer)
                system = text.calls[0]['messages'][0]['content']
                self.assertIn(klein.MODE_RULES[mode], system)
                self.assertNotIn(system, systems)
                systems.add(system)
                self.assertEqual(len(text.calls[0]['messages'][1].get('images', [])), count)
                self.assertFalse(text.calls[0]['think'])
                self.assertEqual(text.calls[0]['options']['temperature'], 0.0)
                self.assertEqual(text.owners, [])
                gpu.claim.assert_called_once_with('text')

    def test_ten_ordered_thumbnails_get_a_bounded_larger_context(self):
        raws = [base64.b64decode(item['base64']) for item in payloads(10)]
        manager, text, _ = self.manager()
        manager.enhance('Объедини все фото', klein.ENGINE, 'multi', raws)
        call = text.calls[0]
        self.assertGreaterEqual(call['options']['num_ctx'], 19456)
        self.assertLessEqual(call['options']['num_ctx'], 24576)
        for index, encoded in enumerate(call['messages'][1]['images']):
            with Image.open(BytesIO(base64.b64decode(encoded))) as picture:
                self.assertEqual(picture.getpixel((0, 0)), (index * 20, 40, 60))

    def test_bad_reference_arity_and_layout_fail_before_model_admission(self):
        for mode, images, context in (('reference', [fixture.png()], {}), ('single', [], {}),
                                     ('multi', [fixture.png()] * 2, {}), ('multi', [fixture.png()] * 11, {}),
                                     ('layout', [], {}), ('layout', [], {'layout_boxes':[dict(BOXES[0], bbox=[5, 4, 3, 2])]})):
            with self.subTest(mode=mode, count=len(images)):
                manager, text, gpu = self.manager()
                with self.assertRaises(ValueError):
                    manager.enhance('Задумка', klein.ENGINE, mode, images, context)
                self.assertEqual(text.calls, [])
                self.assertEqual(gpu.mock_calls, [])

    def test_missing_duplicate_invented_ids_and_generated_tables_retry_once(self):
        bad_outputs = (LAYOUT.replace('<red>', ''), LAYOUT + ' Another <red>.', LAYOUT.replace('<blue>', '<new>'),
                       LAYOUT + ' [{"id":"red","bbox":[0,0,1,1]}]')
        for bad in bad_outputs:
            with self.subTest(bad=bad):
                manager, text, _ = self.manager((bad, LAYOUT))
                result = manager.enhance('Два предмета', klein.ENGINE, 'layout', context={'layout_boxes':BOXES})
                self.assertEqual(result['prompt'], LAYOUT)
                self.assertEqual(len(text.calls), 2)

    def test_layout_literals_and_original_rows_are_protected(self):
        rows = [dict(id='sign', desc='Вывеска с надписью «ЛЕС»', bbox=[100, 100, 900, 900])]
        original = copy.deepcopy(rows)
        good = 'A wooden shop sign <sign> with the exact Cyrillic lettering "ЛЕС" in white paint, softly lit from the side.'
        manager, text, _ = self.manager((good.replace('ЛЕС', 'FOREST'), good))
        result = manager.enhance('Нарисуй вывеску', klein.ENGINE, 'layout', context={'layout_boxes':rows})
        self.assertEqual(result['prompt'], good)
        self.assertEqual(rows, original)
        self.assertIn('layout_plan', json.loads(text.calls[0]['messages'][1]['content'])['context'])

    def test_layout_positions_follow_swapped_boxes_without_renumbering(self):
        rows = copy.deepcopy(BOXES)
        rows[0]['bbox'], rows[1]['bbox'] = rows[1]['bbox'], rows[0]['bbox']
        manager, text, _ = self.manager((LAYOUT,))
        manager.enhance('Два предмета', klein.ENGINE, 'layout', context={'layout_boxes':rows})
        context = json.loads(text.calls[0]['messages'][1]['content'])['context']
        self.assertEqual(context['layout_boxes'], rows)
        self.assertIn('red sphere <red> in the lower right', context['layout_plan'])
        self.assertIn('blue cube <blue> in the upper left', context['layout_plan'])

    def test_default_layout_ids_follow_the_existing_validator(self):
        rows = [dict(desc='red sphere', bbox=[100, 100, 400, 400])]
        good = 'A red sphere <obj_1> in the upper left of a clean studio composition, softly lit against a pale background.'
        manager, _, _ = self.manager((good,))
        self.assertEqual(manager.enhance('Предмет', klein.ENGINE, 'layout', context={'layout_boxes':rows})['prompt'], good)


class KleinBridgeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.BridgeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        server.engine_readiness.return_value['modes'] = list(klein.MODE_RULES)

    def generate(self, **data):
        return self.fixture.generate(engine=klein.ENGINE, **data)

    def test_each_mode_enqueues_one_prepared_prompt_without_native_expansion(self):
        for mode, count in (('prompt', 0), ('single', 1), ('reference', 2), ('multi', 3), ('layout', 0)):
            with self.subTest(mode=mode):
                raws = payloads(count)
                data = {'mode':mode, 'expand':True}
                if raws:
                    data['image1'] = raws[0]
                if count > 1:
                    data['image2'] = raws[1]
                if count > 2:
                    data['extra_images'] = raws[2:]
                if mode == 'layout':
                    data['layout_boxes'] = copy.deepcopy(BOXES)
                self.fixture.text.answers = iter((LAYOUT if mode == 'layout' else fixture.FOX,))
                previous = len(self.fixture.text.calls)
                status, result = self.generate(**data)
                self.assertEqual(status, 200, result)
                graph = self.fixture.graphs[-1]
                self.assertEqual(len(self.fixture.text.calls) - previous, 1)
                self.assertNotIn('26', graph, 'Gemma output must not be rewritten by the native upsampler')
                self.assertNotIn('23', graph)
                self.assertEqual(result['expansion'], 'off')
                self.assertEqual(result['prompt_enhancement']['guide_version'], klein.VERSION)
                self.assertEqual(result['reference_count'], count)
                if mode == 'layout':
                    self.assertEqual(graph['30']['inputs']['string_a'], LAYOUT)
                    self.assertEqual(json.loads(graph['29']['inputs']['value']), BOXES)
                else:
                    self.assertEqual(graph['40']['inputs']['text'], fixture.FOX)

    def test_every_reference_reaches_gemma_and_image_latents_in_original_order(self):
        raws = payloads(10)
        server.upload_image.side_effect = ['ref%d.png' % i for i in range(1, 11)]
        status, result = self.generate(mode='multi', image1=raws[0], image2=raws[1], extra_images=raws[2:])
        self.assertEqual(status, 200, result)
        pictures = self.fixture.text.calls[0]['messages'][1]['images']
        self.assertEqual(len(pictures), 10)
        for index, encoded in enumerate(pictures):
            with Image.open(BytesIO(base64.b64decode(encoded))) as picture:
                self.assertEqual(picture.getpixel((0, 0)), (index * 20, 40, 60))
        graph = self.fixture.graphs[0]
        self.assertEqual(graph['10']['inputs']['image'], 'ref1.png')
        self.assertEqual(graph['11']['inputs']['image'], 'ref2.png')
        for index in range(8):
            self.assertEqual(graph[str(110 + index)]['inputs']['image'], 'ref%d.png' % (index + 3))
            self.assertEqual(graph['43']['inputs']['images.image' + str(index + 2)], [str(110 + index), 0])

    def test_sufficient_interactive_request_goes_directly_to_generation(self):
        self.fixture.text.answers = iter((json.dumps(dict(state='ready', prompt=fixture.FOX)),))
        status, result = self.generate(clarify=True)
        self.assertEqual(status, 200, result)
        self.assertEqual(len(self.fixture.text.calls), 1)
        self.assertIn('prompt_id', result)
        system = self.fixture.text.calls[0]['messages'][0]['content']
        self.assertIn(klein.CLARIFICATION_RULES['prompt'], system)
        self.assertEqual(self.fixture.text.calls[0]['options']['temperature'], 0.0)

    def test_conflicting_choices_have_two_question_cap_and_persist_klein_provenance(self):
        self.fixture.text.answers = iter((json.dumps(fixture.PROPOSAL), fixture.FOX))
        _, first = self.generate(clarify=True)
        status, second = self.generate(clarify=True, clarification_token=first['clarification_token'], clarification_answer='123')
        self.assertEqual((status, second['round']), (200, 2))
        status, result = self.generate(clarify=True, clarification_token=second['clarification_token'], clarification_answer='12')
        self.assertEqual(status, 200, result)
        info = result['prompt_enhancement']['clarification']
        self.assertEqual(info['version'], klein.CLARIFICATION_VERSION)
        self.assertEqual(info['rounds'], 2)
        store = JobHistory(self.fixture.root / 'history.json')
        store.record(self.fixture.ident, 'done', server.JOB_META[self.fixture.ident])
        receipt = store.get(self.fixture.ident)
        self.assertEqual(receipt['enhanced_prompt'], fixture.FOX)
        self.assertEqual(receipt['prompt_enhancement'], result['prompt_enhancement'])

    def test_changed_reference_or_box_cannot_reuse_waiting_choices(self):
        for mode in ('reference', 'layout'):
            with self.subTest(mode=mode):
                self.fixture.text.answers = iter((json.dumps(fixture.PROPOSAL),))
                data = dict(mode=mode, clarify=True)
                if mode == 'reference':
                    data.update(image1=payloads(1)[0], image2=payloads(2)[1])
                else:
                    data['layout_boxes'] = copy.deepcopy(BOXES)
                _, first = self.generate(**data)
                if mode == 'reference':
                    data['image2'] = payloads(3)[2]
                else:
                    data['layout_boxes'][0]['bbox'] = [50, 50, 800, 800]
                status, _ = self.generate(**data, clarification_token=first['clarification_token'], clarification_answer='1')
                self.assertEqual(status, 400)
                self.assertEqual(self.fixture.graphs, [])


if __name__ == '__main__':
    unittest.main()
