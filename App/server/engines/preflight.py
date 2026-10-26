"""Check each engine separately against the actual ComfyUI API schema."""
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
                    autogrow_key = slot in spec[1].get('template', {}).get('names', [])
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
    if missing_classes: reasons.append('Нет узлов ComfyUI: ' + ', '.join(sorted(missing_classes)))
    if missing_assets: reasons.append('Нет моделей: ' + ', '.join(missing_assets) + '. Скачайте их во вкладке «Компоненты».')
    if schema_errors: reasons.append('Workflow несовместим: ' + '; '.join(schema_errors[:8]))
    ready = not reasons
    return {'id':engine.id, 'name':engine.name, 'ready':ready,
            'status':'installed' if ready else ('incompatible' if missing_classes or schema_errors else 'unavailable'),
            'reasons':reasons, 'missing_node_types':sorted(missing_classes),
            'missing_assets':missing_assets, 'schema_errors':schema_errors,
            'modes':list(engine.modes), 'native_prompt':engine.native_prompt,
            'quality_steps':dict(engine.quality_steps), 'workflow_version':engine.workflow_version,
            'cfg':1 if engine.native_prompt else 4,
            'capabilities':'Текст → изображение; редактирование; два изображения' if engine.native_prompt else 'Редактирование; референс; промпт через пустой холст'}


def all_readiness(obj):
    return {ident:engine_readiness(engine, obj) for ident,engine in ENGINES.items()}


def upscale_available(obj):
    options = combo_options(input_specs(obj.get('UpscaleModelLoader', {})).get('model_name')) or []
    return 'ImageUpscaleWithModel' in obj and UPSCALE_MODEL in options
