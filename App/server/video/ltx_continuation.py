"""Bounded temporal windows and a frame-exact audio/video continuation assembly."""
from pathlib import Path

LONG_FRAMES = (360, 720, 1440)
WINDOW_FRAMES = 121
STRIDE_FRAMES = 96
OVERLAP_FRAMES = WINDOW_FRAMES - STRIDE_FRAMES


def windows(frames):
    if frames not in LONG_FRAMES:
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
                f'[{audio}]apad,atrim=end_sample={frames*48000//fps},asetpts=PTS-STARTPTS[audio]']
    graph = folder/'ltx-continuation-filter.txt'
    graph.write_text(';\n'.join(filters), encoding='utf-8')
    command += ['-filter_complex_threads', '2', '-filter_complex', ';'.join(filters),
                '-map', '[video]', '-map', '[audio]', '-c:v', 'libx264', '-threads', '4',
                '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-r', str(fps),
                '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-t', str(frames/fps),
                '-movflags', '+faststart', str(folder/'result.mp4')]
    return command
