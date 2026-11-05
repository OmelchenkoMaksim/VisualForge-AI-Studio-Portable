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
from . import fibo2_prompts as fibo2
from .flux_inputs import layout_brief, resolve_flux_options
from .image_registry import ENGINES

QUOTES = re.compile(r'"([^"]+)"|«([^»]+)»|“([^”]+)”')
CYRILLIC = re.compile(r'[\u0400-\u04ff]')


LETTERING_NOUN = r'(?:надпис[а-яё]*|текст[а-яё]*|слов[а-яё]*|подпис[а-яё]*|lettering|text|words?|writing|labels?|captions?|signs?)'
# Only these may sit between "without" and the quote for the quote to be the thing that is left out.
WITHOUT_LETTERING = re.compile(r'\s*(?:(?:the|this|that|any|all|old|эт[а-яё]+|стар[а-яё]+|люб[а-яё]+|всю?|весь)\s+)*(?:' + LETTERING_NOUN + r')?\s*[:,-]?\s*', re.I)


def obsolete_text(prefix):
    """Recognize explicit removal/replacement clauses so the quote guard does not force old lettering back into an edit."""
    clause = re.split(r'[.!?;\n]', prefix)[-1]
    replacement = re.search(r'\b(?:замени(?:те)?|заменить|replace)\b(.*)$', clause, re.I)
    if replacement and not re.search(r'\b(?:на|with)\b', replacement[1], re.I):
        return True
    # "without" governs only the phrase right after it. In "a cat without a hat with the text «LES»" the quote is wanted lettering;
    # treating any later quote as removed switched the exact-text guard off for it (Gemma could then change the words unnoticed).
    without = list(re.finditer(r'\b(?:без|without)\b', clause, re.I))
    if without and WITHOUT_LETTERING.fullmatch(clause[without[-1].end():]):
        return True
    removal = re.search(r'\b(?:удали(?:те)?|удалить|убери(?:те)?|убрать|сотри(?:те)?|стереть|remove|erase|delete)\b(.*)$', clause, re.I)
    return bool(removal and not re.search(r'\b(?:добавь(?:те)?|напиши(?:те)?|сохрани(?:те)?|поставь(?:те)?|помести(?:те)?|нарисуй(?:те)?|напечатай(?:те)?|'
                                          r'add|write|keep|put|place|draw|print|type)\b', removal[1], re.I))


def protected_words(request):
    """Protect explicit quotations and unquoted uppercase lettering; free-form lettering also has a system instruction."""
    words = [next(part for part in match.groups() if part is not None) for match in QUOTES.finditer(request)
             if not obsolete_text(request[:match.start()])]
    # Without delimiters we can recognize an uppercase label, but cannot reliably parse arbitrary prose as an exact text span.
    # The lookbehind keeps the cue words from matching inside longer words ("bread LOAF", "rewrite SIGN", "контекст ЛЕС").
    labels = re.finditer(r'(?<![A-Za-zА-Яа-яЁё])(?i:надпис[ьиью]+|напиши(?:те)?|написано|текст(?:ом)?|слово(?:м)?|lettering|text|write|reads?)\s*:?\s*'
                         r'([А-ЯЁA-Z0-9][А-ЯЁA-Z0-9 \t-]*[А-ЯЁA-Z0-9])(?=$|[^а-яёa-zА-ЯЁA-Z0-9])', request)
    words += [match[1] for match in labels if not obsolete_text(request[:match.start(1)])]
    return tuple(dict.fromkeys(words))


