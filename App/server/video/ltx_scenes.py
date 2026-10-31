"""One owned offline LTX job: independent scenes joined with hard cuts, each with its own prompt, picture and sound.

Every scene is a normal single LTX window (at most 121 frames) generated in a fresh owned process from its own conditioning; all prompts are
encoded together by one encoder process first, because reading the encoder from disk is the slowest step of a short clip. Pictures are
joined by stream copy (identical H.264 settings, every scene starts on a keyframe); the sound of every scene is padded or cut to its
exact length, faded for 12 ms at the cut so it does not click, and encoded to AAC once.

The requested total is exact: scene lengths come from the model's 8n+1 grid, so their sum can be up to a second longer (8 scenes of 97 frames for a
30 s request gave 32.3 s); every scene then gives up the tail of its last frames, which are usually the idle end of the shot. The generated sound of
the scenes is very uneven (speech about -24 dBFS, the ambience of a scene without speech -60 dBFS, measured 2026-10-31 on a 30 s clip), so each scene is
brought to one average level before the join, with the gain limited so that it never clips.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback

from video.ltx_support import inference_plan

FADE_SECONDS = 0.012
LEVEL_TARGET_DB = -30.0      # the average level every scene's sound is brought to
LEVEL_MAX_BOOST_DB = 24.0
LEVEL_MAX_CUT_DB = 6.0
LEVEL_PEAK_LIMIT_DB = -2.0


def event(name, **values):
    print(json.dumps(dict(event=name, **values), ensure_ascii=False), flush=True)


def child(command, timeout):
    result = subprocess.run(command, stdout=sys.stdout, stderr=sys.stderr, timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('LTX scene worker exited with code '+str(result.returncode))


def scene_keep(total, count, per_scene):
    """Frames each scene keeps so that the clip has exactly `total` frames (the first scenes keep one more when it does not divide)."""
    total = min(total, count*per_scene)
    return [min(per_scene, total//count+(1 if index < total % count else 0)) for index in range(count)]


def level_gain(mean_db, max_db):
    """Gain in dB that brings a scene's average level to the target without clipping; 0 for a silent scene."""
    if mean_db is None or max_db is None:
        return 0.0
    gain = min(max(LEVEL_TARGET_DB-mean_db, -LEVEL_MAX_CUT_DB), LEVEL_MAX_BOOST_DB)
    return round(min(gain, LEVEL_PEAK_LIMIT_DB-max_db), 2)


