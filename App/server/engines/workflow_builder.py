"""Build independent API prompts without changing the stored templates."""
from ui_strings import source as ui_text
import hashlib
import json
from .image_registry import CANVASES, UPSCALE_MODEL
from .flux_inputs import layout_brief, native_dimensions, resolve_flux_options
from .qwen_inputs import resolve_qwen_options
from .image_registry import QWEN_LIGHTNING, QWEN_UNCENSORED


def validate_request(engine, data):
    mode = data.get('mode', 'single')
    if not isinstance(mode, str) or mode not in engine.modes:
        raise ValueError(ui_text('image.validate_mode_not_supported_engine'))
    quality = data.get('quality', 'low')
    if not isinstance(quality, str) or quality not in engine.quality_steps:
        message = ui_text('image.qwen_quality_selection_error') if engine.id == 'qwen-edit-2511' else ui_text('image.validate_quality_must_low_good_high')
        raise ValueError(message)
    upscale = data.get('upscale', 2 if mode in engine.upscale_modes else 1)
    if type(upscale) is not int or upscale not in (1, 2, 4):
        raise ValueError(ui_text('image.validate_upscale_must_off_2_x_4'))
    if mode in engine.upscale_modes and upscale != 2:
        raise ValueError(ui_text('image.detail_requires_twofold_output'))
    if mode == 'inpaint' and upscale != 1:
        raise ValueError(ui_text('image.inpaint_preserves_source_resolution'))
    prompt = data.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 5000:
        raise ValueError(ui_text('image.validate_prompt_must_contain_1_5000_characters'))
    seed = data.get('seed')
    if seed is not None and (type(seed) is not int or not 0 <= seed < (1 << 48)):
        raise ValueError(ui_text('image.validate_seed_must_integer_0_2_48'))
    canvas = data.get('canvas', 'square')
    if mode in engine.text_modes and (not isinstance(canvas, str) or canvas not in CANVASES):
        raise ValueError(ui_text('image.validate_unknown_canvas_format'))
    return mode, quality, upscale, prompt.strip(), seed, canvas


def resolve_detail_strength(mode, data):
    value = data.get('detail_strength')
    if mode != 'detail':
        if value is not None:
            raise ValueError(ui_text('image.detail_strength_only_detail_mode'))
        return None
    value = 0.33 if value is None else value
    if type(value) not in (int, float) or not 0.15 <= value <= 0.35:
        raise ValueError(ui_text('image.detail_strength_supported_range'))
    return float(value)


def resolve_control_settings(mode, data):
    control_type = data.get('control_type')
    strength = data.get('control_strength')
    if mode not in ('control', 'inpaint', 'tile'):
        if control_type is not None or strength is not None:
            raise ValueError(ui_text('image.control_settings_wrong_mode'))
        return None, None
    if mode == 'control':
        control_type = 'canny' if control_type is None else control_type
        if not isinstance(control_type, str) or control_type not in ('canny', 'gray', 'prepared'):
            raise ValueError(ui_text('image.control_unknown_map_type'))
    elif control_type is not None:
        raise ValueError(ui_text('image.control_settings_wrong_mode'))
    strength = (1.0 if mode == 'inpaint' else 0.85) if strength is None else strength
    if type(strength) not in (int, float) or not 0.65 <= strength <= 1.0:
        raise ValueError(ui_text('image.control_strength_supported_range'))
    return control_type, float(strength)


def resolve_expansion(engine, data):
    """Whether the prompt goes through the engine's upsampler; engines without one refuse a request for it."""
    value = data.get('expand')
    if value is None:
        return bool(engine.expand_nodes)
    if type(value) is not bool:
        raise ValueError(ui_text('image.resolve_expansion_prompt_yes_no'))
    if value and not (engine.expand_nodes or engine.expansion_assets):
        raise ValueError(ui_text('image.resolve_expansion_model_does_not_expand_prompts'))
    if value and engine.expansion_assets:
        from .qwen21_expansion import requires_transparency
        # Two real fixed-seed probes painted a checkerboard; a system-prompt warning did not fix it.
        if requires_transparency(data.get('prompt','')):
            raise ValueError(ui_text('image.qwen21_expansion_alpha_unavailable'))
    return value


