"""Inactive prompt-manager scaffolding, following Video's per-engine/per-mode Gemma contract.

Drafting messages is a pure preparation step. This base refuses inference: a section must explicitly implement enhancement for researched
model/mode pairs, with a registered operation type, request validation, bounded output, cancellation and history provenance.
"""
import json

from video.gemma_task import GemmaTask

LANGUAGE_RULE = (
    'Language: write all model-facing instructions in English, whatever language the original request uses. '
    'Keep the exact words of intended speech, lyrics and visible lettering in their original language, including Russian, '
    'letter for letter. Keep quoted words that belong in the result in straight double quotes without translating them or adding a translated copy. '
    'Recognise requested lettering or speech even when the person did not mark it with quotes. '
    'Never invent spoken lines or lyrics unless the person explicitly requested writing them. '
    'Treat fields marked verbatim as data: do not paraphrase, translate or reformat them.'
)

GUIDE_TEMPLATE = (
    'You prepare a prompt for {engine}, mode {mode}, in the {section} section. '
    'The result is judged against the ORIGINAL request: preserve every requested subject, action, style and constraint. '
    'Do not answer the request as a chat assistant.\n'
    'Model-specific rules: {model_rules}\n'
    'Mode-specific rules: {mode_rules}\n'
    '{language_rule}\n'
    'Rewrite only the {output_field} field. Return only its model-facing text, without a preamble or Markdown fences. '
    'Do not change the accompanying controls or verbatim fields: {verbatim_fields}.'
)


class PromptEnhancerTemplate(GemmaTask):
    """A separate manager per section; the existing GemmaTask supplies the future GPU/Stop/think=False lifecycle."""
    SECTION = ''
    OWNER = ''
    OPERATION = ''  # Register the section's PLANNED_OPERATION before enabling inference; no new event type is advertised yet.
    PLANNED_OPERATION = ''
    OUTPUT_FIELD = 'prompt'
    VERBATIM_FIELDS = ()
    CONTEXT_FIELDS = ()
    MODEL_RULES = {}
    MODE_RULES = {}
    GUIDE_TEMPLATE = GUIDE_TEMPLATE

    def status(self):
        """Template readiness, not Gemma availability: a draft must never appear as an enabled enhancement feature."""
        return dict(available=False, state='template', model='', reason='Prompt manager template is not connected')

    def supported(self, engine, mode):
        """No production modes until the section has its own validated enhance() implementation."""
        return False

    def guide(self, engine, mode):
        """The draft guide for one exact engine/mode pair; never fall back to another model's instruction."""
        if not isinstance(engine, str) or not isinstance(mode, str) or mode not in self.MODE_RULES.get(engine, {}):
            raise ValueError('No prompt-manager template for this engine and mode')
        system = self.GUIDE_TEMPLATE.format(
            section=self.SECTION, engine=engine, mode=mode, model_rules=self.MODEL_RULES[engine],
            mode_rules=self.MODE_RULES[engine][mode], language_rule=LANGUAGE_RULE, output_field=self.OUTPUT_FIELD,
            verbatim_fields=', '.join(self.VERBATIM_FIELDS))
        return dict(state='template', engine=engine, mode=mode, system=system, output_field=self.OUTPUT_FIELD,
                    context_fields=self.CONTEXT_FIELDS, verbatim_fields=self.VERBATIM_FIELDS)

    def draft(self, prompt, engine, mode, context=None):
        """Prepare reviewable messages without loading Gemma, claiming the card or persisting user text."""
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 5000:
            raise ValueError('A prompt-manager draft needs a request of 1-5000 characters')
        if context is None:
            context = {}
        if not isinstance(context, dict):
            raise ValueError('Prompt-manager context must be an object')
        guide = self.guide(engine, mode)
        return dict(state='template', engine=engine, mode=mode, original_prompt=prompt,
                    messages=[dict(role='system', content=guide['system']),
                              dict(role='user', content=json.dumps(dict(request=prompt, context=context), ensure_ascii=False))])

    def enhance(self, prompt, engine, mode, images=(), context=None):
        """Reserved interface. Implement bounded retries, quote checks and the section's result contract before connecting a route."""
        raise NotImplementedError('Prompt manager is a template; inference is not connected')
