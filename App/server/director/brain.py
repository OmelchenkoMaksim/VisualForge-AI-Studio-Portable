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
from collections.abc import Callable

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

PLAN_HEAD = (
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
    '{materials_rules}')
PLAN = (PLAN_HEAD +
    '- A scene prompt:\n' + LTX_RULES.strip() + '\n'
    'THE JSON SHAPE: {{"title": "...", "summary": "...", "orientation": "landscape", "quality": "standard", '
    '"style": {{"phrase": "...", "warmth": 0, "contrast": 1.0, "saturation": 1.0}}, '
    '{music_shape}{shape}"scenes": [{{"seconds": 0.0, "prompt": "...", "references": [1], "keyframe": null, "transition": "fade"}}]}}')

# A long film is planned in three levels so that no answer outgrows the model's room (one answer for 50 scenes did not fit into 6000 tokens, and the model forgets the story
# of a film it writes in one breath): the outline (the whole story, the cast, the style, the music and a beat for every scene), then the full prompts of the scenes in groups,
# each group seeing the whole outline, the cast and the scene written just before it.
OUTLINE_SCENES = (
    '- THIS CALL WRITES THE OUTLINE of the film. For every scene write only "seconds", "beat", "references", "speakers" and "transition"; the full prompts and the keyframes are written later, '
    'from your outline, a few scenes at a time. "beat": ONE sentence of at most 25 words: who is in the scene, what happens, where, and what is new compared with the scene before. '
    'The beats tell ONE story with a beginning, a middle and an end (many scenes make acts: the people and the place are set up, the stakes grow, the end resolves them). No scene '
    'repeats another one; the last beat ends the film; the lengths ("seconds") follow the pace of the story.\n'
    '- "cast": the recurring people, creatures and vehicles that have NO attached picture, one visual description each (age, build, hair, clothes, colours; at most 40 words). The same words are '
    'copied into every scene where they appear, so that they look the same from scene to scene. Attached pictures are described already: do not repeat them here.\n'
    '- "speakers": the numbers of the attached voices that speak in the scene, else []. Spread the voices over the film as the idea says.\n'
    '- Write COMPACT JSON: no indentation, one scene on one line. A long answer that is cut off is lost.\n')
OUTLINE = (PLAN_HEAD + OUTLINE_SCENES + '{extent}'
    'THE JSON SHAPE: {{"title": "...", "summary": "...", "orientation": "landscape", "quality": "standard", '
    '"style": {{"phrase": "...", "warmth": 0, "contrast": 1.0, "saturation": 1.0}}, '
    '{music_shape}{shape}"cast": ["..."], "scenes": [{{"seconds": 0.0, "beat": "...", "references": [1], "speakers": [], "transition": "fade"}}]}}')
OUTLINE_PART = ('- In THIS call write the beats of scenes 1 to {last} ONLY: "scenes" has exactly {last} entries. The next calls write the rest, so leave the story open at scene {last}: the film has '
    '{scenes} scenes and ends only at the last one.\n')
OUTLINE_MORE = (
    'You are the director of a film that AI models make one scene at a time. The first part of the OUTLINE of the film is written (the message has it: the idea, the style, the cast and the beats so far). '
    'Continue it: write the beats of scenes {first} to {last} (of {scenes}), in order, as ONE JSON object and nothing else.\n'
    'THE FILM: {seconds} seconds in {scenes} scenes; a scene lasts from 2 to 60 seconds. The beats go on with the same story: the next act, no scene repeats another, and the last scene of the film ends it.\n'
    + OUTLINE_SCENES +
    'THE JSON SHAPE: {{"scenes": [{{"seconds": 0.0, "beat": "...", "references": [1], "speakers": [], "transition": "fade"}}]}} with exactly {count} scenes.')
