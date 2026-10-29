"""Image engine contract: asset closure, workflow wiring and optional Z-Image detail refinement."""
import json
from io import BytesIO
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
from engines.image_preflight import engine_readiness, graph_errors
from engines.image_registry import CANVASES, ENGINES, UPSCALE_MODEL, ZIMAGE_UNION, ZIMAGE_TILE, QWEN_LIGHTNING, QWEN_UNCENSORED
from engines.workflow_builder import build_workflow, collect_expansion, collect_outputs, resolve_expansion, resolve_workflow_mode, validate_request, resolve_detail_strength, resolve_control_settings
from model_requirements import COMPONENTS, facts
from engines.image_inputs import validate_edit_mask, grayscale_png
from engines.flux_inputs import layout_brief, resolve_flux_options, native_dimensions
from engines.qwen_inputs import resolve_qwen_options

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
    profile_assets = [asset for assets in engine.quality_assets.values() for asset in assets]
    if engine.id == 'qwen-edit-2511':
        profile_assets.append(('LoraLoaderModelOnly', 'lora_name', QWEN_UNCENSORED))
        info['LoraLoaderModelOnly'] = {'input': {'optional': {'model':['MODEL'], 'strength_model':['FLOAT']}}}
    for cls, key, filename in (*engine.assets, *profile_assets):
        # Engines can load two files through one loader (two CLIPLoaders): the combo lists all of them.
        files = info[cls]['input']['optional'].get(key)
        files = files if isinstance(files, list) and files and isinstance(files[0], list) else [[]]
        files[0].append(filename if present else 'other.safetensors')
        info[cls]['input']['optional'][key] = files
    if 'UpscaleModelLoader' in info:
        info['UpscaleModelLoader']['input']['optional']['model_name'] = [[UPSCALE_MODEL]]
    if 'ModelPatchLoader' in info:
        info['ModelPatchLoader']['input']['optional']['name'] = [[ZIMAGE_UNION, ZIMAGE_TILE]]
    return info


class CatalogParityTests(unittest.TestCase):
    def test_optional_zimage_patches_are_pinned_and_not_required_by_the_base(self):
        catalog = json.loads((ROOT / 'App/config/component-catalog.json').read_text(encoding='utf-8'))
        components = {row['id']: row for row in catalog['components']}
        base = 'image.model.z-image-turbo'
        for suffix, filename in [('union', ZIMAGE_UNION), ('tile', ZIMAGE_TILE)]:
            ident = 'image.model.z-image-controlnet-' + suffix
            self.assertNotIn(ident, components[base]['requires'])
            self.assertEqual(components[ident]['requires'], ['image.runtime', base])
            item, = components[ident]['files']
            self.assertEqual(item['path'], 'Models/model_patches/' + filename)
            self.assertEqual(item['bytes'], 2_016_627_488)
            self.assertRegex(item['sha256'], r'^[0-9a-f]{64}$')
            self.assertIn('/resolve/5155fc56d17821007d6f62ac192c09e0f0e72016/', item['urls'][0])

    def test_every_engine_has_a_component_that_ships_its_assets(self):
        catalog = json.loads((ROOT / 'App/config/component-catalog.json').read_text(encoding='utf-8'))
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
        catalog = json.loads((ROOT / 'App/config/component-catalog.json').read_text(encoding='utf-8'))
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
        self.assertEqual(row['modes'], ['prompt', 'detail', 'control', 'inpaint', 'tile'])
        self.assertEqual(row['quality_steps'], {'low': 8})
        self.assertEqual(row['cfg'], 1)
        self.assertEqual(row['sampler'], 'res_multistep / simple')
        self.assertIn('изменение по маске', row['capabilities'])
        self.assertEqual(row['mode_presets']['detail']['quality_steps'], {'low': 5})
        self.assertEqual(row['mode_presets']['detail']['sampler'], 'dpmpp_2m_sde / beta')

    def test_optional_detail_dependencies_never_disable_prompt(self):
        for missing in ('UpscaleModelLoader', 'ImageUpscaleWithModel', 'ImageScaleToTotalPixels', 'VAEEncode'):
            info = object_info(ZIMAGE)
            del info[missing]
            row = engine_readiness(ZIMAGE, info)
            self.assertTrue(row['ready'], missing)
            self.assertNotIn('detail', row['modes'], missing)
            self.assertTrue(row['mode_reasons']['detail'], missing)
        self.assertEqual(engine_readiness(ZIMAGE, object_info(ZIMAGE), upscale_enabled=False)['modes'], ['prompt', 'control', 'inpaint'])
        info = object_info(ZIMAGE)
        info['UpscaleModelLoader']['input']['optional']['model_name'] = [['other.pth']]
        self.assertEqual(engine_readiness(ZIMAGE, info)['modes'], ['prompt', 'control', 'inpaint'])

    def test_union_tile_missing_or_disabled_are_independent(self):
        for available, expected in (([], ['prompt', 'detail']), ([ZIMAGE_UNION], ['prompt', 'detail', 'control', 'inpaint']),
                                    ([ZIMAGE_TILE], ['prompt', 'detail', 'tile'])):
            info = object_info(ZIMAGE)
            info['ModelPatchLoader']['input']['optional']['name'] = [available]
            self.assertEqual(engine_readiness(ZIMAGE, info)['modes'], expected)
        row = engine_readiness(ZIMAGE, object_info(ZIMAGE), disabled_components=['image.model.z-image-controlnet-union'])
        self.assertEqual(row['modes'], ['prompt', 'detail', 'tile'])

    def test_qwen_engines_keep_their_reported_settings(self):
        self.assertEqual(engine_readiness(ENGINES['qwen-edit-2511'], object_info(ENGINES['qwen-edit-2511']))['cfg'], 4)
        self.assertEqual(engine_readiness(ENGINES['qwen-image-21-uc-fp8'], object_info(ENGINES['qwen-image-21-uc-fp8']))['cfg'], 1)


