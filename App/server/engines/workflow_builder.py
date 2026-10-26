"""Build independent API prompts without changing the stored templates."""
import hashlib
import json
from .registry import CANVASES, UPSCALE_MODEL


def validate_request(engine, data):
    mode = data.get('mode', 'single')
    if not isinstance(mode, str) or mode not in engine.modes:
        raise ValueError('Режим не поддерживается движком')
    quality = data.get('quality', 'low')
    if not isinstance(quality, str) or quality not in engine.quality_steps:
        raise ValueError('Quality должен быть Low, Good или High')
    upscale = data.get('upscale', 1)
    if type(upscale) is not int or upscale not in (1, 2, 4):
        raise ValueError('Upscale должен быть Off, 2x или 4x')
    prompt = data.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 5000:
        raise ValueError('Промпт должен содержать от 1 до 5000 символов')
    seed = data.get('seed')
    if seed is not None and (type(seed) is not int or not 0 <= seed < (1 << 48)):
        raise ValueError('Seed должен быть целым числом от 0 до 2^48-1')
    canvas = data.get('canvas', 'square')
    if mode == 'prompt' and (not isinstance(canvas, str) or canvas not in CANVASES):
        raise ValueError('Неизвестный формат холста')
    return mode, quality, upscale, prompt.strip(), seed, canvas


def resolve_workflow_mode(engine, selected_mode, operation='generate'):
    if not isinstance(operation, str) or operation not in ('generate', 'variation'):
        raise ValueError('Неизвестная операция генерации')
    workflow_mode = 'single' if operation == 'variation' and selected_mode == 'prompt' else selected_mode
    if workflow_mode not in engine.modes:
        raise ValueError('Операция не поддерживается движком')
    return workflow_mode


def build_workflow(engine, mode, prompt, image1, image2, quality, upscale, seed, canvas='square'):
    graph = engine.template(mode)
    if mode != 'prompt' or not engine.native_prompt:
        graph[engine.image_nodes[0]]['inputs']['image'] = image1
    if mode == 'reference':
        graph[engine.image_nodes[1]]['inputs']['image'] = image2
    if mode == 'prompt' and not engine.native_prompt:
        prompt = ('Create a fully new, original image matching this description. '
                  'The input is only a completely blank neutral canvas; replace it entirely. '
                  'Do not preserve the blank background. The complete requested scene is: ' + prompt)
    if engine.native_prompt and mode == 'reference':
        prompt = ('Edit <image1> as the source image; use <image2> as the reference. '
                  'Follow the user instruction: ' + prompt)
    graph[engine.prompt_node]['inputs']['prompt'] = prompt
    if engine.native_prompt and mode == 'prompt':
        width, height = CANVASES[canvas]
        graph['50']['inputs'].update(width=width, height=height)
    sampler = graph[engine.sampler_node]['inputs']
    sampler.update(seed=seed, steps=engine.quality_steps[quality])
    output = [engine.decode_node, 0]
    if upscale > 1:
        graph['200'] = {'class_type':'UpscaleModelLoader', 'inputs':{'model_name':UPSCALE_MODEL}}
        for ident in ('201',) if upscale == 2 else ('201', '202'):
            graph[ident] = {'class_type':'ImageUpscaleWithModel', 'inputs':{'upscale_model':['200',0], 'image':output}}
            output = [ident, 0]
        graph[engine.output_nodes[0]]['inputs']['images'] = output
        prefix = 'Qwen_Edit_2511' if not engine.native_prompt else 'Qwen_Image_21_UC'
        graph[engine.output_nodes[0]]['inputs']['filename_prefix'] = prefix + '_' + quality.capitalize() + '_x' + str(upscale)
    metadata = {
        'engine':engine.id, 'mode':mode, 'workflow_version':engine.workflow_version,
        'workflow_sha256':hashlib.sha256(engine.workflow_path(mode).read_bytes()).hexdigest(),
        'graph_sha256':hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest(),
        'result_node_ids':list(engine.output_nodes), 'expected_format':'png',
        'seed':seed, 'steps':sampler['steps'],
        'sampler_parameters':{k:sampler[k] for k in ('steps','cfg','sampler_name','scheduler','denoise')},
        'sampler_node_ids':[engine.sampler_node],
        'node_names':{**engine.node_names, '200':'Загрузка Real-ESRGAN', '201':'AI Upscale 2x', '202':'AI Upscale 4x'}
    }
    return graph, metadata


def collect_outputs(history, metadata):
    outputs = (history or {}).get('outputs', {})
    return [image for ident in metadata.get('result_node_ids', ['9'])
            for image in outputs.get(str(ident), {}).get('images', [])]
