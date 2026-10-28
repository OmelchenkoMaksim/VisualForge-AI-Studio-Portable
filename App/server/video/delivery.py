"""Delivery export: re-encode an existing clip into a social-media preset.

Post-processing only, with the bundled FFmpeg: no generation and no GPU. The source clip is never
modified. A smaller source is scaled up with Lanczos; that changes the file size, not the amount of
detail, so every receipt records the source and delivered geometry separately. Neural enhancement,
audio mixing and scene assembly are other stages and are deliberately not hidden in here.

Command line (the first argument is the runtime folder holding ffmpeg/bin):
    python delivery.py --runtime-root <Runtime/Video> clip.mp4 reels-1080x1920 --output-dir <folder>
"""
from ui_strings import source as ui_text
import argparse
import collections
from fractions import Fraction
import json
import math
import os
from pathlib import Path
from typing import Callable, Optional
import subprocess
import sys
import threading
import time
import uuid

SCHEMA_VERSION = 1
FPS = 30
AUDIO_RATE = 48000
AUDIO_BITRATE = '192k'
AUDIO_MODES = ('keep', 'require', 'fill')
X264_PRESETS = ('ultrafast', 'veryfast', 'fast', 'medium', 'slow', 'slower')
DURATION_TOLERANCE = 0.15
FRAME_TOLERANCE = 2

# Fit is always "pad": the whole picture stays visible, bars fill the remaining area. Cropping needs a
# visible focus choice first, so it is not offered here.
PRESETS = {
    'reels-1080x1920': dict(label='Instagram Reels / Stories · Full HD 9:16', width=1080, height=1920, aspect='9:16'),
    'vertical-720x1280': dict(label=ui_text('video.delivery_presets_vertical_video_hd_9_16'), width=720, height=1280, aspect='9:16'),
    'feed-1080x1350': dict(label=ui_text('video.delivery_presets_instagram_feed_portrait_4_5'), width=1080, height=1350, aspect='4:5'),
    'landscape-1920x1080': dict(label=ui_text('video.delivery_presets_landscape_video_full_hd_16_9'), width=1920, height=1080, aspect='16:9'),
}


FRAME_RATE_NOTE = 'frames duplicated or dropped; no new motion is created'


class DeliveryError(RuntimeError):
    """FFmpeg/probe failure or an export that did not pass verification."""


class DeliveryCancelled(DeliveryError):
    """The caller's cancel event was set; the partial output has been removed."""


def ffmpeg_paths(runtime_root):
    """Bundled tools, laid out as in the other workers: <runtime>/ffmpeg/bin/."""
    bin_dir = Path(runtime_root) / 'ffmpeg' / 'bin'
    return bin_dir / 'ffmpeg.exe', bin_dir / 'ffprobe.exe'


def public_presets():
    """Preset list for the interface; the geometry itself always comes from plan_export()."""
    return dict(presets=[dict(id=key, **value) for key, value in PRESETS.items()], audio_modes=list(AUDIO_MODES))


