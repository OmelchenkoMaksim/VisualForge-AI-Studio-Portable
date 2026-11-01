"""The production plan of a film: what Gemma writes, and the checks that make it safe to carry out.

Gemma answers with one JSON object. Nothing in it is trusted: `normalize` keeps only what the rules allow (the number of scenes, their lengths adding up to the
film, the pictures, songs and voices that exist, transitions and music engines from the lists, lines of speech that fit their scene, numbers inside their ranges)
and refuses an answer it cannot repair, with a message that the brain sends back to Gemma.
"""
from ui_strings import source as ui_text
import json
import math
import re

from director import plan_rules as rules

MUSIC_LANGUAGES = ('unknown', 'ru', 'en', 'zh', 'ja', 'ko', 'es', 'fr', 'de', 'it', 'pt')
SENTENCE_END = re.compile(r'[.!?…]["»”)\]]*\s')
PICTURE_WORDS = dict(hero='the person', place='the place', prop='the object')
QUOTE_PAIRS = {'"': '"', '«': '»', '“': '”', '„': '“'}
PICTURE_MENTION = re.compile(r'\b(picture|photo|image)\s+(\d+)\b', re.I)


class Materials:
    """What a plan may point to: the numbers of the attached pictures (of any group), the songs with their lengths and the voices with their group."""

    def __init__(self, items=()):
        items = list(items)
        self.pictures = {row['number'] for row in items if row['kind'] == 'photo'}
        self.groups = {row['number']: row.get('group') for row in items if row['kind'] == 'photo'}
        self.songs = {row['number']: float(row['seconds']) for row in items if row['kind'] == 'audio'}
        self.voices = {row['number']: row['group'] for row in items if row['kind'] == 'voice'}


def _listed(words):
    return words[0] if len(words) == 1 else ', '.join(words[:-1]) + ' and ' + words[-1]


def renumber_pictures(text, references):
    """Gemma names a picture by its number in the draft ("picture 7"), as the pictures are listed to it and as the plan shows them ("Pictures: #7"); the picture model counts the
    pictures it is given, so each mention becomes the picture's place in the scene's list. A number that is not one of the scene's pictures stays as it was written.

    This is done once, when the picture model is asked (`DirectorManager._keyframes`), never in the plan: a plan goes through `normalize` again when the film starts, and a second
    renumbering of text that is already renumbered scrambles it ("picture 3" of a scene whose pictures are 3, 4 and 5 is the first one, but "picture 1" is not any of them)."""
    def place(match):
        number = int(match.group(2))
        return '%s %d' % (match.group(1), references.index(number) + 1) if number in references else match.group(0)
    return PICTURE_MENTION.sub(place, text)


def default_keyframe(prompt, references, materials):
    """The instruction for Klein when Gemma left the keyframe out of a scene that shows several pictures: the pictures named by their numbers in the draft and by what they show, then the scene's first sentence.

    Gemma was told to write one for such a scene and wrote none in the real plans of 2026-11-01 (a hero, a place and a car in one scene): without it the scene starts from its first picture alone and the others only
    appear as words."""
    if not references:                                                       # Ultra gives every scene a keyframe, one drawn from the words alone when the scene shows no picture
        opening = [part.strip().rstrip('.!?… ') for part in SENTENCE_END.split(prompt, 2)[:2] if part.strip()]
        return clean_text('. '.join(opening) + '.', 600)
    people, places, things = [], [], []
    for number in references:
        group = materials.groups.get(number)
        word = '%s from picture %d' % (PICTURE_WORDS.get(group, 'the subject'), number)
        (people if group == 'hero' else places if group == 'place' else things).append(word)
    subjects = people + things
    shown = _listed(subjects) if subjects else 'the scene'
    where = ' in ' + _listed(places) if places else ''
    first = SENTENCE_END.split(prompt, 1)[0].strip()
    return clean_text('Show %s%s. %s%s' % (shown, where, first, '' if first.endswith(('.', '!', '?', '…')) else '.'), 600)


def read_object(answer):
    """The JSON object inside Gemma's answer (it may be wrapped in a code fence or a sentence); ValueError when there is none."""
    text = re.sub(r'^```[a-z]*\s*|\s*```$', '', str(answer).strip(), flags=re.I)
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end <= start:
        raise ValueError(ui_text('director.plan_answer_unreadable'))
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        raise ValueError(ui_text('director.plan_answer_unreadable')) from None
    if not isinstance(value, dict):
        raise ValueError(ui_text('director.plan_answer_unreadable'))
    return value