class Qwen2511Tests(unittest.TestCase):
    def build(self, **overrides):
        args = dict(mode='single', prompt='make the clock green', image1='source.png', image2=None,
                    quality='low', upscale=1, seed=424242)
        args.update(overrides)
        return build_workflow(ENGINES['qwen-edit-2511'], **args)

    def test_base_and_lightning_have_distinct_sampler_and_lora_paths(self):
        standard, _ = self.build()
        fast, meta = self.build(quality='fast')
        self.assertEqual((standard['169']['inputs']['steps'], standard['169']['inputs']['cfg']), (40, 4))
        self.assertNotIn('170', standard)
        self.assertNotIn('171', standard)
        self.assertEqual((fast['169']['inputs']['steps'], fast['169']['inputs']['cfg']), (4, 1))
        self.assertEqual(fast['145']['inputs']['model'], ['171',0])
        self.assertEqual(fast['171']['inputs']['lora_name'], QWEN_LIGHTNING)
        self.assertFalse(meta['use_uncensored_lora'])

    def test_community_lora_is_explicit_and_can_be_combined_with_lightning(self):
        graph, meta = self.build(quality='fast', use_uncensored_lora=True)
        self.assertEqual(graph['171']['inputs']['model'], ['170',0])
        self.assertEqual(graph['170']['inputs']['lora_name'], QWEN_UNCENSORED)
        self.assertTrue(meta['use_uncensored_lora'])

    def test_three_images_reach_both_conditioning_branches_in_order(self):
        engine = ENGINES['qwen-edit-2511']
        graph, meta = self.build(mode='multi', image2='pattern.png', extra_images=['cactus.png'])
        self.assertEqual(graph['84']['inputs']['image'], 'cactus.png')
        for node in ('151','149'):
            self.assertEqual(graph[node]['inputs']['image1'], ['160',0])
            self.assertEqual(graph[node]['inputs']['image2'], ['83',0])
            self.assertEqual(graph[node]['inputs']['image3'], ['84',0])
        self.assertEqual(meta['reference_count'],3)
        self.assertEqual(graph_errors(graph,object_info(engine)),[])
        for extras in ([], ['a.png','b.png']):
            with self.assertRaises(ValueError):
                resolve_flux_options(engine,'multi',{'extra_images':extras})

    def test_negative_prompt_is_persisted_and_never_ignored_by_lightning(self):
        graph, meta = self.build(negative_prompt='duplicate clocks, blur')
        self.assertEqual(graph['149']['inputs']['prompt'], 'duplicate clocks, blur')
        self.assertEqual(meta['negative_prompt'], 'duplicate clocks, blur')
        with self.assertRaises(ValueError): self.build(quality='fast',negative_prompt='blur')
        for data in ({'negative_prompt':[]},{'negative_prompt':'x'*2001},{'use_uncensored_lora':1}):
            with self.assertRaises(ValueError): resolve_qwen_options(ENGINES['qwen-edit-2511'],data)
        with self.assertRaises(ValueError): resolve_qwen_options(FLUX,{'negative_prompt':'blur'})

    def test_missing_or_disabled_addons_do_not_disable_standard_editing(self):
        engine = ENGINES['qwen-edit-2511']
        for present, disabled in ((False,[]),(True,['image.model.qwen-edit-lightning','image.model.qwen-edit-uncensored'])):
            info = object_info(engine)
            if not present: del info['LoraLoaderModelOnly']
            row = engine_readiness(engine,info,disabled_components=disabled)
            self.assertTrue(row['ready'],row['reasons'])
            self.assertEqual(row['quality_steps'],{'low':40})
            self.assertFalse(row['community_lora_available'])
        row = engine_readiness(engine,object_info(engine))
        self.assertEqual(row['quality_cfg'],{'low':4,'fast':1})
        self.assertTrue(row['community_lora_available'])

    def test_catalog_addons_are_optional_and_independently_pinned(self):
        rows = {row['id']:row for row in json.loads((ROOT/'App/config/component-catalog.json').read_text(encoding='utf-8'))['components']}
        base=rows['image.model.qwen-edit-2511']
        self.assertEqual(len(base['files']),3)
        for suffix, filename in (('lightning',QWEN_LIGHTNING),('uncensored',QWEN_UNCENSORED)):
            key='image.model.qwen-edit-'+suffix
            self.assertNotIn(key,base['requires'])
            self.assertIn(base['id'],rows[key]['requires'])
            self.assertEqual(Path(rows[key]['files'][0]['path']).name,filename)
        self.assertEqual(rows['image.model.qwen-edit-lightning']['files'][0]['bytes'],849608296)

    def test_editor_normalizes_by_area_without_the_panorama_cropping_buckets(self):
        for mode in ENGINES['qwen-edit-2511'].modes:
            graph = ENGINES['qwen-edit-2511'].template(mode)
            self.assertEqual(graph['160']['class_type'],'ImageScaleToTotalPixels')
            self.assertEqual(graph['160']['inputs']['resolution_steps'],8)
            self.assertEqual(graph['160']['inputs']['megapixels'],1.0)

    def test_an_older_two_image_encoder_keeps_existing_modes_without_claiming_three_inputs(self):
        engine=ENGINES['qwen-edit-2511']
        info=object_info(engine)
        info['TextEncodeQwenImageEditPlus']['input']['optional'].pop('image3')
        row=engine_readiness(engine,info)
        self.assertTrue(row['ready'])
        self.assertEqual(row['modes'],['single','reference','prompt'])
        self.assertNotIn('1–3',row['capabilities'])


