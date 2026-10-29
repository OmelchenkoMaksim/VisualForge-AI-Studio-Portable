"""Sequential video scenes and optional local voice cloning; no resident model overlap."""
from ui_strings import source as ui_text
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).parents[1]))
from video.video_adapters import freevideo_command

def event(kind, **fields):
    print(json.dumps({'event': kind, **fields}, ensure_ascii=True), flush=True)

def run(command, log):
    with log.open('wb') as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
        if process.wait() != 0:
            raise RuntimeError(log.read_text(encoding='utf-8', errors='replace')[-1800:])

def main():
    parser = argparse.ArgumentParser()
    for name in ('root', 'models', 'folder', 'spec'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--assemble-only', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    row = json.loads(args.spec.read_text(encoding='utf-8'))
    root, folder = args.root, args.folder
    ffmpeg = str(root / 'ffmpeg/bin/ffmpeg.exe')
    probe = str(root / 'ffmpeg/bin/ffprobe.exe')
    count = 6 if row['frames'] == 1440 else 1
    people = row.get('characters', [])
    voices = []
    for index, person in enumerate(people, 1):
        if not person.get('speech_text'):
            continue
        event('voice_start', name=person['name'])
        request = folder / ('speech-%d.json' % index)
        output = folder / ('speech-%d.wav' % index)
        if not args.assemble_only:
            request.write_text(json.dumps({**person, 'model': str(args.models.parent / 'Qwen3TTS'), 'output': str(output)}, ensure_ascii=False), encoding='utf-8')
            command = [str(root / 'python/python.exe'), '-u', str(Path(__file__).with_name('voice_worker.py')), '--packages', str(root / 'voice-packages'), '--request', str(request), '--ffmpeg', ffmpeg]
            run(command, folder / ('speech-%d.log' % index))
        voices.append(output)
    duration = row['frames'] / 24
    if voices:
        total = 0
        for voice in voices:
            info = json.loads(subprocess.check_output([probe, '-v', 'error', '-show_format', '-of', 'json', str(voice)]))
            total += float(info['format']['duration'])
        if total > duration + 0.04:
            raise ValueError(ui_text('video.pipeline_dialogue_takes_s_but_clip_shorten') % (total, duration))
    scenes = []
    for index in range(count):
        event('scene_start', scene=index + 1, scenes=count)
        part = folder / ('scene-%02d' % (index + 1))
        part.mkdir(exist_ok=True)
        if args.assemble_only:
            if not (part / 'result.mp4').is_file():
                raise ValueError('Cannot recover export: a completed scene is missing')
            continue
        spec = copy.deepcopy(row)
        spec.update(frames=243 if count > 1 else row['frames'], seed=str((int(row['seed']) + index) % 2**64))
        prompt = row['original_prompt']
        if people:
            prompt += '\n' + '\n'.join('Reference image %d shows %s. Keep this character appearance consistent.' % (i + 1, p['name']) for i, p in enumerate(people))
            (part / 'media.json').write_text(json.dumps({'version': 1, 'references': [{'kind': 'image', 'path': p['image_path']} for p in people]}), encoding='utf-8')
        (part / 'prompt.txt').write_text(prompt, encoding='utf-8')
        command = freevideo_command(root, args.models, part, spec)
        (part / 'workflow-command.json').write_text(json.dumps(command), encoding='utf-8')
        run(command, part / 'backend.log')
        output = part / 'result.mp4'
        if not output.is_file():
            raise RuntimeError('Scene output missing')
        scenes.append(output)
        # Owned scratch only: first-pass arrays/latents are unnecessary after the MP4 is complete.
        # Keep MP4, input records and logs; do not remove user files or upstream model caches.
        for artifact in part.rglob('*'):
            if artifact.is_file() and artifact.suffix in ('.npy', '.pt', '.safetensors'):
                artifact.unlink()
    event('assembly_start')
    listing = folder / 'scenes.txt'
    # Generated names only; relative paths avoid drive/Unicode escaping in concat syntax.
    listing.write_text(''.join("file 'scene-%02d/result.mp4'\n" % (i + 1) for i in range(count)), encoding='utf-8')
    command = [ffmpeg, '-nostdin', '-v', 'error', '-n', '-f', 'concat', '-safe', '0', '-i', str(listing)]
    for voice in voices:
        command += ['-i', str(voice)]
    command += ['-map', '0:v:0']
    if voices:
        audio = ''.join('[%d:a:0]' % (i + 1) for i in range(len(voices)))
        command += ['-filter_complex', audio + 'concat=n=%d:v=0:a=1,apad[speech]' % len(voices), '-map', '[speech]']
    else:
        command += ['-map', '0:a:0', '-af', 'apad']
    command += ['-vf', 'setpts=N/(24*TB)', '-r', '24', '-fps_mode', 'cfr', '-frames:v', str(row['frames']), '-t', str(duration), '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(folder / 'result.mp4')]
    (folder / 'assembly-command.json').write_text(json.dumps(command), encoding='utf-8')
    run(command, folder / 'assembly.log')
    (folder / 'pipeline-result.json').write_text(json.dumps({'scenes': count, 'duration': duration, 'voice_count': len(voices), 'lip_sync': False, 'scene_cuts_possible': count > 1}), encoding='utf-8')

if __name__ == '__main__':
    main()
