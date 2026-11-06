"""The numbers and rules of the Director: how long a film may be, how many scenes it may have, what may be attached and how one scene is asked for.

The Director page repeats `scene_range` for its fields and takes the groups of materials from `limits()`; `tests/test_director_rules.py` and
`tests/test_director_panel.py` keep the page and these rules equal.
"""
import math

from ui_strings import source as ui_text
from video.ltx_continuation import MAX_CHAIN_FRAMES as CHAIN_LIMIT, chain_frames, windows as ltx_windows

MIN_SECONDS, MAX_SECONDS = 5, 900            # a quarter of an hour: 75 scenes at most; the shooting can be continued after a stop, which hours-long films need
ADVICE_MAX_SECONDS = 300                    # the longest film that the advice on running time suggests (a longer one is the person's decision)
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
DISK_BASE_BYTES, DISK_PER_MINUTE_BYTES = 3 * 1024 ** 3, 150 * 1024 ** 2

# Picture size and LTX quality of a film: LTX 2.5 draws 768x512 in one pass ("draft"), and 1024x576 or 1280x704 in two passes.
QUALITIES = {
    'draft': dict(landscape=(768, 512), portrait=(512, 768), ltx='preview'),
    'standard': dict(landscape=(1024, 576), portrait=(576, 1024), ltx='standard'),
    'max': dict(landscape=(1280, 704), portrait=(704, 1280), ltx='standard'),
}
# Two ways to shoot a film, chosen with the "Ultra" toggle of the page (on by default). A profile fixes what the planner can only be advised about: the picture quality used when nobody
# chose one, which scenes get a keyframe ("all", or "several": only a scene that shows several pictures together) and which music engines come first. Ultra spends the strongest
# settings the studio has: the largest LTX size, a keyframe for every scene and the heaviest music engine. Measured on 2026-11-06, the largest LTX size costs little more than the standard
# one (a window of 121 frames takes 56 s against 52), so what makes Ultra slow is the keyframes and the engines, not the picture. The picture model (Klein) has one quality level, so the
# pictures gain in number and in the detail of their instructions, not in steps.
PROFILES = dict(
    ultra=dict(quality='max', keyframes='all', music=('minimax-music3-int8', 'ace-step-15-turbo', 'stable-audio-3-medium')),
    normal=dict(quality='standard', keyframes='several', music=('ace-step-15-turbo', 'stable-audio-3-medium', 'minimax-music3-int8')),
)
SHOT_FRAMES = (49, 97, 121)          # one LTX generation: 2.0, 4.0 and 5.0 seconds
# Longer scenes are one continued take whose length fits the scene (`chain_frames`: 6 s to 60 s in steps of a third of a second or so, two to fifteen windows). They used to be
# rounded up to 15, 30 or 60 seconds, which made a scene of 6.6 s cost four windows instead of two (and a 16 s scene eight instead of four).

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
    """The Video request for one scene: a single LTX shot up to five seconds, otherwise one continuous take as long as the scene needs (trimmed later).

    `seconds` already contains the overlap that the transition into the next scene eats. A scene of the longest length a scene may have (60 s) plus that overlap is 60.6 s, which is
    more than the longest take LTX can continue (1440 frames): it is asked for the longest take, and the transition eats 0.6 s of the scene's end instead (the film is up to 0.6 s
    short for each such scene, and the run says so). Without this the film failed after hours of work, in the shot of the first scene of that length."""
    size = QUALITIES[quality]
    width, height = size[orientation]
    need = seconds * FPS
    if seconds <= MAX_SCENE_SECONDS + TRANSITION_SECONDS + 1e-6:
        need = min(need, float(CHAIN_LIMIT))
    frames = next((n for n in SHOT_FRAMES if n >= need - 1e-6), None) or chain_frames(need - 1e-6)
    if frames is None:
        raise ValueError(ui_text('director.rules_scene_too_long', int(MAX_SCENE_SECONDS)))
    return dict(engine='ltx25-distilled', mode='i2v' if has_image else 't2v', quality=size['ltx'], width=width, height=height, frames=frames, fps=FPS, seed=str(seed))


def disk_needed_bytes(seconds):
    """Free space a film of `seconds` needs on the drive of its work files and on the drive of the video scratch folder: the scene clips (kept until the film is done), the continuation windows of
    the scene being shot, the tracks and the film itself. Generous on purpose: a full disk in the middle of a scene stops the film hours after its start."""
    return DISK_BASE_BYTES + DISK_PER_MINUTE_BYTES * math.ceil(seconds / 60)


def profile(ultra):
    """The profile of the Ultra toggle. Only a real True asks for Ultra: a flag that came from a page or a script in any other shape is the normal profile."""
    return PROFILES['ultra' if ultra is True else 'normal']


def music_order(engines, seconds, ultra):
    """The music engines (dicts with `id` and `max_duration`) in the order the planner is told to prefer them: those whose longest track covers the film first (a shorter track
    would have to be made again and again), each group in the order of the profile (Ultra the strongest, normal the fastest) and then the longer track first.

    When no engine covers the film the longest track comes first, whatever the profile says: a film of ten minutes needs four tracks of three minutes or eleven of one minute,
    and every track is a model run and a seam."""
    order = {name: place for place, name in enumerate(profile(ultra)['music'])}

    def preference(engine):
        covers = engine['max_duration'] >= seconds
        return (not covers, order.get(engine['id'], len(order)) if covers else 0, -engine['max_duration'], order.get(engine['id'], len(order)))
    return sorted(engines, key=preference)