def caption(answer, request, has_source, *, allowed_lettering=()):
    """Reject unusable output and changed literal text instead of silently feeding it to the image model."""
    text = answer.strip()
    if text.startswith('```') and text.endswith('```'):
        text = re.sub(r'^```[^\n]*\n|\n```$', '', text).strip()
    text = re.sub(r'^(?:enhanced prompt|rewritten prompt|prompt)\s*:\s*', '', text, flags=re.I).strip()
    text = QUOTES.sub(lambda match: '"' + next(part for part in match.groups() if part is not None) + '"', text)
    # A bounded edit such as "Make the car red." is a complete instruction (Edit 2511 is told to keep simple edits short), so the floor is
    # only "not an empty or one-word reply"; the earlier 30-character floor refused it twice and failed the whole generation.
    if not 12 <= len(text) <= 5000 or len(text.split()) < 3 or text.startswith(('{', '[', '#', '```')) or '<think' in text.lower():
        return None
    if any(ord(char) < 32 and char not in '\n\r\t' for char in text):
        return None
    if any(word not in text for word in protected_words(request)):
        return None
    prose = text
    for match in reversed(list(QUOTES.finditer(text))):
        literal = next(part for part in match.groups() if part is not None)
        # A source picture can contain lettering absent from the typed request; its spelling is a vision responsibility.
        if CYRILLIC.search(literal) and not has_source and literal not in request and literal not in allowed_lettering:
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
            # The side limit equals image_convert's, so every picture the Image page accepts is accepted here. The earlier 32-megapixel cap refused
            # ordinary 45 MP camera photos (8192x5464) after they had passed the upload check, and the whole generation failed as "invalid source".
            if max(source.size) > 8192:
                raise ValueError('Image dimensions exceed the vision budget')
            # A JPEG decodes at a reduced scale (about 1/8 of the work); other formats ignore draft().
            source.draft('RGB', (2048, 2048))
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


READY_JSON = re.compile(r'\{\s*"state"\s*:\s*"ready"\s*,\s*"prompt"\s*:\s*"(.*)"\s*\}', re.S)


def parse_decision(raw):
    """Parse Gemma's JSON decision, recovering one known slip.

    The rules ask for requested lettering in straight double quotes, and a model often leaves those quotes unescaped inside the JSON
    string ({"prompt":"a sign saying "СНЕГ" ..."}). json.loads refuses that, and the single retry tends to repeat it, so a request with
    lettering failed outright. The ready caption is still checked by `caption()`; questions need a real structure and are never repaired.
    """
    text = re.sub(r'^```[^\n]*\n|\n```$', '', raw.strip())
    try:
        return json.loads(text)
    except ValueError:
        match = READY_JSON.search(text)
        if match is None:
            raise
        return dict(state='ready', prompt=match[1].replace('\\"', '"').replace('\\n', '\n'))


class ImagePreparationCancelled(ValueError):
    """The person stopped preparation; keep it distinct from an invalid model answer."""


