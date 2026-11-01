"""The numbers and rules of the Director: how long a film may be, how many scenes it may have, what may be attached and how one scene is asked for.

The Director page repeats `scene_range` for its fields and takes the groups of materials from `limits()`; `tests/test_director_rules.py` and
`tests/test_director_panel.py` keep the page and these rules equal.
"""
import math

from ui_strings import source as ui_text

MIN_SECONDS, MAX_SECONDS = 5, 300
MIN_SCENES, SCENES_PER_MINUTE = 2, 5
MIN_SCENE_SECONDS, MAX_SCENE_SECONDS = 2.0, 60.0
MAX_VIDEO_SECONDS, MAX_VIDEO_BYTES = 600, 2 * 1024 ** 3
MAX_PHOTO_BYTES = 100 * 1024 ** 2
MAX_SONG_SECONDS, MAX_SONG_BYTES = 3600, 1536 * 1024 ** 2         # an hour of lossless stereo at 48 kHz and 24 bits is about a gigabyte
MAX_VOICE_BYTES, MAX_VOICE_SOURCE_SECONDS = 200 * 1024 ** 2, 1800
VOICE_SAMPLE_SECONDS, MIN_VOICE_SECONDS = 30, 3                  # a timbre needs a few seconds of clean speech; more than half a minute adds nothing
ORIENTATIONS = ('landscape', 'portrait')
# ffmpeg `xfade` names; 'cut' is no transition at all. A transition overlaps the end of one scene with the start of the next.
TRANSITIONS = ('cut', 'fade', 'dissolve', 'fadeblack', 'wipeleft', 'slideright', 'circleopen', 'smoothleft')
TRANSITION_SECONDS = 0.6
FPS = 24

# Picture size and LTX quality of a film: LTX 2.5 draws 768x512 in one pass ("draft"), and 1024x576 or 1280x704 in two passes.
QUALITIES = {
    'draft': dict(landscape=(768, 512), portrait=(512, 768), ltx='preview'),
    'standard': dict(landscape=(1024, 576), portrait=(576, 1024), ltx='standard'),
    'max': dict(landscape=(1280, 704), portrait=(704, 1280), ltx='standard'),
}
# Two ways to shoot a film, chosen with the "Ultra" toggle of the page (on by default). A profile fixes what the planner can only be advised about: the picture quality used when nobody
# chose one, which scenes get a keyframe ("all", or "several": only a scene that shows several pictures together) and which music engines come first. Ultra spends the strongest
# settings the studio has: the largest LTX size is about 2.3 times slower than the standard one (70 against 30 seconds of work for a second of film, see `estimate_minutes`), and a keyframe
# for every scene and the heaviest music engine add to it; normal is the usual quality in a good part of that time. The picture model (Klein) has one quality level, so the pictures gain
# in number and in the detail of their instructions, not in steps.
PROFILES = dict(
    ultra=dict(quality='max', keyframes='all', music=('minimax-music3-int8', 'ace-step-15-turbo', 'stable-audio-3-medium')),
    normal=dict(quality='standard', keyframes='several', music=('ace-step-15-turbo', 'stable-audio-3-medium', 'minimax-music3-int8')),
)
SHOT_FRAMES = (49, 97, 121)          # one LTX generation: 2.0, 4.0 and 5.0 seconds
CHAIN_FRAMES = (360, 720, 1440)      # LTX continuation windows: 15, 30 and 60 seconds as one take

# What the person can attach. One row per kind of material: the page builds its blocks from this list (the bridge sends it with the limits), a draft refuses a file
# over the limit of its group, and the planner is told what each group is for. A new kind of picture, song or voice is one row here plus its strings
# (`director.group_<id>_*`, `director.slot_<id>_*`); a new kind of file also needs a reader in media.py.
# `media` is how a file is read and kept: photo (PNG), video (H.264 MP4), audio (a song kept as AAC) or voice (a 24 kHz mono WAV sample to clone).
GROUPS = (
    dict(id='hero', media='photo', limit=10),
    dict(id='place', media='photo', limit=20),
    dict(id='prop', media='photo', limit=10),
    dict(id='video', media='video', limit=8),
    dict(id='song', media='audio', limit=6),
    dict(id='narrator', media='voice', limit=3),
    dict(id='character', media='voice', limit=10),
)
MEDIA_OF = {row['id']: row['media'] for row in GROUPS}
LIMIT_OF = {row['id']: row['limit'] for row in GROUPS}
DEFAULT_GROUP = dict(photo='hero', video='video', audio='song', voice='narrator')      # for drafts that predate the groups and for files dropped without a block

