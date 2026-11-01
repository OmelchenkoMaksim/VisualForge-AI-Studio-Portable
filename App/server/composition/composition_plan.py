"""Bounded composition plans, installed capabilities and mandatory quality settings."""
import json
import re

from engines.image_registry import ENGINES
from engines.image_prompt_enhancer import caption
from ui_strings import source as ui_text

GROUPS = (dict(id='hero', limit=6), dict(id='place', limit=4), dict(id='object', limit=8), dict(id='reference', limit=4))
MAX_VARIANTS, MAX_STEPS = 5, 12
ABOUT = {
    'flux3-klein-int8': 'RGB composition/editing with up to ten pictures; spatial layouts; native high-resolution text generation. One 16-step preset. Fine writing/counts/identity can drift.',
    'qwen-image-21-uc-fp8': 'Regular 7B unified generation/editing, real RGBA, native 2K, up to ten pictures; useful for transparency, lettering and transfers. Complex compositions may omit objects.',
    'qwen-edit-2511': '20B RGB instruction editor, at most three pictures; bounded material/object/text edits. Prompt is experimental canvas editing. Standard 40 steps, CFG 4. No alpha or pixel locks.',
    'z-image-turbo-int8': '8-step text generation without references. Detail/Tile redraw existing texture at 2K; these are not semantic object editors and can damage exact writing or identity.',
}


def request_data(data):
    prompt, count, canvas = data.get('prompt'), data.get('variants', 1), data.get('canvas', 'auto')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 5000:
        raise ValueError(ui_text('composition.panel_prompt_needed_text'))
    if type(count) is not int or not 1 <= count <= MAX_VARIANTS or canvas not in ('auto', 'square', 'landscape', 'portrait'):
        raise ValueError(ui_text('composition.panel_invalid_options_text'))
    return dict(prompt=prompt.strip(), variants=count, canvas=canvas, language='English' if data.get('language') == 'English' else 'Russian')


def capabilities(preflight):
    result = {}
    for ident, row in preflight.get('engines', {}).items():
        if ident not in ABOUT or not row.get('ready'):
            continue
        modes = [mode for mode in row['modes'] if mode in ('prompt', 'single', 'reference', 'multi', 'detail', 'tile')]
        result[ident] = dict(modes=modes, maximum_inputs=ENGINES[ident].max_reference_images, about=ABOUT[ident],
                             native_megapixels=list(ENGINES[ident].native_megapixels))
    if not result:
        raise ValueError(ui_text('composition.panel_no_models_text'))
    return dict(engines=result, upscale=preflight.get('upscale_available') is True)


def read_object(raw):
    raw = re.sub(r'^```[^\n]*\n|\n```$', '', raw.strip())
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(ui_text('composition.panel_invalid_plan_text'))
    return value


def normalize(value, request, caps, items):
    """Reject dropped materials, unsupported modes and broken chains instead of silently guessing a plan."""
    fail = lambda: ValueError(ui_text('composition.panel_invalid_plan_text'))
    if not isinstance(value, dict) or not isinstance(value.get('variants'), list) or len(value['variants']) != request['variants']:
        raise fail()
    canvas = request['canvas'] if request['canvas'] != 'auto' else value.get('canvas', 'square')
    if canvas not in ('square', 'landscape', 'portrait'):
        raise fail()
    materials = {item['id'] for item in items}
    variants, signatures = [], set()
    for number, variant in enumerate(value['variants'], 1):
        if not isinstance(variant, dict) or not isinstance(variant.get('steps'), list) or not 1 <= len(variant['steps']) <= MAX_STEPS:
            raise fail()
        used, steps = set(), []
        for index, step in enumerate(variant['steps']):
            if not isinstance(step, dict):
                raise fail()
            engine, mode, sources = step.get('engine'), step.get('mode'), step.get('sources')
            if not isinstance(engine, str) or engine not in caps['engines'] or mode not in caps['engines'][engine]['modes']:
                raise fail()
            if not isinstance(sources, list) or any(not isinstance(source, str) for source in sources) or len(set(sources)) != len(sources):
                raise fail()
            if any(source not in materials | ({'previous'} if index else set()) for source in sources):
                raise fail()
            # A later step must carry the previous composition forward; otherwise earlier material silently disappears.
            if index and (not sources or sources[0] != 'previous'):
                raise fail()
            count = len(sources)
            expected = 0 if mode == 'prompt' else 1 if mode in ('single', 'detail', 'tile') else 2 if mode == 'reference' else None
            if (expected is not None and count != expected) or (mode == 'multi' and not 3 <= count <= ENGINES[engine].max_reference_images):
                raise fail()
            final = index == len(variant['steps']) - 1
            prompt = caption(step.get('prompt', '') if isinstance(step.get('prompt'), str) else '', request['prompt'] if final else '', bool(sources))
            if prompt is None:
                raise fail()
            upscale = step.get('upscale', 1)
            if type(upscale) is not int or upscale not in (1, 2, 4) or (upscale > 1 and not caps['upscale']):
                raise fail()
            if mode in ('detail', 'tile'):
                upscale = 2
                if not caps['upscale']:
                    raise fail()
            native = step.get('native_megapixels', 4 if engine in ('flux3-klein-int8', 'qwen-image-21-uc-fp8') else 1)
            if engine != 'qwen-image-21-uc-fp8' and mode != 'prompt':
                native = 1
            if type(native) is not int or native not in ENGINES[engine].native_megapixels or (native == 16 and upscale != 1):
                raise fail()
            used.update(source for source in sources if source != 'previous')
            steps.append(dict(engine=engine, mode=mode, sources=list(sources), prompt=prompt, upscale=upscale, native_megapixels=native))
        if used != materials:
            raise ValueError(ui_text('composition.panel_materials_missing_text'))
        signature = json.dumps(steps, sort_keys=True)
        if signature in signatures:
            raise ValueError(ui_text('composition.panel_invalid_plan_text'))
        signatures.add(signature)
        variants.append(dict(number=number, title=str(variant.get('title') or '')[:120], steps=steps))
    return dict(canvas=canvas, variants=variants)


def image_options(step):
    """Maximum supported quality is policy, never an LLM-controlled speed switch."""
    engine = ENGINES[step['engine']]
    quality = 'good' if engine.id == 'qwen-image-21-uc-fp8' else 'low'
    value = dict(engine=engine.id, mode=step['mode'], prompt=step['prompt'], quality=quality, upscale=step['upscale'])
    if engine.id in ('flux3-klein-int8', 'qwen-image-21-uc-fp8'):
        value['native_megapixels'] = step['native_megapixels']
    if engine.id == 'qwen-edit-2511':
        value.update(negative_prompt='', use_uncensored_lora=False)
    return value