def resolve_workflow_mode(engine, selected_mode, operation='generate'):
    if not isinstance(operation, str) or operation not in ('generate', 'variation'):
        raise ValueError(ui_text('image.resolve_workflow_mode_unknown_generation_operation'))
    if operation == 'variation' and 'single' not in engine.modes:
        raise ValueError(ui_text('image.resolve_workflow_mode_operation_not_supported_engine'))
    workflow_mode = 'single' if operation == 'variation' and selected_mode in engine.text_modes else selected_mode
    if workflow_mode not in engine.modes:
        raise ValueError(ui_text('image.resolve_workflow_mode_operation_not_supported_engine'))
    return workflow_mode


def build_workflow(engine, mode, prompt, image1, image2, quality, upscale, seed, canvas='square', expand=None, detail_strength=None,
                   control_type=None, control_strength=None, extra_images=None, layout_boxes=None, native_megapixels=1,
                   negative_prompt=None, use_uncensored_lora=None):
    if mode not in engine.modes:
        raise ValueError(ui_text('image.validate_mode_not_supported_engine'))
    expand = bool(engine.expand_nodes) if expand is None else expand
    if engine.id == 'flux3-klein-int8':
        settings = dict(native_megapixels=native_megapixels, upscale=upscale)
        if mode == 'multi': settings['extra_images'] = extra_images
        if mode == 'layout': settings['layout_boxes'] = layout_boxes
        _, layout_boxes, native_megapixels = resolve_flux_options(engine, mode, settings)
    graph = engine.template(mode)
    negative_prompt, use_uncensored_lora = resolve_qwen_options(engine, dict(
        negative_prompt=negative_prompt, use_uncensored_lora=use_uncensored_lora, quality=quality))
    if engine.id == 'qwen-edit-2511':
        graph['149']['inputs']['prompt'] = negative_prompt
        model = ['161', 0]
        if use_uncensored_lora:
            graph['170'] = {'class_type':'LoraLoaderModelOnly', 'inputs':{'model':model, 'lora_name':QWEN_UNCENSORED, 'strength_model':1.0}}
            model = ['170', 0]
        if quality == 'fast':
            graph['171'] = {'class_type':'LoraLoaderModelOnly', 'inputs':{'model':model, 'lora_name':QWEN_LIGHTNING, 'strength_model':1.0}}
            model = ['171', 0]
            graph[engine.sampler_node]['inputs']['cfg'] = 1.0
        graph['145']['inputs']['model'] = model
    if mode not in engine.text_modes or not engine.native_prompt:
        graph[engine.image_nodes[0]]['inputs']['image'] = image1
    if mode in ('reference', 'inpaint', 'multi'):
        graph[engine.image_nodes[1]]['inputs']['image'] = image2
    if mode == 'multi' and engine.id == 'qwen-edit-2511':
        if not isinstance(extra_images, list) or len(extra_images) != 1:
            raise ValueError(ui_text('image.extra_images_count_error', 1))
        graph['84']['inputs']['image'] = extra_images[0]
    elif mode == 'multi' and engine.id == 'qwen-image-21-uc-fp8':
        from .reference_inputs import resolve_extra_images
        resolve_extra_images(engine, mode, {'extra_images':extra_images})
        for index, filename in enumerate(extra_images):
            node, joined = str(110 + index), str(310 + index)
            graph[node] = {'class_type':'LoadImage', 'inputs':{'image':filename}}
            # LoadImage separates alpha from RGB; rejoin it before the RGBA reference VAE.
            graph[joined] = {'class_type':'JoinImageWithAlpha', 'inputs':{'image':[node, 0], 'alpha':[node, 1]}}
            graph['40']['inputs']['images.image_' + str(index + 3)] = [joined, 0]
    elif mode == 'multi':
        for index, filename in enumerate(extra_images or []):
            node = str(110 + index)
            graph[node] = {'class_type':'LoadImage', 'inputs':{'image':filename}}
            for target in ('28', '43'):
                graph[target]['inputs']['images.image' + str(index + 2)] = [node, 0]
        graph.pop('12')
    layout_names = layout_brief(layout_boxes) if mode == 'layout' else ''
    if mode == 'layout':
        # Plain caption expansion invented positions that contradicted swapped boxes and duplicated objects.
        # Give the layout captioner their actual positions and preserve the validated table outside generated text.
        layout_text = json.dumps(layout_boxes, ensure_ascii=False)
        graph['29']['inputs']['value'] = layout_text
        if expand:
            prompt += '\nFixed layout plan: ' + layout_names + '\nValidated element table: ' + layout_text
    if mode == 'prompt' and not engine.native_prompt:
        prompt = ('Create a fully new, original image matching this description. '
                  'The input is only a completely blank neutral canvas; replace it entirely. '
                  'Do not preserve the blank background. The complete requested scene is: ' + prompt)
    if mode == 'reference' and engine.reference_prefix:
        prompt = engine.reference_prefix + prompt
    if engine.expand_nodes and not expand:
        # Without expansion the upsampler is not loaded at all: its nodes leave the graph and the caption is the user's text.
        # Edit templates carry one more upsampler node (the image batch) than the prompt template, so absent ids are skipped.
        for ident in (*engine.expand_nodes, engine.expansion_preview):
            graph.pop(ident, None)
        graph[engine.encode_node]['inputs']['text'] = prompt
        if mode == 'layout':
            graph['30']['inputs']['string_a'] = prompt + ' ' + layout_names
            graph[engine.encode_node]['inputs']['text'] = ['30', 0]
    else:
        graph[engine.prompt_node]['inputs'][engine.prompt_key] = prompt
    if expand and engine.expansion_assets:
        resolve_expansion(engine, {'expand':True, 'prompt':prompt})
        from .qwen21_expansion import add_expansion
        add_expansion(graph, engine, mode, prompt, seed)
    if engine.native_prompt and mode in engine.text_modes:
        width, height = native_dimensions(CANVASES[canvas], native_megapixels)
        graph[engine.size_node]['inputs'].update(width=width, height=height)
    if engine.id == 'qwen-image-21-uc-fp8':
        if type(native_megapixels) is not int or native_megapixels not in engine.native_megapixels:
            raise ValueError(ui_text('image.qwen21_resolution_error'))
        # Reference encoding returns the latent on image_1's resized aspect ratio.
        # Raising only the prompt canvas would leave edit modes silently at 1 MP.
        graph['40']['inputs']['resolution'] = 2048 if native_megapixels == 4 else 1024
    sampler = graph[engine.sampler_node]['inputs']
    sampler.update(seed=seed, steps=engine.steps(mode, quality))
    if mode == 'detail':
        if upscale != 2:
            raise ValueError(ui_text('image.detail_requires_twofold_output'))
        sampler['denoise'] = resolve_detail_strength(mode, {'detail_strength': detail_strength})
    control_type, control_strength = resolve_control_settings(mode, {'control_type': control_type, 'control_strength': control_strength})
    if control_strength is not None:
        graph['26']['inputs']['strength'] = control_strength
        if mode == 'control' and control_type != 'canny':
            graph.pop('15')
            graph['26']['inputs']['image'] = ['11', 0]
    output = [engine.decode_node, 0]
    # Detail encodes the enlarged source BEFORE sampling. Rewiring the post-decode upscale here would create a cycle.
    if upscale > 1 and mode not in engine.upscale_modes:
        graph['200'] = {'class_type':'UpscaleModelLoader', 'inputs':{'model_name':UPSCALE_MODEL}}
        for ident in ('201',) if upscale == 2 else ('201', '202'):
            graph[ident] = {'class_type':'ImageUpscaleWithModel', 'inputs':{'upscale_model':['200',0], 'image':output}}
            output = [ident, 0]
        graph[engine.output_nodes[0]]['inputs']['images'] = output
        graph[engine.output_nodes[0]]['inputs']['filename_prefix'] = engine.output_prefix + '_' + quality.capitalize() + '_x' + str(upscale)
    metadata = {
        'engine':engine.id, 'mode':mode, 'workflow_version':engine.workflow_version,
        'workflow_sha256':hashlib.sha256(engine.workflow_path(mode).read_bytes()).hexdigest(),
        'graph_sha256':hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest(),
        'result_node_ids':list(engine.output_nodes), 'expected_format':'png',
        'seed':seed, 'steps':sampler['steps'],
        'sampler_parameters':{k:sampler[k] for k in ('steps','cfg','sampler_name','scheduler','denoise')},
        'sampler_node_ids':[engine.sampler_node],
        'node_names':{**engine.node_names, '200':ui_text('image.build_workflow_loading_real_esrgan'), '201':'AI Upscale 2x', '202':'AI Upscale 4x'}
    }
    if engine.expand_nodes:
        metadata.update(expansion='upsampler' if expand else 'off', expansion_preview_node=engine.expansion_preview if expand else None)
    elif engine.expansion_assets:
        metadata.update(expansion='upsampler' if expand else 'off', expansion_preview_node='27' if expand else None,
                        expansion_model=engine.expansion_assets[mode] if expand else None)
        if expand:
            metadata['node_names'].update({'23':'Qwen 2.1 prompt enhancer', '26':'Prompt expansion', '27':'Expanded prompt'})
    if engine.id == 'flux3-klein-int8':
        metadata.update(native_megapixels=native_megapixels,
                        reference_count=0 if mode in engine.text_modes else 2 + len(extra_images or []) if mode in ('reference', 'multi') else 1)
    if engine.id == 'qwen-image-21-uc-fp8':
        metadata.update(native_megapixels=native_megapixels,
                        reference_count=0 if mode in engine.text_modes else 2 + len(extra_images or []) if mode in ('reference', 'multi') else 1)
        for slot in range(1, metadata['reference_count'] + 1):
            joined = graph['40']['inputs']['images.image_' + str(slot)][0]
            metadata['node_names'][joined] = ui_text('image.qwen21_rgba_stage', slot)
    if engine.id == 'qwen-edit-2511':
        metadata.update(negative_prompt=negative_prompt, use_uncensored_lora=use_uncensored_lora,
                        reference_count=0 if mode == 'prompt' else 3 if mode == 'multi' else 2 if mode == 'reference' else 1)
        metadata['node_names'].update({'84':ui_text('image.qwen_third_image_label'), '171':'Lightning 4-step LoRA'})
    if mode == 'layout':
        metadata.update(layout_boxes=layout_boxes)
        metadata['node_names'].update({'29':'Layout elements', '30':'Compose caption and layout'})
    if mode == 'detail':
        metadata.update(detail_strength=sampler['denoise'], input_megapixels=1, output_scale=2)
        metadata['node_names'].update({'10':'Source image', '11':'Normalize source resolution', '50':'Encode enlarged image'})
    if control_strength is not None:
        metadata.update(control_type=control_type, control_strength=control_strength)
        metadata['node_names'].update({'10':'Source image', '11':'Normalize source resolution', '12':'Edit mask', '13':'Read edit mask',
                                       '14':'Threshold edit mask', '15':'Canny edges', '24':'Load ControlNet', '26':'Apply ControlNet',
                                       '50':'Encode image', '91':'Preserve pixels outside mask'})
    return graph, metadata


def collect_outputs(history, metadata):
    outputs = (history or {}).get('outputs', {})
    return [image for ident in metadata.get('result_node_ids', ['9'])
            for image in outputs.get(str(ident), {}).get('images', [])]


def collect_expansion(history, metadata):
    """The caption the upsampler wrote, taken from the preview node's text output; '' when there is none."""
    node = metadata.get('expansion_preview_node')
    texts = ((history or {}).get('outputs', {}).get(str(node), {}).get('text') or []) if node else []
    return texts[0] if texts and isinstance(texts[0], str) else ''
