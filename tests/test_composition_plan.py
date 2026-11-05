"""Composition contracts, material limits, quality policy and Gemma's bounded planning (CPU only)."""
from contextlib import nullcontext
import base64
import copy
from io import BytesIO
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from composition.composition_plan import capabilities, estimate_minutes, image_options, normalize, request_data, GROUPS  # noqa: E402
from composition.composition_brain import CompositionBrain  # noqa: E402
from engines.image_registry import ENGINES  # noqa: E402
from gpu_coordinator import Busy, GpuCoordinator  # noqa: E402
import test_image_prompt_manager as fixture  # noqa: E402
from PIL import Image  # noqa: E402


def caps():
    return capabilities(dict(upscale_available=True, engines={ident:dict(ready=True, modes=list(engine.modes)) for ident, engine in ENGINES.items()}))


def step(engine='qwen-image-21-uc-fp8', mode='prompt', sources=None, prompt=fixture.FOX):
    return dict(engine=engine, mode=mode, sources=sources or [], prompt=prompt, upscale=1,
                native_megapixels=4 if engine in ('flux3-klein-int8', 'qwen-image-21-uc-fp8') else 1)


def plan(steps=None, count=1):
    return dict(canvas='landscape', variants=[dict(number=i + 1, title='Вариант %d' % (i + 1), steps=copy.deepcopy(steps or [step(prompt=fixture.FOX + ' Use composition %d.' % i)])) for i in range(count)])


