"""One owned offline LTX job: independent scenes joined with hard cuts, each with its own prompt, picture and sound.

Every scene is a normal single LTX window (at most 121 frames) generated in a fresh owned process from its own conditioning; all prompts are
encoded together by one encoder process first, because reading the encoder from disk is the slowest step of a short clip. Pictures are
joined by stream copy (identical H.264 settings, every scene starts on a keyframe); the sound of every scene is padded or cut to its
exact length, faded for 12 ms at the cut so it does not click, and encoded to AAC once.

The requested total is exact: scene lengths come from the model's 8n+1 grid, so their sum can be up to a second longer (8 scenes of 97 frames for a
30 s request gave 32.3 s); every scene then gives up the tail of its last frames, which are usually the idle end of the shot. The generated sound of
the scenes is very uneven (speech about -24 dBFS, the ambience of a scene without speech -60 dBFS, measured 2026-10-31 on a 30 s clip), so each scene is
brought to one average level before the join, with the gain limited so that it never clips.
A scene whose prompt has spoken lines is also listened to (speech_check.py) and generated again with another seed when the words are not heard.
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
from video.speech_check import ACCEPT as SPEECH_ACCEPT, Listener, heard_share, reseed, spoken_lines

SPEECH_RETRIES = 2        # extra tries of a scene that does not say its lines
SPEECH_MAX_WORDS_PER_SECOND = 5.0     # LTX said 14 words in 3.75 s (3.7 per second); above 5 per second no seed helps

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


def repair_speech(root, ffmpeg, folder, request, prompts, outputs, keep, make):
    """Listen to every scene whose prompt has spoken lines; a scene that does not say them is generated again with another seed, at most twice.

    The best try of each scene wins. When no speech model is installed, or anything about the listening fails, the scenes are used as they are: the
    check must never cost the clip. The listener runs only between scene processes, so it never shares the card with LTX."""
    wanted = {index: lines for index, lines in ((i, spoken_lines(prompt)) for i, prompt in enumerate(prompts)) if lines}
    if not wanted:
        return outputs
    listener = Listener(root.parents[1] if len(root.parents) > 1 else root, ffmpeg, folder)
    if not listener.available():
        event('speech_check_skipped', reason='no speech model or Music runtime')
        return outputs
    outputs, best, pending = list(outputs), {}, sorted(wanted)
    too_long = set()
    try:
        for attempt in range(SPEECH_RETRIES+1):
            event('speech_check_start', scenes=len(pending), attempt=attempt)       # the job service shows it: Whisper loads for a while without any other sign
            try:
                listener.start()
                for index in pending:
                    text = ' '.join(line for line, _ in wanted[index])
                    heard = listener.hear(outputs[index]/'ltx-audio.wav', wanted[index][0][1], keep[index]/request['fps'])
                    score = heard_share(text, heard)
                    event('speech_check', scene=index+1, attempt=attempt, score=round(score, 2), expected=text, heard=heard)
                    if index not in best or score > best[index][0]:
                        best[index] = (score, outputs[index])
            finally:
                listener.stop()
            pending = [index for index in pending if best[index][0] < SPEECH_ACCEPT]
            # A line that cannot be spoken in the scene at a human pace is not worth new tries (a 30-word line in 5 s scored 0.61, 0.62 and 0.15).
            hopeless = [index for index in pending if sum(len(line.split()) for line, _ in wanted[index])/(keep[index]/request['fps']) > SPEECH_MAX_WORDS_PER_SECOND]
            for index in hopeless:
                event('speech_too_long', scene=index+1, words=sum(len(line.split()) for line, _ in wanted[index]), seconds=round(keep[index]/request['fps'], 2))
            too_long.update(index+1 for index in hopeless)
            pending = [index for index in pending if index not in hopeless]
            if not pending or attempt == SPEECH_RETRIES:
                break
            for index in pending:
                outputs[index] = make(index, attempt+1)
    except Exception as error:   # the listening is an extra; whatever it did not settle stays as generated
        event('speech_check_failed', error='%s: %s' % (type(error).__name__, str(error)[:200]))
    for index, (_, part) in best.items():
        outputs[index] = part
    event('speech_check_done', scores={str(index+1): round(score, 2) for index, (score, _) in sorted(best.items())}, too_long=sorted(too_long))
    return outputs


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
    def make(index, attempt=0):
        """Generate scene `index` (0-based) in its own folder; a later `attempt` is a new try of the same scene with another seed."""
        event('scene_start', scene=index+1, scenes=count, attempt=attempt)
        part = folder/('scene-%02d' % (index+1) + ('-try%d' % attempt if attempt else ''))
        part.mkdir(exist_ok=False)
        spec = {key: value for key, value in request.items() if key not in ('segment_prompts', 'scenes', 'scene_frames', 'requested_frames', 'join', 'scene_images')
                and not re.fullmatch(r'scene_\d\d_sha256', key)}
        # A scene starts from its own keyframe when it has one (the hero of the film in the pose and place of that shot); the uploaded first
        # picture belongs to scene 1 of an i2v film only.
        media = json.loads((folder/'media.json').read_text(encoding='utf-8')) if (folder/'media.json').is_file() else {}
        picture = (media.get('scenes') or {}).get(str(index)) or (media.get('first') if index == 0 and request['mode'] == 'i2v' else None)
        spec.update(frames=per_scene, prompt=prompts[index], mode='i2v' if picture else 't2v')
        if attempt:
            spec['seed'] = reseed(request['seed'], attempt)
        if picture:
            source = Path(picture).resolve()
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
        return part
    outputs = [make(index) for index in range(count)]
    ffmpeg = args.root/'ffmpeg/bin/ffmpeg.exe'
    keep = scene_keep(request.get('requested_frames') or request['frames'], count, per_scene)
    outputs = repair_speech(args.root, ffmpeg, folder, request, prompts, outputs, keep, make)
    event('assembly_start')
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
