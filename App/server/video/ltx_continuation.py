"""Bounded temporal windows and a frame-exact audio/video continuation assembly."""
from pathlib import Path

LONG_FRAMES = (360, 720, 1440)          # the lengths the Video panel offers for one continuous take
WINDOW_FRAMES = 121
STRIDE_FRAMES = 96
OVERLAP_FRAMES = WINDOW_FRAMES - STRIDE_FRAMES
MIN_LAST_WINDOW = 49                     # the shortest window that was verified with a context in front of it (a single 49-frame clip is the shortest tested one)
MAX_CHAIN_FRAMES = 1440


def _chain_lengths():
    """Every frame count the chain can make exactly: whole strides of 96 frames plus a last window of 8n+1 frames (49 to 121).

    The panel offers only 15, 30 and 60 seconds, but a window costs the same however long the clip is, so a scene of 6.6 seconds asked for as 15 seconds paid for four windows
    where two are enough. The last window has to keep the model's 8n+1 grid, which is why not every length is possible (122 to 143 frames are not: 144 is the next)."""
    return tuple(sorted({strides * STRIDE_FRAMES + tail - 1 for strides in range(1, MAX_CHAIN_FRAMES // STRIDE_FRAMES + 1) for tail in range(MIN_LAST_WINDOW, WINDOW_FRAMES + 1, 8)
                         if strides * STRIDE_FRAMES + tail - 1 <= MAX_CHAIN_FRAMES}))


CHAIN_FRAMES = _chain_lengths()
# 240 frames is a total of the panel's "scenes" mode (ten seconds in several shots); the validator keeps refusing it as one continued take, so the Director asks for 248.
SCENES_ONLY_FRAMES = 240
PUBLIC_CHAIN_FRAMES = tuple(n for n in CHAIN_FRAMES if n != SCENES_ONLY_FRAMES)


def chain_frames(minimum):
    """The shortest chain length that reaches `minimum` frames, or None when even the longest does not."""
    return next((n for n in PUBLIC_CHAIN_FRAMES if n >= minimum), None)
# The generated sound is very uneven: speech reached -10..-25 dBFS while the ambience between the lines sat at -60..-68 dBFS, i.e. silence (measured
# 2026-10-31 on a 30 s clip). A dynamic normalizer raises the quiet stretches by up to 20x (about 26 dB) over a window of a quarter second and a
# smoothing of 15 frames, and a limiter keeps the peaks below -2 dBFS; on that clip the ambience came up to about -40 dBFS, the speech stayed
# between -14 and -25 dBFS and Whisper heard the same words.
LEVELING = 'dynaudnorm=f=250:g=15:p=0.6:m=20:s=0,alimiter=limit=0.8:level=disabled'


def windows(frames):
    if frames not in CHAIN_FRAMES:
        raise ValueError('Unsupported LTX continuation duration')
    # LTX's causal video grid is 8n+1. The last extra frame is trimmed during muxing.
    native_frames = frames + 1
    result = []
    start = 0
    while True:
        length = min(WINDOW_FRAMES, native_frames - start)
        result.append(dict(start=start, frames=length, overlap=OVERLAP_FRAMES if start else 0))
        if start + length >= native_frames:
            return result
        start += STRIDE_FRAMES


def assembly_command(ffmpeg, folder, parts, frames, fps=24):
    """Crossfade only the shared context, preserving exact requested frame/sample counts."""
    folder = Path(folder)
    plan = windows(frames)
    if len(parts) != len(plan):
        raise ValueError('LTX continuation is incomplete')
    command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n']
    for part in parts:
        path = Path(part).resolve()
        if not path.is_relative_to(folder.resolve()):
            raise ValueError('Continuation media outside owned job')
        command += ['-threads', '2', '-i', str(path)]
    filters = []
    for index, item in enumerate(plan):
        filters += [f'[{index}:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[v{index}]',
                    f'[{index}:a]atrim=end_sample={item["frames"] * 48000 // fps},asetpts=PTS-STARTPTS[a{index}]']
    video, audio = 'v0', 'a0'
    for index, item in enumerate(plan[1:], 1):
        next_video, next_audio = 'vx'+str(index), 'ax'+str(index)
        filters += [f'[{video}][v{index}]xfade=transition=fade:duration={OVERLAP_FRAMES/fps:.12f}:offset={item["start"]/fps:.12f}[{next_video}]',
                    f'[{audio}][a{index}]acrossfade=ns={OVERLAP_FRAMES*48000//fps}:c1=tri:c2=tri[{next_audio}]']
        video, audio = next_video, next_audio
    filters += [f'[{video}]fps={fps},trim=end_frame={frames},setpts=PTS-STARTPTS[video]',
                f'[{audio}]apad,atrim=end_sample={frames*48000//fps},asetpts=PTS-STARTPTS,{LEVELING}[audio]']
    graph = folder/'ltx-continuation-filter.txt'
    graph.write_text(';\n'.join(filters), encoding='utf-8')
    command += ['-filter_complex_threads', '2', '-filter_complex', ';'.join(filters),
                '-map', '[video]', '-map', '[audio]', '-c:v', 'libx264', '-threads', '4',
                '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-r', str(fps),
                '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-t', str(frames/fps),
                '-movflags', '+faststart', str(folder/'result.mp4')]
    return command