class CompositionPlanTests(unittest.TestCase):
    def test_intermediate_text_stage_allows_only_the_roots_requested_cyrillic(self):
        request = dict(prompt='Лиса и надпись «УТРО».', variants=1, canvas='landscape')
        final = step('fibo2-dev-int8', 'single', ['previous'], fixture.FOX + ' A sign reads "УТРО".')
        first = step('fibo2-dev-int8', prompt=fixture.FOX + ' A sign reads "УТРО".')
        self.assertEqual(len(normalize(plan([first, final]), request, caps(), [])['variants'][0]['steps']), 2)
        first['prompt'] = fixture.FOX
        normalize(plan([first, final]), request, caps(), [])
        first['prompt'] = fixture.FOX + ' A sign reads "НОЧЬ".'
        with self.assertRaises(ValueError):
            normalize(plan([first, final]), request, caps(), [])

    def test_request_bounds_and_six_hero_limit(self):
        self.assertEqual(next(row['limit'] for row in GROUPS if row['id'] == 'hero'), 6)
        for count in range(1, 6):
            self.assertEqual(request_data(dict(prompt='Лиса', variants=count))['variants'], count)
        for value in (dict(prompt=''), dict(prompt='x'*5001), dict(prompt='Лиса', variants=True),
                      dict(prompt='Лиса', variants=6), dict(prompt='Лиса', canvas=[])):
            with self.assertRaises(ValueError): request_data(value)

    def test_capabilities_only_advertise_ready_known_models(self):
        value = capabilities(dict(engines={'missing':dict(ready=True), 'qwen-edit-2511':dict(ready=True, modes=['prompt', 'single'])}))
        self.assertEqual(set(value['engines']), {'qwen-edit-2511'})
        self.assertFalse(value['upscale'])
        with self.assertRaises(ValueError): capabilities(dict(engines={}))

    def test_maximum_profiles_are_code_policy_and_not_plan_speed_flags(self):
        for ident in ENGINES:
            value = image_options(dict(engine=ident, mode='prompt', prompt=fixture.FOX, upscale=1, native_megapixels=4, quality='fast'))
            self.assertEqual(value['quality'], 'good' if ident == 'qwen-image-21-uc-fp8' else 'low')
            if ident == 'qwen-edit-2511':
                self.assertFalse(value['use_uncensored_lora'])
                self.assertEqual(value['negative_prompt'], '')

    def test_each_variant_is_distinct_and_keeps_the_requested_canvas_and_lettering(self):
        request = request_data(dict(prompt='Нарисуй лес', variants=5, canvas='portrait'))
        result = normalize(plan(count=5), request, caps(), [])
        self.assertEqual(result['canvas'], 'portrait')
        self.assertEqual(len(result['variants']), 5)
        bad = plan(count=2)
        bad['variants'][1]['steps'] = copy.deepcopy(bad['variants'][0]['steps'])
        with self.assertRaises(ValueError): normalize(bad, request_data(dict(prompt='Лес', variants=2)), caps(), [])
        request = request_data(dict(prompt='Плакат с надписью «ЛЕС»'))
        with self.assertRaises(ValueError): normalize(plan(), request, caps(), [])

    def test_many_materials_are_composed_in_stages_without_discarding_previous_work(self):
        items = [dict(id='m%d' % i) for i in range(12)]
        steps = [step('flux3-klein-int8', 'multi', [row['id'] for row in items[:10]]),
                 step('qwen-edit-2511', 'multi', ['previous', 'm10', 'm11'])]
        result = normalize(plan(steps), request_data(dict(prompt='Все герои и предметы вместе')), caps(), items)
        self.assertEqual(len(result['variants'][0]['steps']), 2)
        for broken in (steps[:1], [steps[0], step('qwen-edit-2511', 'reference', ['m10', 'm11'])],
                       [step('qwen-edit-2511', 'multi', ['m0', 'm1', 'm2', 'm3'])]):
            with self.assertRaises(ValueError): normalize(plan(broken), request_data(dict(prompt='Все вместе')), caps(), items)

    def test_reference_mode_follows_source_count_and_material_ids_become_picture_numbers(self):
        items = [dict(id='hero-a'), dict(id='place-b')]
        drafted = step('flux3-klein-int8', 'multi', ['place-b', 'hero-a'],
                       'Place the woman from material hero-a on the pier from material place-b. Preserve her face and the lake. Keep a sign reading "hero-a".')
        result = normalize(plan([drafted]), request_data(dict(prompt='Героиня на причале, надпись "hero-a"')), caps(), items)['variants'][0]['steps'][0]
        self.assertEqual(result['mode'], 'reference')
        self.assertEqual(result['sources'], ['place-b', 'hero-a'])
        self.assertIn('woman from Picture 2', result['prompt'])
        self.assertIn('pier from Picture 1', result['prompt'])
        self.assertEqual(result['prompt'].count('hero-a'), 1)
        self.assertIn('"hero-a"', result['prompt'])
        too_many = step('qwen-edit-2511', 'reference', ['a', 'b', 'c', 'd'])
        with self.assertRaises(ValueError):
            normalize(plan([too_many]), request_data(dict(prompt='Все вместе')), caps(), [dict(id=ident) for ident in 'abcd'])

    def test_repeated_style_donor_is_one_input_and_numbered_roles_follow_it(self):
        drafted = step('flux3-klein-int8', 'multi', ['style-id', 'style-id'],
                       'Use Picture 2 only for watercolor style. Draw one whole fox in a snowy forest, with a sign reading "ЛЕС".')
        result = normalize(plan([drafted]), request_data(dict(prompt='Лиса, надпись "ЛЕС"')), caps(), [dict(id='style-id')])
        actual = result['variants'][0]['steps'][0]
        self.assertEqual(actual['sources'], ['style-id'])
        self.assertEqual(actual['mode'], 'single')
        self.assertIn('Picture 1 only for watercolor style', actual['prompt'])
        self.assertEqual(normalize(result, request_data(dict(prompt='Лиса, надпись "ЛЕС"')), caps(), [dict(id='style-id')]), result)

    def test_requested_alpha_requires_a_capable_final_stage_but_not_quoted_lettering(self):
        request = request_data(dict(prompt='Один чайник на прозрачном фоне, настоящий альфа-канал PNG'))
        rgb = step('flux3-klein-int8')
        with self.assertRaisesRegex(ValueError, 'alpha'):
            normalize(plan([rgb]), request, caps(), [])
        rgba = step('qwen-image-21-uc-fp8', 'single', ['previous'])
        self.assertEqual(normalize(plan([rgb, rgba]), request, caps(), [])['variants'][0]['steps'][-1]['engine'], 'qwen-image-21-uc-fp8')
        for text in ('Лиса без прозрачного фона', 'A fox without transparency'):
            self.assertEqual(normalize(plan([rgb]), request_data(dict(prompt=text)), caps(), [])['variants'][0]['steps'][0]['engine'], 'flux3-klein-int8')
        quoted = step('flux3-klein-int8', prompt='A red teapot on a wooden table. A small sign reads "RGBA" in clear lettering.')
        normalize(plan([quoted]), request_data(dict(prompt='Чайник и табличка "RGBA"')), caps(), [])

    def test_quoted_source_group_labels_are_not_required_as_visible_lettering(self):
        items = [dict(id='hero-a'), dict(id='place-b')]
        drafted = plan([step('flux3-klein-int8', 'reference', ['hero-a', 'place-b'],
                             'Place the woman from Picture 1 on the wooden pier from Picture 2. Preserve her face, jacket and the lake.')])
        result = normalize(drafted, request_data(dict(prompt='Героиня из фото «Герои» на причале из фото «Места». Без текста.')), caps(), items)
        self.assertEqual(len(result['variants']), 1)
        with self.assertRaises(ValueError):
            normalize(drafted, request_data(dict(prompt='Героиня на причале, табличка с надписью «Герои».')), caps(), items)

    def test_matching_canvas_source_is_first_and_picture_roles_are_renumbered_once(self):
        items = [dict(id='ref', width=1000, height=1000), dict(id='hero', width=800, height=1200),
                 dict(id='place', width=1200, height=800)]
        prompt = ('Place the woman from the second source on the pier from Image 3. Use Picture 1 for the veranda. '
                  'Keep a small sign reading "Picture 1".')
        request = request_data(dict(prompt='Героиня на веранде', canvas='landscape'))
        result = normalize(plan([step('flux3-klein-int8', 'multi', ['ref', 'hero', 'place'], prompt)]), request, caps(), items)
        first = result['variants'][0]['steps'][0]
        self.assertEqual(first['sources'], ['place', 'ref', 'hero'])
        self.assertIn('woman from Picture 3', first['prompt'])
        self.assertIn('pier from Picture 1', first['prompt'])
        self.assertIn('Picture 2 for the veranda', first['prompt'])
        self.assertIn('"Picture 1"', first['prompt'])
        self.assertEqual(normalize(result, request, caps(), items), result)
        chained = plan([step('flux3-klein-int8', 'multi', ['ref', 'hero', 'place'], prompt),
                        step('qwen-edit-2511', 'reference', ['previous', 'place'])])
        self.assertEqual(normalize(chained, request, caps(), items)['variants'][0]['steps'][1]['sources'], ['previous', 'place'])

    def test_unsupported_inputs_controls_and_missing_components_are_rejected(self):
        cases = [step('unknown'), step('z-image-turbo-int8', 'single', ['m1']),
                 step('qwen-image-21-uc-fp8', 'reference', ['m1', 'missing']), step('flux3-klein-int8', 'layout')]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize(plan([value]), request_data(dict(prompt='Лиса')), caps(), [dict(id='m1')])
        value = step(); value['upscale'] = 4
        unavailable = caps(); unavailable['upscale'] = False
        with self.assertRaises(ValueError): normalize(plan([value]), request_data(dict(prompt='Лиса')), unavailable, [])

    def test_a_refused_plan_says_why_so_that_the_retry_can_fix_it(self):
        items = [dict(id='m1'), dict(id='m2')]
        request = request_data(dict(prompt='Лиса'))
        cases = [([step('unknown')], 'engine'), ([step('z-image-turbo-int8', 'single', ['m1', 'm2'])], 'modes'),
                 ([step('qwen-edit-2511', 'single')], 'takes 1 sources, not 0'), ([step('qwen-edit-2511', 'single', ['previous'])], '"previous" does not exist in the first step'),
                 ([step('qwen-edit-2511', 'single', ['zzz'])], '"zzz" is not a material ID'),
                 ([step('flux3-klein-int8', 'single', ['m1']), step('qwen-edit-2511', 'single', ['m2'])], 'FIRST source'),
                 ([step('qwen-edit-2511', 'single', ['m1'], prompt='Короткий?')], 'prompt is unusable'),
                 ([step('flux3-klein-int8', 'single', ['m1'])], 'leaves out: m2')]
        for steps, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError) as caught:
                    normalize(plan(steps), request, caps(), items)
                self.assertIn(reason, str(caught.exception))
        again = plan(count=2); again['variants'][1]['steps'] = copy.deepcopy(again['variants'][0]['steps'])
        with self.assertRaises(ValueError) as caught:
            normalize(again, request_data(dict(prompt='Лиса', variants=2)), caps(), [])
        self.assertIn('variant 2 repeats', str(caught.exception))

    def test_the_picture_may_not_grow_beyond_four_k_and_a_crowded_qwen_stage_stays_at_one_megapixel(self):
        request = request_data(dict(prompt='Лиса'))
        for native, upscale, allowed in ((1, 4, True), (4, 2, True), (4, 4, False), (16, 1, True), (16, 2, False)):
            value = step('flux3-klein-int8'); value.update(native_megapixels=native, upscale=upscale)
            with self.subTest(native=native, upscale=upscale):
                if allowed:
                    self.assertEqual(normalize(plan([value]), request, caps(), [])['variants'][0]['steps'][0]['native_megapixels'], native)
                else:
                    with self.assertRaises(ValueError) as caught: normalize(plan([value]), request, caps(), [])
                    self.assertIn('16', str(caught.exception))
        items = [dict(id='m%d' % i) for i in range(6)]
        crowded = step('qwen-image-21-uc-fp8', 'multi', [row['id'] for row in items]); crowded['native_megapixels'] = 4
        few = step('qwen-image-21-uc-fp8', 'multi', ['m0', 'm1', 'm2']); few['native_megapixels'] = 4
        self.assertEqual(normalize(plan([crowded]), request, caps(), items)['variants'][0]['steps'][0]['native_megapixels'], 1)
        self.assertEqual(normalize(plan([few]), request, caps(), items[:3])['variants'][0]['steps'][0]['native_megapixels'], 4)

    def test_the_estimate_grows_with_steps_resolution_and_upscale(self):
        one = normalize(plan([step('flux3-klein-int8')]), request_data(dict(prompt='Лиса')), caps(), [])
        many = normalize(plan([step('flux3-klein-int8', 'prompt'), step('qwen-edit-2511', 'single', ['previous'])]), request_data(dict(prompt='Лиса')), caps(), [])
        self.assertEqual(estimate_minutes(one), round((120 + 40 * 1.5 + 45) / 60))
        self.assertGreater(estimate_minutes(many), estimate_minutes(one))
        five = dict(canvas='square', variants=one['variants'] * 5)
        self.assertGreater(estimate_minutes(five), 2 * estimate_minutes(one))
        pair = plan([step('flux3-klein-int8', 'reference', ['m0', 'm1'])])
        four = plan([step('flux3-klein-int8', 'multi', ['m0', 'm1', 'm2', 'm3'])])
        self.assertGreater(estimate_minutes(four), estimate_minutes(pair))

    def test_composition_reservation_blocks_other_modules_but_admits_its_own_steps(self):
        gpu = GpuCoordinator(probes=dict(composition=lambda:True))
        with self.assertRaises(Busy): gpu.claim('image')
        with gpu.acting_for('composition'):
            gpu.claim('text')
            gpu.claim('image')
        with self.assertRaises(Busy): gpu.claim('video')