def _run(command, timeout, cancel=None, progress: Optional[Callable] = None):
    """Run one tool and return its stdout (empty when `progress` consumes it).

    The exact child is polled, so the deadline or a set `cancel` event stops that process and nothing else.
    `progress(seconds)` receives FFmpeg `-progress` out_time values.
    """
    cancel = cancel or threading.Event()
    try:
        process = subprocess.Popen([str(part) for part in command], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError as error:
        raise DeliveryError(ui_text('video.run_start_failed') % (command[0], error)) from None
    output, tail = [], collections.deque(maxlen=40)

    def read_output():
        for line in process.stdout:
            if progress is None:
                output.append(line)
            elif line.startswith(b'out_time_us='):
                try:
                    progress(int(line[12:]) / 1e6)
                except ValueError:
                    pass

    readers = [threading.Thread(target=read_output, daemon=True),
               threading.Thread(target=lambda: tail.extend(process.stderr), daemon=True)]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if cancel.is_set():
                raise DeliveryCancelled(ui_text('video.run_export_cancelled'))
            if time.monotonic() > deadline:
                raise DeliveryError(ui_text('video.run_f_fmpeg_did_not_finish_within') % timeout)
            cancel.wait(0.1)
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        for reader in readers:
            reader.join(5)
        process.stdout.close()
        process.stderr.close()
    if process.returncode != 0:
        raise DeliveryError(b''.join(tail).decode('utf-8', errors='replace')[-1500:].strip() or ui_text('video.run_f_fmpeg_failed'))
    return b''.join(output)


run_tool = _run


def _ratio(text):
    """'30000/1001' -> Fraction; 0 or unknown -> Fraction(0)."""
    try:
        value = Fraction(str(text).replace(':', '/'))
    except (ValueError, ZeroDivisionError):
        return Fraction(0)
    return value if value > 0 else Fraction(0)


def probe(ffprobe, path, timeout=60):
    """Facts about the first video stream (and whether audio exists), as display geometry."""
    data = json.loads(_run([ffprobe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path], timeout))
    streams = data.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
    if video is None:
        raise ValueError(ui_text('video.probe_file_has_no_video_stream'))
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    width, height = int(video.get('width') or 0), int(video.get('height') or 0)
    if width < 2 or height < 2:
        raise ValueError(ui_text('video.probe_determine_frame_dimensions_failed'))
    rotation = video.get('tags', {}).get('rotate')
    for item in video.get('side_data_list', []):
        if 'rotation' in item:
            rotation = item['rotation']
    quarter_turn = int(round(float(rotation or 0))) % 180 == 90
    sar = _ratio(video.get('sample_aspect_ratio', '1:1')) or Fraction(1)
    display_w, display_h = (height, width * sar) if quarter_turn else (width * sar, height)
    fps = _ratio(video.get('avg_frame_rate')) or _ratio(video.get('r_frame_rate'))
    duration = float(video.get('duration') or 0) or float(data.get('format', {}).get('duration') or 0)
    if duration <= 0 or not fps:
        raise ValueError(ui_text('video.probe_determine_duration_frame_rate_failed'))
    return dict(width=width, height=height, display_width=max(2, round(display_w)), display_height=max(2, round(display_h)),
                fps=fps, duration=duration, video_codec=video.get('codec_name'), pix_fmt=video.get('pix_fmt'),
                has_audio=audio is not None,
                audio=None if audio is None else dict(codec=audio.get('codec_name'), sample_rate=int(audio.get('sample_rate') or 0),
                                                        channels=int(audio.get('channels') or 0)))


def count_frames(ffprobe, path, timeout=600):
    out = _run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-count_frames', '-show_entries', 'stream=nb_read_frames',
                '-of', 'json', path], timeout)
    return int(json.loads(out)['streams'][0]['nb_read_frames'])


