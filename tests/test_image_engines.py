"""Image engine contract: asset closure against the catalog, workflow wiring, and the prompt-only Z-Image Turbo."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
from engines.preflight import engine_readiness
from engines.registry import CANVASES, ENGINES
from engines.workflow_builder import build_workflow, collect_expansion, collect_outputs, resolve_expansion, resolve_workflow_mode, validate_request
from model_requirements import COMPONENTS, facts

ZIMAGE = ENGINES['z-image-turbo-int8']
FLUX = ENGINES['flux3-klein-int8']
AWAITING_CATALOG = set()  # ids whose catalog entry is not pinned yet


def object_info(engine, present=True):
    """A minimal /object_info for one engine: every template input is declared (optional: modes use different subsets), loader files are combos."""
    info = {}
    for mode in engine.modes:
        for node in engine.template(mode).values():
            required = info.setdefault(node['class_type'], {'input': {'optional': {}}})['input']['optional']
            for key in node['inputs']:
                container, _, slot = key.partition('.')
                if slot and container in node['inputs']:
                    # Nested "<dynamic combo>.<input>" keys: declare the combo with the option the graph selects.
                    option = {'key': node['inputs'][container], 'inputs': {'required': {slot: ['*']}}}
                    combo = required.get(container)
                    if not combo or combo[0] != 'COMFY_DYNAMICCOMBO_V3':
                        combo = required[container] = ['COMFY_DYNAMICCOMBO_V3', {'options': []}]
                    existing = next((o for o in combo[1]['options'] if o['key'] == option['key']), None)
                    if existing is None:
                        combo[1]['options'].append(option)
                    else:
                        existing['inputs']['required'].update(option['inputs']['required'])
                elif slot:
                    # Autogrow slots ("images.image0"): a prefix template exposes only prefix/min/max, as ComfyUI's object_info does.
                    prefix = slot.rstrip('0123456789')
                    required.setdefault(container, ['COMFY_AUTOGROW_V3', {'template': {'input': {'required': {prefix: ['*']}}, 'prefix': prefix, 'min': 1, 'max': 100}}])
                else:
                    required.setdefault(key, ['*'])
    for cls, key, filename in engine.assets:
        # Engines can load two files through one loader (two CLIPLoaders): the combo lists all of them.
        files = info[cls]['input']['optional'][key]
        files = files if isinstance(files, list) and files and isinstance(files[0], list) else [[]]
        files[0].append(filename if present else 'other.safetensors')
        info[cls]['input']['optional'][key] = files
    return info


class CatalogParityTests(unittest.TestCase):
    def test_every_engine_has_a_component_that_ships_its_assets(self):
        catalog = json.loads((ROOT / 'App/config/components.json').read_text(encoding='utf-8'))
        files = {c['id']: {Path(f['path']).name for f in c.get('files', [])} for c in catalog['components']}
        for ident, engine in ENGINES.items():
            component = COMPONENTS['image:' + ident]
            if ident in AWAITING_CATALOG:
                self.assertNotIn(component, files, ident)
                continue
            self.assertIn(component, files, ident)
            for _, _, filename in engine.assets:
                self.assertIn(filename, files[component], ident + ': ' + filename)

    def test_zimage_component_is_pinned_and_hash_checked(self):
        catalog = json.loads((ROOT / 'App/config/components.json').read_text(encoding='utf-8'))
        component = next(c for c in catalog['components'] if c['id'] == 'image.model.z-image-turbo')
        self.assertEqual(component['requires'], ['image.runtime'])
        self.assertEqual(component['license'], 'Apache-2.0')
        self.assertEqual(len(component['files']), 3)
        for item in component['files']:
            self.assertRegex(item['sha256'], r'^[0-9a-f]{64}$')
            self.assertTrue(item['bytes'] > 0)
            self.assertIn('/resolve/6fc90a3b1b653e935a0d175e260736de25b84df5/', item['urls'][0])


class ReadinessTests(unittest.TestCase):
    def test_every_engine_is_ready_when_its_files_and_nodes_exist(self):
        for ident, engine in ENGINES.items():
            self.assertTrue(engine_readiness(engine, object_info(engine))['ready'], ident)

    def test_missing_weights_point_to_components(self):
        row = engine_readiness(ZIMAGE, object_info(ZIMAGE, present=False))
        self.assertFalse(row['ready'])
        self.assertEqual(row['status'], 'unavailable')
        self.assertIn('z_image_turbo_int8_convrot.safetensors', row['missing_assets'])
        self.assertIn('Компоненты', ' '.join(row['reasons']))

    def test_missing_node_class_makes_the_engine_incompatible(self):
        info = object_info(ZIMAGE)
        del info['ModelSamplingAuraFlow']
        row = engine_readiness(ZIMAGE, info)
        self.assertEqual(row['status'], 'incompatible')
        self.assertEqual(row['missing_node_types'], ['ModelSamplingAuraFlow'])

    def test_zimage_reports_only_what_it_does(self):
        row = engine_readiness(ZIMAGE, object_info(ZIMAGE))
        self.assertEqual(row['modes'], ['prompt'])
        self.assertEqual(row['quality_steps'], {'low': 8})
        self.assertEqual(row['cfg'], 1)
        self.assertEqual(row['sampler'], 'res_multistep / simple')
        self.assertIn('без редактирования', row['capabilities'])

    def test_qwen_engines_keep_their_reported_settings(self):
        self.assertEqual(engine_readiness(ENGINES['qwen-edit-2511'], object_info(ENGINES['qwen-edit-2511']))['cfg'], 4)
        self.assertEqual(engine_readiness(ENGINES['qwen-image-21-uc-fp8'], object_info(ENGINES['qwen-image-21-uc-fp8']))['cfg'], 1)


class FluxWorkflowTests(unittest.TestCase):
    def build(self, **overrides):
        args = dict(mode='prompt', prompt='a red fox', image1=None, image2=None, quality='low', upscale=1, seed=5, canvas='portrait', expand=None)
        args.update(overrides)
        return build_workflow(FLUX, args.pop('mode'), args.pop('prompt'), args.pop('image1'), args.pop('image2'), args.pop('quality'),
                              args.pop('upscale'), args.pop('seed'), args.pop('canvas'), args.pop('expand'))

    def test_expansion_is_the_default_and_feeds_the_text_encode(self):
        graph, meta = self.build()
        self.assertEqual(graph['25']['inputs']['value'], 'a red fox')
        self.assertEqual(graph['40']['inputs']['text'], ['26', 0])
        self.assertEqual(graph['26']['inputs']['system_prompt'], ['24', 0])
        self.assertIn('prompt upsampler', graph['24']['inputs']['value'])
        self.assertEqual((meta['expansion'], meta['expansion_preview_node']), ('upsampler', '27'))
        self.assertEqual((graph['60']['inputs']['steps'], graph['60']['inputs']['cfg'], graph['60']['inputs']['sampler_name']), (16, 1, 'euler'))
        self.assertEqual((graph['50']['inputs']['width'], graph['50']['inputs']['height']), CANVASES['portrait'])

    def test_without_expansion_the_upsampler_never_enters_the_graph(self):
        graph, meta = self.build(expand=False)
        for ident in ('23', '24', '25', '26', '27'):
            self.assertNotIn(ident, graph)
        self.assertEqual(graph['40']['inputs']['text'], 'a red fox')
        self.assertEqual((meta['expansion'], meta['expansion_preview_node']), ('off', None))
        self.assertNotIn('TextGenerate', {node['class_type'] for node in graph.values()})
        self.assertEqual(sum(node['class_type'] == 'CLIPLoader' for node in graph.values()), 1)

    def test_every_link_points_at_a_node_that_exists(self):
        for mode in FLUX.modes:
            for expand in (True, False):
                graph, _ = self.build(mode=mode, image1='a.png', image2='b.png', expand=expand)
                for ident, node in graph.items():
                    for value in node['inputs'].values():
                        if isinstance(value, list):
                            self.assertIn(value[0], graph, '%s %s -> %s (expand=%s)' % (mode, ident, value[0], expand))

    def test_single_feeds_one_image_to_the_reference_latent_and_the_upsampler(self):
        graph, meta = self.build(mode='single', image1='source.png', prompt='make it blue')
        self.assertEqual(graph['10']['inputs']['image'], 'source.png')
        self.assertNotIn('11', graph)
        self.assertEqual(graph['43']['inputs']['images.image0'], ['10', 0])
        self.assertNotIn('images.image1', graph['43']['inputs'])
        self.assertEqual(graph['28']['inputs'], {'images.image0': ['10', 0]})
        self.assertEqual(graph['26']['inputs']['image'], ['28', 0])
        self.assertEqual(graph['25']['inputs']['value'], 'make it blue')
        self.assertIn('image-editing model', graph['24']['inputs']['value'])
        self.assertEqual([graph['60']['inputs'][k] for k in ('positive', 'negative', 'latent_image')], [['43', 0], ['43', 1], ['43', 2]])
        self.assertEqual(graph['43']['inputs']['positive'], ['40', 0])
        self.assertEqual(graph['42']['inputs']['conditioning'], ['40', 0])
        self.assertNotIn('50', graph)
        self.assertEqual((meta['mode'], meta['expansion']), ('single', 'upsampler'))

    def test_reference_adds_the_second_image_in_order_and_keeps_the_prompt_as_written(self):
        graph, _ = self.build(mode='reference', image1='source.png', image2='style.png', prompt='use the jacket')
        self.assertEqual((graph['10']['inputs']['image'], graph['11']['inputs']['image']), ('source.png', 'style.png'))
        for ident in ('28', '43'):
            self.assertEqual([graph[ident]['inputs']['images.image0'], graph[ident]['inputs']['images.image1']], [['10', 0], ['11', 0]])
        # Unlike Qwen 2.1, FLUX.3 adds no wrapper text: the upsampler sees both images and the plain request.
        self.assertEqual(graph['25']['inputs']['value'], 'use the jacket')

    def test_edit_modes_without_expansion_drop_the_upsampler_and_the_image_batch(self):
        for mode in ('single', 'reference'):
            graph, meta = self.build(mode=mode, image1='a.png', image2='b.png', expand=False, prompt='<ref_image_0> in blue')
            for ident in ('23', '24', '25', '26', '27', '28'):
                self.assertNotIn(ident, graph, mode)
            self.assertEqual(graph['40']['inputs']['text'], '<ref_image_0> in blue')
            self.assertIn('43', graph)
            self.assertEqual(meta['expansion'], 'off')

    def test_the_two_image_prefix_stays_with_qwen_21_only(self):
        qwen = ENGINES['qwen-image-21-uc-fp8']
        graph, _ = build_workflow(qwen, 'reference', 'swap the jacket', 'a.png', 'b.png', 'low', 1, 1)
        self.assertTrue(graph['40']['inputs']['prompt'].startswith('Edit <image1> as the source image'))
        self.assertTrue(graph['40']['inputs']['prompt'].endswith('swap the jacket'))

    def test_flux_variations_are_edits_of_the_result_but_zimage_has_none(self):
        self.assertEqual(resolve_workflow_mode(FLUX, 'prompt', 'variation'), 'single')
        with self.assertRaises(ValueError):
            resolve_workflow_mode(ZIMAGE, 'prompt', 'variation')

    def test_prefix_autogrow_slots_are_accepted_and_unknown_ones_are_not(self):
        info = object_info(FLUX)
        self.assertEqual(info['BatchImagesNode']['input']['optional']['images'][0], 'COMFY_AUTOGROW_V3')
        self.assertTrue(engine_readiness(FLUX, info)['ready'])
        info['BatchImagesNode']['input']['optional']['images'][1]['template']['max'] = 1  # only image0 exists
        row = engine_readiness(FLUX, info)
        self.assertIn('28: unknown input images.image1', ' '.join(row['schema_errors']))

    def test_every_mode_is_ready_when_nodes_and_files_exist(self):
        row = engine_readiness(FLUX, object_info(FLUX))
        self.assertTrue(row['ready'], row['reasons'])
        self.assertEqual(row['modes'], ['single', 'reference', 'prompt'])
        self.assertIn('редактирование', row['capabilities'])

    def test_the_stored_template_keeps_its_upsampler_nodes(self):
        self.build(expand=False)
        self.assertIn('26', FLUX.template('prompt'))

    def test_expansion_requests_are_validated_per_engine(self):
        self.assertTrue(resolve_expansion(FLUX, {}))
        self.assertFalse(resolve_expansion(FLUX, {'expand': False}))
        self.assertFalse(resolve_expansion(ZIMAGE, {}))
        self.assertFalse(resolve_expansion(ZIMAGE, {'expand': False}))
        with self.assertRaises(ValueError):
            resolve_expansion(ZIMAGE, {'expand': True})
        with self.assertRaises(ValueError):
            resolve_expansion(FLUX, {'expand': 'yes'})

    def test_all_three_modes_are_offered_and_validated(self):
        self.assertEqual(FLUX.modes, ('single', 'reference', 'prompt'))
        for mode in FLUX.modes:
            self.assertEqual(validate_request(FLUX, dict(mode=mode, prompt='x', quality='low'))[0], mode)
        with self.assertRaises(ValueError):
            validate_request(FLUX, dict(mode='outpaint', prompt='x', quality='low'))

    def test_the_expanded_caption_is_read_from_the_preview_node(self):
        _, meta = self.build()
        history = {'outputs': {'27': {'text': ['A detailed caption.']}, '90': {'images': [{'filename': 'a.png'}]}}}
        self.assertEqual(collect_expansion(history, meta), 'A detailed caption.')
        self.assertEqual(collect_expansion({'outputs': {}}, meta), '')
        _, plain = self.build(expand=False)
        self.assertEqual(collect_expansion(history, plain), '')

    def test_readiness_needs_the_upsampler_and_flags_expansion(self):
        # The upsampler file is one of two CLIPLoader files; the card and readiness both list it.
        row = engine_readiness(FLUX, object_info(FLUX))
        self.assertTrue(row['ready'], row['reasons'])
        self.assertTrue(row['expansion'])
        info = object_info(FLUX)
        info['CLIPLoader']['input']['optional']['clip_name'] = [['qwen3vl_4b_int8_convrot.safetensors']]
        missing = engine_readiness(FLUX, info)
        self.assertFalse(missing['ready'])
        self.assertEqual(missing['missing_assets'], ['qwen3.5_9b_flux3_prompt_upsampler_int8_convrot.safetensors'])

    def test_a_nested_dynamic_combo_input_the_server_does_not_declare_is_reported(self):
        info = object_info(FLUX)
        info['TextGenerate']['input']['optional']['sampling_mode'][1]['options'][0]['inputs']['required'].pop('temperature')
        row = engine_readiness(FLUX, info)
        self.assertEqual(row['status'], 'incompatible')
        self.assertIn('sampling_mode.temperature', ' '.join(row['schema_errors']))

    def test_license_row_and_weights_appear_on_the_card(self):
        rows = dict(facts('image', 'flux3-klein-int8'))
        self.assertIn('FLUX Kommunity License', rows['Лицензия'])
        self.assertIn('upsampler', rows['Веса'])


class FluxCatalogTests(unittest.TestCase):
    def test_component_is_pinned_gated_and_ships_the_license(self):
        catalog = json.loads((ROOT / 'App/config/components.json').read_text(encoding='utf-8'))
        component = next(c for c in catalog['components'] if c['id'] == 'image.model.flux3-klein')
        self.assertIn('HF_TOKEN', component['note'])
        self.assertIn('Qualifying User', component['note'])
        paths = {item['path'] for item in component['files']}
        self.assertIn('Models/Image/FLUX3-Klein/LICENSE.md', paths)
        for item in component['files']:
            self.assertRegex(item['sha256'], r'^[0-9a-f]{64}$')
            self.assertIn('/resolve/feef60b0aaf63a4773ace96636ebbcf43b881312/', item['urls'][0])
        self.assertFalse(any('base' in path.lower() for path in paths), 'Base is not offered')


class ZImageWorkflowTests(unittest.TestCase):
    def build(self, **overrides):
        args = dict(mode='prompt', prompt='a red kettle', image1=None, image2=None, quality='low', upscale=1, seed=7, canvas='landscape')
        args.update(overrides)
        return build_workflow(ZIMAGE, args.pop('mode'), args.pop('prompt'), args.pop('image1'), args.pop('image2'),
                              args.pop('quality'), args.pop('upscale'), args.pop('seed'), args.pop('canvas'))

    def test_prompt_size_seed_and_steps_reach_their_nodes(self):
        graph, meta = self.build()
        self.assertEqual(graph['40']['inputs']['text'], 'a red kettle')
        self.assertEqual((graph['50']['inputs']['width'], graph['50']['inputs']['height']), CANVASES['landscape'])
        self.assertEqual((graph['60']['inputs']['seed'], graph['60']['inputs']['steps'], graph['60']['inputs']['cfg']), (7, 8, 1))
        self.assertEqual(meta['engine'], 'z-image-turbo-int8')
        self.assertEqual(meta['result_node_ids'], ['90'])
        self.assertEqual(meta['sampler_parameters']['sampler_name'], 'res_multistep')

    def test_template_is_not_mutated_between_builds(self):
        self.build(prompt='first')
        graph, _ = self.build(prompt='second', seed=9)
        self.assertEqual(graph['40']['inputs']['text'], 'second')
        self.assertEqual(ZIMAGE.template('prompt')['40']['inputs']['text'], '__PROMPT__')

    def test_no_node_carries_an_image_input(self):
        classes = {node['class_type'] for node in ZIMAGE.template('prompt').values()}
        self.assertNotIn('LoadImage', classes)
        self.assertEqual(ZIMAGE.image_nodes, ())

    def test_upscale_rewires_the_save_node_with_the_engine_prefix(self):
        graph, _ = self.build(upscale=4)
        self.assertEqual(graph['90']['inputs']['images'], ['202', 0])
        self.assertEqual(graph['90']['inputs']['filename_prefix'], 'Z_Image_Turbo_Low_x4')

    def test_edit_modes_and_variations_are_rejected(self):
        for mode in ('single', 'reference'):
            with self.assertRaises(ValueError):
                validate_request(ZIMAGE, dict(mode=mode, prompt='x', quality='low'))
            with self.assertRaises(ValueError):
                self.build(mode=mode, image1='a.png', image2='b.png')
        with self.assertRaises(ValueError):
            resolve_workflow_mode(ZIMAGE, 'prompt', 'variation')
        self.assertEqual(resolve_workflow_mode(ZIMAGE, 'prompt', 'generate'), 'prompt')

    def test_only_the_single_preset_is_valid(self):
        validate_request(ZIMAGE, dict(mode='prompt', prompt='x', quality='low'))
        for quality in ('good', 'high'):
            with self.assertRaises(ValueError):
                validate_request(ZIMAGE, dict(mode='prompt', prompt='x', quality=quality))

    def test_outputs_are_read_from_the_save_node(self):
        _, meta = self.build()
        history = {'outputs': {'90': {'images': [{'filename': 'a.png'}]}, '9': {'images': [{'filename': 'other.png'}]}}}
        self.assertEqual(collect_outputs(history, meta), [{'filename': 'a.png'}])


class RequirementsCardTests(unittest.TestCase):
    def test_card_states_the_limits(self):
        rows = dict(facts('image', 'z-image-turbo-int8'))
        self.assertEqual(rows['Возможности'], 'Изображение по тексту')
        self.assertIn('INT8', rows['Веса'])
        self.assertIn('нет редактирования', rows['Особенности'])

    def test_edit_engines_have_no_prompt_only_note(self):
        self.assertNotIn('Особенности', dict(facts('image', 'qwen-edit-2511')))


class GpuClaimTests(unittest.TestCase):
    def test_new_engines_wait_for_memory_below_their_measured_peak_and_qwen_does_not(self):
        # Measured peaks above idle on the 16 GB card: Z-Image about 13.6 GB, FLUX.3 about 13.9-14.7 GB.
        self.assertEqual({ident: engine.claim_vram_mb for ident, engine in ENGINES.items()},
                         {'qwen-edit-2511': 0, 'qwen-image-21-uc-fp8': 0, 'z-image-turbo-int8': 11000, 'flux3-klein-int8': 12000})

    def test_the_start_route_claims_the_engines_estimate_and_counts_comfyuis_own_cache(self):
        # The generate route needs a live ComfyUI to reach its claim, so the wiring is checked in the source.
        source = (ROOT / 'App/server/server.py').read_text(encoding='utf-8')
        self.assertIn("GPU.claim('image', engine.claim_vram_mb)", source)
        self.assertIn('held=dict(image=_comfy_held_mb)', source)

    def test_comfyuis_own_memory_is_read_from_system_stats(self):
        from unittest.mock import patch
        from urllib.error import URLError
        import server
        self.addCleanup(server._COMFY_RETRY_AT.__setitem__, 0, 0)
        server._COMFY_RETRY_AT[0] = 0
        with patch.object(server, 'json_upstream', return_value={'devices': [{'torch_vram_total': 10 * 1024 ** 3}]}) as call:
            self.assertEqual(server._comfy_held_mb(), 10240)
        call.assert_called_once_with('/system_stats', timeout=3)
        with patch.object(server, 'json_upstream', return_value={}):
            self.assertEqual(server._comfy_held_mb(), 0)
        with patch.object(server, 'json_upstream', side_effect=URLError('refused')):
            self.assertEqual(server._comfy_held_mb(), 0, 'a ComfyUI that does not answer holds nothing')


class EngineWordingTests(unittest.TestCase):
    def test_only_prompt_only_engines_say_that_prompt_is_their_only_mode(self):
        # FLUX.3 once told users "только режим Prompt" although it edits in Single and Reference too.
        script = (ROOT / 'App/frontend/app.js').read_text(encoding='utf-8')
        for ident, engine in ENGINES.items():
            block = re.search(r"'%s': \{(.*?)\n  \}" % re.escape(ident), script, re.S)
            if not block:
                continue  # Qwen engines use the generic wording
            with self.subTest(engine=ident):
                self.assertEqual('только режим Prompt' in block.group(1), engine.modes == ('prompt',))
        self.assertIsNotNone(re.search(r"'flux3-klein-int8': \{", script), 'the FLUX.3 wording block moved; update this test')


@unittest.skipUnless(shutil.which('node'), 'node is not installed')
class EnginePolicyTests(unittest.TestCase):
    def test_selection_policy_and_guides(self):
        done = subprocess.run([shutil.which('node'), str(ROOT / 'tests/engine_state_test.js')], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('engine state ok', done.stdout)

    def test_the_page_lists_every_engine_and_registers_its_release(self):
        page = (ROOT / 'App/frontend/index.html').read_text(encoding='utf-8')
        releases = (ROOT / 'App/frontend/studio-ui.js').read_text(encoding='utf-8')
        components = (ROOT / 'App/frontend/components.js').read_text(encoding='utf-8')
        for ident in ENGINES:
            self.assertIn('<option value="%s">' % ident, page)
            self.assertIn("'%s':" % ident, releases)
            self.assertIn("'image:%s':'%s'" % (ident, COMPONENTS['image:' + ident]), components)


if __name__ == '__main__':
    unittest.main()
