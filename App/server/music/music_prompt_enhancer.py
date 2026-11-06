"""Music captions for the exact installed checkpoints; lyrics never pass through a rewriter."""
import re
from audio_preparation import AudioPromptManager, english_direction
from ui_strings import source as ui_text
from .music_registry import validate as validate_request
from .music_intent import checked_features


class PromptEnhancer(AudioPromptManager):
    VERSION = 'audio-music-v3-2026-11-06'
    SECTION = 'Music'
    OWNER = 'music-prompt'
    PLANNED_OPERATION = 'MusicPromptEnhancement'
    OPERATION = PLANNED_OPERATION
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
    GUIDE_TEMPLATE = (
        'Prepare the musical caption for {engine}, mode {mode}, in the Music section. Preserve the original intent. '
        'Model-specific rules: {model_rules}\nMode-specific rules: {mode_rules}\n{language_rule}\n'
        # Naming accompanying verbatim fields in the rewrite template made real Gemma echo invented copies of them.
        'Return only a JSON object with exactly one field: caption (English string). No Markdown or additional fields. '
        'Context controls are immutable input constraints. Protected lyrics are handled outside this caption call. '
        '\nExplicit controls win over conflicting prose. Do not add genres, instruments, voices, effects, lyrics, '
        'or emotional changes that the request did not authorize. Keep sparse requests sparse. '
        'If no genre or instruments were requested, leave those choices unspecified; mood and vocal character are sufficient. '
        'Translate use under dialogue into low density and restrained dynamics, not extra instruments. '
        'No quality slogans, equipment lists or negative keyword piles. Preserve requested exclusions concisely. '
        'Do not copy lyrics into the caption. Do not write lyrics. '
        'ACE-Step Turbo: at most 512 characters; concise genre/instruments/mood/tempo feel/requested voice. '
        'MiniMax: at most 1600 characters; use Global Metadata, Vocal Details (songs only), Arrangement, '
        'with only requested details. Align development to existing sections; do not invent a long song for a short clip. '
        'For instrumental MiniMax use one short positive statement of an instrumental arrangement; do not enumerate types of unwanted voices. '
        'In songs, describe requested singing concretely: register, diction, intensity and phrasing, without naming a singer. '
        'A restrained verse and fuller chorus are useful only when requested or already present in the supplied intent; '
        'do not add backing singers, harmonies, belting, breathiness, reverb or a new genre by default. '
        'Stable Audio Medium: at most 1000 characters; distinguish instrumental music, ambience and discrete events. '
        'Describe audible sources, texture and temporal development. No song section tags or unsupported controls.'
    )

    def prepare(self, request, cancelled=lambda: False):
        request = validate_request(request)
        creation = None
        if request['mode'] == 'song' and not request['lyrics'].strip() and request.get('write_lyrics'):
            def validate_lyrics(value):
                if not isinstance(value, dict) or set(value) != {'lyrics'} or not isinstance(value['lyrics'], str) or not 1 <= len(value['lyrics']) <= 6000:
                    raise ValueError('Expected lyrics string, 1-6000 characters')
                if not re.search(r'\w', re.sub(r'\[[^\]]*\]', '', value['lyrics'])):
                    raise ValueError('Lyrics must contain sung words')
                validate_request(dict(request, lyrics=value['lyrics']))
                return value
            writer = PromptEnhancer(self.text, self.gpu, self.lock)
            # Distinguish content creation in event history; keep the same Text owner so Music Stop reaches either phase.
            writer.OPERATION = 'MusicLyricCreation'
            creation = writer.structured(request['caption'], request['engine'], request['mode'],
                dict(duration=request['duration'], language=request['language']), validate_lyrics, cancelled,
                system='The user explicitly authorized writing ORIGINAL song lyrics from their idea. '
                'Return only JSON with exactly lyrics (string). Use the explicitly selected vocal language; '
                'if unknown, use the language of the request. No translation duplicate. Fit the requested duration '
                'conservatively, about 1-1.5 words per second; do not attempt a full long song in a short clip. '
                'Use only [verse], [chorus], [bridge], [outro] section labels ALONE on their own lines. '
                'No musical directions, performance coaching or prose outside the lyrics. Preserve the requested theme.')
            request['lyrics'] = creation['lyrics']
        context = {key: request[key] for key in ('duration', 'bpm', 'language', 'planner', 'seed', 'mode')}
        # Only structure and density go to Gemma. The full protected lyric payload stays outside model output.
        context['lyric_sections'] = re.findall(r'^\s*(\[[^\]\n]+\])', request['lyrics'], re.M)
        context['lyric_words'] = len(re.findall(r'\w+', re.sub(r'\[[^\]]*\]', '', request['lyrics'])))
        def validate(value):
            if not isinstance(value, dict) or not isinstance(value.get('caption'), str):
                raise ValueError('Expected exactly caption')
            errors = [] if set(value) == {'caption'} else ['Return caption only; remove ALL other output fields']
            limit = {'ace-step-15-turbo': 512, 'minimax-music3-int8': 1600, 'stable-audio-3-medium': 1000}[request['engine']]
            caption = english_direction(value['caption'], limit)
            if request['mode'] != 'song':
                positive = re.sub(r'\b(?:no|without|avoid|exclude|free of)\b[^.;,\n]*', '', caption, flags=re.I)
                if re.search(r'\b(?:singing|vocals|choir|humming|rapping|spoken words)\b', positive, re.I):
                    raise ValueError('Instrumental/audio mode cannot add vocals')
            # Reject a numeric tempo contradicting the explicit control instead of relying on prompt precedence.
            bpms = re.findall(r'\b(\d{2,3})\s*BPM\b', caption, re.I)
            if request['bpm'] is not None and any(int(bpm) != request['bpm'] for bpm in bpms):
                raise ValueError('Caption contradicts explicit BPM')
            try:
                intent = checked_features(request['caption'], caption)
            except ValueError as error:
                errors.append(str(error))
            else:
                if not errors:
                    return dict(caption=caption, checked_intent=intent)
            # Schema echoes and invented musical details can coexist; one allowed correction must see both.
            raise ValueError('; '.join(errors))
        result = self.structured(request['caption'], request['engine'], request['mode'], context, validate, cancelled)
        result['warnings'] = []
        if creation:
            result['lyric_creation'] = creation
        if request['mode'] == 'song' and context['lyric_words'] > request['duration'] * 2:
            result['warnings'].append(ui_text('music.lyrics_density_warning'))
        return result

    def enhance(self, prompt, engine, mode, images=(), context=None):
        return self.prepare(dict(context or {}, caption=prompt, engine=engine, mode=mode))