CHUNK = (
    'You are the director of a film that AI models make one scene at a time. The OUTLINE of the whole film is fixed (the message has it). Write the full prompts of scenes {first} to {last} only, '
    'in the order of the outline, as ONE JSON object and nothing else.\n'
    'WHAT THE STUDIO CAN DO:\n{abilities}\n'
    'RULES:\n'
    '- Every prompt is English, even when the person writes in another language. Words that are spoken, sung or written in the picture stay exactly as the person wrote them, '
    'in straight double quotes, with the language named.\n'
    '- Each scene follows its beat in the outline and goes on from the scene before it. The people and the places of the film look the same in every scene: copy the descriptions of the attached '
    'pictures and the cast of the outline into the prompt in the same words each time. A picture is never described by "the same man": name the description again.\n'
    '- "style.phrase" opens EVERY scene prompt unchanged.\n'
    '- Write COMPACT JSON: no indentation, one scene on one line. A long answer that is cut off is lost.\n'
    '{mode_rules}{speech_rules}'
    '- A scene prompt:\n' + LTX_RULES.strip() + '\n'
    'THE JSON SHAPE: {{"scenes": [{{"scene": {first}, "prompt": "...", "keyframe": null}}], {speech_shape}}} with exactly {count} scenes, numbered {first} to {last}.')
OUTLINE_FROM = 8           # a film of more scenes than this is planned in levels; the shorter ones in one answer, as measured with the real model
OUTLINE_GROUP = 20         # beats written in one answer
CHUNK_SCENES = 6           # full scene prompts written in one answer
# What an answer costs, in tokens, as the real model showed on 2026-11-06: it indents its JSON, which doubles the tokens of the text. An outline of 25 beats with its header was
# cut off at 3100 tokens in the middle of the last scene and the whole answer was lost, twice. The room is cut when the question leaves less (see `_ask_json`).
BEAT_TOKENS, OUTLINE_HEADER_TOKENS = 130, 1500
SCENE_TOKENS = dict(normal=330, ultra=450)
MAX_ANSWER_TOKENS = 8192
NUM_CTX = 12288            # the model's room (the card holds the model, 13.7 GB, and little more); a question that does not fit is shortened, not sent
CHARS_PER_TOKEN = 3        # a cautious guess for Russian and English mixed (English alone is nearer 4)

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