def idea_language(text):
    """The language of the person's own words, named as the speech engines name languages: Russian when the text has at least as much Cyrillic as Latin, else English.

    The words of the voices follow it, not the language of the page: a person who writes the idea in Russian on an English page still wants a Russian film."""
    cyrillic, latin = len(re.findall(r'[А-Яа-яЁё]', text or '')), len(re.findall(r'[A-Za-z]', text or ''))
    return 'Russian' if cyrillic and cyrillic >= latin else 'English'


def clean_text(value, limit, fallback=''):
    """`value` as one line of at most `limit` characters, or `fallback` when it is not text."""
    return ' '.join(value.split())[:limit] if isinstance(value, str) and value.strip() else fallback


def _number(value, low, high, default):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return default
    return round(min(high, max(low, float(value))), 2)


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def fit_seconds(values, total):
    """Scene lengths scaled so that they add up to `total` (0.1 s steps, each between the shortest and the longest scene)."""
    low, high = rules.MIN_SCENE_SECONDS, rules.MAX_SCENE_SECONDS
    count = len(values)
    total = min(count * high, max(count * low, float(total)))                        # a film no scenes could add up to is brought to what they can
    weights = [max(float(v), 1e-6) if math.isfinite(float(v)) else 1.0 for v in values]

    def clamped(scale):
        return [min(high, max(low, weight * scale)) for weight in weights]
    # The lengths are proportional to what Gemma planned, except that none is shorter or longer than a scene may be. The total of the clamped lengths grows with the scale
    # without jumps, so the scale that makes it equal to the film is found by bisection: it works however wildly the planned numbers differ (one scene a million times
    # longer than the others is as good as a tenth of a second).
    near, far = 0.0, 2 * high / min(weights)
    for _ in range(200):
        middle = (near + far) / 2
        near, far = (middle, far) if sum(clamped(middle)) < total else (near, middle)
    result = clamped(far)
    result = [round(v, 1) for v in result]
    for _ in range(4 * count + 4):                                                     # rounding to a tenth leaves a little: give it to the scenes that have room, a tenth at a time
        drift = round(total - sum(result), 1)
        if abs(drift) < 0.05:
            break
        step = 0.1 if drift > 0 else -0.1
        room = [i for i in range(count) if low <= result[i] + step <= high]
        if not room:
            break
        index = max(room, key=lambda i: (high - result[i]) if step > 0 else (result[i] - low))
        result[index] = round(result[index] + step, 1)
    return result


