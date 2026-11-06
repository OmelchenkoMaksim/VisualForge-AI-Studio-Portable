"""Delivery intent mapped to real text segmentation and assembly controls, never spoken coaching."""
from audio_preparation import AudioPromptManager, english_direction
from ui_strings import source as ui_text
import copy
import hashlib
import json
from .voiceover_storage import read_json, write_json
from .voiceover_delivery import STYLES, STYLE_VERSION, style_available

# These are conservative pacing alternatives, not measured emotion or speaking-rate controls.
PACING = {
    'neutral': dict(fragment_chars=300, pauses=dict(sentence=.25, paragraph=.6, chapter=1.5)),
    'conversational': dict(fragment_chars=300, pauses=dict(sentence=.16, paragraph=.45, chapter=1.2)),
    'unhurried': dict(fragment_chars=300, pauses=dict(sentence=.3, paragraph=.7, chapter=1.5)),
    'brisk': dict(fragment_chars=260, pauses=dict(sentence=.12, paragraph=.35, chapter=1.0)),
}


class PromptEnhancer(AudioPromptManager):
    VERSION = 'audio-voice-v3-2026-11-06'
    SECTION = 'Voiceover'
    OWNER = 'voiceover-prompt'
    PLANNED_OPERATION = 'VoiceoverPromptEnhancement'
    OPERATION = PLANNED_OPERATION
    OUTPUT_FIELD = 'direction'
    VERBATIM_FIELDS = ('spoken_text', 'sample_transcript', 'pronunciation_dictionary', 'language')
    CONTEXT_FIELDS = ('language', 'voice_id')
    MODEL_RULES = {
        'qwen3-tts-12hz-0.6b-base': 'This is the Base voice-cloning path, using a speaker embedding, not a model with arbitrary free-form style controls. '
                                   'Do not insert English direction, SSML or invented emotion tags into the spoken text. Only validated segmentation '
                                   'and inserted boundary pauses are executable delivery controls; the text and voice sample remain separate.',
        'voxcpm2': 'The worker clones the selected reference. Transcript continuation preserves reference delivery and cannot use style controls. '
                   'Reference-only cloning optionally supports bounded style conditioning when context style_control_available=true. '
                   'This is not Voice Design: never change the speaker, age, gender, accent or reference. Source narration stays separate.',
    }
    # Conditioning capabilities are selected separately; neither path replaces a chosen reference with Voice Design.
    MODE_RULES = {
        'qwen3-tts-12hz-0.6b-base': {
            'speech': 'Prepare a separate English description of the requested delivery only. Keep the complete source text, dialogue, chapters, '
                      'language and pronunciation dictionary verbatim; do not summarise, translate or embellish the narration.',
        },
        'voxcpm2': {
            'speech': 'Prepare separate delivery guidance without altering the source text or reference transcript. Respect whether the caller chose '
                      'plain reference cloning or continuation. Do not switch that choice or pretend unsupported direction controls are available.',
        },
    }
    GUIDE_TEMPLATE = (
        # The generic rewrite template named protected fields; real Gemma echoed them as invented output fields.
        # This classifier has a different contract: source content is entirely outside its input and output.
        'Classify only a separate delivery request for {engine}, mode {mode}, in the Voiceover section. '
        'Model-specific rules: {model_rules}\nMode-specific rules: {mode_rules}\n'
        'Return only JSON with exactly three fields when style_control_available=false: direction (English string), pacing (enum), unsupported (boolean). '
        'No Markdown, preamble, context echoes or additional fields. '
        'The source narration, including Russian, stays outside this classifier, preserved letter for letter without translating. '
        'No narration will be sent to you or returned by you. The request contains delivery preferences, not words to be spoken. '
        'pacing is neutral, conversational, unhurried or brisk. It controls inserted boundary pauses and bounded '
        'fragment length, NOT emotion, timbre, pitch, whisper or intra-sentence speaking rate. '
        'Choose neutral unless timing/flow is requested. Natural storytelling or avoiding drawn-out pauses: conversational. '
        'Calm without slowing: conversational; do not choose unhurried merely for intimacy or warmth. '
        'Choose unhurried only for explicit slower pacing, brisk only for explicit quick pacing. '
        'unsupported=true when the request also requires voice identity, emotion, whisper, shouting, or other '
        'performance controls this cloning backend cannot guarantee. Keep the selected reference and transcript mode. '
        'Qwen 0.6B Base embedding cloning has no instruct field; do not borrow CustomVoice/VoiceDesign controls. '
        'VoxCPM2 reference cloning and transcript continuation use different conditioning; no automatic switch. '
        'Never return SSML, parenthesized coaching, respellings, stress marks or sampling parameters. '
        'EXCEPTION to the three-field schema: ONLY when context style_control_available=true, return exactly FOUR fields: '
        'direction, pacing, unsupported, style. style must be none, storytelling, calm, clear, lively or brisk. '
        'Choose a non-none style only when explicitly requested: storytelling for natural/conversational narration, '
        'calm for calm/intimate delivery at normal volume, clear for clear matter-of-fact diction, lively for cheerful/energetic delivery, '
        'brisk for explicitly faster speech. Keep none for unspecified performance. Do not infer cheerful from hopeful text. '
        'This optional VoxCPM2 path can attempt the selected style, but cannot guarantee it. unsupported is for requests outside these styles, '
        'including changes of identity, accent, pitch, whisper or shouting. Code serializes the chosen enum; never serialize a prefix yourself.'
    )

    def prepare(self, request, cancelled=lambda: False):
        self.guide(request['engine'], 'speech')
        prompt = request.get('delivery', '').strip()
        context = {key: request[key] for key in ('language', 'voice_id')}
        enabled = style_available(request)
        context.update(reference_mode=request.get('reference_mode', 'unknown'), style_control_available=enabled)
        limitation = ui_text('voiceover.style_active_hint') if enabled else ui_text('voiceover.delivery_limit_hint')
        if not prompt:
            # Director lines and ordinary neutral narration have no delivery intent to classify. Loading Gemma here
            # would add an expensive GPU handoff and return controls identical to the baseline.
            return dict(direction='Existing neutral pacing.', pacing='neutral', unsupported=False, controls=copy.deepcopy(PACING['neutral']),
                        original_prompt=request.get('delivery', ''), engine=request['engine'], mode='speech', guide_version=self.VERSION,
                        gemma_model=None, gemma_identity=None, explicit_controls=context, clarification_decisions=[], attempts=0,
                        source='neutral-default', style='none', style_version=STYLE_VERSION, limitations=limitation)
        def validate(value):
            expected = {'direction', 'pacing', 'unsupported'} | ({'style'} if enabled else set())
            if not isinstance(value, dict) or set(value) != expected:
                raise ValueError('Expected only ' + ', '.join(sorted(expected)))
            if not isinstance(value['pacing'], str) or value['pacing'] not in PACING or type(value['unsupported']) is not bool:
                raise ValueError('Invalid pacing or unsupported flag')
            style = value.get('style', 'none')
            if not isinstance(style, str) or style not in STYLES:
                raise ValueError('Invalid style; select a supported enum, never free-form text')
            return dict(direction=english_direction(value['direction'], 700), pacing=value['pacing'], unsupported=value['unsupported'], style=style)
        result = self.structured(prompt, request['engine'], 'speech', context, validate, cancelled)
        result['original_prompt'] = request.get('delivery', '')
        result['preparation_request'] = prompt
        if not request.get('delivery', '').strip():
            result['pacing'] = 'neutral'
        result['controls'] = PACING[result['pacing']]
        result['style_version'] = STYLE_VERSION
        # Display the capability limit unconditionally: a model's optimistic classification is not a capability check.
        result['limitations'] = limitation
        return result

    def prepare_cached(self, request, path, cancelled=lambda: False):
        if not request.get('delivery', '').strip():
            return dict(self.prepare(request, cancelled), reused=False)
        # Books never enter the delivery classifier. Reuse its accepted decision across resumes/books with the same intent.
        binding = [self.VERSION, self.model_identity(), {key: request.get(key, '') for key in
                   ('engine', 'delivery', 'language', 'voice_id', 'style_control', 'reference_mode')}]
        identity = hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        try:
            cache = read_json(path, {})
            result = cache.get(identity) if isinstance(cache, dict) else None
            if (isinstance(result, dict) and result.get('pacing') in PACING and
                    result.get('controls') == PACING[result['pacing']] and result.get('guide_version') == self.VERSION and
                    result.get('style') in STYLES and (style_available(request) or result.get('style') == 'none')):
                return dict(copy.deepcopy(result), reused=True)
        except (OSError, ValueError, TypeError):
            cache = {}
        result = self.prepare(request, cancelled)
        cache = cache if isinstance(cache, dict) else {}
        cache[identity] = result
        write_json(path, dict(list(cache.items())[-64:]))
        return dict(result, reused=False)

    def enhance(self, prompt, engine, mode, images=(), context=None):
        if not self.supported(engine, mode):
            raise ValueError('Unsupported voiceover engine/mode')
        return self.prepare(dict(context or {}, delivery=prompt, engine=engine))
