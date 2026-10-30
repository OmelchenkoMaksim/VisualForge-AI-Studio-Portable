"""One owned offline LTX job: independent scenes joined with hard cuts, each with its own prompt, picture and sound.

Every scene is a normal single LTX window (at most 121 frames) generated in a fresh owned process from its own conditioning; all prompts are
encoded together by one encoder process first, because reading the encoder from disk is the slowest step of a short clip. Pictures are
joined by stream copy (identical H.264 settings, every scene starts on a keyframe); the sound of every scene is padded or cut to its
exact length, faded for 12 ms at the cut so it does not click, and encoded to AAC once.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

from video.ltx_support import inference_plan

FADE_SECONDS = 0.012


def event(name, **values):
    print(json.dumps(dict(event=name, **values), ensure_ascii=False), flush=True)


def child(command, timeout):
    result = subprocess.run(command, stdout=sys.stdout, stderr=sys.stderr, timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('LTX scene worker exited with code '+str(result.returncode))


def assembly_command(ffmpeg, folder, parts, scene_frames, fps=24):
    """ffmpeg command that joins the scene pictures by copy and lays the scene sounds end to end, frame-exact."""
    folder = Path(folder).resolve()
    listing = []
    command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n']
    for part in parts:
        picture = (Path(part)/'ltx-picture.mp4').resolve()
        if not picture.is_relative_to(folder):
            raise ValueError('Scene media outside owned job')
        listing.append("file '%s'" % picture.as_posix().replace("'", "'\\''"))
    (folder/'ltx-scenes-list.txt').write_text('\n'.join(listing), encoding='utf-8')
    command += ['-f', 'concat', '-safe', '0', '-i', str(folder/'ltx-scenes-list.txt')]
    seconds = scene_frames/fps
    samples = scene_frames*48000//fps
    filters = []
    for index, part in enumerate(parts, 1):
        sound = (Path(part)/'ltx-audio.wav').resolve()
        if not sound.is_relative_to(folder):
            raise ValueError('Scene media outside owned job')
        command += ['-i', str(sound)]
        filters.append(f'[{index}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=end_sample={samples},asetpts=PTS-STARTPTS,'
                       f'afade=t=in:st=0:d={FADE_SECONDS},afade=t=out:st={seconds-FADE_SECONDS:.6f}:d={FADE_SECONDS}[a{index}]')
    filters.append(''.join(f'[a{index}]' for index in range(1, len(parts)+1))+f'concat=n={len(parts)}:v=0:a=1[audio]')
    command += ['-filter_complex', ';'.join(filters), '-map', '0:v:0', '-map', '[audio]', '-c:v', 'copy',
                '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-t', str(len(parts)*seconds),
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
    child(assembly_command(args.root/'ffmpeg/bin/ffmpeg.exe', folder, outputs, per_scene, request['fps']), 1800)
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