class Qwen21Tests(unittest.TestCase):
    def build(self, mode='prompt', **kwargs):
        return build_workflow(ENGINES['qwen-image-21-uc-fp8'], mode, 'keep the subject', 'source.png',
                              'reference.png', 'low', 1, 42, **kwargs)

    def test_rgba_is_rejoined_before_every_reference(self):
        for mode, count in [('single',1), ('reference',2)]:
            graph, _ = self.build(mode)
            for slot in range(1, count + 1):
                joined = str(30 + slot)
                self.assertEqual(graph['40']['inputs']['images.image_' + str(slot)], [joined,0])
                self.assertEqual(graph[joined]['class_type'], 'JoinImageWithAlpha')
                self.assertEqual(graph[joined]['inputs']['alpha'], [str(10 + slot),1])

    def test_ten_ordered_references_retain_alpha_and_actual_count(self):
        extras = ['ref%d.png' % slot for slot in range(3,11)]
        graph, meta = self.build('multi', extra_images=extras)
        self.assertEqual(meta['reference_count'],10)
        self.assertEqual(graph['117']['inputs']['image'],'ref10.png')
        self.assertEqual(graph['317']['inputs']['alpha'],['117',1])
        self.assertEqual(graph['40']['inputs']['images.image_10'],['317',0])
        self.assertEqual(graph_errors(graph,object_info(ENGINES['qwen-image-21-uc-fp8'])),[])
        with self.assertRaises(ValueError): self.build('multi',extra_images=extras+['overflow.png'])

    def test_native_two_k_changes_the_edit_encoder_and_prompt_canvas(self):
        for mode in ('prompt','single','reference','multi'):
            graph, meta = self.build(mode,native_megapixels=4,**({'extra_images':['third.png']} if mode=='multi' else {}))
            self.assertEqual(graph['40']['inputs']['resolution'],2048)
            self.assertEqual(meta['native_megapixels'],4)
            if mode=='prompt':
                self.assertEqual((graph['50']['inputs']['width'],graph['50']['inputs']['height']),(2048,2048))
        for invalid in (True,2,16,'4'):
            with self.assertRaises(ValueError): resolve_flux_options(ENGINES['qwen-image-21-uc-fp8'],'single',{'native_megapixels':invalid})

    def test_negative_prompts_are_rejected_at_fixed_cfg_one(self):
        with self.assertRaises(ValueError): resolve_qwen_options(ENGINES['qwen-image-21-uc-fp8'],{'negative_prompt':'blur'})

    def test_optional_expanders_are_distinct_and_absent_when_off(self):
        engine = ENGINES['qwen-image-21-uc-fp8']
        graph, meta = self.build()
        self.assertNotIn('26',graph)
        self.assertEqual(meta['expansion'],'off')
        for mode, task in [('prompt','t2i'),('reference','i2i')]:
            graph, meta = self.build(mode,expand=True)
            self.assertIn('_pe_'+task+'.',graph['23']['inputs']['clip_name'])
            self.assertEqual(graph['40']['inputs']['prompt'],['26',0])
            self.assertEqual(meta['expansion_preview_node'],'27')
            self.assertEqual(meta['expansion_model'],engine.expansion_assets[mode])
            self.assertEqual('28' in graph, mode!='prompt')
        graph, _ = self.build('multi',extra_images=['a.png','b.png'],expand=True)
        self.assertEqual(list(graph['28']['inputs'].values()),[['11',0],['12',0],['110',0],['111',0]])

    def test_optional_expansion_missing_or_disabled_does_not_disable_base(self):
        engine = ENGINES['qwen-image-21-uc-fp8']
        info = object_info(engine)
        row = engine_readiness(engine,info)
        self.assertTrue(row['ready'])
        self.assertEqual(row['expansion_available_modes'],[])
        extra = object_info(FLUX)
        for cls in ('TextGenerate','PreviewAny','BatchImagesNode'): info[cls]=extra[cls]
        info['CLIPLoader']['input']['optional']['clip_name'][0].extend(set(engine.expansion_assets.values()))
        row = engine_readiness(engine,info)
        self.assertTrue(row['ready'])
        self.assertEqual(row['expansion_available_modes'],list(engine.modes))
        row = engine_readiness(engine,info,disabled_components=['image.model.qwen21-pe-i2i'])
        self.assertTrue(row['ready'])
        self.assertEqual(row['expansion_available_modes'],['prompt'])

    def test_explicit_transparency_bypasses_the_checkerboard_rewriter(self):
        engine = ENGINES['qwen-image-21-uc-fp8']
        for prompt in ('an RGBA clock','keep the alpha channel','a transparent PNG','удали фон, нужен прозрачный PNG'):
            with self.assertRaises(ValueError): resolve_expansion(engine,{'expand':True,'prompt':prompt})
            self.assertFalse(resolve_expansion(engine,{'expand':False,'prompt':prompt}))
        for prompt in ('a transparent glass cup on a wooden table','a poster with the exact title ALPHA'):
            self.assertTrue(resolve_expansion(engine,{'expand':True,'prompt':prompt}))


