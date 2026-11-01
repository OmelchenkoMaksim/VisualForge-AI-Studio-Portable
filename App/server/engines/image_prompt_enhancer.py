"""Image prompt enhancer: researched Z-Image modes, with inactive drafts for the other engines."""
import base64
from io import BytesIO
import json
import re

from prompt_clarification import Clarifications, fingerprint, question
from prompt_template import PromptEnhancerTemplate
from ui_strings import source as ui_text
from video.gemma_task import GemmaTask
from . import zimage_prompts as zimage

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


def vision_image(raw, *, mask=False):
    """Bounded, oriented PNG for Gemma. A missing or unreadable source is an error, never a text-only substitute."""
    from PIL import Image, ImageOps
    try:
        if not isinstance(raw, bytes) or not raw or len(raw) > 100 * 1024 * 1024:
            raise ValueError('Invalid image bytes')
        with Image.open(BytesIO(raw)) as source:
            if max(source.size) > 8192 or source.width * source.height > 32_000_000:
                raise ValueError('Image dimensions exceed the vision budget')
            image = ImageOps.exif_transpose(source).convert('RGB')
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
    VERBATIM_FIELDS = ('lettering',)
    CONTEXT_FIELDS = ('images_in_order', 'lettering', 'negative_prompt', 'canvas', 'layout_boxes', 'control_type', 'detail_strength')
    MODEL_RULES = {
        'qwen-edit-2511': 'An instruction editor with up to three ordered images and RGB output. Prompt mode edits a neutral canvas; '
                          'it is not native text-to-image. Do not invent RGBA, hard pixel locks or negative-prompt support for CFG-1 Lightning.',
        'qwen-image-21-uc-fp8': 'A native generation/editing model with RGBA and up to ten ordered images. Keep explicit alpha and transparent-background '
                              'requests literal; do not turn transparency into a drawn checkerboard. The first reference determines proportions.',
        'z-image-turbo-int8': zimage.MODEL_RULES,
        'flux3-klein-int8': 'The distilled 16-step model supports instruction edits, ordered references and text layout. Do not transfer the Base '
                            'checkpoint settings to it. Box guidance is probabilistic; editing follows the first image proportions. Output is RGB.',
    }
    MODE_RULES = {
        'qwen-edit-2511': {
            'single': 'State the requested change to Image 1 and what must stay as shown; do not replace the request with a caption of a new scene.',
            'reference': 'Image 1 is the source and Image 2 is the reference. Name the attributes to transfer and the source details to preserve.',
            'prompt': 'Describe the complete desired scene as an instruction for the supplied neutral canvas; ignore unrelated selected uploads.',
            'multi': 'Keep the roles of Images 1, 2 and 3 distinct and their order unchanged. State which subject or property comes from each.',
        },
        'qwen-image-21-uc-fp8': {
            'single': 'Edit <image1> by the requested instruction. Preserve explicit output-alpha requirements and do not promise exact segmentation.',
            'reference': 'Use <image1> as the source and <image2> in its stated reference role. Keep the source composition unless asked to change it.',
            'prompt': 'Write one complete visual description: subject, composition, action, setting, light and style. Include an explicit RGBA request when supplied.',
            'multi': 'Address ordered references as <image1>, <image2> and so on, up to ten. Do not exchange their roles or invent absent references.',
        },
        'z-image-turbo-int8': zimage.MODE_RULES,
        'flux3-klein-int8': {
            'single': 'Write a focused instruction for Image 1: the change and the details to keep. Do not recaption away an explicit edit boundary.',
            'reference': 'Distinguish the source in Image 1 from the subject or attributes in Image 2; preserve their assigned roles.',
            'prompt': 'Write a full still-image caption, preserving the requested camera, framing, subject count, light and lettering.',
            'multi': 'Describe the roles of all ordered references, up to ten. Keep identities and sources distinct instead of merging them implicitly.',
            'layout': 'Use the exact supplied object IDs and their requested properties. The validated box table stays separate and unchanged; '
                      'do not invent positions that contradict it or duplicate objects.',
        },
    }

    def __init__(self, text, gpu, submission_lock=None):
        super().__init__(text, gpu, submission_lock)
        self.clarifications = Clarifications()

    def supported(self, engine, mode):
        return isinstance(engine, str) and isinstance(mode, str) and engine == zimage.ENGINE and mode in zimage.MODE_RULES

    def status(self):
        return dict(GemmaTask.status(self), state='active', engines={zimage.ENGINE: list(zimage.MODE_RULES)})

    def guide(self, engine, mode):
        result = super().guide(engine, mode)
        if self.supported(engine, mode):
            result.update(state='active', version=zimage.VERSION)
        return result

    def _messages(self, prompt, engine, mode, images, context):
        if not self.supported(engine, mode):
            raise ValueError(ui_text('image.prompt_manager_unsupported'))
        draft = self.draft(prompt, engine, mode, context)
        expected = 0 if mode == 'prompt' else 2 if mode == 'inpaint' else 1
        if not isinstance(images, (tuple, list)) or len(images) != expected:
            raise ValueError(ui_text('image.prompt_manager_source_invalid'))
        pictures = [vision_image(raw, mask=mode == 'inpaint' and index == 1) for index, raw in enumerate(images)]
        messages = draft['messages']
        if mode == 'control':
            control_type = (context or {}).get('control_type')
            if not isinstance(control_type, str) or control_type not in zimage.CONTROL_RULES:
                raise ValueError(ui_text('image.control_unknown_map_type'))
            messages[0]['content'] += '\n' + zimage.CONTROL_RULES[control_type]
        if pictures:
            messages[1]['images'] = pictures
        return messages, len(pictures)

    def prepare(self, prompt, engine, mode, images=(), context=None, *, binding=None, token=None, answer=None, language='Russian'):
        """Interactive Image path. Waiting questions retain bounded CPU state, never an active Text/GPU session."""
        if not self.supported(engine, mode):
            raise ValueError(ui_text('image.prompt_manager_unsupported'))
        if language not in ('Russian', 'English'):
            raise ValueError('Invalid clarification language')
        identity = fingerprint(prompt, engine, mode, context, binding, images)
        if token is not None:
            with self.clarifications.answer(token, identity, answer) as decision:
                if decision['state'] == 'clarification':
                    return decision
                agreed = [option['value'] for option in decision['choices']]
                result = self.enhance(prompt, engine, mode, images, {**(context or {}), 'clarification_choices':agreed})
                result['clarification'] = dict(decision['clarification'], version=zimage.CLARIFICATION_VERSION)
                return result
        if answer is not None:
            raise ValueError(ui_text('image.clarification_session_expired'))
        messages, pictures = self._messages(prompt, engine, mode, images, context)
        messages[0]['content'] += zimage.CLARIFICATION_CONTRACT + '\n' + zimage.CLARIFICATION_RULES[mode] + '\nUI language: ' + language
        with self.session() as cancel:
            for attempt in range(2):
                if cancel.is_set():
                    raise ValueError(ui_text('image.prompt_manager_cancelled'))
                raw = self.ask(messages, cancel, num_ctx=10240 if len(prompt) > 2500 or pictures > 1 else 6144,
                               num_predict=1800, temperature=0.3, top_p=0.9)
                if cancel.is_set():
                    raise ValueError(ui_text('image.prompt_manager_cancelled'))
                try:
                    value = json.loads(re.sub(r'^```[^\n]*\n|\n```$', '', raw.strip()))
                    if isinstance(value, dict) and value.get('state') == 'ready':
                        result = caption(value.get('prompt', ''), prompt, bool(pictures))
                        if result:
                            return dict(prompt=result, model=self.text.gemma_model(), guide_version=zimage.VERSION)
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
        literal_request = prompt + '\n' + '\n'.join(agreed)
        if agreed:
            messages[0]['content'] += ('\nThe person selected clarification_choices in the context. Apply these latest preferences, preserving '
                                       'unrelated original requirements. Omit unselected alternatives. This is the final caption step: never ask questions.')
        with self.session() as cancel:
            for attempt in range(2):
                if cancel.is_set():
                    raise ValueError(ui_text('image.prompt_manager_cancelled'))
                answer = self.ask(messages, cancel, num_ctx=10240 if len(prompt) > 2500 or pictures > 1 else 6144,
                                  num_predict=1800, temperature=0.4, top_p=0.9)
                if cancel.is_set():
                    raise ValueError(ui_text('image.prompt_manager_cancelled'))
                result = caption(answer, literal_request, bool(pictures))
                if result is not None:
                    return dict(prompt=result, model=self.text.gemma_model(), guide_version=zimage.VERSION)
                if attempt == 0:
                    # Keep the original request/images in the bounded context instead of appending another long, invalid answer.
                    messages[0]['content'] += (
                        '\nThe previous answer failed validation. Rewrite the final caption only, in English. '
                        'Keep every desired final quoted word and uppercase label exactly as supplied, '
                        'including Russian, inside straight double quotes. No Russian prose outside literal writing, no JSON or commentary. '
                        'Use 30-5000 characters and preserve the original scene and mode constraints.')
        raise ValueError(ui_text('image.prompt_manager_invalid_answer'))

# FLUX/Qwen remain drafts: their native upsamplers must not silently rewrite Gemma output a second time.