# Speech: about twelve characters a second leaves room for the pauses between sentences; a line is a few sentences, never a speech.
SPEECH_CHARS_PER_SECOND, MAX_LINE_CHARS, MAX_LINES_PER_SCENE = 12, 300, 3
SPEECH_LANGUAGES = ('Russian', 'English', 'German', 'French', 'Spanish', 'Italian', 'Portuguese', 'Chinese', 'Japanese', 'Korean')       # the names the Voiceover module takes
MAX_SONG_CUES, CUE_CROSSFADE_SECONDS = 8, 2.0
SPEECH_SECONDS_PER_VOICE = 10        # a voice that was attached should get at least one line to say
ADVICE_CODES = ('baseline', 'places', 'voices', 'songs', 'song_excerpt')

_ENGLISH_NAMES = dict(hero='Hero', place='Place', prop='Object', video='Reference video', song='Song')


def pick(value, options):
    """`value` when it is one of `options` (a tuple or a dict of names), else None: a value from a page or from Gemma may be of any type, a list included, which a dict lookup
    would refuse with an error instead of an answer."""
    return value if isinstance(value, str) and value in options else None


def group_of(identifier):
    """The row of a group, or ValueError for a name that is not one."""
    for row in GROUPS:
        if row['id'] == identifier:
            return row
    raise ValueError(ui_text('director.rules_unknown_group'))


def slot_name(identifier, position):
    """The English name of the `position`-th (counted from 1) attachment of a group, the way the planner is told it: "Narrator 2", "Second main character"."""
    if identifier == 'narrator':
        return 'Narrator' if position == 1 else 'Narrator %d' % position
    if identifier == 'character':
        return 'Main character' if position == 1 else 'Second main character' if position == 2 else 'Character %d' % position
    return '%s %d' % (_ENGLISH_NAMES[identifier], position)