class FluxWorkflowTests(unittest.TestCase):
    def build(self, **overrides):
        args = dict(mode='prompt', prompt='a red fox', image1=None, image2=None, quality='low', upscale=1, seed=5, canvas='portrait', expand=None)
        args.update(overrides)
        if args['mode'] == 'multi':
            args.setdefault('extra_images', ['third.png'])
        if args['mode'] == 'layout':
            args.setdefault('layout_boxes', [dict(id='obj_1', desc='a red sphere', bbox=[100,100,450,450])])
        return build_workflow(FLUX, args.pop('mode'), args.pop('prompt'), args.pop('image1'), args.pop('image2'), args.pop('quality'),
                              args.pop('upscale'), args.pop('seed'), args.pop('canvas'), args.pop('expand'), **args)

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
        self.assertEqual(row['modes'], ['single', 'reference', 'prompt', 'multi', 'layout'])
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

    def test_all_flux_modes_are_offered_and_validated(self):
        self.assertEqual(FLUX.modes, ('single', 'reference', 'prompt', 'multi', 'layout'))
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

    def test_all_ten_references_reach_both_the_image_model_and_the_upsampler_in_order(self):
        extra = ['ref%d.png' % index for index in range(3, 11)]
        for expand in (True, False):
            graph, meta = self.build(mode='multi', image1='a.png', image2='b.png', extra_images=extra, expand=expand)
            self.assertEqual(meta['reference_count'], 10)
            self.assertNotIn('12', graph)
            for index, filename in enumerate(extra):
                node = str(110 + index)
                self.assertEqual(graph[node]['inputs']['image'], filename)
                self.assertEqual(graph['43']['inputs']['images.image' + str(index + 2)], [node, 0])
                if expand:
                    self.assertEqual(graph['28']['inputs']['images.image' + str(index + 2)], [node, 0])
            self.assertEqual('28' in graph, expand)

    def test_layout_survives_expansion_and_direct_caption_paths(self):
        for expand in (True, False):
            graph, meta = self.build(mode='layout', expand=expand)
            self.assertEqual(graph['40']['inputs']['text'], ['30', 0])
            self.assertIn('"id": "obj_1"', graph['29']['inputs']['value'])
            self.assertIn('"bbox": [100, 100, 450, 450]', graph['29']['inputs']['value'])
            if expand:
                self.assertEqual(graph['30']['inputs']['string_a'], ['26',0])
                caption_input = graph[FLUX.prompt_node]['inputs'][FLUX.prompt_key]
            else:
                caption_input = graph['30']['inputs']['string_a']
            self.assertIn('<obj_1> in the upper left', caption_input)
            if expand:
                self.assertEqual(graph['27']['inputs']['source'], ['30',0])
            self.assertEqual(meta['layout_boxes'][0]['desc'], 'a red sphere')

    def test_swapping_boxes_updates_caption_locations_without_changing_identities(self):
        rows = [dict(id='red', desc='red sphere', bbox=[100,100,400,400]),
                dict(id='blue', desc='blue cube', bbox=[600,600,900,900])]
        self.assertIn('red sphere <red> in the upper left', layout_brief(rows))
        rows[0]['bbox'], rows[1]['bbox'] = rows[1]['bbox'], rows[0]['bbox']
        brief = layout_brief(rows)
        self.assertIn('red sphere <red> in the lower right', brief)
        self.assertIn('blue cube <blue> in the upper left', brief)

    def test_native_size_changes_the_latent_before_sampling_without_an_upscaler(self):
        graph, meta = self.build(native_megapixels=4, expand=False)
        self.assertEqual((graph['50']['inputs']['width'], graph['50']['inputs']['height']), (1536,2304))
        self.assertNotIn('200', graph)
        self.assertEqual(meta['native_megapixels'], 4)
        self.assertEqual(native_dimensions((1024,1024), 4), (2048,2048))

    def test_fourk_is_bounded_and_requires_its_measured_memory_profile(self):
        self.assertEqual(native_dimensions((1024,1024), 16), (4096,4096))
        self.assertEqual(native_dimensions((1152,768), 16), (4096,2736))
        self.assertEqual(FLUX.required_vram('prompt', 16), 15000)
        self.assertEqual(FLUX.required_vram('layout', 16), 15000)
        self.assertEqual(FLUX.required_vram('prompt', 4), 12000)
        with self.assertRaises(ValueError):
            resolve_flux_options(FLUX, 'prompt', dict(native_megapixels=16, upscale=2))

    def test_missing_layout_node_does_not_disable_existing_flux_modes(self):
        info = object_info(FLUX)
        info.pop('StringConcatenate')
        readiness = engine_readiness(FLUX, info)
        self.assertTrue(readiness['ready'])
        self.assertEqual(readiness['modes'], ['single','reference','prompt','multi'])
        self.assertTrue(readiness['mode_reasons']['layout'])

    def test_edit_system_instructions_are_not_json_escaped_again(self):
        for mode in ('single','reference','multi'):
            instruction = FLUX.template(mode)['24']['inputs']['value']
            self.assertIn('\n\nPick the voice', instruction)
            self.assertIn('"Remove the ..."', instruction)