def fit_geometry(source_w, source_h, target_w, target_h):
    """Largest even-sized picture that fits the target without distortion, centred with even offsets."""
    scale = min(Fraction(target_w, source_w), Fraction(target_h, source_h))
    width = min(target_w, max(2, math.floor(source_w * scale) // 2 * 2))
    height = min(target_h, max(2, math.floor(source_h * scale) // 2 * 2))
    return dict(width=width, height=height, x=(target_w - width) // 2 // 2 * 2, y=(target_h - height) // 2 // 2 * 2,
                scale=float(scale))


def build_filter(geometry, preset):
    parts = ['scale=%d:%d:flags=lanczos' % (geometry['width'], geometry['height'])]
    if (geometry['width'], geometry['height']) != (preset['width'], preset['height']):
        parts.append('pad=%d:%d:%d:%d:color=black' % (preset['width'], preset['height'], geometry['x'], geometry['y']))
    return ','.join(parts + ['setsar=1', 'fps=%d' % FPS, 'format=yuv420p'])


def build_command(ffmpeg, source, output, info, geometry, preset, audio='keep', x264_preset='slow', crf=18):
    """Argument list for one export; `output` must not exist (-n)."""
    add_silence = audio == 'fill' and not info['has_audio']
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-progress', 'pipe:1', '-nostats', '-n', '-i', source]
    if add_silence:
        command += ['-f', 'lavfi', '-i', 'anullsrc=r=%d:cl=stereo' % AUDIO_RATE]
    command += ['-map', '0:v:0']
    has_audio = info['has_audio'] or add_silence
    if has_audio:
        command += ['-map', '1:a:0' if add_silence else '0:a:0']
    command += ['-vf', build_filter(geometry, preset), '-fps_mode', 'cfr', '-c:v', 'libx264', '-preset', x264_preset,
                '-crf', str(crf), '-profile:v', 'high', '-pix_fmt', 'yuv420p']
    if has_audio:
        # apad + -shortest: the audio never cuts the video short and never extends the file past it.
        command += ['-af', 'aresample=%d,apad' % AUDIO_RATE, '-c:a', 'aac', '-ar', str(AUDIO_RATE), '-ac', '2',
                    '-b:a', AUDIO_BITRATE, '-shortest']
    else:
        command += ['-an']
    return command + ['-movflags', '+faststart', output]


def _reserve(directory, base, receipt_suffix='.delivery.json'):
    """Exclusively create the .mp4 and its receipt names so an existing user file is never replaced."""
    index = 0
    while True:
        stem = base + ('' if not index else ' %d' % index)
        video, receipt = directory / (stem + '.mp4'), directory / (stem + receipt_suffix)
        try:
            video.open('xb').close()
        except FileExistsError:
            index += 1
            continue
        try:
            receipt.open('xb').close()
        except FileExistsError:
            video.unlink()
            index += 1
            continue
        return video, receipt


def _verify(ffprobe, output, preset, source, expect_audio):
    result = probe(ffprobe, output)
    problems = []
    if (result['width'], result['height']) != (preset['width'], preset['height']):
        problems.append(ui_text('video.verify_dimensions_x') % (result['width'], result['height']))
    if (result['display_width'], result['display_height']) != (preset['width'], preset['height']):
        problems.append(ui_text('video.verify_non_square_pixel'))
    if result['fps'] != FPS:
        problems.append(ui_text('video.verify_frame_rate') % result['fps'])
    if result['video_codec'] != 'h264' or result['pix_fmt'] != 'yuv420p':
        problems.append(ui_text('video.export_validation_unexpected_codec_template') % (result['video_codec'], result['pix_fmt']))
    if result['has_audio'] != expect_audio:
        problems.append(ui_text('video.verify_audio_track_does_not_match_expectations'))
    if abs(result['duration'] - source['duration']) > DURATION_TOLERANCE:
        problems.append(ui_text('video.verify_duration_s_instead') % (result['duration'], source['duration']))
    frames = count_frames(ffprobe, output)
    expected = round(source['duration'] * FPS)
    if abs(frames - expected) > FRAME_TOLERANCE:
        problems.append(ui_text('video.verify_frames_instead_about') % (frames, expected))
    if problems:
        raise DeliveryError(ui_text('video.verify_export_validation_failed') + '; '.join(problems))
    result['frames'] = frames
    return result


def plan_export(info, preset_id, audio='keep'):
    """What an export of a probed clip would produce. The preview in the interface and the real export share it.

    `blocked` is a user-readable reason when the choice cannot be exported (it is None otherwise).
    """
    preset = PRESETS.get(preset_id)
    if preset is None:
        raise ValueError(ui_text('video.plan_export_unknown_preset'))
    if audio not in AUDIO_MODES:
        raise ValueError(ui_text('video.plan_export_audio_keep_require_fill'))
    geometry = fit_geometry(info['display_width'], info['display_height'], preset['width'], preset['height'])
    expect_audio = info['has_audio'] or audio == 'fill'
    picture = dict(width=geometry['width'], height=geometry['height'], x=geometry['x'], y=geometry['y'],
                   scale_factor=round(geometry['scale'], 4), scaled_up=geometry['scale'] > 1)
    return dict(preset=preset_id, label=preset['label'], aspect=preset['aspect'],
                target=dict(width=preset['width'], height=preset['height'], fps=FPS), picture=picture,
                source=dict(width=info['display_width'], height=info['display_height'], fps=str(info['fps']),
                            duration=round(info['duration'], 3), has_audio=info['has_audio']),
                frame_rate_conversion='none' if info['fps'] == FPS else FRAME_RATE_NOTE,
                audio_origin='source' if info['has_audio'] else ('silence' if expect_audio else 'none'),
                expect_audio=expect_audio, enhancement='none',
                blocked=ui_text('video.plan_export_source_clip_has_no_audio_but') if audio == 'require' and not info['has_audio'] else None)


def export_clip(source, directory, preset_id, ffmpeg, ffprobe, audio='keep', x264_preset='slow', crf=18, base=None,
                timeout=3600, cancel=None, progress=None):
    """Export `source` to `directory` as an MP4 plus a .delivery.json receipt; returns the receipt.

    `cancel` is a threading.Event; `progress(fraction)` is called with 0..1 while encoding.
    """
    if preset_id not in PRESETS:
        raise ValueError(ui_text('video.plan_export_unknown_preset'))
    preset = PRESETS[preset_id]
    if audio not in AUDIO_MODES:
        raise ValueError(ui_text('video.plan_export_audio_keep_require_fill'))
    if x264_preset not in X264_PRESETS or type(crf) is not int or not 12 <= crf <= 30:
        raise ValueError(ui_text('video.export_clip_invalid_encoding_parameters'))
    source, directory = Path(source), Path(directory)
    if not source.is_file():
        raise ValueError(ui_text('video.export_clip_source_file_not_found'))
    info = probe(ffprobe, source)
    plan = plan_export(info, preset_id, audio)
    if plan['blocked']:
        raise ValueError(plan['blocked'])
    geometry, expect_audio = plan['picture'], plan['expect_audio']
    directory.mkdir(parents=True, exist_ok=True)
    target, receipt_path = _reserve(directory, base or '%s - %s' % (source.stem, preset_id))
    partial = target.with_name('.%s.%s.partial.mp4' % (target.stem, uuid.uuid4().hex[:8]))
    try:
        started = time.monotonic()
        report = None if progress is None else (lambda seconds: progress(max(0.0, min(1.0, seconds / info['duration']))))
        _run(build_command(ffmpeg, source, partial, info, geometry, preset, audio, x264_preset, crf), timeout, cancel, report or (lambda seconds: None))
        delivered = _verify(ffprobe, partial, preset, info, expect_audio)
        os.replace(partial, target)
        receipt = dict(
            schema_version=SCHEMA_VERSION, kind='delivery_export', preset=preset_id, label=preset['label'],
            output_file=target.name, fit='pad', created_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            elapsed_seconds=round(time.monotonic() - started, 2),
            source=dict(file=source.name, width=info['width'], height=info['height'], display_width=info['display_width'],
                        display_height=info['display_height'], fps=str(info['fps']), duration=round(info['duration'], 3),
                        video_codec=info['video_codec'], audio=info['audio']),
            delivered=dict(width=delivered['width'], height=delivered['height'], fps=FPS, frames=delivered['frames'],
                           duration=round(delivered['duration'], 3), video_codec='h264', pix_fmt='yuv420p',
                           audio=delivered['audio'], audio_origin=plan['audio_origin']),
            picture=geometry, frame_rate_conversion=plan['frame_rate_conversion'], enhancement='none', encoder=dict(video='libx264 crf %d preset %s' % (crf, x264_preset),
                                              audio='aac %s %d Hz' % (AUDIO_BITRATE, AUDIO_RATE)))
        temporary = receipt_path.with_name(receipt_path.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, receipt_path)
        return receipt
    except BaseException:
        for leftover in (partial, target, receipt_path):
            leftover.unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='Export an existing clip into a delivery preset.')
    parser.add_argument('source', nargs='?', type=Path)
    parser.add_argument('preset', nargs='?', choices=sorted(PRESETS))
    parser.add_argument('--runtime-root', type=Path, default=os.environ.get('VISUALFORGE_VIDEO_RUNTIME'))
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--audio', choices=AUDIO_MODES, default='keep')
    parser.add_argument('--x264-preset', choices=X264_PRESETS, default='slow')
    parser.add_argument('--crf', type=int, default=18)
    parser.add_argument('--list', action='store_true', help='print the presets and exit')
    args = parser.parse_args(argv)
    if args.list:
        print(json.dumps(PRESETS, ensure_ascii=True, indent=2))
        return 0
    if not (args.source and args.preset and args.output_dir and args.runtime_root):
        parser.error('source, preset, --output-dir and --runtime-root (or VISUALFORGE_VIDEO_RUNTIME) are required')
    ffmpeg, ffprobe = ffmpeg_paths(args.runtime_root)
    try:
        receipt = export_clip(args.source, args.output_dir, args.preset, ffmpeg, ffprobe, args.audio, args.x264_preset, args.crf)
    except (ValueError, DeliveryError) as error:
        print('ERROR: %s' % error, file=sys.stderr)
        return 1
    print(json.dumps(receipt, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