def scene_range(seconds):
    """(fewest, most) scenes of a film of `seconds`: exactly two below a minute (level 0), then up to five for every minute, and at least one for every minute because a scene
    lasts a minute at most (a five-minute film cannot be two scenes)."""
    if seconds < 60:
        return MIN_SCENES, MIN_SCENES
    return max(MIN_SCENES, math.ceil(seconds / 60)), max(MIN_SCENES, int(seconds * SCENES_PER_MINUTE // 60))


def check_request(seconds, scenes):
    """A ValueError with a message for the person unless the length and the number of scenes go together."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise ValueError(ui_text('director.rules_seconds_range', MIN_SECONDS, MAX_SECONDS))
    if isinstance(scenes, bool) or not isinstance(scenes, int):
        raise ValueError(ui_text('director.rules_scenes_whole_number'))
    fewest, most = scene_range(seconds)
    if not fewest <= scenes <= most:
        raise ValueError(ui_text('director.rules_scenes_for_length_below_minute', scenes, MIN_SCENES) if seconds < 60
                         else ui_text('director.rules_scenes_for_length', scenes, fewest, most, SCENES_PER_MINUTE))


def scene_job(seconds, quality, orientation, has_image, seed):
    """The Video request for one scene: a single LTX shot up to five seconds, otherwise one continuous take of 15, 30 or 60 seconds (trimmed later).

    `seconds` already contains the overlap that the transition into the next scene eats."""
    size = QUALITIES[quality]
    width, height = size[orientation]
    need = seconds * FPS
    frames = next((n for n in SHOT_FRAMES if n >= need - 1e-6), None) or next((n for n in CHAIN_FRAMES if n >= need - 1e-6), None)
    if frames is None:
        raise ValueError(ui_text('director.rules_scene_too_long', int(MAX_SCENE_SECONDS)))
    return dict(engine='ltx25-distilled', mode='i2v' if has_image else 't2v', quality=size['ltx'], width=width, height=height, frames=frames, fps=FPS, seed=str(seed))


def profile(ultra):
    """The profile of the Ultra toggle. Only a real True asks for Ultra: a flag that came from a page or a script in any other shape is the normal profile."""
    return PROFILES['ultra' if ultra is True else 'normal']


def music_order(engines, seconds, ultra):
    """The music engines (dicts with `id` and `max_duration`) in the order the planner is told to prefer them: those whose longest track covers the film first (a shorter track would
    have to loop), each group in the order of the profile (Ultra the strongest, normal the fastest) and then the longer track first."""
    order = {name: place for place, name in enumerate(profile(ultra)['music'])}
    return sorted(engines, key=lambda engine: (engine['max_duration'] < seconds, order.get(engine['id'], len(order)), -engine['max_duration']))


def estimate_minutes(seconds, quality, keyframes, music, speech_lines=0, scenes=2):
    """A rough wall-time guess for the plan screen, from what was measured on an RTX 5070 Ti: seconds of film times a factor per quality, a minute for every scene
    (sampling, decoding, the upsampler), the loads of the models (the video model alone takes about four minutes from a cold hard disk) and the assembly.

    Measured once, 2026-11-01: a draft film of 10 s in two scenes with two voices and a song of the person took 10.6 minutes (speech 1.7, the video model's load and
    the first scene 6.5, the second scene 2, assembly and sound 0.3)."""
    per_second = dict(draft=12, standard=30, max=70)[quality]
    return max(2, round((seconds * per_second + 60 * scenes + 90 * keyframes + (150 if music else 0) + (150 + 12 * speech_lines if speech_lines else 0) + 240 + 60) / 60))


def advise(items):
    """The running time that suits the attached materials, in seconds: dict(low, high, ideal, reasons). Each reason is dict(code, ...numbers) of ADVICE_CODES.

    A scene is shot in four to eight seconds at best and a film has at most five scenes a minute, so the places that were attached (one scene each), the voices
    (a line each) and the songs (a whole song is a natural length) all push the running time up; with nothing attached any length from 10 to 60 seconds does well."""
    count = {row['id']: sum(1 for item in items if item.get('group') == row['id']) for row in GROUPS}
    songs = [float(item['seconds']) for item in items if item.get('group') == 'song' and item.get('seconds')]
    voices = count['narrator'] + count['character']
    low, high, reasons = 10, 60, []
    if count['place'] > MIN_SCENES:
        need = max(60, -(-count['place'] * 60 // SCENES_PER_MINUTE))          # more than two scenes need a film of a minute, then five scenes a minute: one scene for every place
        low, high = max(low, need), max(high, need)
        reasons.append(dict(code='places', count=count['place'], seconds=need))
    if voices:
        need = SPEECH_SECONDS_PER_VOICE * voices
        low, high = max(low, need), max(high, need)
        reasons.append(dict(code='voices', count=voices, seconds=need))
    full = 0
    if songs:
        full = int(min(MAX_SECONDS, round(sum(min(MAX_SECONDS, length) for length in songs))))
        low, high = max(low, min(60, full)), max(high, full)
        reasons.append(dict(code='songs', count=len(songs), seconds=full))
        if any(length > MAX_SECONDS for length in songs):
            reasons.append(dict(code='song_excerpt', seconds=MAX_SECONDS))
    low, high = min(MAX_SECONDS, max(MIN_SECONDS, low)), min(MAX_SECONDS, max(MIN_SECONDS, high))
    ideal = min(MAX_SECONDS, max(low, full if songs else min(high, 30)))
    if not reasons:                                                          # nothing asks for more: the general advice
        reasons.append(dict(code='baseline', low=low, high=high))
    # The scenes that go with it: one for every place, within what the length allows (the page fills the field when the advice is taken).
    fewest, most = scene_range(ideal)
    return dict(low=low, high=max(low, high), ideal=ideal, scenes=min(most, max(fewest, count['place'], -(-ideal // 15))), reasons=reasons)

