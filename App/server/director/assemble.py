"""Join the scene clips of a film: trim each to its length, hand over with the planned transitions, grade the colour, level the sound, encode once.

Every clip is generated a little longer than its scene (the length of the transition into the next scene is added), so the overlap of a transition eats only
what was added and the film keeps exactly the planned length. The picture is cut to the exact frame count and the sound to the exact sample count.
"""
from pathlib import Path

from director import media
from director import plan_rules as rules
from video.ltx_continuation import LEVELING
from video.video_delivery import run_tool

SAMPLE_RATE = 48000


def overlaps(scenes):
    """Seconds of transition after each scene: the last scene has none, a 'cut' has none."""
    return [0.0 if index == len(scenes) - 1 or row['transition'] == 'cut' else rules.TRANSITION_SECONDS for index, row in enumerate(scenes)]


def clip_lengths(scenes):
    """How long each clip must be at least: its scene plus the overlap that the transition after it consumes."""
    return [round(row['seconds'] + tau, 3) for row, tau in zip(scenes, overlaps(scenes), strict=True)]


def grade_filters(style):
    """ffmpeg filters of the colour grade (`warmth` -1..1 cold to warm, contrast, saturation); neutral values add nothing."""
    parts = []
    if abs(style['contrast'] - 1) > 0.005 or abs(style['saturation'] - 1) > 0.005:
        parts.append('eq=contrast=%.3f:saturation=%.3f' % (style['contrast'], style['saturation']))
    if abs(style['warmth']) > 0.005:
        warm = style['warmth']
        parts.append('colorbalance=rs=%.3f:bs=%.3f:rm=%.3f:bm=%.3f:rh=%.3f:bh=%.3f' % (warm * 0.05, -warm * 0.05, warm * 0.08, -warm * 0.08, warm * 0.04, -warm * 0.04))
    return parts


def graph(plan, durations, has_audio, size):
    """The filter_complex text and the exact length of the film. `durations`: the real length of each clip (they may be longer than needed)."""
    scenes = plan['scenes']
    width, height = size
    lengths, taus = clip_lengths(scenes), overlaps(scenes)
    filters = []
    for index, length in enumerate(lengths):
        length = min(length, durations[index])
        filters.append('[%d:v]trim=duration=%.3f,setpts=PTS-STARTPTS,scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1,fps=%d,format=yuv420p[v%d]'
                       % (index, length, width, height, width, height, rules.FPS, index))
        source = '[%d:a]atrim=duration=%.3f,asetpts=PTS-STARTPTS' % (index, length) if has_audio[index] else 'anullsrc=r=%d:cl=stereo,atrim=duration=%.3f' % (SAMPLE_RATE, length)
        filters.append('%s,aresample=%d,aformat=sample_fmts=fltp:channel_layouts=stereo,apad=whole_dur=%.3f,asetpts=PTS-STARTPTS[a%d]' % (source, SAMPLE_RATE, length, index))
    video, audio, running = 'v0', 'a0', min(lengths[0], durations[0])
    for index in range(1, len(scenes)):
        length, tau = min(lengths[index], durations[index]), taus[index - 1]
        if tau == 0:
            filters.append('[%s][v%d]concat=n=2:v=1:a=0[xv%d]' % (video, index, index))
            filters.append('[%s][a%d]concat=n=2:v=0:a=1[xa%d]' % (audio, index, index))
            running += length
        else:
            filters.append('[%s][v%d]xfade=transition=%s:duration=%.3f:offset=%.3f[xv%d]' % (video, index, scenes[index - 1]['transition'], tau, running - tau, index))
            filters.append('[%s][a%d]acrossfade=d=%.3f:c1=tri:c2=tri[xa%d]' % (audio, index, tau, index))
            running += length - tau
        video, audio = 'xv%d' % index, 'xa%d' % index
    total = min(running, float(plan['seconds']))
    frames = int(round(total * rules.FPS))
    seconds = frames / rules.FPS
    picture = grade_filters(plan['style']) + ['fade=t=in:st=0:d=0.4', 'fade=t=out:st=%.3f:d=0.6' % max(0.0, seconds - 0.6), 'fps=%d' % rules.FPS, 'trim=end_frame=%d' % frames, 'setpts=PTS-STARTPTS']
    filters.append('[%s]%s[video]' % (video, ','.join(picture)))
    filters.append('[%s]apad,atrim=end_sample=%d,asetpts=PTS-STARTPTS,%s,afade=t=in:st=0:d=0.3,afade=t=out:st=%.3f:d=0.8[audio]'
                   % (audio, int(round(seconds * SAMPLE_RATE)), LEVELING, max(0.0, seconds - 0.8)))
    return ';'.join(filters), frames


def assemble(ffmpeg, ffprobe, clips, plan, size, output, cancel=None, progress=None):
    """Write the film to `output`; returns dict(frames, seconds, command). `clips` are the scene files in order, `progress(fraction)` is optional."""
    if len(clips) != len(plan['scenes']):
        raise ValueError('Every scene needs its clip')
    infos = [media.probe(ffprobe, clip) for clip in clips]
    text, frames = graph(plan, [info['seconds'] for info in infos], [info['has_audio'] for info in infos], size)
    seconds = frames / rules.FPS
    command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-progress', 'pipe:1', '-nostats']
    for clip in clips:
        command += ['-i', str(Path(clip))]
    command += ['-filter_complex', text, '-map', '[video]', '-map', '[audio]', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
                '-r', str(rules.FPS), '-c:a', 'aac', '-b:a', '192k', '-ar', str(SAMPLE_RATE), '-ac', '2', '-t', '%.4f' % seconds, '-movflags', '+faststart', str(output)]
    run_tool(command, int(600 + seconds * 12), cancel, (lambda done: progress(min(1.0, done / seconds))) if progress else None)
    return dict(frames=frames, seconds=seconds, command=command)
