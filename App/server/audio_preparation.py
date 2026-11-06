"""Bounded audio preparation and inheritance of GPU admission into owned job threads."""
from contextlib import ExitStack
import hashlib
import json
import re
import threading

from generation_identity import scope
from prompt_template import PromptEnhancerTemplate
from video.gemma_task import GemmaTask
from ui_strings import source as ui_text


class AudioPreparationCancelled(ValueError):
    """Typed cancellation, including a Stop between admission and the first token."""


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


class AudioPromptManager(PromptEnhancerTemplate):
    VERSION = 'audio-v1-2026-11-06'

    def status(self):
        status = GemmaTask.status(self)
        if status['available'] and hasattr(self.text, 'installed_models'):
            usable = [row['name'] for row in self.text.installed_models() if row['ok'] and row.get('family', '').lower().startswith('gemma')]
            if status['model'] not in usable:
                return dict(available=False, model='', reason=ui_text('music.preparation_gemma_required'))
        return status

    def supported(self, engine, mode):
        return isinstance(engine, str) and isinstance(mode, str) and mode in self.MODE_RULES.get(engine, {})

    def model_identity(self, model=None):
        model = model or self.text.gemma_model()
        rows = self.text.installed_models() if hasattr(self.text, 'installed_models') else ()
        row = next((row for row in rows if row['name'] == model), {})
        return dict(name=model, manifest_sha256=row.get('manifest_sha256'), quantization=row.get('quantization'))

    def guide(self, engine, mode):
        result = super().guide(engine, mode)
        result.update(state='active', version=self.VERSION)
        return result

    def structured(self, prompt, engine, mode, context, validate, cancelled=lambda: False, system=None):
        messages = self.draft(prompt, engine, mode, context)['messages']
        if system is not None:
            messages[0]['content'] = system
        def check(cancel=None):
            if cancelled() or (cancel is not None and cancel.is_set()):
                raise AudioPreparationCancelled(ui_text('music.preparation_cancelled_label'))
        check()
        with self.session() as cancel:
            check(cancel)
            model = self.text.gemma_model()
            identity = self.model_identity(model)
            for attempt in range(2):
                try:
                    raw = self.ask(messages, cancel, model=model, temperature=0.15, num_ctx=8192, num_predict=1000)
                except Exception:
                    check(cancel)
                    raise
                check(cancel)
                try:
                    if len(raw) > 10000:
                        raise ValueError('Response too long')
                    # The configured local Gemma returned valid JSON inside one Markdown fence even after correction.
                    # Accept only that whole-response wrapper; prose, multiple objects and schema changes still fail.
                    fenced = re.fullmatch(r'\s*```(?:json)?[ \t]*\r?\n(.*?)\r?\n```\s*', raw, flags=re.S | re.I)
                    value = json.loads(fenced.group(1) if fenced else raw, object_pairs_hook=unique_fields)
                    result = validate(value)
                    return dict(result, original_prompt=prompt, engine=engine, mode=mode,
                                guide_version=self.VERSION, guide_sha256=hashlib.sha256(messages[0]['content'].encode('utf-8')).hexdigest(),
                                gemma_model=model, gemma_identity=identity,
                                explicit_controls=context, clarification_decisions=[], attempts=attempt + 1,
                                response_format='json-fence' if fenced else 'json')
                except (ValueError, TypeError, KeyError) as error:
                    if attempt:
                        raise ValueError(ui_text('music.preparation_invalid_error')) from None
                    # Correct only the invalid schema; never restart or reinterpret an approved intent.
                    messages += [dict(role='assistant', content=raw[:10000]),
                                 dict(role='user', content='Invalid result: ' + str(error)[:300] +
                                      '. Return the required JSON only. Preserve the original intent and all explicit controls.')]


def english_direction(value, limit):
    if not isinstance(value, str) or not 3 <= len(value.strip()) <= limit:
        raise ValueError('Invalid caption/direction length')
    if re.search(r'[\x00-\x08\x0b-\x1f]|[\u0400-\u04ff]|<[^>]+>|```', value):
        raise ValueError('Use English technical direction only, no tags or narration')
    return value.strip()


def job_thread(manager, row, directory):
    """Carry Director authority across a thread hop; busy jobs still exclude unrelated callers."""
    enhancer = getattr(manager, 'prompt_enhancer', None)
    actors = enhancer.gpu.current_actors() if enhancer else ()
    def run():
        with ExitStack() as stack:
            if enhancer:
                for actor in (*actors, enhancer.SECTION.lower()):
                    stack.enter_context(enhancer.gpu.acting_for(actor))
            stack.enter_context(scope(row['generation']))
            manager._run(row, directory)
    return threading.Thread(target=run, daemon=True)


def stop_preparation(manager):
    enhancer = getattr(manager, 'prompt_enhancer', None)
    if enhancer and enhancer.text is not None and manager.active and manager.active.get('preparing_prompt'):
        enhancer.text.stop_chat(enhancer.OWNER)
