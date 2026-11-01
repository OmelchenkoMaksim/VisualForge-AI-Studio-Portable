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
    """Reject dropped materials, unsupported modes and broken chains instead of silently guessing a plan.

    The reason travels in the error: the planner gets one retry with it, and a retry told only "the plan is invalid" repeats the same mistake."""
    def fail(reason):
        return ValueError('%s (%s)' % (ui_text('composition.panel_invalid_plan_text'), reason))
    if not isinstance(value, dict) or not isinstance(value.get('variants'), list) or len(value['variants']) != request['variants']:
        raise fail('"variants" must be a list of exactly %d' % request['variants'])
    canvas = request['canvas'] if request['canvas'] != 'auto' else value.get('canvas', 'square')
    if canvas not in ('square', 'landscape', 'portrait'):
        raise fail('"canvas" must be square, landscape or portrait')
    materials = {item['id'] for item in items}
    variants, signatures = [], set()
    for number, variant in enumerate(value['variants'], 1):
        if not isinstance(variant, dict) or not isinstance(variant.get('steps'), list) or not 1 <= len(variant['steps']) <= MAX_STEPS:
            raise fail('variant %d needs 1 to %d "steps"' % (number, MAX_STEPS))
        used, steps = set(), []
        for index, step in enumerate(variant['steps']):
            where = 'variant %d, step %d' % (number, index + 1)
            if not isinstance(step, dict):
                raise fail(where + ' is not an object')
            engine, mode, sources = step.get('engine'), step.get('mode'), step.get('sources')
            if not isinstance(engine, str) or engine not in caps['engines']:
                raise fail('%s: "engine" must be one of %s' % (where, ', '.join(caps['engines'])))
            if mode not in caps['engines'][engine]['modes']:
                raise fail('%s: %s has the modes %s' % (where, engine, ', '.join(caps['engines'][engine]['modes'])))
            if not isinstance(sources, list) or any(not isinstance(source, str) for source in sources) or len(set(sources)) != len(sources):
                raise fail(where + ': "sources" must be a list of distinct IDs')
            allowed = materials | ({'previous'} if index else set())
            unknown = [source for source in sources if source not in allowed]
            if unknown:
                raise fail('%s: "%s" is not a material ID%s' % (where, unknown[0], '' if index else ' ("previous" does not exist in the first step)'))
            # A later step must carry the previous composition forward; otherwise earlier material silently disappears.
            if index and (not sources or sources[0] != 'previous'):
                raise fail(where + ': "previous" must be the FIRST source of every step after the first')
            count = len(sources)
            expected = 0 if mode == 'prompt' else 1 if mode in ('single', 'detail', 'tile') else 2 if mode == 'reference' else None
            if (expected is not None and count != expected) or (mode == 'multi' and not 3 <= count <= ENGINES[engine].max_reference_images):
                need = expected if expected is not None else '3 to %d' % ENGINES[engine].max_reference_images
                raise fail('%s: mode %s of %s takes %s sources, not %d' % (where, mode, engine, need, count))
            final = index == len(variant['steps']) - 1
            prompt = caption(step.get('prompt', '') if isinstance(step.get('prompt'), str) else '', request['prompt'] if final else '', bool(sources))
            if prompt is None:
                raise fail(where + ': the prompt is unusable (English prose of 30 to 5000 characters, no question marks, no Cyrillic outside quotes, '
                                   'and the final step keeps the lettering of the request verbatim)')
            upscale = step.get('upscale', 1)
            if type(upscale) is not int or upscale not in (1, 2, 4) or (upscale > 1 and not caps['upscale']):
                raise fail(where + ': "upscale" must be 1' + (', 2 or 4' if caps['upscale'] else ' (AI upscaling is not installed)'))
            if mode in ('detail', 'tile'):
                upscale = 2
                if not caps['upscale']:
                    raise fail(where + ': detail and tile need AI upscaling, which is not installed')
            native = step.get('native_megapixels', 4 if engine in ('flux3-klein-int8', 'qwen-image-21-uc-fp8') else 1)
            if engine != 'qwen-image-21-uc-fp8' and mode != 'prompt':
                native = 1
            if type(native) is not int or native not in ENGINES[engine].native_megapixels:
                raise fail('%s: "native_megapixels" of %s must be one of %s' % (where, engine, ', '.join(map(str, ENGINES[engine].native_megapixels))))
            # Qwen 2.1 gives every picture the selected pixel budget, so a stage with many inputs at 2K is far more expensive than one: it stays at one megapixel.
            if engine == 'qwen-image-21-uc-fp8' and mode != 'prompt' and count > 3:
                native = 1
            # The finished picture is native megapixels times the upscale squared: 4K is the largest that was ever run (native 2K with x2, or 1 MP with x4).
            if native * upscale ** 2 > 16:
                raise fail('%s: native_megapixels %d with upscale %d would make a %d-megapixel picture; the limit is 16' % (where, native, upscale, native * upscale ** 2))
            used.update(source for source in sources if source != 'previous')
            steps.append(dict(engine=engine, mode=mode, sources=list(sources), prompt=prompt, upscale=upscale, native_megapixels=native))
        if used != materials:
            raise ValueError('%s (variant %d leaves out: %s)' % (ui_text('composition.panel_materials_missing_text'), number, ', '.join(sorted(materials - used))))
        signature = json.dumps(steps, sort_keys=True)
        if signature in signatures:
            raise fail('variant %d repeats an earlier variant: every variation needs its own composition, light, framing or rendering' % number)
        signatures.add(signature)
        variants.append(dict(number=number, title=str(variant.get('title') or '')[:120], steps=steps))
    return dict(canvas=canvas, variants=variants)


# Rough seconds of one step on the author's RTX 5070 Ti (docs of 2026-10-29: Klein 15-35 s warm and 160 s cold, Qwen 2.1 25-221 s, Edit 2511 192-453 s, Z-Image seconds). Never compared with a real composition.
STEP_SECONDS = {'flux3-klein-int8': 40, 'qwen-image-21-uc-fp8': 150, 'qwen-edit-2511': 300, 'z-image-turbo-int8': 30}


def estimate_minutes(plan):
    """A rough wall time of a checked plan for the panel: Gemma's look and plan, then every step with its own prompt preparation, native resolution and upscale."""
    seconds = 120
    for variant in plan['variants']:
        for step in variant['steps']:
            seconds += STEP_SECONDS[step['engine']] * (1.5 if step['native_megapixels'] > 1 else 1) + (25 if step['upscale'] > 1 else 0) + 45
    return max(1, round(seconds / 60))


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
