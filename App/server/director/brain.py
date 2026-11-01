"""Gemma as the film director: it looks at what the person attached and writes the production plan of the film.

Two kinds of calls, both English to the model (quoted speech and lettering keep the person's language, as everywhere in the Video section):
* `look` describes every new picture and video once, in batches that fit the model's picture budget; the captions are kept in the draft, so planning again
  (another wording, another number of scenes) does not look again, and any number of pictures costs only text in the planning call. Songs and voices are not
  looked at: Gemma reads their measured length and the quiet and loud stretches instead;
* `plan` turns the idea, the captions and the limits of the film into one JSON object that `plan.normalize` checks.
"""
from ui_strings import source as ui_text
import base64
import json
import re
import time
from pathlib import Path

from director import media, plan
from director import plan_rules as rules
from video.gemma_task import GemmaTask, small_image
from video.scene_planner import LTX_COMMON

OWNER = 'video-director'
PHOTOS_PER_CALL = 4
VIDEO_FRAMES = 3
# The prompt rules of an LTX scene are the scenario planner's own (one paragraph, a fixed look, named sound sources, quoted speech); only its last line,
# the answer format, is dropped.
LTX_RULES = re.sub(r'- Choose ONE style phrase[^\n]*\n', '', LTX_COMMON.rsplit('Answer with', 1)[0])     # the film's style phrase is a field of its own

LOOK_PHOTOS = (
    'You describe pictures for a film director. The pictures arrive in order; the message names the attachment number of each one and what the person says it is. For EVERY picture say what is shown, '
    'in plain English, the way a casting sheet does: for a person the age, build, hair, clothes and colours; for a vehicle or an object its type, colour, era and condition; '
    'for a place the setting, the time of day and the light. Add a role: character, vehicle, object, place, style or other.\n'
    'Answer with ONLY a JSON array of objects {"number": <attachment number>, "what": "<one or two sentences>", "role": "<role>"}.')
LOOK_VIDEO = (
    'You describe a reference video for a film director from {count} frames taken in time order and measured numbers. Say in plain English, in three short sentences: '
    'what happens, how it is filmed and edited (camera, pace, the kind of transitions you can infer), and its colour grade and mood.\n'
    'Answer with ONLY a JSON object {{"what": "<what happens>", "style": "<camera, pace, transitions, colour grade, mood>"}}.')

PLAN = (
    'You are the director and the producer of a short film that AI models will make one scene at a time. The person gives an idea, and maybe pictures, reference videos, songs and voices; '
    'you answer with the production plan as ONE JSON object and nothing else.\n'
    'THE FILM: {seconds} seconds in exactly {scenes} scenes, in order. The "seconds" of the scenes add up to {seconds}; a scene lasts from 2 to 60 seconds.\n'
    'WHAT THE STUDIO CAN DO:\n{abilities}\n'
    'RULES:\n'
    '- Every prompt is English, even when the person writes in another language. Words that are spoken, sung or written in the picture stay exactly as the person wrote them, '
    'in straight double quotes, with the language named. "title" and "summary" are written in {language}.\n'
    '- "summary": two or three sentences for the person: the idea of every scene, the music and the voices.\n'
    '- "references": the numbers of the attached pictures that show who or what is in the scene (a hero, a place, a vehicle or an object). Copy their descriptions into the prompt in the '
    'same words in every scene, so the cast looks the same from scene to scene. A picture is never described by "the same man": name the description again. '
    'Pictures of heroes are the cast, pictures of places are where the scenes happen, pictures of objects must be in the picture as shown.\n'
    '- Reference VIDEOS are the example to imitate: take their pace (shots as long as theirs), their colour palette and grade (put the palette into "style" and its numbers into '
    'warmth, contrast and saturation) and the kind of their transitions. Choose each "transition" (the way the scene hands over to the next one) from: '
    + ', '.join(rules.TRANSITIONS) + '.\n'
    '- "style.phrase" opens EVERY scene prompt unchanged: the look of the whole film (film stock or camera, light, colour). warmth is -1 (cold) to 1 (warm), contrast 0.8 to 1.3, '
    'saturation 0 to 1.4: 1 leaves the colours as they are, 0 makes the film black and white (write 0 whenever the person wants black and white, the words of the phrase alone '
    'do not take the colour out of a picture the scene starts from).\n'
    '- "orientation": landscape or portrait.\n'
    '{mode_rules}'
    '- "music": null for a film without generated music, or ONE track {{"engine": "<one of the music engines above>", "mode": "<a mode of that engine>", "caption": "<the music in English: '
    'genre, instruments, mood, tempo, the voice>", "lyrics": "<a few short lines, sparse when the person wants rare vocals; only for mode song, else empty>", "language": "en", "gain_db": -16, '
    '"start": 0}}. Always write "caption": it is what the music model is given. gain_db -30 to -6: -16 is unobtrusive. "start": the second of the film where the track begins (0 for the whole film). '
    'When the person asks for music, write the track.\n'
    '{materials_rules}'
    '- A scene prompt:\n' + LTX_RULES.strip() + '\n'
    'THE JSON SHAPE: {{"title": "...", "summary": "...", "orientation": "landscape", "quality": "standard", '
    '"style": {{"phrase": "...", "warmth": 0, "contrast": 1.0, "saturation": 1.0}}, '
    '{music_shape}{shape}"scenes": [{{"seconds": 0.0, "prompt": "...", "references": [1], "keyframe": null, "transition": "fade"}}]}}')

