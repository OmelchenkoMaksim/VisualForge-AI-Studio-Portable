"""Researched per-engine Image guides with bounded clarification and source-aware validation."""
import base64
from io import BytesIO
import json
import re

from prompt_clarification import Clarifications, fingerprint, question
from prompt_template import PromptEnhancerTemplate
from ui_strings import source as ui_text
from video.gemma_task import GemmaTask
from . import zimage_prompts as zimage
from . import klein_prompts as klein
from . import qwen21_prompts as qwen21
from . import qwen2511_prompts as qwen2511
from .flux_inputs import layout_brief, resolve_flux_options
from .image_registry import ENGINES

QUOTES = re.compile(r'"([^"]+)"|«([^»]+)»|“([^”]+)”')
CYRILLIC = re.compile(r'[\u0400-\u04ff]')


def obsolete_text(prefix):
    """Recognize explicit removal/replacement clauses so the quote guard does not force old lettering back into an edit."""
    clause = re.split(r'[.!?;\n]', prefix)[-1]
    replacement = re.search(r'\b(?:замени(?:те)?|заменить|replace)\b(.*)$', clause, re.I)
    if replacement and not re.search(r'\b(?:на|with)\b', replacement[1], re.I):
        return True
    removal = re.search(r'\b(?:удали(?:те)?|удалить|убери(?:те)?|убрать|сотри(?:те)?|стереть|без|remove|erase|delete|without)\b(.*)$', clause, re.I)
    return bool(removal and not re.search(r'\b(?:добавь(?:те)?|напиши(?:те)?|сохрани(?:те)?|add|write|keep)\b', removal[1], re.I))


def protected_words(request):
    """Protect explicit quotations and unquoted uppercase lettering; free-form lettering also has a system instruction."""
    words = [next(part for part in match.groups() if part is not None) for match in QUOTES.finditer(request)
             if not obsolete_text(request[:match.start()])]
    # Without delimiters we can recognize an uppercase label, but cannot reliably parse arbitrary prose as an exact text span.
    labels = re.finditer(r'(?i:надпис[ьиью]+|напиши(?:те)?|написано|текст(?:ом)?|слово(?:м)?|lettering|text|write|reads?)\s*:?\s*'
                         r'([А-ЯЁA-Z0-9][А-ЯЁA-Z0-9 \t-]*[А-ЯЁA-Z0-9])(?=$|[^а-яёa-zА-ЯЁA-Z0-9])', request)
    words += [match[1] for match in labels if not obsolete_text(request[:match.start(1)])]
    return tuple(dict.fromkeys(words))


def caption(answer, request, has_source):
    """Reject unusable output and changed literal text instead of silently feeding it to the image model."""
    text = answer.strip()
    if text.startswith('```') and text.endswith('```'):
        text = re.sub(r'^```[^\n]*\n|\n```$', '', text).strip()
    text = re.sub(r'^(?:enhanced prompt|rewritten prompt|prompt)\s*:\s*', '', text, flags=re.I).strip()
    text = QUOTES.sub(lambda match: '"' + next(part for part in match.groups() if part is not None) + '"', text)
    if not 30 <= len(text) <= 5000 or text.startswith(('{', '[', '#', '```')) or '<think' in text.lower():
        return None
    if any(ord(char) < 32 and char not in '\n\r\t' for char in text):
        return None
    if any(word not in text for word in protected_words(request)):
        return None
    prose = text
    for match in reversed(list(QUOTES.finditer(text))):
        literal = next(part for part in match.groups() if part is not None)
        # A source picture can contain lettering absent from the typed request; its spelling is a vision responsibility.
        if CYRILLIC.search(literal) and not has_source and literal not in request:
            return None
        prose = prose[:match.start()] + prose[match.end():]
    if '?' in prose or CYRILLIC.search(prose) or not re.search(r'[A-Za-z]{3}', prose):
        return None
    return text


