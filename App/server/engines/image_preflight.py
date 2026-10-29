"""Check each engine separately against the actual ComfyUI API schema."""
from ui_strings import source as ui_text
from .image_registry import ENGINES, UPSCALE_MODEL, QWEN_UNCENSORED


def combo_options(spec):
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    if isinstance(spec[0], (list, tuple)):
        return spec[0]
    if spec[0] == 'COMBO' and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get('options')
    return None


def input_specs(info):
    declared = info.get('input', {})
    return {**declared.get('required', {}), **declared.get('optional', {}), **declared.get('hidden', {})}


def graph_errors(graph, obj):
    errors = []
    for ident, node in graph.items():
        cls = node['class_type']
        if cls not in obj:
            errors.append(f'{ident}: missing class {cls}')
            continue
        declared = input_specs(obj[cls])
        supplied = node['inputs']
        for key in obj[cls].get('input', {}).get('required', {}):
            spec = declared.get(key)
            autogrow = isinstance(spec, (list, tuple)) and spec and spec[0] == 'COMFY_AUTOGROW_V3'
            if key not in supplied and not autogrow:
                errors.append(f'{ident}: missing input {key}')
        for key, value in supplied.items():
            # Autogrow keys are expanded by the V3 schema. Some versions expose
            # only the container in object_info; /prompt remains authoritative.
            autogrow_key = False
            if '.' in key:
                container, slot = key.split('.', 1)
                spec = declared.get(container, [])
                if spec and spec[0] == 'COMFY_AUTOGROW_V3':
                    template = spec[1].get('template', {})
                    # A names template lists its slots; a prefix template only gives prefix, min and max ("image0" .. "image99").
                    names = template.get('names') or ['%s%d' % (template.get('prefix', ''), i) for i in range(template.get('max', 0))]
                    autogrow_key = slot in names
                elif spec and spec[0] == 'COMFY_DYNAMICCOMBO_V3':
                    # A dynamic combo adds the inputs of the option the graph selects, addressed as "<combo>.<input>".
                    for option in spec[1].get('options', []):
                        if option.get('key') == supplied.get(container):
                            nested = option.get('inputs', {})
                            autogrow_key = slot in {**nested.get('required', {}), **nested.get('optional', {})}
            if key not in declared and not autogrow_key:
                errors.append(f'{ident}: unknown input {key}')
                continue
            options = combo_options(declared.get(key))
            if options is not None and not isinstance(value, list) and value not in options and not (isinstance(value, str) and value.startswith('__')):
                errors.append(f'{ident}: unavailable {key}={value}')
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                if value[0] not in graph:
                    errors.append(f'{ident}: missing source {value[0]}')
    return errors


