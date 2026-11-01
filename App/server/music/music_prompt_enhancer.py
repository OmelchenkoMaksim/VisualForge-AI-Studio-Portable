"""Music prompt enhancer drafts: the future caption contract, not lyrics or ACE-Step's musical planner."""
from prompt_template import PromptEnhancerTemplate


class PromptEnhancer(PromptEnhancerTemplate):
    SECTION = 'Music'
    OWNER = 'music-prompt'
    PLANNED_OPERATION = 'MusicPromptEnhancement'
    OUTPUT_FIELD = 'caption'
    VERBATIM_FIELDS = ('lyrics', 'vocal_language')
    CONTEXT_FIELDS = ('lyrics', 'vocal_language', 'duration', 'bpm', 'instrumental', 'planner')
    MODEL_RULES = {
        'ace-step-15-turbo': 'Prepare the musical caption for ACE-Step 1.5 Turbo. Describe genre, instruments, mood, energy and arrangement. '
                             'Its optional 0.6B planner is a separate stage, not this Gemma manager. Preserve the selected tempo and duration.',
        'minimax-music3-int8': 'Prepare MiniMax Music3 metadata as a coherent caption: global musical style, vocal details when requested, and arrangement. '
                              'Do not apply ACE-Step planner controls. Its duration is an upper bound and cannot be guaranteed by wording.',
        'stable-audio-3-medium': 'The installed Medium workflow takes an English audio description, duration and seed. It has no lyrics or vocal-language '
                                 'input, categories or reprompt stage. Do not invent these controls or use the separate Base checkpoint contract.',
    }
    MODE_RULES = {
        'ace-step-15-turbo': {
            'instrumental': 'Describe instruments, rhythm, mood and development with no singing or spoken lines. Keep supplied control values separate.',
            'song': 'Describe the musical accompaniment and requested vocal character. Lyrics, stanza labels and their original language remain in the separate lyrics field verbatim.',
        },
        'minimax-music3-int8': {
            'instrumental': 'Request an instrumental arrangement with no vocals; preserve the idea without promising perfect instrumental-only adherence.',
            'song': 'Describe global style, voice and arrangement. Preserve the supplied verse/chorus structure and every lyric in the separate lyrics field.',
        },
        'stable-audio-3-medium': {
            'audio': 'Describe the requested instrumental music or sound effect in English, including its audible source and development. Do not add lyrics or singing.',
        },
    }