class PromptEnhancer(PromptEnhancerTemplate):
    SECTION = 'Image'
    OWNER = 'image-prompt'
    PLANNED_OPERATION = 'ImagePromptEnhancement'
    OPERATION = PLANNED_OPERATION
    OUTPUT_FIELD = 'prompt'
    VERBATIM_FIELDS = ('lettering', 'negative_prompt')
    CONTEXT_FIELDS = ('images_in_order', 'source_alpha', 'lettering', 'negative_prompt', 'sampling_profile', 'canvas', 'layout_boxes', 'layout_plan', 'control_type', 'detail_strength')
    ACTIVE_GUIDES = {zimage.ENGINE: zimage, klein.ENGINE: klein, qwen21.ENGINE: qwen21, qwen2511.ENGINE: qwen2511, fibo2.ENGINE: fibo2}
    MODEL_RULES = {
        fibo2.ENGINE: fibo2.MODEL_RULES,
        qwen2511.ENGINE: qwen2511.MODEL_RULES,
        qwen21.ENGINE: qwen21.MODEL_RULES,
        'z-image-turbo-int8': zimage.MODEL_RULES,
        klein.ENGINE: klein.MODEL_RULES,
    }
    MODE_RULES = {
        fibo2.ENGINE: fibo2.MODE_RULES,
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
        if context.get('composition_intent'):
            # A real second variant lost "full body" in its plan and became a cropped portrait. Keep the human's
            # hard constraints available at the final rewrite rather than treating a lossy step draft as the whole request.
            messages[0]['content'] += (
                '\nThis stage completes the content of a Composition variant or preserves it during final texture finishing. '
                'composition_intent is the original human request: '
                'its hard constraints on framing (including full body), actions, pose, object placement, relationships, counts, subjects, identity, style, exclusions and lettering '
                'are authoritative even if this step draft omitted them. Preserve this variant\'s creative choices only when compatible. '
                'composition_variant identifies ONE separately generated image: never combine the requested variants or their different '
                'views into a collage or duplicate subjects. Keep this step draft\'s specific view while restoring shared scene requirements. '
                'When full body is required, frame both feet with clear space below the shoes and above the head; '
                'choose a wide enough view instead of cropping the figure for a closer portrait. '
                'composition_sources maps actual picture numbers to upload groups, their one-based group_index and labels. '
                'Use this map for references such as the first hero; group_index is not an attached-picture number. '
                'Refer only to the attached pictures in their actual order; prior material may already be in the composed first picture. '
                'Quoted upload group names identify sources, not visible lettering unless explicitly requested as writing.')
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
        if context.get('composition_lettering'):
            literals.append(context['composition_lettering'])
        if engine == klein.ENGINE and mode == 'layout':
            literals.extend(row['desc'] for row in context['layout_boxes'])
        result = caption(answer, '\n'.join(literals), bool(pictures))
        if result is not None and engine == fibo2.ENGINE and not fibo2.valid_references(result, pictures):
            return None
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
            # A real plan invented "alpha transparency on white" for an opaque white-background request.
            # Code-owned format cues must follow the human's intent, not reinforce that conflicting step draft.
            alpha_intent = context.get('composition_intent') or '\n'.join(literals)
            result = qwen21.rgba_prompt(result, alpha_intent)
            if len(result) > 5000:
                return None
        return result

    def _finish_prompt(self, candidate, prompt, engine, pictures, context, cancel):
        if engine != fibo2.ENGINE:
            return candidate
        # Keep the shared clarification/prose fidelity checks, then serialize once for FIBO. Feeding their
        # prose directly to this encoder would silently omit the structured scene and typography conditioning.
        context = context or {}
        literals = '\n'.join([prompt, *context.get('clarification_choices', []), context.get('composition_lettering', '')])
        required = protected_words(literals)
        conversion = [dict(role='system', content=fibo2.VGL_RULES),
                      dict(role='user', content=json.dumps(dict(approved_scene=candidate, context=context,
                           operation='edit' if pictures else 'generate', reference_count=pictures,
                           required_lettering=required), ensure_ascii=False))]
        # A real recolour conversion saw the source again and recaptioned its yellow jacket, undoing the
        # approved blue edit and omitting edit_instruction. Serialize the already grounded, checked intent
        # without images; keep that instruction in code so the format pass cannot rewrite the requested edit.
        for attempt in range(2):
            if cancel.is_set():
                raise ImagePreparationCancelled(ui_text('image.prompt_manager_cancelled'))
            raw = self.ask(conversion, cancel, num_ctx=max(12288, self._context_size(candidate, pictures)),
                           num_predict=3200, temperature=.1, top_p=.9)
            if cancel.is_set():
                raise ImagePreparationCancelled(ui_text('image.prompt_manager_cancelled'))
            result = fibo2.vgl_caption(raw, pictures, required, instruction=candidate if pictures else None)
            if result is not None:
                return result
            if not attempt:
                conversion[0]['content'] += (' The previous JSON failed validation. Include every required top-level key '
                    'and exact required_lettering in text_render; English descriptions only. For several inputs use '
                    'every actual <image_N> tag; for zero or one input use no tags. Do not write edit_instruction. '
                    'lighting and aesthetics MUST be nested objects, never plain strings. '
                    'Return a complete, concise object without extra keys or fences.')
        raise fibo2.InvalidVGL(ui_text('image.prompt_manager_invalid_answer'))

    @staticmethod
    def _context_size(prompt, pictures, extra=''):
        # Ten reference thumbnails need a larger context than the original one-source Z-Image path.
        base = max(10240 if len(prompt) + len(extra) > 2500 or pictures > 1 else 6144, 4096 + 1536 * pictures)
        # A final Composition pass also carries up to 5000 characters of original intent beside its step draft.
        return base + (len(extra) + 1) // 2

    @staticmethod
    def _choices_opening(candidate, choices):
        def normalize(text):
            return re.sub(r'[^\w]+', ' ', text).casefold().strip()
        return normalize(candidate).startswith(' '.join(normalize(choice) for choice in choices))

    def _apply_choices(self, candidate, prompt, engine, mode, pictures, context, cancel, budget):
        choices = context['clarification_choices']
        if self._choices_opening(candidate, choices):
            checked = self._caption(candidate, prompt, engine, mode, pictures, context)
            if checked is not None:
                return checked
        # Real choices were fragments ("blue jacket", "green apples"). Requiring the VLM to reproduce them as a
        # sentence opening rejected a faithful paraphrase twice. Keep every outcome in code and reconcile the body separately.
        prefix = ' '.join(choice if choice.endswith(('.', '!')) else choice + '.' for choice in choices)
        messages = [dict(role='system', content=
                         'Reconcile this English caption with ALL clarification_choices in context. They are agreed compatible outcomes, '
                         'possibly short attributes rather than sentences. Apply every one; remove contradictory clauses, including '
                         'preservation wording that would undo another selected edit. Keep unrelated original requirements and grounded details. '
                         'The supplied fixed_prefix will be prepended by code; write only a compatible, fluent body, without repeating that list. '
                         'Keep valid model-specific reference markers and exact requested lettering in its original language. '
                         'Do not add controls, JSON, questions or commentary. Return English caption prose of at most ' + str(5000 - len(prefix) - 1) + ' characters.'),
                    dict(role='user', content=json.dumps(dict(request=prompt, context=context, candidate=candidate, fixed_prefix=prefix), ensure_ascii=False))]
        for attempt in range(2):
            if cancel.is_set():
                raise ImagePreparationCancelled(ui_text('image.prompt_manager_cancelled'))
            body = self.ask(messages, cancel, num_ctx=budget, num_predict=1800, temperature=.1, top_p=.9)
            if cancel.is_set():
                raise ImagePreparationCancelled(ui_text('image.prompt_manager_cancelled'))
            combined = body if self._choices_opening(body, choices) else prefix + ' ' + body
            result = self._caption(combined, prompt, engine, mode, pictures, context)
            if result is not None:
                return result
            if not attempt:
                messages[0]['content'] += ' The combined caption failed validation. Preserve all required literal words and reference bindings, with no conflicting or repeated layout markers.'
        raise ValueError(ui_text('image.prompt_manager_invalid_answer'))

    @staticmethod
    def _wrong_ui_script(proposal, language):
        labels = ' '.join(QUOTES.sub('', text) for text in [proposal['question'], *[row['label'] for row in proposal['options']]])
        # A real Russian-UI vision call repeatedly returned Ukrainian. This narrow guard detects its distinctive letters,
        # not every possible wrong language; quoted source lettering is allowed in either UI language.
        return bool((re.search('[іІїЇєЄґҐ]', labels) or not CYRILLIC.search(QUOTES.sub('', proposal['question'])))
                    if language == 'Russian' else CYRILLIC.search(labels))

    def _localize_question(self, proposal, language, cancel):
        if not self._wrong_ui_script(proposal, language):
            return proposal
        original = [proposal['question'], *[row['label'] for row in proposal['options']]]
        # Repeating the vision decision reproduced the same wrong language. A text-only translation keeps the already
        # validated choice meanings/order/groups outside the model's editable fields and adds no user-facing question.
        messages = [dict(role='system', content='Write the question and labels in ' + language +
                         ' (ru-RU for Russian), including explanations in parentheses. Return only JSON '
                         '{"question":"translated question","labels":["translated label",...]}. Keep the same number and order of labels. '
                         'The read-only English instructions define the actual choices: make each label clearly describe its outcome, '
                         'including a chosen colour or other change. Make the question match these choices; do not ask for information '
                         'that none of the choices can supply. Do not alter, combine or invent outcomes. '
                         'The JSON string delimiters do not mark verbatim content. Only the supplied verbatim substrings must stay unchanged. '
                         'Return only the UI fields; add no choices or explanation.'),
                    dict(role='user', content=json.dumps(dict(question=original[0], labels=original[1:],
                         instructions=[row['value'] for row in proposal['options']],
                         verbatim=list(dict.fromkeys(word for text in original for word in protected_words(text)))), ensure_ascii=False))]
        for attempt in range(2):
            raw = self.ask(messages, cancel, num_ctx=4096, num_predict=900, temperature=0)
            if cancel.is_set():
                raise ImagePreparationCancelled(ui_text('image.prompt_manager_cancelled'))
            try:
                value = json.loads(re.sub(r'^```[^\n]*\n|\n```$', '', raw.strip()))
                if not isinstance(value, dict) or not isinstance(value.get('labels'), list) or len(value['labels']) != len(proposal['options']):
                    raise ValueError('Invalid translated clarification')
                translated = question(dict(question=value.get('question'), options=[dict(row, label=label)
                                      for row, label in zip(proposal['options'], value['labels'])]))
                final = [translated['question'], *[row['label'] for row in translated['options']]]
                if self._wrong_ui_script(translated, language) or any(word not in after for before, after in zip(original, final) for word in protected_words(before)):
                    raise ValueError('Invalid translated clarification language or lettering')
                return translated
            except (ValueError, TypeError):
                if attempt:
                    raise
                messages[0]['content'] += ' The previous translation failed validation. Translate ALL UI prose, including parentheses, and preserve only verbatim substrings.'

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
        messages[0]['content'] += (guide.CLARIFICATION_CONTRACT + '\n' + guide.CLARIFICATION_RULES[mode] +
                                  '\nUI language for this decision: English. Write the question and every label in English; '
                                  'a separate text-only step localizes these UI fields when needed. Keep original-language lettering only inside quotes. '
                                  'The question must be fully answerable using option numbers alone. Each value must specify a concrete final '
                                  'visual outcome. If asking both which object and what colour, offer numbered choices for BOTH properties, '
                                  'using separate exclusive groups. Each label must state the actual change in its value, including any chosen colour. '
                                  'Every option must explicitly include label, value and group; use an empty group only for compatible additions. '
                                  'Do not ask for information that none of the options can supply.')
        with self.session() as cancel:
            for attempt in range(2):
                if cancel.is_set():
                    raise ImagePreparationCancelled(ui_text('image.prompt_manager_cancelled'))
                raw = self.ask(messages, cancel, num_ctx=self._context_size(prompt, pictures),
                               num_predict=1800, temperature=guide.DECISION_TEMPERATURE, top_p=0.9)
                if cancel.is_set():
                    raise ImagePreparationCancelled(ui_text('image.prompt_manager_cancelled'))
                reason = 'Invalid caption or decision structure'
                try:
                    value = parse_decision(raw)
                    if isinstance(value, dict) and value.get('state') == 'ready':
                        result = self._caption(value.get('prompt', ''), prompt, engine, mode, pictures, context)
                        if result:
                            result = self._finish_prompt(result, prompt, engine, pictures, context, cancel)
                            return dict(prompt=result, model=self.text.gemma_model(), guide_version=guide.VERSION)
                    elif isinstance(value, dict) and value.get('state') == 'clarification':
                        proposal = question(value)
                        # Missing groups made alternative colours look like compatible additions, disabling the conflict-only second question.
                        if any('group' not in row for row in value['options']):
                            raise ValueError('Every option must explicitly include group: same nonempty ID for alternatives to one property, empty only for compatible additions')
                        if any(CYRILLIC.search(QUOTES.sub('', option['value'])) for option in proposal['options']):
                            raise ValueError('Option instructions must be English')
                        proposal = self._localize_question(proposal, language, cancel)
                        return self.clarifications.start(identity, proposal)
                except (ImagePreparationCancelled, fibo2.InvalidVGL):
                    raise
                except (ValueError, TypeError, AttributeError) as error:
                    reason = str(error)[:250]
                if attempt == 0:
                    messages[0]['content'] += ('\nPrevious output was invalid: ' + reason + '. Return the exact JSON contract with a valid caption or 2-8 distinct choices. '
                                               'The question, labels and option values must be English, apart from quoted lettering.')
        raise ValueError(ui_text('image.prompt_manager_invalid_answer'))

    def _review_composition(self, candidate, prompt, engine, mode, pictures, context, cancel, budget):
        """Text-only fidelity check: vision grounding must not override a requested action or physical relationship."""
        # A real vision rewrite kept "full body" but dropped "standing" and inherited the donor's seated mug-holding pose.
        # Remove the images from this check so the original request, rather than the donor pose, drives corrections.
        messages = [dict(role='system', content=
                         'Check this final English caption for ' + engine + ', mode ' + mode + '. '
                         'context.composition_intent is the original human request and is authoritative. '
                         # A real request for two separate teapot views became "two different views" in one final caption.
                         'context.composition_variant identifies ONE separately generated image. The original request may list several '
                         'variant views; keep only this step request\'s selected view, never a collage, split view or duplicated subject '
                         'unless explicitly requested WITHIN each individual image. '
                         'Restore every omitted action, pose, framing, count, object placement, relationship, style and exclusion. '
                         'Keep grounded appearance details from candidate when compatible; no images are attached in this check, so do not invent observations. '
                         'Heroes supply identity and appearance, not their original pose, action or held props unless requested. '
                         'A requested standing person must be standing upright; an object assigned to the table must stay there rather than in their hands. '
                         'For full-body framing explicitly keep both feet and the head inside the frame with some room around them. '
                         'Use context.composition_sources for actual input numbers; previous_composition already contains earlier material. '
                         'Keep the candidate\'s model-specific image markers and their bindings. Keep requested lettering exactly in its language. '
                         'Quoted upload group names are source labels, not words to draw. Do not change controls, add settings, JSON, questions or commentary. '
                         'Return only the corrected model-facing caption, in English, at most 5000 characters.'),
                    dict(role='user', content=json.dumps(dict(request=prompt, context=context, candidate=candidate), ensure_ascii=False))]
        for attempt in range(2):
            if cancel.is_set():
                raise ImagePreparationCancelled(ui_text('image.prompt_manager_cancelled'))
            answer = self.ask(messages, cancel, num_ctx=budget, num_predict=1800, temperature=.1, top_p=.9)
            if cancel.is_set():
                raise ImagePreparationCancelled(ui_text('image.prompt_manager_cancelled'))
            result = self._caption(answer, prompt, engine, mode, pictures, context)
            if result is not None:
                return result
            if not attempt:
                messages[0]['content'] += ' The previous caption failed language, reference-marker, length or literal-lettering validation. Keep the existing valid marker bindings and every requested final word.'
        raise ValueError(ui_text('image.prompt_manager_invalid_answer'))

    def enhance(self, prompt, engine, mode, images=(), context=None):
        messages, pictures = self._messages(prompt, engine, mode, images, context)
        intent = (context or {}).get('composition_intent', '')
        if (context or {}).get('composition_sources'):
            intent += json.dumps(context['composition_sources'], ensure_ascii=False)
        agreed = (context or {}).get('clarification_choices', [])
        if agreed:
            intent += json.dumps(agreed, ensure_ascii=False)
        budget = self._context_size(prompt, pictures, intent)
        if agreed:
            messages[0]['content'] += ('\nThe person selected clarification_choices in the context. Apply these latest preferences, preserving '
                                       'unrelated original requirements. Apply EVERY outcome in a fluent caption; short attributes may be combined '
                                       'grammatically. Omit unselected alternatives and contradictory preservation clauses. Never ask questions.')
        with self.session() as cancel:
            for attempt in range(2):
                if cancel.is_set():
                    raise ImagePreparationCancelled(ui_text('image.prompt_manager_cancelled'))
                answer = self.ask(messages, cancel, num_ctx=budget,
                                  num_predict=1800, temperature=self.ACTIVE_GUIDES[engine].TEMPERATURE, top_p=0.9)
                if cancel.is_set():
                    raise ImagePreparationCancelled(ui_text('image.prompt_manager_cancelled'))
                result = self._caption(answer, prompt, engine, mode, pictures, context)
                if result is not None:
                    if (context or {}).get('composition_intent'):
                        result = self._review_composition(result, prompt, engine, mode, pictures, context, cancel, budget)
                    if agreed:
                        result = self._apply_choices(result, prompt, engine, mode, pictures, context, cancel, budget)
                    result = self._finish_prompt(result, prompt, engine, pictures, context, cancel)
                    return dict(prompt=result, model=self.text.gemma_model(), guide_version=self.ACTIVE_GUIDES[engine].VERSION)
                if attempt == 0:
                    # Keep the original request/images in the bounded context instead of appending another long, invalid answer.
                    messages[0]['content'] += (
                        '\nThe previous answer failed validation. Rewrite the final caption only, in English. '
                        'Keep every desired final quoted word and uppercase label exactly as supplied, '
                        'including Russian, inside straight double quotes. No Russian prose outside literal writing, no JSON or commentary. '
                        'Use a full sentence of at most 5000 characters and preserve the original scene and mode constraints. '
                        'For Layout include every supplied <id> exactly once, no other bracket tokens or JSON table. '
                        'For Qwen 2.1 with multiple images cite each real <imageN>; with zero or one image use no tags. '
                        'For FIBO with multiple images cite each real <image_N>; with zero or one image use no tags. '
                        'For Edit 2511 use Picture 1 through the actual input count, never <imageN>; no Picture numbers in Prompt mode.')
        raise ValueError(ui_text('image.prompt_manager_invalid_answer'))