def normalize(raw, request, capabilities, materials):
    """The plan Gemma wrote, checked against `request` (seconds, scenes, orientation, quality, language) and what the studio can do; ValueError tells what to fix.

    `capabilities`: dict(keyframes=bool, music=[dict(id, modes, max_duration)], speech=dict(languages) or None). `materials`: a `Materials` of the draft."""
    ultra = request.get('ultra') is True
    profile = rules.profile(ultra)
    capabilities = dict(capabilities, music=rules.music_order(capabilities.get('music', []), request['seconds'], ultra))
    scenes = raw.get('scenes')
    if not isinstance(scenes, list) or len(scenes) != request['scenes']:
        raise ValueError(ui_text('director.plan_wrong_scene_count', request['scenes'], len(scenes) if isinstance(scenes, list) else 0))

    def planned(row):
        value = row.get('seconds') if isinstance(row, dict) else None
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else request['seconds'] / request['scenes']
    seconds = fit_seconds([planned(row) for row in scenes], request['seconds'])
    result_scenes = []
    for number, (row, length) in enumerate(zip(scenes, seconds, strict=True), 1):
        if not isinstance(row, dict):
            raise ValueError(ui_text('director.plan_scene_unreadable', number))
        prompt = clean_text(row.get('prompt'), 1500)
        if len(prompt) < 20:
            raise ValueError(ui_text('director.plan_scene_prompt_missing', number))
        references = [n for n in (row.get('references') if isinstance(row.get('references'), list) else []) if _whole(n) and n in materials.pictures]
        transition = rules.pick(row.get('transition'), rules.TRANSITIONS) or 'fade'
        keyframe = clean_text(row.get('keyframe'), 600) if capabilities.get('keyframes') else ''
        references = list(dict.fromkeys(references))[:6]
        if not keyframe and capabilities.get('keyframes') and (len(references) > 1 or profile['keyframes'] == 'all'):
            keyframe = default_keyframe(prompt, references, materials)
        result_scenes.append(dict(seconds=length, prompt=prompt, references=references, keyframe=keyframe or None,
                                  transition='cut' if number == len(scenes) else transition))
    style = raw.get('style') if isinstance(raw.get('style'), dict) else {}
    orientation = rules.pick(raw.get('orientation'), rules.ORIENTATIONS)
    quality = rules.pick(raw.get('quality'), rules.QUALITIES)
    notes = []
    speech = speech_lines(raw.get('speech'), result_scenes, materials, capabilities.get('speech'), request.get('speech_language') or request.get('language') or 'Russian', notes)
    return dict(
        title=clean_text(raw.get('title'), 120, clean_text(request.get('prompt'), 60, 'Film')),
        summary=clean_text(raw.get('summary'), 1200),
        orientation=request.get('orientation') or orientation or 'landscape',
        quality=request.get('quality') or quality or profile['quality'],
        seconds=request['seconds'],
        ultra=ultra,
        # Saturation may reach 0 (a black-and-white film): with a floor of 0.7 a plan that said "black and white" could not take the colour out, and a real film shot from a colour
        # picture came out in full colour although its style phrase asked for black and white.
        style=dict(phrase=clean_text(style.get('phrase'), 300), warmth=_number(style.get('warmth'), -1, 1, 0.0), contrast=_number(style.get('contrast'), 0.8, 1.3, 1.0),
                   saturation=_number(style.get('saturation'), 0.0, 1.4, 1.0)),
        music=_music(raw.get('music'), capabilities, request['seconds']),
        songs=song_cues(raw.get('songs'), materials, request['seconds']),
        speech=speech,
        notes=notes,
        scenes=result_scenes)


def _music(raw, capabilities, film_seconds):
    if not isinstance(raw, dict):
        return None
    engines = {engine['id']: engine for engine in capabilities.get('music', [])}
    engine = engines.get(rules.pick(raw.get('engine'), engines)) or next(iter(engines.values()), None)
    if engine is None:
        return None
    mode = rules.pick(raw.get('mode'), engine['modes']) or engine['modes'][0]
    # Gemma sometimes describes the track in fields of its own (genre, mood, instruments...) instead of one caption: the words are put together, nothing is lost.
    caption = clean_text(raw.get('caption'), 1500) or ', '.join(part for part in (clean_text(raw.get(key), 300) for key in ('description', 'genre', 'style', 'mood', 'instruments', 'tempo', 'voice')) if part)[:1500]
    lyrics = raw.get('lyrics').strip()[:3000] if isinstance(raw.get('lyrics'), str) else ''
    if len(caption) < 3:
        return None
    if mode == 'song' and not lyrics:
        mode = 'instrumental' if 'instrumental' in engine['modes'] else mode
    if mode == 'song' and not lyrics:
        return None
    language = rules.pick(raw.get('language'), MUSIC_LANGUAGES) or 'en'
    start = _number(raw.get('start'), 0, max(0.0, film_seconds - 5), 0.0)         # the track may begin later in the film, after a song of the person
    length = film_seconds - start
    return dict(engine=engine['id'], mode=mode, caption=caption, lyrics=lyrics if mode == 'song' else '', language=language,
                gain_db=_number(raw.get('gain_db'), -30, -6, -16.0), seconds=int(min(engine['max_duration'], max(10, round(length)))), start=start,
                loop=length > engine['max_duration'])