VOICES_OUTLINE = '- The person attached VOICES (listed in the message). Say in "speakers" which of them speak in each scene; the lines themselves are written later.\n'

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
        self.progress = None            # called with dict(stage, first, last, total) before each question of a plan; the page shows where a plan of many scenes is

    def _record(self, request, answer, result, problem, stage=None):
        """Keep what Gemma answered, one JSON line for each try (the log holds the last ~200 KB): "why does my plan have no music" is answered by reading it.

        `stage` names the part of a long film's plan the answer belongs to ("outline", "scenes 7-12"); a plan written in one answer has none."""
        if self.log is None:
            return
        row = dict(at=time.strftime('%Y-%m-%d %H:%M:%S'), prompt=request['prompt'][:300], seconds=request['seconds'], scenes=request['scenes'], ultra=request.get('ultra') is True, answer=str(answer)[:8000],
                   problem=str(problem) if problem else None, stage=stage, chars=len(str(answer)),
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

    def _tell(self, stage, first, last, total):
        if self.progress is not None:
            try:
                self.progress(dict(stage=stage, first=first, last=last, total=total))
            except Exception:
                pass                       # a page that cannot be told never fails a plan

    def _ask(self, messages, cancel, **options):
        return self.ask(messages, cancel, **dict(dict(temperature=0.4, top_p=0.9, top_k=40, num_ctx=6144, num_predict=900), **options))

    def look(self, draft, cancel):
        """Describe the pictures and videos that have no caption yet and keep the captions in the draft. A picture that cannot be described gets a plain one: never a failure."""
        captions = draft.captions()
        todo = [row for row in draft.items() if str(row['number']) not in captions and row['kind'] in ('photo', 'video')]
        photos = [row for row in todo if row['kind'] == 'photo']
        for start in range(0, len(photos), PHOTOS_PER_CALL):
            batch = photos[start:start + PHOTOS_PER_CALL]
            self._tell('look', start + 1, start + len(batch), len(todo))
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
        for looked, row in enumerate((r for r in todo if r['kind'] == 'video'), len(photos) + 1):
            if cancel.is_set():
                break
            self._tell('look', looked, looked, len(todo))
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
            if request['scenes'] > OUTLINE_FROM:
                raw = self._plan_in_levels(cancel, request, capabilities, items, captions, ultra, has_songs, has_voices, language)
                result = plan.normalize(raw, request, capabilities, materials)
                self._record(request, json.dumps(raw, ensure_ascii=False), result, None, stage='merged')
                return result
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
            self._tell('plan', 1, request['scenes'], request['scenes'])
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

    # --- a long film: the outline, then the scenes in groups ----------------------------------------------------------------------------------
    def _ask_json(self, messages, cancel, request, stage, num_predict, check: Callable[[dict], tuple[dict, list]]) -> tuple[dict, list]:
        """One JSON answer from Gemma, checked by `check` (which returns what to keep or raises ValueError with what is wrong); a wrong answer is sent back once.

        The question is measured first: what does not fit into the model's room together with the answer would silently lose its beginning (the rules), so the answer's room is cut instead.
        An answer that ends without its closing brace was cut off by its own length, which is not a mistake of content: the second try is told to be shorter and gets half as much room again,
        and the cut answer is not sent back (it would take the room that the shorter one needs)."""
        num_predict = max(1500, min(num_predict, MAX_ANSWER_TOKENS))
        problem = None
        for _ in range(2):
            # A correction adds the rejected answer to the context. Recheck each call; a minimum answer size
            # must never override the remaining room and silently evict the original instructions.
            used = sum(len(message['content']) for message in messages) // CHARS_PER_TOKEN
            room = NUM_CTX - used - 300
            if room < 1500:
                raise ValueError(ui_text('director.brain_context_too_large'))
            num_predict = min(num_predict, room)
            answer = self._ask(messages, cancel, temperature=0.6, num_ctx=NUM_CTX, num_predict=num_predict)
            if cancel.is_set():
                raise ValueError(ui_text('director.brain_planning_was_stopped'))
            try:
                kept = check(plan.read_object(answer))
                self._record(request, answer, None, None, stage)
                return kept
            except ValueError as error:
                problem = error
                self._record(request, answer, None, error, stage)
                if not answer.rstrip().removesuffix('```').rstrip().endswith('}'):
                    note = 'Your previous answer was cut off because it was too long. Write shorter beats and prompts, write compact JSON, and answer again with only the JSON object.'
                    messages = messages[:-1] + [dict(messages[-1], content=messages[-1]['content'] + '\n\n' + note)]
                    num_predict = min(int(num_predict * 1.5), MAX_ANSWER_TOKENS)
                else:
                    messages = messages + [dict(role='assistant', content=answer), dict(role='user', content='Wrong: %s. Answer again with only the JSON object.' % error)]
        raise ValueError(ui_text('director.brain_plan_not_written', problem))

    @staticmethod
    def _beats(value, count):
        """The checked beats of an outline answer: a list of `count` dicts (seconds, beat, references, speakers, transition)."""
        rows = value.get('scenes')
        if not isinstance(rows, list) or len(rows) < count:
            raise ValueError(ui_text('director.plan_wrong_scene_count', count, len(rows) if isinstance(rows, list) else 0))
        rows = rows[:count]            # the model sometimes writes on past the end of its part (45 beats for the 20 asked, 2026-11-06): the beats asked for are the first ones, the rest is written again by the next call
        def whole_numbers(row, key):
            return [n for n in row[key] if isinstance(n, int) and not isinstance(n, bool)] if isinstance(row.get(key), list) else []
        beats = []
        for number, row in enumerate(rows, 1):
            text = plan.clean_text(row.get('beat') if isinstance(row, dict) else None, 400)
            if len(text) < 5:
                raise ValueError(ui_text('director.plan_scene_prompt_missing', number))
            seconds = row.get('seconds')
            beats.append(dict(seconds=seconds if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0 else 0, beat=text,
                              references=whole_numbers(row, 'references'), speakers=whole_numbers(row, 'speakers'), transition=row.get('transition')))
        return beats

    @staticmethod
    def _outline_text(beats, first=0, last=0):
        """The outline as numbered lines for a question: the beats of the scenes asked about and their neighbours in full, the others cut short (a film of 75 scenes must leave room for the answer)."""
        lines = []
        for number, row in enumerate(beats, 1):
            near = first - 3 <= number <= last + 3 or not first
            beat = row['beat'] if near else ' '.join(row['beat'].split()[:12])
            who = ' (voices %s)' % ', '.join(map(str, row['speakers'])) if row['speakers'] else ''
            pictures = ' [pictures %s]' % ', '.join(map(str, row['references'])) if row['references'] else ''
            lines.append('%d. %.1f s%s%s: %s' % (number, row['seconds'] or 0, pictures, who, beat))
        return '\n'.join(lines)

    def _plan_in_levels(self, cancel, request, capabilities, items, captions, ultra, has_songs, has_voices, language):
        """The raw plan (the shape `plan.normalize` checks) of a film of many scenes: the outline of the story in groups of `OUTLINE_GROUP` beats, then the prompts of the scenes
        in groups of `CHUNK_SCENES`, each group seeing the whole outline, the cast and the scene written just before it."""
        count, seconds = request['scenes'], request['seconds']
        hints = 'Format: %s. Quality: %s.' % (request.get('orientation') or 'choose it from the idea', request.get('quality') or 'choose it from the idea')
        if has_voices:
            hints += ' The idea is written in %s: the words of the voices are in it unless the idea asks for another language.' % request['speech_language']
        idea = 'Idea of the person:\n%s\n\nThe film: %d seconds, exactly %d scenes (about %.0f seconds each). %s' % (request['prompt'].strip(), seconds, count, seconds / count, hints)
        mode_rules = ULTRA_RULES if ultra else NORMAL_RULES
        voices = [row for row in items if row['kind'] == 'voice']
        # Level 1: the outline. The first answer also carries what belongs to the whole film (title, summary, style, music, songs, cast).
        head, beats = {}, []
        for first in range(1, count + 1, OUTLINE_GROUP):
            last = min(count, first + OUTLINE_GROUP - 1)
            if first == 1:
                system = OUTLINE.format(seconds=seconds, scenes=count, extent=OUTLINE_PART.format(last=last, scenes=count) if last < count else '', abilities=abilities(capabilities, has_voices, ultra), language=language, mode_rules=mode_rules,
                                        materials_rules=(SONGS_RULES if has_songs else '') + (VOICES_OUTLINE if has_voices else ''),
                                        music_shape=('"music": {"engine": "...", "mode": "instrumental", "caption": "...", "lyrics": "", "language": "en", "gain_db": -16, "start": 0}, '
                                                     if capabilities.get('music') else '"music": null, '),
                                        shape='"songs": [], ' if has_songs else '')
                user = idea + '\n\n' + self.listing(items, captions)
            else:
                system = OUTLINE_MORE.format(first=first, last=last, scenes=count, seconds=seconds, count=last - first + 1)
                known = 'Title: %s\nStyle: %s\nCast: %s\n\nThe outline so far:\n%s' % (head['title'], head['style'].get('phrase', ''), ' | '.join(head['cast']) or 'none', self._outline_text(beats))
                user = idea + '\n\n' + known + '\n\nWrite the beats of scenes %d to %d now.' % (first, last)
            group = last - first + 1
            self._tell('outline', first, last, count)
            found = self._ask_json([dict(role='system', content=system), dict(role='user', content=user)], cancel, request, 'outline %d-%d' % (first, last), BEAT_TOKENS * group + (OUTLINE_HEADER_TOKENS if first == 1 else 300),
                                   lambda value, group=group: (value, self._beats(value, group)))
            if first == 1:
                value = found[0]
                head = dict(value, title=plan.clean_text(value.get('title'), 120, 'Film'),
                            style=value.get('style') if isinstance(value.get('style'), dict) else {},
                            cast=[plan.clean_text(item, 400) for item in (value.get('cast') if isinstance(value.get('cast'), list) else []) if isinstance(item, str) and item.strip()][:8])
            beats += found[1]
        # Level 2: the full prompts, a few scenes at a time.
        written, spoken = {}, []
        for first in range(1, count + 1, CHUNK_SCENES):
            last = min(count, first + CHUNK_SCENES - 1)
            needed = {number for row in beats[first - 1:last] for number in row['references']}
            shown = [row for row in items if row['kind'] == 'voice' or row['kind'] == 'photo' and row['number'] in needed]
            speakers = {number for row in beats[first - 1:last] for number in row['speakers']}
            unused = [row['number'] for row in voices if row['number'] not in {line.get('voice') for line in spoken}] if has_voices and last == count else []
            system = CHUNK.format(first=first, last=last, count=last - first + 1, abilities=abilities(capabilities, has_voices, ultra), mode_rules=mode_rules,
                                  speech_rules=SPEECH_RULES if has_voices else '', speech_shape='"speech": [] ' if has_voices else '')
            user = ('%s\n\nSTYLE PHRASE (opens every prompt): %s\nTHE CAST WITHOUT PICTURES (copy these words into every scene where they appear):\n%s\n\n%s\n\nTHE OUTLINE OF THE FILM:\n%s\n\n%s'
                    'Write the prompts of scenes %d to %d now.') % (
                idea, plan.clean_text(head['style'].get('phrase'), 300), '\n'.join('- ' + item for item in head['cast']) or 'none', self.listing(shown, captions),
                self._outline_text(beats, first, last), ('The scene just before (already written):\n%s\n\n' % written[first - 1]['prompt']) if first - 1 in written else '', first, last)
            if unused:
                user += ' The voices %s have not spoken yet: they must speak in these scenes.' % ', '.join(map(str, unused))
            elif speakers:
                user += ' Write lines for the voices the outline names in these scenes.'
            self._tell('scenes', first, last, count)
            limit = SCENE_TOKENS['ultra' if ultra else 'normal'] * (last - first + 1) + 400 + (int(seconds / count * (last - first + 1) * rules.SPEECH_CHARS_PER_SECOND / 2) if has_voices else 0)
            rows, lines = self._ask_json([dict(role='system', content=system), dict(role='user', content=user)], cancel, request, 'scenes %d-%d' % (first, last), limit,
                                         lambda value, first=first, last=last: self._chunk(value, first, last))
            written.update(rows)
            spoken += lines
        raw = {key: head.get(key) for key in ('title', 'summary', 'orientation', 'quality', 'style', 'music', 'songs')}
        raw['scenes'] = [dict(seconds=row['seconds'], prompt=written[number]['prompt'], references=row['references'], keyframe=written[number]['keyframe'], transition=row['transition'])
                         for number, row in enumerate(beats, 1)]
        raw['speech'] = spoken
        return raw

    @staticmethod
    def _chunk(value, first, last):
        """(the prompts by scene number, the lines of speech of these scenes) of a group answer; ValueError when a scene of the group has no prompt."""
        rows = value.get('scenes')
        count = last - first + 1
        if not isinstance(rows, list):
            raise ValueError(ui_text('director.plan_wrong_scene_count', count, 0))
        wanted = set(range(first, last + 1))
        numbered = all(isinstance(row, dict) and isinstance(row.get('scene'), int) for row in rows) and wanted <= {row['scene'] for row in rows}
        if numbered:                    # numbered by the model: the scenes asked for, whatever else it wrote
            rows = [row for row in rows if row['scene'] in wanted]
        elif len(rows) != count:
            raise ValueError(ui_text('director.plan_wrong_scene_count', count, len(rows)))
        found = {}
        for position, row in enumerate(rows):
            number = row['scene'] if numbered else first + position                # an answer without numbers is taken in order
            prompt = plan.clean_text(row.get('prompt') if isinstance(row, dict) else None, 1500)
            if len(prompt) < 20:
                raise ValueError(ui_text('director.plan_scene_prompt_missing', number))
            found[number] = dict(prompt=prompt, keyframe=plan.clean_text(row.get('keyframe'), 600) or None)
        lines = [line for line in (value.get('speech') if isinstance(value.get('speech'), list) else []) if isinstance(line, dict) and isinstance(line.get('scene'), int) and first <= line['scene'] <= last]
        return found, lines


def _tag(row):
    """` [label]` when the person named the attachment, so that Gemma can match a voice to a hero by name."""
    return ' [%s]' % row['label'] if row.get('label') else ''