def vision_image(raw, *, mask=False, alpha_matte=False):
    """Bounded, oriented PNG for Gemma. A missing or unreadable source is an error, never a text-only substitute."""
    from PIL import Image, ImageOps
    try:
        if not isinstance(raw, bytes) or not raw or len(raw) > 100 * 1024 * 1024:
            raise ValueError('Invalid image bytes')
        with Image.open(BytesIO(raw)) as source:
            if max(source.size) > 8192 or source.width * source.height > 32_000_000:
                raise ValueError('Image dimensions exceed the vision budget')
            image = ImageOps.exif_transpose(source)
            if alpha_matte:
                # Hidden RGB in transparent pixels is not visible content. Show a flat matte, never a checkerboard scene.
                image = Image.alpha_composite(Image.new('RGBA', image.size, (127, 127, 127, 255)), image.convert('RGBA'))
            image = image.convert('RGB')
            image.thumbnail((1024, 1024), Image.Resampling.NEAREST if mask else Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, 'PNG')
        return base64.b64encode(output.getvalue()).decode('ascii')
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise ValueError(ui_text('image.prompt_manager_source_invalid')) from error


class PromptEnhancer(PromptEnhancerTemplate):
    SECTION = 'Image'
    OWNER = 'image-prompt'
    PLANNED_OPERATION = 'ImagePromptEnhancement'
    OPERATION = PLANNED_OPERATION
    OUTPUT_FIELD = 'prompt'
    VERBATIM_FIELDS = ('lettering', 'negative_prompt')
    CONTEXT_FIELDS = ('images_in_order', 'source_alpha', 'lettering', 'negative_prompt', 'sampling_profile', 'canvas', 'layout_boxes', 'layout_plan', 'control_type', 'detail_strength')
    ACTIVE_GUIDES = {zimage.ENGINE: zimage, klein.ENGINE: klein, qwen21.ENGINE: qwen21, qwen2511.ENGINE: qwen2511}
    MODEL_RULES = {
        qwen2511.ENGINE: qwen2511.MODEL_RULES,
        qwen21.ENGINE: qwen21.MODEL_RULES,
        'z-image-turbo-int8': zimage.MODEL_RULES,
        klein.ENGINE: klein.MODEL_RULES,
    }
    MODE_RULES = {
        qwen2511.ENGINE: qwen2511.MODE_RULES,
        qwen21.ENGINE: qwen21.MODE_RULES,
        'z-image-turbo-int8': zimage.MODE_RULES,
        klein.ENGINE: klein.MODE_RULES,
    }

    def __init__(self, text, gpu, submission_lock=None):
        super().__init__(text, gpu, submission_lock)
        self.clarifications = Clarifications()

    def supported(self, engine, mode):
        return isinstance(engine, str) and isinstance(mode, str) and engine in self.ACTIVE_GUIDES and mode in self.MODE_RULES[engine]

    def status(self):
        return dict(GemmaTask.status(self), state='active', engines={engine:list(guide.MODE_RULES) for engine, guide in self.ACTIVE_GUIDES.items()})

    def guide(self, engine, mode):
        result = super().guide(engine, mode)
        if self.supported(engine, mode):
            result.update(state='active', version=self.ACTIVE_GUIDES[engine].VERSION)
        return result

    def _messages(self, prompt, engine, mode, images, context):
        if not self.supported(engine, mode):
            raise ValueError(ui_text('image.prompt_manager_unsupported'))
        context = dict(context or {})
        draft = self.draft(prompt, engine, mode, context)
        expected = ((0,) if mode in ENGINES[engine].text_modes else (2,) if mode in ('reference', 'inpaint') else
                    range(3, ENGINES[engine].max_reference_images + 1) if mode == 'multi' else (1,))
        if not isinstance(images, (tuple, list)) or len(images) not in expected:
            raise ValueError(ui_text('image.prompt_manager_source_invalid'))
        if engine == klein.ENGINE and mode == 'layout':
            _, rows, _ = resolve_flux_options(ENGINES[engine], mode, {'layout_boxes':context.get('layout_boxes')})
            context.update(layout_boxes=rows, layout_plan=layout_brief(rows))
            draft = self.draft(prompt, engine, mode, context)
        pictures = [vision_image(raw, mask=mode == 'inpaint' and index == 1, alpha_matte=engine == qwen21.ENGINE)
                    for index, raw in enumerate(images)]
        if engine == qwen21.ENGINE:
            context['source_alpha'] = qwen21.source_alpha(images)
            draft = self.draft(prompt, engine, mode, context)
        messages = draft['messages']
        if engine == qwen2511.ENGINE:
            profile = context.get('sampling_profile', 'standard')
            if not isinstance(profile, str) or profile not in qwen2511.PROFILE_RULES:
                raise ValueError(ui_text('image.qwen_quality_selection_error'))
            messages[0]['content'] += '\n' + qwen2511.PROFILE_RULES[profile]
        if mode == 'control':
            control_type = (context or {}).get('control_type')
            if not isinstance(control_type, str) or control_type not in zimage.CONTROL_RULES:
                raise ValueError(ui_text('image.control_unknown_map_type'))
            messages[0]['content'] += '\n' + zimage.CONTROL_RULES[control_type]
        if pictures:
            messages[1]['images'] = pictures
        return messages, len(pictures)

    @staticmethod
    def _caption(answer, prompt, engine, mode, pictures, context):
        context = context or {}
        literals = [prompt, *context.get('clarification_choices', [])]
        if engine == klein.ENGINE and mode == 'layout':
            literals.extend(row['desc'] for row in context['layout_boxes'])
        result = caption(answer, '\n'.join(literals), bool(pictures))
        if result is not None and engine == qwen2511.ENGINE and not qwen2511.valid_references(QUOTES.sub('', result), pictures):
            return None
        if result is not None and engine == klein.ENGINE:
            prose = QUOTES.sub('', result)
            tags = re.findall(r'<([^<>]+)>', prose)
            ids = [row.get('id', 'obj_' + str(index + 1)) for index, row in enumerate(context['layout_boxes'])] if mode == 'layout' else []
            # The authoritative table is appended by code, once. Gemma may cite each ID once, never replace the table.
            if sorted(tags) != sorted(ids) or re.search(r'\[\s*\{', prose):
                return None
        if result is not None and engine == qwen21.ENGINE:
            tags = set(re.findall(r'<([^<>]+)>', QUOTES.sub('', result)))
            expected = {'image' + str(index + 1) for index in range(pictures)} if pictures > 1 else set()
            # Multi-image edits need every real tag; a single-image edit uses no tags in the publisher contract.
            if tags != expected:
                return None
            result = qwen21.rgba_prompt(result, '\n'.join(literals))
            if len(result) > 5000:
                return None
        return result

    @staticmethod
    def _context_size(prompt, pictures):
        # Ten reference thumbnails need a larger context than the original one-source Z-Image path.
        return max(10240 if len(prompt) > 2500 or pictures > 1 else 6144, 4096 + 1536 * pictures)

    def prepare(self, prompt, engine, mode, images=(), context=None, *, binding=None, token=None, answer=None, language='Russian'):
        """Interactive Image path. Waiting questions retain bounded CPU state, never an active Text/GPU session."""
        if not self.supported(engine, mode):
            raise ValueError(ui_text('image.prompt_manager_unsupported'))
        guide = self.ACTIVE_GUIDES[engine]
        if language not in ('Russian', 'English'):
            raise ValueError('Invalid clarification language')
        identity = fingerprint(prompt, engine, mode, context, binding, images)
        if token is not None:
            with self.clarifications.answer(token, identity, answer) as decision:
                if decision['state'] == 'clarification':
                    return decision
                agreed = [option['value'] for option in decision['choices']]
                result = self.enhance(prompt, engine, mode, images, {**(context or {}), 'clarification_choices':agreed})
                result['clarification'] = dict(decision['clarification'], version=guide.CLARIFICATION_VERSION)
                return result
        if answer is not None:
            raise ValueError(ui_text('image.clarification_session_expired'))
        messages, pictures = self._messages(prompt, engine, mode, images, context)
        messages[0]['content'] += guide.CLARIFICATION_CONTRACT + '\n' + guide.CLARIFICATION_RULES[mode] + '\nUI language: ' + language
        with self.session() as cancel:
            for attempt in range(2):
                if cancel.is_set():
                    raise ValueError(ui_text('image.prompt_manager_cancelled'))
                raw = self.ask(messages, cancel, num_ctx=self._context_size(prompt, pictures),
                               num_predict=1800, temperature=guide.DECISION_TEMPERATURE, top_p=0.9)
                if cancel.is_set():
                    raise ValueError(ui_text('image.prompt_manager_cancelled'))
                try:
                    value = json.loads(re.sub(r'^```[^\n]*\n|\n```$', '', raw.strip()))
                    if isinstance(value, dict) and value.get('state') == 'ready':
                        result = self._caption(value.get('prompt', ''), prompt, engine, mode, pictures, context)
                        if result:
                            return dict(prompt=result, model=self.text.gemma_model(), guide_version=guide.VERSION)
                    elif isinstance(value, dict) and value.get('state') == 'clarification':
                        proposal = question(value)
                        if any(CYRILLIC.search(QUOTES.sub('', option['value'])) for option in proposal['options']):
                            raise ValueError('Option instructions must be English')
                        return self.clarifications.start(identity, proposal)
                except (ValueError, TypeError, AttributeError):
                    pass
                if attempt == 0:
                    messages[0]['content'] += '\nPrevious output was invalid. Return the exact JSON contract with a valid caption or 2-8 distinct choices.'
        raise ValueError(ui_text('image.prompt_manager_invalid_answer'))

    def enhance(self, prompt, engine, mode, images=(), context=None):
        messages, pictures = self._messages(prompt, engine, mode, images, context)
        agreed = (context or {}).get('clarification_choices', [])
        if agreed:
            messages[0]['content'] += ('\nThe person selected clarification_choices in the context. Apply these latest preferences, preserving '
                                       'unrelated original requirements. Omit unselected alternatives. This is the final caption step: never ask questions.')
        with self.session() as cancel:
            for attempt in range(2):
                if cancel.is_set():
                    raise ValueError(ui_text('image.prompt_manager_cancelled'))
                answer = self.ask(messages, cancel, num_ctx=self._context_size(prompt, pictures),
                                  num_predict=1800, temperature=self.ACTIVE_GUIDES[engine].TEMPERATURE, top_p=0.9)
                if cancel.is_set():
                    raise ValueError(ui_text('image.prompt_manager_cancelled'))
                result = self._caption(answer, prompt, engine, mode, pictures, context)
                if result is not None:
                    return dict(prompt=result, model=self.text.gemma_model(), guide_version=self.ACTIVE_GUIDES[engine].VERSION)
                if attempt == 0:
                    # Keep the original request/images in the bounded context instead of appending another long, invalid answer.
                    messages[0]['content'] += (
                        '\nThe previous answer failed validation. Rewrite the final caption only, in English. '
                        'Keep every desired final quoted word and uppercase label exactly as supplied, '
                        'including Russian, inside straight double quotes. No Russian prose outside literal writing, no JSON or commentary. '
                        'Use 30-5000 characters and preserve the original scene and mode constraints. '
                        'For Layout include every supplied <id> exactly once, no other bracket tokens or JSON table. '
                        'For Qwen 2.1 with multiple images cite each real <imageN>; with zero or one image use no tags. '
                        'For Edit 2511 use Picture 1 through the actual input count, never <imageN>; no Picture numbers in Prompt mode.')
        raise ValueError(ui_text('image.prompt_manager_invalid_answer'))