# The Ultra toggle of the page, told to the planner. Ultra is the best the studio can make and costs hours; normal is a good film in a good part of that time (see `PROFILES` in
# plan_rules.py: `plan.normalize` applies the same choices when Gemma's answer does not follow them).
ULTRA_RULES = (
    '- QUALITY MODE "ultra". The person wants the best film these models can make and accepts hours of shooting, so use the strongest settings of the studio:\n'
    '  * "quality": "max" (the largest picture) unless the idea asks for a draft or a test.\n'
    '  * Give EVERY scene a "keyframe": its own first frame, made by the picture model before the scene is shot. A scene with pictures composes them, a scene without "references" is drawn from '
    'the words alone. Write each keyframe as a rich still: who or what is in it, clothes and colours, the place, the light, the lens and the shot size, the mood.\n'
    '  * Write every scene prompt in full detail: one clear action, one camera move, named light, named materials, named sound sources.\n'
    '  * Music: the engines are listed strongest first; take the first one whose longest track covers the whole film, write its caption in detail (genre, instruments, tempo, mood, how the '
    'recording sounds) and lay one track under the whole film unless the idea says otherwise.\n')
NORMAL_RULES = (
    '- QUALITY MODE "normal". The person wants a good film in a reasonable time, two or three times faster than the best settings allow:\n'
    '  * "quality": "standard" ("draft" when the idea asks for a draft or a test; "max" only when the idea explicitly asks for the highest quality).\n'
    '  * Give a "keyframe" only to a scene that needs several attached pictures together or a look no single picture shows; otherwise null.\n'
    '  * Keep the scene prompts focused (one action, one camera move) and the music plain: the engines are listed fastest first, take the first one that fits.\n')
SONGS_RULES = (
    '- The person attached SONGS (listed with their length and their quiet and loud stretches). Use them as the soundtrack unless the idea says otherwise. "songs" is a list of excerpts: '
    '{"song": <number>, "offset": <seconds into the song where the excerpt begins>, "start": <seconds into the film where it begins>, "seconds": <its length>, "gain_db": <-24 to 0>, '
    '"fade_in": <seconds>, "fade_out": <seconds>}. Put loud stretches under big moments and quiet ones under calm moments. Excerpts follow each other; what no song covers may be filled by "music" '
    '(give it a "start"), but when the songs cover the whole film "music" is null: never compose a second soundtrack over a song of the person. '
    'gain_db: -6 when a song is the soundtrack on its own, -12 under a narrator or a character. The first excerpt of a song usually starts at its offset 0 only when the song begins the film.\n')