def measure_level(ffmpeg, sound):
    """(mean dB, max dB) of a wav as ffmpeg's volumedetect reports them, or (None, None) when it hears nothing."""
    result = subprocess.run([str(ffmpeg), '-hide_banner', '-nostdin', '-i', str(sound), '-af', 'volumedetect', '-f', 'null', '-'],
                            capture_output=True, text=True, timeout=300, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    mean, peak = (re.search(name+r': (-?[0-9.]+) dB', result.stderr) for name in ('mean_volume', 'max_volume'))
    return (float(mean[1]), float(peak[1])) if mean and peak else (None, None)


def assembly_command(ffmpeg, folder, parts, scene_frames, fps=24, keep=None, gains=None):
    """ffmpeg command that joins the scene pictures by copy and lays the scene sounds end to end, frame-exact.

    `keep` is how many frames each scene keeps (default all of them), `gains` the level correction of each scene's sound in dB."""
    folder = Path(folder).resolve()
    keep = keep or [scene_frames]*len(parts)
    gains = gains or [0.0]*len(parts)
    pictures = []
    for part in parts:
        picture = (Path(part)/'ltx-picture.mp4').resolve()
        if not picture.is_relative_to(folder):
            raise ValueError('Scene media outside owned job')
        pictures.append(picture)
    # Stream copy cannot cut a scene on an exact frame (the B-frames after the cut point stay in the file: 5 extra frames measured), so a clip
    # whose scene tails are trimmed is encoded once instead, with the same settings as the continuation join; untrimmed scenes are still copied.
    trimmed = any(count < scene_frames for count in keep)
    command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n']
    filters = []
    if trimmed:
        for picture in pictures:
            command += ['-i', str(picture)]
        offset = len(parts)
        for index, count in enumerate(keep):
            filters.append(f'[{index}:v]trim=end_frame={count},setpts=PTS-STARTPTS,settb=AVTB,format=yuv420p[v{index}]')
        filters.append(''.join(f'[v{index}]' for index in range(len(parts)))+f'concat=n={len(parts)}:v=1:a=0[video]')
        video = ['-map', '[video]', '-c:v', 'libx264', '-threads', '4', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-r', str(fps)]
    else:
        (folder/'ltx-scenes-list.txt').write_text('\n'.join("file '%s'" % picture.as_posix().replace("'", "'\\''") for picture in pictures), encoding='utf-8')
        command += ['-f', 'concat', '-safe', '0', '-i', str(folder/'ltx-scenes-list.txt')]
        offset = 1
        video = ['-map', '0:v:0', '-c:v', 'copy']
    for index, part in enumerate(parts):
        seconds = keep[index]/fps
        samples = keep[index]*48000//fps
        sound = (Path(part)/'ltx-audio.wav').resolve()
        if not sound.is_relative_to(folder):
            raise ValueError('Scene media outside owned job')
        command += ['-i', str(sound)]
        filters.append(f'[{offset+index}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=end_sample={samples},asetpts=PTS-STARTPTS,volume={gains[index]}dB,'
                       f'afade=t=in:st=0:d={FADE_SECONDS},afade=t=out:st={seconds-FADE_SECONDS:.6f}:d={FADE_SECONDS}[a{index}]')
    filters.append(''.join(f'[a{index}]' for index in range(len(parts)))+f'concat=n={len(parts)}:v=0:a=1[audio]')
    command += ['-filter_complex', ';'.join(filters)] + video + ['-map', '[audio]',
                '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-t', str(sum(keep)/fps),
                '-movflags', '+faststart', str(folder/'result.mp4')]
    return command


def run(args):
    started = time.monotonic()
    folder = args.folder.resolve()
    request = json.loads(args.spec.read_text(encoding='utf-8'))
    count, per_scene, prompts = request['scenes'], request['scene_frames'], request['segment_prompts']
    if len(prompts) != count or per_scene*count != request['frames']:
        raise ValueError('LTX scenes: the scenario does not match the number of scenes')
    worker = Path(__file__).with_name('ltx_worker.py')
    base = [sys.executable, '-X', 'faulthandler', '-u', str(worker), '--root', str(args.root), '--models', str(args.models)]
    child(base+['--folder', str(folder), '--spec', str(args.spec), '--encode-only'], 2400)
    config = inference_plan(request)|dict(scenes=count, scene_frames=per_scene, join='hard cuts, one prompt, picture and sound per scene')
    (folder/'ltx-inference-config.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    event('sampling_plan', **config)
    outputs = []
    for index in range(count):
        event('scene_start', scene=index+1, scenes=count)
        part = folder/('scene-%02d' % (index+1))
        part.mkdir(exist_ok=False)
        spec = {key: value for key, value in request.items() if key not in ('segment_prompts', 'scenes', 'scene_frames', 'requested_frames', 'join')}
        spec.update(frames=per_scene, prompt=prompts[index], mode='i2v' if index == 0 and request['mode'] == 'i2v' else 't2v')
        if spec['mode'] == 'i2v':
            media = json.loads((folder/'media.json').read_text(encoding='utf-8'))
            source = Path(media['first']).resolve()
            if not source.is_relative_to(folder):
                raise ValueError('Source image outside owned LTX job')
            target = part/'first.png'
            shutil.copy2(source, target)
            (part/'media.json').write_text(json.dumps(dict(first=str(target))), encoding='utf-8')
        spec_path = part/'ltx-request.json'
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding='utf-8')
        child(base+['--folder', str(part), '--spec', str(spec_path), '--conditioning', str(folder/('ltx-conditioning-%02d.pt' % index))], 7200)
        if not (part/'ltx-picture.mp4').is_file() or not (part/'ltx-audio.wav').is_file():
            raise RuntimeError('LTX scene did not finish')
        outputs.append(part)
    event('assembly_start')
    ffmpeg = args.root/'ffmpeg/bin/ffmpeg.exe'
    keep = scene_keep(request.get('requested_frames') or request['frames'], count, per_scene)
    levels = [measure_level(ffmpeg, part/'ltx-audio.wav') if ffmpeg.is_file() else (None, None) for part in outputs]
    gains = [level_gain(*level) for level in levels]
    event('scene_levels', mean_db=[level[0] for level in levels], max_db=[level[1] for level in levels], gain_db=gains, keep_frames=keep)
    child(assembly_command(ffmpeg, folder, outputs, per_scene, request['fps'], keep, gains), 1800)
    event('ltx_metrics', inference_sec=round(time.monotonic()-started, 2), scenes=count)


def main():
    parser = argparse.ArgumentParser()
    for key in ('root', 'models', 'folder', 'spec'):
        parser.add_argument('--'+key, type=Path, required=True)
    try:
        run(parser.parse_args())
    except Exception:
        traceback.print_exc(limit=12)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
