"""Bounded composition plans, installed capabilities and mandatory quality settings."""
import json
import re

from engines.image_registry import ENGINES
from engines.image_prompt_enhancer import QUOTES, caption, protected_words
from engines.qwen21_prompts import ENGINE as RGBA_ENGINE, transparent_output
from ui_strings import source as ui_text

GROUPS = (dict(id='hero', limit=6), dict(id='place', limit=4), dict(id='object', limit=8), dict(id='reference', limit=4))
MAX_VARIANTS, MAX_STEPS = 5, 12
CANVAS = dict(square=(2048, 2048), landscape=(2048, 1365), portrait=(1365, 2048))
FILL_TOLERANCE = 0.06
ABOUT = {
    'fibo2-dev-int8': 'FIBO 2.0 Dev 8B INT8, non-distilled 50 steps CFG 5. Native one-megapixel RGB generation/editing with up to four pictures. Six local trials gave natural fur/skin/materials, correct three-apple counts and Russian lettering, a successful fabric transfer, watercolor and a full-body four-reference new scene. Useful for deliberate photographic composition and bounded edits. A recolour also changed jacket construction and mug shape slightly: no pixel locks or exact preservation guarantee. No alpha, native 2K or layout boxes; AI upscale is separate. Four references are substantially slower than text generation.',
    'flux3-klein-int8': 'RGB composition/editing with up to ten pictures; spatial layouts; native high-resolution text generation. One 16-step preset. Local tests produced convincing photos, watercolor and a three-source new scene, but two small mushroom replacement/removal edits failed. Four-reference photos sometimes cropped the figure or copied a donor pose/held prop; consider other editors when strict reframing or object placement is critical. Fine writing/counts/identity can drift.',
    'qwen-image-21-uc-fp8': 'Regular 7B unified generation/editing, real RGBA, native 2K, up to ten pictures; useful for transparency, lettering and transfers. Local tests preserved a portrait, short Russian lettering and three source roles. Alpha edges had a halo; complex compositions may omit objects.',
    'qwen-edit-2511': '20B RGB instruction editor, at most three pictures; bounded material/object/text edits. Local tests carried out recolouring, mug replacement and a three-source new scene. Prompt is experimental canvas editing: a tested new fox looked rendered, so prefer native text generators for new photographs. Standard 40 steps, CFG 4. No alpha or pixel locks.',
    'z-image-turbo-int8': '8-step text generation without references. Local tests produced photographic fur/light and watercolor, but missed an exact object count. Detail/Tile redraw existing texture at 2K; these are not semantic object editors and can damage exact writing or identity. Tile may overemphasize texture.',
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
                             native_megapixels=list(ENGINES[ident].native_megapixels), alpha=ident == RGBA_ENGINE,
                             local_rating=dict(ENGINES[ident].rating))
    if not result:
        raise ValueError(ui_text('composition.panel_no_models_text'))
    return dict(engines=result, upscale=preflight.get('upscale_available') is True)


def read_object(raw):
    raw = re.sub(r'^```[^\n]*\n|\n```$', '', raw.strip())
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(ui_text('composition.panel_invalid_plan_text'))
    return value


def aspect_sources(sources, items, canvas):
    """Avoid synthetic margins when a real material already has the requested canvas shape."""
    if len(sources) < 2 or sources[0] == 'previous':
        return list(sources)
    materials = {item['id']:item for item in items}
    ratio = CANVAS[canvas][0] / CANVAS[canvas][1]
    def distance(source):
        item = materials.get(source, {})
        width, height = item.get('width'), item.get('height')
        return abs(width / height / ratio - 1) if type(width) is int and type(height) is int and width > 0 and height > 0 else float('inf')
    if distance(sources[0]) <= FILL_TOLERANCE:
        return list(sources)
    anchor = min(sources, key=distance)
    if distance(anchor) > FILL_TOLERANCE:
        return list(sources)
    return [anchor, *[source for source in sources if source != anchor]]


def numbered_prompt(text, sources, ordered_sources=None):
    """Resolve opaque material IDs against this stage's actual input order, keeping quoted lettering literal."""
    if not sources:
        return text
    positions = {source:number for number, source in enumerate(ordered_sources or sources, 1)}
    numbers = {number:positions[source] for number, source in enumerate(sources, 1)}
    ordinals = ('first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth')
    references = re.compile(r'<image(10|[1-9])>|\b(?:Picture|Image|source)\s*(10|[1-9])\b|\b(?:the\s+)?(' + '|'.join(ordinals) + r')\s+(?:source|picture|image)\b', re.I)
    def reference(match):
        number = ordinals.index(match[3].lower()) + 1 if match[3] else int(match[1] or match[2])
        return 'Picture %d' % numbers[number] if number in numbers else match.group()
    def rewrite(part):
        # Remap existing numbers in one pass before inserting IDs, so swaps cannot renumber a reference twice.
        part = references.sub(reference, part)
        for source, number in positions.items():
            if source != 'previous':
                part = re.sub(r'(?i:\bmaterial\s+)?\b' + re.escape(source) + r'\b', 'Picture %d' % number, part)
        return part
    parts, position = [], 0
    for match in QUOTES.finditer(text):
        parts.extend((rewrite(text[position:match.start()]), match.group()))
        position = match.end()
    return ''.join(parts) + rewrite(text[position:])