SPEECH_RULES = (
    '- The person attached VOICES (voice samples; each one speaks the lines you write). "speech" is a list of lines: {"voice": <number>, "scene": <scene number>, "text": "<the words>", '
    '"language": "<Russian, English, ...>", "delay": <seconds after the scene starts>, "gain_db": 0}. A narrator voice tells the story from outside the picture; a character voice speaks as that '
    'person. Write the words in the language of the idea, spelled the way they are said (no digits, abbreviations or symbols). A scene has room for about 12 characters of speech per second of the scene, '
    'at most 3 lines; scenes may have none. The voices are heard OVER the picture with no lip sync: a scene prompt never contains the quoted words of a voice you gave a line to - '
    'show that person from behind, in profile, far away, listening or thinking instead. Spread several narrators over the film as the idea says (one may open it, another close it). '
    'Use every voice at least once unless the idea says otherwise.\n')

GROUP_TITLES = dict(
    hero='Pictures of the HEROES (the people or characters of the film)',
    place='Pictures of PLACES (where the action happens)',
    prop='Pictures of OBJECTS and vehicles (they must appear as shown)',
    video='Reference VIDEOS (the example to imitate)',
    song='SONGS of the person (excerpts of them may be the soundtrack; times are inside the song)')
GROUP_WORD = dict(hero='a hero', place='a place', prop='an object')


def abilities(capabilities, voices=False, ultra=False):
    """What the planner may use, as a bullet list: video always, keyframe pictures, the music engines and the speech engine when they are installed."""
    lines = ['- Video: LTX 2.5 draws the picture and the sound together. A scene is ONE continuous take made from a text prompt or from a start picture (up to 5 seconds in '
             'one generation; a longer scene continues the same take).']
    if capabilities.get('keyframes'):
        lines.append('- Pictures: FLUX.3 Klein composes a keyframe from the attached photos before the scene is shot. ' + (
                     'Give every scene a "keyframe". ' if ultra else
                     'Give a scene a "keyframe" when the scene needs several attached pictures together '
                     'or a look no single picture shows (a hero in a place); leave it null when one attached picture already is the first frame or none is needed. ') + 'Write the keyframe as one English instruction that '
                     'names the pictures by their numbers as listed, e.g. "The woman from picture 4 stands next to the red car from picture 7 on a desert road at dawn, '
                     'wide shot". With no "references" it is drawn from the words alone.')
    for engine in capabilities.get('music', []):
        lines.append('- Music "%s": %s, up to %d seconds. %s' % (engine['id'], ' or '.join(engine['modes']), engine['max_duration'], engine['about']))
    if not capabilities.get('music'):
        lines.append('- There is no music engine: "music" must be null.')
    if voices and capabilities.get('speech'):
        lines.append('- Speech: the attached voices speak the lines you write, in %s.' % ', '.join(capabilities['speech']['languages']))
    return '\n'.join(lines)


def read_array(answer):
    """The JSON array inside an answer, as a list of dicts; ValueError when there is none."""
    text = re.sub(r'^```[a-z]*\s*|\s*```$', '', str(answer).strip(), flags=re.I)
    start, end = text.find('['), text.rfind(']')
    try:
        value = json.loads(text[start:end + 1]) if start != -1 and end > start else None
    except ValueError:
        value = None
    if not isinstance(value, list):
        raise ValueError(ui_text('director.plan_answer_unreadable'))
    return [row for row in value if isinstance(row, dict)]


def _data_url(png):
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')


def slot_names(items):
    """{number: English name} of the attachments, counted per group in the order they were attached: "Narrator 2", "Hero 1"."""
    seen, names = {}, {}
    for row in items:
        seen[row['group']] = seen.get(row['group'], 0) + 1
        names[row['number']] = rules.slot_name(row['group'], seen[row['group']])
    return names


