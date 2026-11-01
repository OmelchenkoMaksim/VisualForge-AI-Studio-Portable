"""Voiceover prompt enhancer drafts for speech direction; no route or worker consumes them yet."""
from prompt_template import PromptEnhancerTemplate


class PromptEnhancer(PromptEnhancerTemplate):
    SECTION = 'Voiceover'
    OWNER = 'voiceover-prompt'
    PLANNED_OPERATION = 'VoiceoverPromptEnhancement'
    OUTPUT_FIELD = 'direction'
    VERBATIM_FIELDS = ('spoken_text', 'sample_transcript', 'pronunciation_dictionary', 'language')
    CONTEXT_FIELDS = ('spoken_text', 'sample_transcript', 'pronunciation_dictionary', 'language', 'voice_id')
    MODEL_RULES = {
        'qwen3-tts-12hz-0.6b-base': 'This is the Base voice-cloning path, using a speaker embedding, not a model with arbitrary free-form style controls. '
                                   'Do not insert English direction, SSML or invented emotion tags into the spoken text. A future adapter must decide '
                                   'which delivery suggestions can actually be used; the text and voice sample remain separate.',
        'voxcpm2': 'The current worker clones from a reference sample, optionally with its transcript as continuation. Do not change that transcript, '
                   'translate the spoken text or add direction to the words to be synthesized. Voice Design belongs to the separate built-in-voice '
                   'creation worker and is not an instruction field of an ordinary voiceover job.',
    }
    # Voiceover has one public synthesis flow per engine; no new style/design mode is advertised by these drafts.
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