class CompositionBrainTests(unittest.TestCase):
    def setUp(self):
        current = patch('video.gemma_task.operation', side_effect=lambda *args, **kwargs: nullcontext())
        current.start(); self.addCleanup(current.stop)

    def test_planner_retries_once_and_uses_gemma_task_without_thinking(self):
        text, gpu = fixture.Text(('invalid json', json.dumps(plan()))), Mock()
        brain = CompositionBrain(text, gpu)
        value = brain.make_plan(request_data(dict(prompt='Лиса')), caps(), [], {}, threading.Event())
        self.assertEqual(len(value['variants']), 1)
        self.assertEqual(len(text.calls), 2)
        self.assertTrue(all(call['think'] is False for call in text.calls))
        self.assertEqual(text.owners, [])

    def test_materials_are_seen_in_order_in_bounded_batches_before_planning(self):
        items = [dict(id='m%d' % i, group='hero', label='Герой %d' % i) for i in range(6)]
        output = plan([step('flux3-klein-int8', 'multi', [row['id'] for row in items])])
        text, gpu = fixture.Text(('First three heroes.', 'Next three heroes.', json.dumps(output))), Mock()
        brain = CompositionBrain(text, gpu)
        brain.make_plan(request_data(dict(prompt='Все вместе')), caps(), items, {row['id']:fixture.png() for row in items}, threading.Event())
        self.assertEqual([len(call['messages'][1].get('images', [])) for call in text.calls], [3, 3, 0])
        for index in range(2):
            self.assertEqual([row['id'] for row in json.loads(text.calls[index]['messages'][1]['content'])], ['m%d' % i for i in range(index*3, index*3+3)])

    def test_cancellation_during_a_plan_cannot_return_a_valid_late_result(self):
        stopped = threading.Event()
        text = fixture.Text((json.dumps(plan()),))
        text.on_answer = stopped.set
        brain = CompositionBrain(text, Mock())
        with self.assertRaises(ValueError): brain.make_plan(request_data(dict(prompt='Лиса')), caps(), [], {}, stopped)
        self.assertEqual(text.owners, [])
        self.assertEqual(len(text.calls), 1)

    def test_transparent_material_does_not_show_hidden_rgb_to_the_planner(self):
        source = BytesIO()
        Image.new('RGBA', (8, 8), (180, 0, 220, 0)).save(source, 'PNG')
        raw = source.getvalue()
        items = [dict(id='object1', group='object', label='Transparent object')]
        text = fixture.Text(('Transparent object.', json.dumps(plan([step('flux3-klein-int8', 'single', ['object1'])]))))
        CompositionBrain(text, Mock()).make_plan(request_data(dict(prompt='Keep the object')), caps(), items,
                                               {'object1':raw}, threading.Event())
        encoded = text.calls[0]['messages'][1]['images'][0]
        with Image.open(BytesIO(base64.b64decode(encoded))) as observed:
            self.assertEqual(observed.getpixel((0, 0)), (127, 127, 127))
        self.assertEqual(source.getvalue(), raw)

    def test_planner_receives_oriented_dimensions_without_mutating_materials(self):
        buffer = BytesIO()
        image = Image.new('RGB', (48, 96), 'green')
        exif = Image.Exif(); exif[274] = 6
        image.save(buffer, 'PNG', exif=exif)
        items = [dict(id='m1', group='place', label='Lake')]
        text = fixture.Text(('Landscape lake.', json.dumps(plan([step('flux3-klein-int8', 'single', ['m1'])]))))
        CompositionBrain(text, Mock()).make_plan(request_data(dict(prompt='Lake', canvas='landscape')), caps(), items,
                                               {'m1':buffer.getvalue()}, threading.Event())
        observed = json.loads(text.calls[0]['messages'][1]['content'])[0]
        planned = json.loads(text.calls[1]['messages'][1]['content'])['materials'][0]
        self.assertEqual((observed['width'], observed['height']), (96, 48))
        self.assertEqual(planned, observed)
        self.assertNotIn('width', items[0])
