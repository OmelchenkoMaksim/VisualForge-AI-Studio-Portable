"""Build independent API prompts without changing the stored templates."""
from ui_strings import source as ui_text
import hashlib
import json
from .registry import CANVASES, UPSCALE_MODEL


def validate_request(engine, data):
    mode = data.get('mode', 'single')
    if not isinstance(mode, str) or mode not in engine.modes:
        raise ValueError(ui_text('image.validate_mode_not_supported_engine'))
    quality = data.get('quality', 'low')
    if not isinstance(quality, str) or quality not in engine.quality_steps:
        raise ValueError(ui_text('image.validate_quality_must_low_good_high'))
    upscale = data.get('upscale', 1)
    if type(upscale) is not int or upscale not in (1, 2, 4):
        raise ValueError(ui_text('image.validate_upscale_must_off_2_x_4'))
    prompt = data.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 5000:
        raise ValueError(ui_text('image.validate_prompt_must_contain_1_5000_characters'))
    seed = data.get('seed')
    if seed is not None and (type(seed) is not int or not 0 <= seed < (1 << 48)):
        raise ValueError(ui_text('image.validate_seed_must_integer_0_2_48'))
    canvas = data.get('canvas', 'square')
    if mode == 'prompt' and (not isinstance(canvas, str) or canvas not in CANVASES):
        raise ValueError(ui_text('image.validate_unknown_canvas_format'))
    return mode, quality, upscale, prompt.strip(), seed, canvas


def resolve_expansion(engine, data):
    """Whether the prompt goes through the engine's upsampler; engines without one refuse a request for it."""
    value = data.get('expand')
    if value is None:
        return bool(engine.expand_nodes)
    if type(value) is not bool:
        raise ValueError(ui_text('image.resolve_expansion_prompt_yes_no'))
    if value and not engine.expand_nodes:
        raise ValueError(ui_text('image.resolve_expansion_model_does_not_expand_prompts'))
    return value


def resolve_workflow_mode(engine, selected_mode, operation='generate'):
    if not isinstance(operation, str) or operation not in ('generate', 'variation'):
        raise ValueError(ui_text('image.resolve_workflow_mode_unknown_generation_operation'))
    workflow_mode = 'single' if operation == 'variation' and selected_mode == 'prompt' else selected_mode
    if workflow_mode not in engine.modes:
        raise ValueError(ui_text('image.resolve_workflow_mode_operation_not_supported_engine'))
    return workflow_mode


def build_workflow(engine, mode, prompt, image1, image2, quality, upscale, seed, canvas='square', expand=None):
    if mode not in engine.modes:
        raise ValueError(ui_text('image.validate_mode_not_supported_engine'))
    expand = bool(engine.expand_nodes) if expand is None else expand
    graph = engine.template(mode)
    if mode != 'prompt' or not engine.native_prompt:
        graph[engine.image_nodes[0]]['inputs']['image'] = image1
    if mode == 'reference':
        graph[engine.image_nodes[1]]['inputs']['image'] = image2
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
    else:
        graph[engine.prompt_node]['inputs'][engine.prompt_key] = prompt
    if engine.native_prompt and mode == 'prompt':
        width, height = CANVASES[canvas]
        graph[engine.size_node]['inputs'].update(width=width, height=height)
    sampler = graph[engine.sampler_node]['inputs']
    sampler.update(seed=seed, steps=engine.quality_steps[quality])
    output = [engine.decode_node, 0]
    if upscale > 1:
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
