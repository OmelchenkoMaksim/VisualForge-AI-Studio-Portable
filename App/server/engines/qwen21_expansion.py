"""Optional Qwen 2.1 prompt rewriting, using separate publisher checkpoints for text and edits."""
import json
import re
from pathlib import Path

from ui_strings import source as ui_text


def requires_transparency(prompt):
    """Recognize explicit alpha terminology; do not infer it from an ordinary background edit."""
    return bool(re.search(r'\brgba\b|\balpha[\s-]*channel\b|\btransparency\b|'
                          r'\btransparent[\s-]+(?:background|png|image|layer|canvas)\b|'
                          r'\bbackground\b.{0,30}\btransparent\b|прозрачност|'
                          r'прозрач[а-яё\s-]*(?:фон|png|сло)|(?:фон|png|сло)[а-яё\s-]*прозрач',
                          prompt, re.IGNORECASE))


def add_expansion(graph, engine, mode, prompt, seed):
    task = 't2i' if mode == 'prompt' else 'i2i'
    config = json.loads((Path(__file__).resolve().parents[2] / 'config/qwen21-prompt-enhancement.json').read_text(encoding='utf-8'))[task]
    graph['23'] = {'class_type':'CLIPLoader', 'inputs':{'clip_name':engine.expansion_assets[mode],
                   'type':'stable_diffusion' if task == 't2i' else 'qwen_image', 'device':'default'}}
    inputs = {'clip':['23',0], 'prompt':prompt, 'system_prompt':config['system_prompt'],
              'max_length':4096, 'sampling_mode':'on', 'sampling_mode.temperature':1.0,
              'sampling_mode.top_k':20, 'sampling_mode.top_p':0.95, 'sampling_mode.min_p':0.0,
              'sampling_mode.repetition_penalty':1.0, 'sampling_mode.seed':seed,
              'sampling_mode.presence_penalty':1.5 if task == 't2i' else 0.0,
              'thinking':True, 'use_default_template':True, 'mtp':'auto'}
    if task == 'i2i':
        sources = ['11'] + (['12'] if mode in ('reference','multi') else [])
        if mode == 'multi':
            sources += sorted((ident for ident, node in graph.items() if node['class_type'] == 'LoadImage' and int(ident) >= 110), key=int)
        # Vision receives RGB, as in the publisher template; the edit encoder separately receives rejoined RGBA.
        graph['28'] = {'class_type':'BatchImagesNode', 'inputs':{'images.image'+str(index):[ident,0] for index, ident in enumerate(sources)}}
        inputs['image'] = ['28',0]
    graph['26'] = {'class_type':'TextGenerate', 'inputs':inputs}
    graph['27'] = {'class_type':'PreviewAny', 'inputs':{'source':['26',0]}}
    graph['40']['inputs']['prompt'] = ['26',0]


def expansion_errors(engine, mode, obj, disabled_components):
    from .image_preflight import graph_errors
    # Validating this small graph catches missing nodes and model combo entries without disabling base generation.
    graph = engine.template(mode)
    add_expansion(graph, engine, mode, 'probe', 0)
    optional = {ident:node for ident, node in graph.items() if ident in ('23','26','27','28')}
    optional.update({ident:node for ident,node in graph.items() if node['class_type'] == 'LoadImage'})
    errors = graph_errors(optional, obj)
    component = 'image.model.qwen21-pe-' + ('t2i' if mode == 'prompt' else 'i2i')
    if component in disabled_components:
        errors.append(ui_text('image.qwen21_expansion_unavailable'))
    return [ui_text('image.qwen21_expansion_unavailable')] if errors else []