def song_cues(raw, materials, film_seconds):
    """The excerpts of the person's songs that sound in the film, in time order: dict(song, offset, start, seconds, gain_db, fade_in, fade_out).

    `offset` is where the excerpt begins in the song, `start` where it begins in the film. An excerpt never runs past the song or the film, and one that overlaps the
    next is cut short, leaving a crossfade."""
    cues = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict) or not _whole(row.get('song')) or row['song'] not in materials.songs or len(cues) >= rules.MAX_SONG_CUES:
            continue
        length = materials.songs[row['song']]
        start = _number(row.get('start'), 0, max(0.0, film_seconds - 2), 0.0)
        seconds = _number(row.get('seconds'), 2, film_seconds - start, min(film_seconds - start, length))
        offset = _number(row.get('offset'), 0, max(0.0, length - 2), 0.0)
        seconds = round(min(seconds, length - offset, film_seconds - start), 2)
        if seconds < 2:
            continue
        cues.append(dict(song=row['song'], offset=offset, start=start, seconds=seconds, gain_db=_number(row.get('gain_db'), -24, 0, -10.0),
                         fade_in=_number(row.get('fade_in'), 0, 8, 1.5), fade_out=_number(row.get('fade_out'), 0, 10, 3.0)))
    cues.sort(key=lambda cue: cue['start'])
    for index in range(len(cues) - 1):
        room = cues[index + 1]['start'] + rules.CUE_CROSSFADE_SECONDS - cues[index]['start']
        if cues[index]['seconds'] > room:
            cues[index]['seconds'] = round(max(2.0, room), 2)
    for cue in cues:
        cue['fade_in'] = round(min(cue['fade_in'], cue['seconds'] / 2), 2)
        cue['fade_out'] = round(min(cue['fade_out'], cue['seconds'] / 2), 2)
    return cues


def fit_line(text, limit):
    """`text` as one line of at most `limit` characters, cut after the last whole sentence (else the last whole word) that fits; '' when nothing speakable is left."""
    text = clean_text(text, 4000)
    if limit < 8:                                 # a scene whose room for speech is used up takes no more lines
        return ''
    if len(text) <= limit:
        return text
    head = text[:limit + 1]                       # one character more than fits: a sentence that ends exactly at the limit is followed by a space in it
    ends = [match.end() for match in SENTENCE_END.finditer(head)]
    cut = head[:max(ends)].strip() if ends and max(ends) >= limit * 0.4 else head[:head.rfind(' ')].rstrip(' ,;:-') if ' ' in head else ''
    return cut if len(re.findall(r'\w+', cut)) >= 2 else ''


def unquoted(text):
    """The words without one pair of quotation marks around all of them. Gemma copies the quotes that the scene prompts put around spoken words into the lines too (seen in the real plans of
    2026-11-01), and a voice is given the words, not the marks. Quotes inside the line (a quotation in a sentence) stay."""
    if len(text) > 2 and QUOTE_PAIRS.get(text[0]) == text[-1] and text[0] not in text[1:-1] and text[-1] not in text[1:-1]:
        return text[1:-1].strip()
    return text


def speech_lines(raw, scenes, materials, speech, language, notes):
    """The lines the attached voices speak: dict(voice, scene, text, language, delay, gain_db) in film order.

    Without a speech engine, or without voices, a plan has none (the notes say so when the person had attached voices). Every scene gets at most as much text as
    can be spoken in it (`SPEECH_CHARS_PER_SECOND`), and a line that is too long is cut at the end of a sentence instead of being sped up beyond hearing."""
    if not materials.voices:
        return []
    if not speech:
        notes.append(ui_text('director.plan_note_no_speech_engine'))
        return []
    spoken = {}
    kept = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict) or not _whole(row.get('voice')) or row['voice'] not in materials.voices or not _whole(row.get('scene')) or not 1 <= row['scene'] <= len(scenes):
            continue
        budget = int(scenes[row['scene'] - 1]['seconds'] * rules.SPEECH_CHARS_PER_SECOND) - spoken.get(row['scene'], 0)
        text = fit_line(unquoted(clean_text(row.get('text'), 4000)), min(rules.MAX_LINE_CHARS, budget))
        if not text or sum(1 for line in kept if line['scene'] == row['scene']) >= rules.MAX_LINES_PER_SCENE:
            continue
        spoken[row['scene']] = spoken.get(row['scene'], 0) + len(text)
        wanted = rules.pick(row.get('language'), rules.SPEECH_LANGUAGES) or language
        kept.append(dict(voice=row['voice'], scene=row['scene'], text=text, language=wanted if wanted in speech['languages'] else language,
                         delay=_number(row.get('delay'), 0, max(0.0, scenes[row['scene'] - 1]['seconds'] - 1), 0.3), gain_db=_number(row.get('gain_db'), -12, 6, 0.0)))
    kept.sort(key=lambda line: line['scene'])
    if materials.voices and not kept:
        notes.append(ui_text('director.plan_note_voices_unused'))
    return kept