def engine_readiness(engine, obj, upscale_enabled=True, disabled_components=()):
    missing_classes, missing_assets, schema_errors = set(), [], []
    mode_reasons = {}
    templates = {mode: engine.template(mode) for mode in engine.modes}
    for cls, key, filename in engine.assets:
        options = combo_options(input_specs(obj.get(cls, {})).get(key))
        if options is None or filename not in options:
            missing_assets.append(filename)
    for mode in engine.modes:
        graph = templates[mode]
        absent = {n['class_type'] for n in graph.values() if n['class_type'] not in obj}
        asset_keys = {key for _, key, _ in engine.assets}
        errors = [err for err in graph_errors(graph, obj)
                  if not any(': unavailable ' + key + '=' in err for key in asset_keys)]
        if mode in engine.optional_modes:
            mode_reasons[mode] = errors
            if mode in engine.upscale_modes and (not upscale_enabled or not upscale_available(obj)):
                mode_reasons[mode].append(ui_text('image.detail_requires_upscale_model'))
            if set(engine.mode_components.get(mode, ())) & set(disabled_components):
                mode_reasons[mode].append(ui_text('image.control_component_disabled_message'))
        else:
            missing_classes.update(absent)
            schema_errors.extend(f'{mode}: {err}' for err in errors)
    reasons = []
    if missing_classes: reasons.append(ui_text('image.engine_readiness_missing_comfy_ui_nodes') + ', '.join(sorted(missing_classes)))
    if missing_assets: reasons.append(ui_text('image.engine_readiness_missing_models') + ', '.join(missing_assets) + ui_text('image.engine_readiness_download_them_components'))
    if schema_errors: reasons.append(ui_text('image.engine_readiness_incompatible_workflow') + '; '.join(schema_errors[:8]))
    ready = not reasons
    sampler = templates[engine.modes[0]][engine.sampler_node]['inputs']
    available_modes = [mode for mode in engine.modes if not mode_reasons.get(mode)]
    quality_reasons = {}
    for quality, assets in engine.quality_assets.items():
        available = all(filename in (combo_options(input_specs(obj.get(cls, {})).get(key)) or []) for cls, key, filename in assets)
        if not available or engine.quality_components.get(quality) in disabled_components:
            quality_reasons[quality] = ui_text('image.qwen_lightning_unavailable')
    quality_steps = {key:value for key,value in engine.quality_steps.items() if key not in quality_reasons}
    quality_cfg = {key:1 if engine.id == 'qwen-edit-2511' and key == 'fast' else sampler['cfg'] for key in quality_steps}
    community_available = (engine.id == 'qwen-edit-2511' and
        QWEN_UNCENSORED in (combo_options(input_specs(obj.get('LoraLoaderModelOnly', {})).get('lora_name')) or []) and
        'image.model.qwen-edit-uncensored' not in disabled_components)
    expansion_reasons = {}
    if engine.expansion_assets:
        from .qwen21_expansion import expansion_errors
        expansion_reasons = {mode:expansion_errors(engine, mode, obj, disabled_components) for mode in available_modes}
    if 'detail' in available_modes:
        capabilities = ui_text('image.detail_model_capabilities')
    elif engine.id == 'z-image-turbo-int8':
        capabilities = ui_text('image.zimage_base_capabilities')
    elif available_modes == ['prompt']:
        capabilities = ui_text('image.engine_readiness_text_image_no_editing_references')
    elif engine.id == 'flux3-klein-int8':
        capabilities = ui_text('image.flux_model_capabilities')
    elif engine.id == 'qwen-edit-2511':
        capabilities = ui_text('image.qwen_model_capabilities') if 'multi' in available_modes else ui_text('image.qwen_model_capabilities_two')
    elif engine.id == 'qwen-image-21-uc-fp8':
        capabilities = ui_text('image.qwen21_model_capabilities') if 'multi' in available_modes else ui_text('image.qwen21_model_capabilities_two')
    elif engine.native_prompt:
        capabilities = ui_text('image.engine_readiness_text_image_editing_two_images')
    else:
        capabilities = ui_text('image.engine_readiness_editing_reference_prompt_through_blank_canvas')
    guided_capabilities = {'control': ui_text('image.control_capability_control_label'),
                           'inpaint': ui_text('image.control_capability_inpaint_label'),
                           'tile': ui_text('image.control_capability_tile_label'),
                           'layout': ui_text('image.flux_layout_capability_label')}
    capability_parts = [capabilities]
    for mode, label in guided_capabilities.items():
        if mode in available_modes:
            capability_parts.append(label)
    return {'id':engine.id, 'name':engine.name, 'ready':ready,
            'status':'installed' if ready else ('incompatible' if missing_classes or schema_errors else 'unavailable'),
            'reasons':reasons, 'missing_node_types':sorted(missing_classes),
            'missing_assets':missing_assets, 'schema_errors':schema_errors,
            'modes':available_modes, 'mode_reasons':mode_reasons, 'native_prompt':engine.native_prompt,
            'mode_presets':{mode: {'quality_steps':engine.mode_quality_steps.get(mode, quality_steps), 'quality_cfg':quality_cfg,
                                   'cfg':templates[mode][engine.sampler_node]['inputs']['cfg'],
                                   'sampler':templates[mode][engine.sampler_node]['inputs']['sampler_name'] + ' / ' + templates[mode][engine.sampler_node]['inputs']['scheduler']}
                            for mode in available_modes},
            'quality_steps':quality_steps, 'quality_cfg':quality_cfg, 'quality_reasons':quality_reasons,
            'community_lora_available':community_available, 'workflow_version':engine.workflow_version,
            'text_modes':list(engine.text_modes), 'max_reference_images':engine.max_reference_images,
            'native_megapixels':list(engine.native_megapixels),
            'cfg':sampler['cfg'], 'sampler':sampler['sampler_name'] + ' / ' + sampler['scheduler'],
            'expansion':bool(engine.expand_nodes or engine.expansion_assets),
            'expansion_available_modes':[mode for mode in available_modes if not expansion_reasons.get(mode)] if engine.expansion_assets else available_modes if engine.expand_nodes else [],
            'expansion_reasons':expansion_reasons,
            'capabilities':'; '.join(capability_parts), 'capability_parts':capability_parts}


def all_readiness(obj, upscale_enabled=True, disabled_components=()):
    return {ident:engine_readiness(engine, obj, upscale_enabled, disabled_components) for ident,engine in ENGINES.items()}


def upscale_available(obj):
    options = combo_options(input_specs(obj.get('UpscaleModelLoader', {})).get('model_name')) or []
    return 'ImageUpscaleWithModel' in obj and UPSCALE_MODEL in options