class DirectorBrain(GemmaTask):
    OWNER = OWNER
    OPERATION = 'VideoDirector'
    LOG_LIMIT = 200_000

    def __init__(self, text, gpu, submission_lock=None, log=None):
        super().__init__(text, gpu, submission_lock)
        self.log = Path(log) if log else None

    def _record(self, request, answer, result, problem):
        """Keep what Gemma answered, one JSON line for each try (the log holds the last ~200 KB): "why does my plan have no music" is answered by reading it."""
        if self.log is None:
            return
        row = dict(at=time.strftime('%Y-%m-%d %H:%M:%S'), prompt=request['prompt'][:300], seconds=request['seconds'], scenes=request['scenes'], ultra=request.get('ultra') is True, answer=str(answer)[:8000],
                   problem=str(problem) if problem else None,
                   kept=None if result is None else dict(music=bool(result['music']), songs=len(result['songs']), lines=len(result['speech']), notes=result['notes']))
        try:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with self.log.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            if self.log.stat().st_size > self.LOG_LIMIT:
                tail = self.log.read_bytes()[-self.LOG_LIMIT // 2:]
                self.log.write_bytes(tail[tail.find(b'\n') + 1:])
        except OSError:
            pass                           # a log that cannot be written never fails a plan

    def _ask(self, messages, cancel, **options):
        return self.ask(messages, cancel, **dict(dict(temperature=0.4, top_p=0.9, top_k=40, num_ctx=6144, num_predict=900), **options))

    def look(self, draft, cancel):
        """Describe the pictures and videos that have no caption yet and keep the captions in the draft. A picture that cannot be described gets a plain one: never a failure."""
        captions = draft.captions()
        todo = [row for row in draft.items() if str(row['number']) not in captions and row['kind'] in ('photo', 'video')]
        photos = [row for row in todo if row['kind'] == 'photo']
        for start in range(0, len(photos), PHOTOS_PER_CALL):
            batch = photos[start:start + PHOTOS_PER_CALL]
            pictures = [small_image(_data_url(draft.path(row['number']).read_bytes()), 768) for row in batch]
            names = ', '.join('picture %d = attachment %d (the person says it is %s)' % (index + 1, row['number'], GROUP_WORD.get(row['group'], 'a picture')) for index, row in enumerate(batch))
            message = dict(role='user', content='Pictures in order: %s.' % names, images=[p for p in pictures if p])
            found = {}
            try:
                for row in read_array(self._ask([dict(role='system', content=LOOK_PHOTOS), message], cancel)):
                    if isinstance(row.get('number'), int):
                        found[row['number']] = row
            except ValueError:
                pass
            for row in batch:
                said = found.get(row['number'], {})
                captions[str(row['number'])] = dict(what=plan.clean_text(said.get('what'), 300, 'a photo'), role=plan.clean_text(said.get('role'), 20, 'other'))
            if cancel.is_set():
                break
        for row in (r for r in todo if r['kind'] == 'video'):
            if cancel.is_set():
                break
            frames = media.frame_pngs(draft.ffmpeg, draft.path(row['number']), row['seconds'], VIDEO_FRAMES, 768)
            pictures = [p for p in (small_image(_data_url(png), 768) for png in frames) if p]
            message = dict(role='user', content='Measured: %s.' % media.describe(row), images=pictures)
            said = {}
            try:
                said = plan.read_object(self._ask([dict(role='system', content=LOOK_VIDEO.format(count=len(pictures))), message], cancel))
            except ValueError:
                pass
            captions[str(row['number'])] = dict(what=plan.clean_text(said.get('what'), 300, 'a reference video'), style=plan.clean_text(said.get('style'), 300))
        draft.save_captions(captions)
        return captions

    def listing(self, items, captions):
        """The attachments as numbered English lines for the planning call, grouped by what the person attached them as."""
        names = slot_names(items)
        blocks = []
        for group in ('hero', 'place', 'prop'):
            rows = [row for row in items if row['group'] == group]
            if rows:
                blocks.append(GROUP_TITLES[group] + ' - use their numbers in "references":\n' + '\n'.join(
                    '%d%s. %s (%s)' % (row['number'], _tag(row), captions.get(str(row['number']), {}).get('what', 'a photo'), media.describe(row)) for row in rows))
        videos = [row for row in items if row['group'] == 'video']
        if videos:
            blocks.append(GROUP_TITLES['video'] + ':\n' + '\n'.join(
                '%d%s. %s Style: %s (%s)' % (row['number'], _tag(row), captions.get(str(row['number']), {}).get('what', 'a reference video'),
                                              captions.get(str(row['number']), {}).get('style', ''), media.describe(row)) for row in videos))
        songs = [row for row in items if row['group'] == 'song']
        if songs:
            blocks.append(GROUP_TITLES['song'] + ' - use their numbers in "songs":\n' + '\n'.join('%d%s. %s' % (row['number'], _tag(row), media.describe(row)) for row in songs))
        voices = [row for row in items if row['kind'] == 'voice']
        if voices:
            blocks.append('VOICES (samples that speak the lines you write) - use their numbers in "speech":\n' + '\n'.join(
                '%d%s. %s (%s)' % (row['number'], _tag(row), names[row['number']], media.describe(row)) for row in voices))
        return '\n\n'.join(blocks) if blocks else 'Nothing is attached: the film comes from the idea alone.'

    def make_plan(self, draft, request, capabilities, language):
        """Look at the new pictures and videos, then write and check the plan. `request`: prompt, seconds, scenes and the optional orientation and quality chosen on the page."""
        rules.check_request(request['seconds'], request['scenes'])
        request = dict(request, language=language, speech_language=plan.idea_language(request['prompt']))
        ultra = request.get('ultra') is True
        capabilities = dict(capabilities, music=rules.music_order(capabilities.get('music', []), request['seconds'], ultra))        # the order the engines are listed in is the order of preference
        with self.session() as cancel:
            captions = self.look(draft, cancel)
            items = draft.items()
            materials = plan.Materials(items)
            has_songs, has_voices = bool(materials.songs), bool(materials.voices) and bool(capabilities.get('speech'))
            system = PLAN.format(seconds=request['seconds'], scenes=request['scenes'], abilities=abilities(capabilities, has_voices, ultra), language=language,
                                 mode_rules=ULTRA_RULES if ultra else NORMAL_RULES,
                                 materials_rules=(SONGS_RULES if has_songs else '') + (SPEECH_RULES if has_voices else ''),
                                 music_shape=('"music": {"engine": "...", "mode": "instrumental", "caption": "...", "lyrics": "", "language": "en", "gain_db": -16, "start": 0}, '
                                              if capabilities.get('music') else '"music": null, '),
                                 shape=('"songs": [], ' if has_songs else '') + ('"speech": [], ' if has_voices else ''))
            hints = 'Format: %s. Quality: %s.' % (request.get('orientation') or 'choose it from the idea', request.get('quality') or 'choose it from the idea')
            if has_voices:
                hints += ' The idea is written in %s: the words of the voices are in it unless the idea asks for another language.' % request['speech_language']
            user = dict(role='user', content='Idea of the person:\n%s\n\nThe film: %d seconds, exactly %d scenes. %s\n\n%s' % (
                request['prompt'].strip(), request['seconds'], request['scenes'], hints, self.listing(items, captions)))
            messages = [dict(role='system', content=system), user]
            limit = min(6144, (360 if ultra else 200) * request['scenes'] + 900 + (int(request['seconds'] * rules.SPEECH_CHARS_PER_SECOND / 2) if has_voices else 0))      # Ultra writes a keyframe for every scene
            problem = None
            for _ in range(2):
                answer = self._ask(messages, cancel, temperature=0.6, num_ctx=12288, num_predict=limit)
                if cancel.is_set():
                    raise ValueError(ui_text('director.brain_planning_was_stopped'))
                try:
                    result = plan.normalize(plan.read_object(answer), request, capabilities, materials)
                    self._record(request, answer, result, None)
                    return result
                except ValueError as error:
                    problem = error
                    self._record(request, answer, None, error)
                    messages += [dict(role='assistant', content=answer), dict(role='user', content='Wrong: %s. Answer again with only the JSON object.' % error)]
            raise ValueError(ui_text('director.brain_plan_not_written', problem))


def _tag(row):
    """` [label]` when the person named the attachment, so that Gemma can match a voice to a hero by name."""
    return ' [%s]' % row['label'] if row.get('label') else ''