def caption_request(text):
    """A quoted source group such as 'from photo «Герои»' names an upload group, not lettering to draw."""
    groups = {'герои', 'герой', 'места', 'локации', 'места и локации', 'предметы', 'объекты', 'референсы',
              'hero', 'heroes', 'places', 'locations', 'places and locations', 'objects', 'references'}
    source = re.compile(r'\b(?:из|с|from(?:\s+the)?)\s+(?:фото|изображения|раздела|группы|photos?|pictures?|images?|section|group)\s*$', re.I)
    return QUOTES.sub(lambda match: '' if next(part for part in match.groups() if part is not None).casefold() in groups
                      and source.search(text[:match.start()]) else match.group(), text)


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
            if not isinstance(sources, list) or any(not isinstance(source, str) for source in sources):
                raise fail(where + ': "sources" must be a list of IDs')
            # Real style-only plans repeated the same donor to fill a multi-input mode, even on retry.
            # Collapse identical files and remap all drafted picture numbers before deriving the actual mode.
            drafted_sources = sources
            sources = list(dict.fromkeys(sources))
            allowed = materials | ({'previous'} if index else set())
            unknown = [source for source in sources if source not in allowed]
            if unknown:
                raise fail('%s: "%s" is not a material ID%s' % (where, unknown[0], '' if index else ' ("previous" does not exist in the first step)'))
            # A later step must carry the previous composition forward; otherwise earlier material silently disappears.
            if index and (not sources or sources[0] != 'previous'):
                raise fail(where + ': "previous" must be the FIRST source of every step after the first')
            count = len(sources)
            # Real Gemma repeatedly labelled a two-source edit "multi". These three modes differ only by input count;
            # derive the route from the validated sources rather than spend a retry on that mechanical naming slip.
            if mode in ('single', 'reference', 'multi'):
                canonical = 'single' if count == 1 else 'reference' if count == 2 else 'multi' if count >= 3 else None
                if canonical in caps['engines'][engine]['modes']:
                    mode = canonical
            expected = 0 if mode == 'prompt' else 1 if mode in ('single', 'detail', 'tile') else 2 if mode == 'reference' else None
            if (expected is not None and count != expected) or (mode == 'multi' and not 3 <= count <= ENGINES[engine].max_reference_images):
                need = expected if expected is not None else '3 to %d' % ENGINES[engine].max_reference_images
                raise fail('%s: mode %s of %s takes %s sources, not %d' % (where, mode, engine, need, count))
            final = index == len(variant['steps']) - 1
            # Gemma chose RGB Klein for a real transparent-PNG request. Prose cannot add a missing output channel.
            if final and transparent_output(request['prompt']) and engine != RGBA_ENGINE:
                raise fail('%s: requested alpha output requires a final %s stage; %s produces RGB' % (where, RGBA_ENGINE, engine))
            raw_prompt = step.get('prompt', '') if isinstance(step.get('prompt'), str) else ''
            # A real four-source result retained blurred bands from its square anchor despite an available landscape place.
            # Align the first stage mechanically and remap its roles; later stages must keep `previous` first.
            ordered = aspect_sources(sources, items, canvas) if not index else list(sources)
            # An initial text-only stage may already draw the requested Cyrillic before another stage finishes it.
            # Allow the root's exact lettering there without requiring it in every intermediate picture or allowing invented text.
            prompt = caption(numbered_prompt(raw_prompt, drafted_sources, ordered), caption_request(request['prompt']) if final else '', bool(sources),
                             allowed_lettering=protected_words(caption_request(request['prompt'])))
            sources = ordered
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


# Rough graph seconds on the author's RTX 5070 Ti from the model audits. Real 2026-11-05 runs also exposed
# material observation and per-reference Gemma costs: four-source preparation took about 155 s, not a flat 45 s.
STEP_SECONDS = {'flux3-klein-int8': 40, 'qwen-image-21-uc-fp8': 150, 'qwen-edit-2511': 300, 'z-image-turbo-int8': 30,
                'fibo2-dev-int8': 45}


def estimate_minutes(plan):
    """A rough wall time of a checked plan for the panel: Gemma's look and plan, then every step with its own prompt preparation, native resolution and upscale."""
    materials = {source for variant in plan['variants'] for step in variant['steps'] for source in step['sources'] if source != 'previous'}
    seconds = 120 + 45 * ((len(materials) + 2) // 3)
    for variant in plan['variants']:
        for step in variant['steps']:
            preparation = 45 + 30 * len(step['sources'])
            graph_seconds = STEP_SECONDS[step['engine']]
            if step['engine'] == 'fibo2-dev-int8':
                # Measured graphs: 44 s without inputs, 101/136 s with one/two, 277 s with four.
                # Each reference adds latent tokens; the text-only graph estimate cannot cover a four-source edit.
                graph_seconds += 60 * len(step['sources'])
            seconds += graph_seconds * (1.5 if step['native_megapixels'] > 1 else 1) + (25 if step['upscale'] > 1 else 0) + preparation
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
