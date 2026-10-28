"""Check each engine separately against the actual ComfyUI API schema."""
from ui_strings import source as ui_text
from .registry import ENGINES, UPSCALE_MODEL


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


def engine_readiness(engine, obj):
    missing_classes, missing_assets, schema_errors = set(), [], []
    for cls, key, filename in engine.assets:
        options = combo_options(input_specs(obj.get(cls, {})).get(key))
        if options is None or filename not in options:
            missing_assets.append(filename)
    for mode in engine.modes:
        graph = engine.template(mode)
        missing_classes.update(n['class_type'] for n in graph.values() if n['class_type'] not in obj)
        asset_keys = {key for _, key, _ in engine.assets}
        schema_errors.extend(f'{mode}: {err}' for err in graph_errors(graph, obj)
                             if not any(': unavailable ' + key + '=' in err for key in asset_keys))
    reasons = []
    if missing_classes: reasons.append(ui_text('image.engine_readiness_missing_comfy_ui_nodes') + ', '.join(sorted(missing_classes)))
    if missing_assets: reasons.append(ui_text('image.engine_readiness_missing_models') + ', '.join(missing_assets) + ui_text('image.engine_readiness_download_them_components'))
    if schema_errors: reasons.append(ui_text('image.engine_readiness_incompatible_workflow') + '; '.join(schema_errors[:8]))
    ready = not reasons
    sampler = engine.template(engine.modes[0])[engine.sampler_node]['inputs']
    if engine.modes == ('prompt',):
        capabilities = ui_text('image.engine_readiness_text_image_no_editing_references')
    elif engine.native_prompt:
        capabilities = ui_text('image.engine_readiness_text_image_editing_two_images')
    else:
        capabilities = ui_text('image.engine_readiness_editing_reference_prompt_through_blank_canvas')
    return {'id':engine.id, 'name':engine.name, 'ready':ready,
            'status':'installed' if ready else ('incompatible' if missing_classes or schema_errors else 'unavailable'),
            'reasons':reasons, 'missing_node_types':sorted(missing_classes),
            'missing_assets':missing_assets, 'schema_errors':schema_errors,
            'modes':list(engine.modes), 'native_prompt':engine.native_prompt,
            'quality_steps':dict(engine.quality_steps), 'workflow_version':engine.workflow_version,
            'cfg':sampler['cfg'], 'sampler':sampler['sampler_name'] + ' / ' + sampler['scheduler'], 'expansion':bool(engine.expand_nodes),
            'capabilities':capabilities}


def all_readiness(obj):
    return {ident:engine_readiness(engine, obj) for ident,engine in ENGINES.items()}


def upscale_available(obj):
    options = combo_options(input_specs(obj.get('UpscaleModelLoader', {})).get('model_name')) or []
    return 'ImageUpscaleWithModel' in obj and UPSCALE_MODEL in options