# Seconds of work, measured on an RTX 5070 Ti with the warm file cache (docs/VisualForge-Director.md, "What was measured", calibration of 2026-11-06). A scene is one LTX job: it pays
# `SCENE_SETUP_SECONDS` (the encoder reads the prompt in 8 s, the clip is assembled and copied, the page polls) and, for every window it is made of, `WINDOW_BASE_SECONDS` (the worker starts, the
# weights come from the file cache, the picture is decoded) plus `WINDOW_FRAME_SECONDS` for each frame of the window: a window of 121 frames takes 35 s at draft, 50 at standard and 57 at maximum
# (least squares over 31 windows of 13 jobs; the jobs themselves left 7 to 35 s beside their windows, 15 s typically).
# A continued take generates 25 frames more than it keeps in every window after the first, which is why a scene is counted by its windows' frames, not by its length.
# The first scene of a film also pays for reading the video model from the disk (`VIDEO_LOAD_SECONDS`: the first job of the session took 374 s against 45 s warm).
SCENE_SETUP_SECONDS = 15
WINDOW_BASE_SECONDS = dict(draft=18.5, standard=26.3, max=22.2)
WINDOW_FRAME_SECONDS = dict(draft=0.133, standard=0.196, max=0.291)
VIDEO_LOAD_SECONDS = 300
KEYFRAME_SECONDS, KEYFRAME_LOAD_SECONDS = 15, 95            # Klein: 14-15 s a keyframe, 109 s for the first one (the model is read from the disk); measured in the Ultra film
SPEECH_LOAD_SECONDS, SPEECH_LINE_SECONDS = 60, 12
MUSIC_TRACK_SECONDS, MUSIC_LOAD_SECONDS = 75, 60
ASSEMBLE_PER_FILM_SECOND, MIX_PER_FILM_SECOND, MIX_FIXED_SECONDS = 0.11, 0.04, 20


def scene_windows(seconds):
    """The frames of each window of the video model that a clip of `seconds` costs (the scene plus the overlap of its transition): one window for a single shot, else those of the continued take."""
    frames = scene_job(seconds, 'draft', 'landscape', False, 0)['frames']
    return [frames] if frames <= SHOT_FRAMES[-1] else [window['frames'] for window in ltx_windows(frames)]


def scene_seconds(seconds, quality):
    """The estimated work of shooting one scene whose clip is `seconds` long."""
    return SCENE_SETUP_SECONDS + sum(WINDOW_BASE_SECONDS[quality] + WINDOW_FRAME_SECONDS[quality] * frames for frames in scene_windows(seconds))


def clip_seconds(lengths):
    """The clip each scene is shot as: its length plus the overlap of the transition into the next scene (the last scene has none)."""
    return [round(seconds + (TRANSITION_SECONDS if index + 1 < len(lengths) else 0), 3) for index, seconds in enumerate(lengths)]


def estimate_seconds(lengths, quality, keyframes=0, music_tracks=0, speech_lines=0, voices=0):
    """A rough wall-time guess for the plan screen, in seconds: the scenes (by their windows), the loads of the models, the keyframes, the tracks, the voices and the assembly."""
    total = VIDEO_LOAD_SECONDS + sum(scene_seconds(clip, quality) for clip in clip_seconds(lengths))
    total += (KEYFRAME_LOAD_SECONDS + KEYFRAME_SECONDS * keyframes) if keyframes else 0
    total += (MUSIC_LOAD_SECONDS + MUSIC_TRACK_SECONDS * music_tracks) if music_tracks else 0
    total += (SPEECH_LOAD_SECONDS * max(1, voices) + SPEECH_LINE_SECONDS * speech_lines) if speech_lines else 0
    return total + (ASSEMBLE_PER_FILM_SECOND + MIX_PER_FILM_SECOND) * sum(lengths) + MIX_FIXED_SECONDS


def estimate_minutes(seconds, quality, keyframes, music, speech_lines=0, scenes=2, lengths=None, music_tracks=1, voices=1):
    """The estimate in whole minutes (at least two); `lengths` are the planned scene lengths, else the film is taken as `scenes` scenes of equal length."""
    lengths = lengths or [seconds / scenes] * scenes
    return max(2, round(estimate_seconds(lengths, quality, keyframes, music_tracks if music else 0, speech_lines, voices) / 60))


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
        full = int(min(ADVICE_MAX_SECONDS, round(sum(min(ADVICE_MAX_SECONDS, length) for length in songs))))
        low, high = max(low, min(60, full)), max(high, full)
        reasons.append(dict(code='songs', count=len(songs), seconds=full))
        if any(length > ADVICE_MAX_SECONDS for length in songs):
            reasons.append(dict(code='song_excerpt', seconds=ADVICE_MAX_SECONDS))
    low, high = min(ADVICE_MAX_SECONDS, max(MIN_SECONDS, low)), min(ADVICE_MAX_SECONDS, max(MIN_SECONDS, high))
    ideal = min(ADVICE_MAX_SECONDS, max(low, full if songs else min(high, 30)))
    if not reasons:                                                          # nothing asks for more: the general advice
        reasons.append(dict(code='baseline', low=low, high=high))
    # The scenes that go with it: one for every place, within what the length allows (the page fills the field when the advice is taken).
    fewest, most = scene_range(ideal)
    return dict(low=low, high=max(low, high), ideal=ideal, scenes=min(most, max(fewest, count['place'], -(-ideal // 15))), reasons=reasons)