class FluxInputTests(unittest.TestCase):
    def test_invalid_ignored_or_excessive_controls_are_rejected(self):
        for engine, mode, data in [
            (ZIMAGE,'prompt',{'native_megapixels':4}),
            (FLUX,'single',{'native_megapixels':4}),
            (FLUX,'prompt',{'native_megapixels':True}),
            (FLUX,'prompt',{'native_megapixels':4.0}),
            (FLUX,'prompt',{'extra_images':['x']}),
            (FLUX,'multi',{'extra_images':[]}),
            (FLUX,'multi',{'extra_images':['x']*9}),
            (FLUX,'single',{'layout_boxes':[]}),
        ]:
            with self.subTest(mode=mode, data=data), self.assertRaises(ValueError):
                resolve_flux_options(engine, mode, data)

    def test_layout_rejects_invalid_dimensions_ids_and_descriptions(self):
        good = dict(id='obj_1', desc='a cube', bbox=[10,20,600,700])
        for changes in (dict(bbox=[True,20,600,700]), dict(bbox=[10,20,10,700]),
                        dict(bbox=[-1,20,600,700]), dict(bbox=[10,20,600,1001]),
                        dict(desc=''), dict(desc='x'*601), dict(id='</text>')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                resolve_flux_options(FLUX, 'layout', {'layout_boxes':[{**good, **changes}]})
        with self.assertRaises(ValueError):
            resolve_flux_options(FLUX, 'layout', {'layout_boxes':[good, good]})
        self.assertEqual(resolve_flux_options(FLUX, 'layout', {'layout_boxes':[good]})[1], [good])

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
        catalog = json.loads((ROOT / 'App/config/component-catalog.json').read_text(encoding='utf-8'))
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
        self.assertEqual(ZIMAGE.image_nodes, ('10', '12'))

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

    def test_detail_encodes_upscaled_source_before_sampling_without_cycles(self):
        graph, meta = self.build(mode='detail', image1='source.png', upscale=2)
        self.assertEqual(graph['10']['inputs']['image'], 'source.png')
        self.assertEqual(graph['11']['inputs']['megapixels'], 1)
        self.assertEqual(graph['50']['inputs']['pixels'], ['201', 0])
        self.assertEqual(graph['201']['inputs']['image'], ['11', 0])
        self.assertEqual(graph['90']['inputs']['images'], ['70', 0])
        self.assertEqual(meta['sampler_parameters']['steps'], 5)
        self.assertEqual(meta['detail_strength'], 0.33)
        self.assertEqual(graph_errors(graph, object_info(ZIMAGE)), [])
        def walk(ident, ancestors):
            self.assertNotIn(ident, ancestors, 'workflow dependency cycle')
            for value in graph[ident]['inputs'].values():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    walk(value[0], ancestors | {ident})
        walk('90', set())

    def test_detail_strength_is_bounded_and_cannot_be_silently_ignored(self):
        for value in (0.2, 0.33):
            strength = resolve_detail_strength('detail', {'detail_strength': value})
            graph, meta = build_workflow(ZIMAGE, 'detail', 'scene', 'source.png', None, 'low', 2, 7, detail_strength=strength)
            self.assertEqual(graph['60']['inputs']['denoise'], value)
            self.assertEqual(meta['detail_strength'], value)
        for value in (True, '0.2', 0, 0.36, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                resolve_detail_strength('detail', {'detail_strength': value})
        with self.assertRaises(ValueError):
            resolve_detail_strength('prompt', {'detail_strength': 0.2})
        for upscale in (1, 4):
            with self.assertRaises(ValueError):
                validate_request(ZIMAGE, dict(mode='detail', prompt='scene', upscale=upscale))
        with self.assertRaises(ValueError):
            resolve_workflow_mode(ZIMAGE, 'detail', 'variation')


class GuidedImageTests(unittest.TestCase):
    def build(self, mode, **settings):
        upscale = 2 if mode == 'tile' else 1
        return build_workflow(ZIMAGE, mode, 'a green clock', 'source.png', 'mask.png', 'low', upscale, 42, **settings)

    def test_each_optional_graph_is_valid_and_has_no_dependency_cycle(self):
        for mode in ZIMAGE.optional_modes:
            graph, meta = (build_workflow(ZIMAGE, mode, 'scene', 'source.png', 'mask.png', 'low', 2, 42)
                           if mode == 'detail' else self.build(mode))
            self.assertEqual(graph_errors(graph, object_info(ZIMAGE)), [], mode)
            def walk(ident, parents):
                self.assertNotIn(ident, parents, mode)
                for value in graph[ident]['inputs'].values():
                    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                        walk(value[0], parents | {ident})
            walk('90', set())

    def test_guidance_and_map_choices_reach_the_graph(self):
        canny, _ = self.build('control', control_type='canny', control_strength=0.65)
        prepared, meta = self.build('control', control_type='prepared', control_strength=1)
        self.assertEqual(canny['26']['inputs']['image'], ['15', 0])
        self.assertEqual(canny['26']['inputs']['strength'], 0.65)
        self.assertNotIn('15', prepared)
        self.assertEqual(prepared['26']['inputs']['image'], ['11', 0])
        self.assertEqual(meta['control_strength'], 1)
        for mode, patch in (('control', ZIMAGE_UNION), ('inpaint', ZIMAGE_UNION), ('tile', ZIMAGE_TILE)):
            graph, _ = self.build(mode)
            self.assertEqual(graph['24']['inputs']['name'], patch)
        for value in (0, True, '1', 1.01, float('nan')):
            with self.assertRaises(ValueError):
                resolve_control_settings('control', {'control_strength': value})
        with self.assertRaises(ValueError):
            resolve_control_settings('prompt', {'control_strength': 0.85})
        with self.assertRaises(ValueError):
            resolve_control_settings('control', {'control_type': 'auto-pose'})

    def test_inpaint_composites_only_the_soft_mask_onto_original_resolution(self):
        graph, _ = self.build('inpaint')
        self.assertEqual(graph['26']['inputs']['mask'], ['14', 0])
        self.assertEqual(graph['91']['inputs']['mask'], ['13', 0])
        self.assertEqual(graph['91']['inputs']['destination'], ['10', 0])
        self.assertTrue(graph['91']['inputs']['resize_source'])
        self.assertEqual(graph['90']['inputs']['images'], ['91', 0])
        with self.assertRaises(ValueError):
            validate_request(ZIMAGE, dict(mode='inpaint', prompt='x', upscale=2))

    @staticmethod
    def png(color, size=(32, 32), mode='RGB'):
        from PIL import Image
        output = BytesIO()
        Image.new(mode, size, color).save(output, format='PNG')
        return output.getvalue()

    def test_empty_colored_transparent_or_misaligned_masks_are_rejected(self):
        source = self.png('blue')
        for mask in (self.png('black'), self.png('red'), self.png((255, 255, 255, 0), mode='RGBA'),
                     self.png('white', (16, 32)), b'not a PNG'):
            with self.assertRaises(ValueError):
                validate_edit_mask(source, mask)

    def test_inward_feather_never_modifies_keep_pixels_or_erases_a_thin_selection(self):
        from PIL import Image, ImageDraw
        for box in ((8, 8, 23, 23), (15, 4, 15, 27)):
            mask = Image.new('RGB', (32, 32), 'black')
            ImageDraw.Draw(mask).rectangle(box, fill='white')
            output = BytesIO()
            mask.save(output, format='PNG')
            prepared = Image.open(BytesIO(validate_edit_mask(self.png('blue'), output.getvalue()))).convert('L')
            self.assertGreater(prepared.getextrema()[1], 127)
            for original, value in zip(mask.convert('L').getdata(), prepared.getdata()):
                if original == 0:
                    self.assertEqual(value, 0)
        gray = Image.open(BytesIO(grayscale_png(self.png('red'))))
        self.assertEqual(len(set(gray.getpixel((0, 0)))), 1)


class RequirementsCardTests(unittest.TestCase):
    def test_card_states_the_limits(self):
        rows = dict(facts('image', 'z-image-turbo-int8'))
        self.assertIn('Изображение по тексту', rows['Возможности'])
        self.assertIn('Детализация', rows['Возможности'])
        self.assertIn('INT8', rows['Веса'])
        self.assertIn('Без Single и Reference', rows['Особенности'])

    def test_edit_engines_have_no_prompt_only_note(self):
        self.assertNotIn('Особенности', dict(facts('image', 'qwen-edit-2511')))


class GpuClaimTests(unittest.TestCase):
    def test_new_engines_wait_for_memory_below_their_measured_peak_and_qwen_does_not(self):
        # Measured peaks above idle on the 16 GB card: Z-Image about 13.6 GB, FLUX.3 about 13.9-14.7 GB.
        self.assertEqual({ident: engine.claim_vram_mb for ident, engine in ENGINES.items()},
                         {'qwen-edit-2511': 0, 'qwen-image-21-uc-fp8': 0, 'z-image-turbo-int8': 11000, 'flux3-klein-int8': 12000})

    def test_the_start_route_claims_the_engines_estimate_and_counts_comfyuis_own_cache(self):
        # The generate route needs a live ComfyUI to reach its claim, so the wiring is checked in the source.
        source = (ROOT / 'App/server/studio_server.py').read_text(encoding='utf-8')
        self.assertIn('claim_vram_mb = engine.required_vram(mode, native_megapixels)', source)
        self.assertIn("GPU.claim('image', claim_vram_mb)", source)
        self.assertIn('held=dict(image=_comfy_held_mb)', source)

    def test_comfyuis_own_memory_is_read_from_system_stats(self):
        from unittest.mock import patch
        from urllib.error import URLError
        import studio_server as server
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
        script = (ROOT / 'App/frontend/image-studio.js').read_text(encoding='utf-8')
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
        page = (ROOT / 'App/frontend/studio-index.html').read_text(encoding='utf-8')
        releases = (ROOT / 'App/frontend/studio-ui.js').read_text(encoding='utf-8')
        components = (ROOT / 'App/frontend/component-manager.js').read_text(encoding='utf-8')
        for ident in ENGINES:
            self.assertIn('<option value="%s">' % ident, page)
            self.assertIn("'%s':" % ident, releases)
            self.assertIn("'image:%s':'%s'" % (ident, COMPONENTS['image:' + ident]), components)


if __name__ == '__main__':
    unittest.main()
