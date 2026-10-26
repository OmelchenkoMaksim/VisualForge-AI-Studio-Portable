"""Audio mixer: lay music tracks under a finished clip and save a new MP4.

Post-processing only, with the bundled FFmpeg: no generation and no GPU. The source clip is never modified; the
video stream of the new file is copied bit for bit (no re-encode), only the audio is rebuilt as AAC stereo 48 kHz.
The clip's own sound is kept unless the caller mutes it, and music can be lowered automatically while the clip's
own sound (speech) is present.
"""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from video.delivery import DeliveryError, _reserve, probe, run_tool

SCHEMA_VERSION = 1
AUDIO_RATE = 48000
AUDIO_BITRATE = '192k'
MAX_TRACKS = 3
NATIVE_MODES = ('keep', 'mute')
AUDIO_SUFFIXES = ('.wav', '.mp3', '.flac', '.m4a', '.ogg')
DURATION_TOLERANCE = 0.15
LIMIT = 0.97
# Fixed ducking profile: music drops roughly 10 dB under the clip's own sound and recovers in about 0.4 s.
DUCKING = 'sidechaincompress=threshold=0.04:ratio=6:attack=15:release=400:makeup=1'


def _number(value, name, low, high, default):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('%s: число от %g до %g' % (name, low, high))
    return float(value)


def validate_request(data):
    """Normalise a mix request; raises ValueError with a user-readable reason."""
    if not isinstance(data, dict) or set(data) - {'id', 'native', 'tracks', 'ducking'}:
        raise ValueError('Запрос микшера содержит неподдерживаемые параметры')
    native = data.get('native') or {}
    if not isinstance(native, dict) or set(native) - {'mode', 'gain_db'} or native.get('mode', 'keep') not in NATIVE_MODES:
        raise ValueError('Собственный звук ролика: оставить или убрать')
    tracks = data.get('tracks')
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= MAX_TRACKS:
        raise ValueError('Добавьте от 1 до %d дорожек музыки' % MAX_TRACKS)
    clean = []
    for track in tracks:
        if not isinstance(track, dict) or set(track) - {'path', 'label', 'start', 'gain_db', 'fade_in', 'fade_out', 'loop'}:
            raise ValueError('Некорректная дорожка')
        path = track.get('path')
        if not isinstance(path, str) or not Path(path).is_absolute() or Path(path).suffix.lower() not in AUDIO_SUFFIXES or not Path(path).is_file():
            raise ValueError('Аудиофайл дорожки недоступен')
        if not isinstance(track.get('loop', False), bool):
            raise ValueError('Повтор дорожки: да или нет')
        clean.append(dict(path=path, label=str(track.get('label') or Path(path).stem)[:120], start=_number(track.get('start'), 'Старт', 0, 3600, 0.0),
                          gain_db=_number(track.get('gain_db'), 'Громкость дорожки, дБ', -40, 12, -12.0),
                          fade_in=_number(track.get('fade_in'), 'Нарастание, с', 0, 30, 0.0), fade_out=_number(track.get('fade_out'), 'Затухание, с', 0, 30, 0.0),
                          loop=bool(track.get('loop', False))))
    if not isinstance(data.get('ducking', False), bool):
        raise ValueError('Приглушение: да или нет')
    return dict(id=data.get('id'), tracks=clean, ducking=bool(data.get('ducking', False)),
                native=dict(mode=native.get('mode', 'keep'), gain_db=_number(native.get('gain_db'), 'Громкость звука ролика, дБ', -40, 12, 0.0)))


def probe_audio(ffprobe, path, timeout=60):
    data = json.loads(run_tool([ffprobe, '-v', 'error', '-show_format', '-show_streams', '-select_streams', 'a:0', '-of', 'json', path], timeout))
    stream = next(iter(data.get('streams', [])), None)
    duration = float((stream or {}).get('duration') or data.get('format', {}).get('duration') or 0)
    if stream is None or duration <= 0:
        raise ValueError('В файле дорожки нет звука или не определена длительность')
    return dict(duration=duration, sample_rate=int(stream.get('sample_rate') or 0), channels=int(stream.get('channels') or 0), codec=stream.get('codec_name'))


def plan_mix(video, request, track_info):
    """What the mix will contain: spans, notes and lane data for the timeline. `video` comes from delivery.probe()."""
    length = video['duration']
    notes, blocked = [], None
    lanes = [dict(kind='video', label='Видео', start=0.0, end=round(length, 3), gain_db=None)]
    native_in = request['native']['mode'] == 'keep' and video['has_audio']
    if native_in:
        lanes.append(dict(kind='native', label='Звук ролика', start=0.0, end=round(length, 3), gain_db=request['native']['gain_db']))
        if request['native']['gain_db'] > 0:
            notes.append('Звук ролика усиливается: возможны искажения, итог ограничивается по пику')
    elif request['native']['mode'] == 'mute' and video['has_audio']:
        notes.append('Собственный звук ролика будет убран')
    elif not video['has_audio']:
        notes.append('В ролике нет собственного звука: будет только музыка')
    spans = []
    for number, (track, info) in enumerate(zip(request['tracks'], track_info), 1):
        room = length - track['start']
        if room <= 0.05:
            blocked = blocked or 'Дорожка %d начинается после конца ролика (%.1f с)' % (number, length)
            span, ends = 0.0, 'after_end'
        elif track['loop']:
            span, ends = room, 'loop'
            notes.append('Дорожка %d повторяется до конца ролика' % number)
        elif info['duration'] < room - 0.01:
            span, ends = info['duration'], 'file_end'
            notes.append('Дорожка %d закончится на %.1f с, дальше будет тишина: включите повтор или сдвиньте старт' % (number, track['start'] + span))
        else:
            span, ends = room, 'video_end'
            if info['duration'] > room + 0.01:
                notes.append('Дорожка %d длиннее ролика: обрежется на %.1f с' % (number, length))
        if span and track['fade_in'] + track['fade_out'] > span + 1e-6:
            blocked = blocked or 'Дорожка %d: нарастание и затухание вместе (%.1f с) длиннее самой дорожки (%.1f с)' % (number, track['fade_in'] + track['fade_out'], span)
        spans.append(dict(span=round(span, 3), ends=ends))
        lanes.append(dict(kind='music', label=track['label'], start=round(track['start'], 3), end=round(track['start'] + span, 3), gain_db=track['gain_db'],
                          fade_in=track['fade_in'], fade_out=track['fade_out'], ends=ends))
    ducking = request['ducking'] and native_in
    if request['ducking'] and not native_in:
        notes.append('Приглушение не применяется: в итоговом ролике не будет собственного звука')
    if ducking:
        notes.append('Музыка автоматически стихнет примерно на 10 дБ, пока звучит собственный звук ролика')
    return dict(duration=round(length, 3), native_in=native_in, ducking=ducking, spans=spans, lanes=lanes, notes=notes, blocked=blocked,
                has_audio=video['has_audio'])


def build_filter(request, plan):
    """The FFmpeg filter graph; input 0 is the clip, inputs 1..n are the tracks in request order."""
    parts, music = [], []
    for number, (track, span) in enumerate(zip(request['tracks'], plan['spans']), 1):
        chain = ['aresample=%d' % AUDIO_RATE, 'aformat=channel_layouts=stereo', 'atrim=0:%.3f' % span['span'], 'asetpts=PTS-STARTPTS']
        if track['fade_in']:
            chain.append('afade=t=in:st=0:d=%.3f' % track['fade_in'])
        if track['fade_out']:
            chain.append('afade=t=out:st=%.3f:d=%.3f' % (span['span'] - track['fade_out'], track['fade_out']))
        chain.append('volume=%.2fdB' % track['gain_db'])
        if track['start'] > 0:
            chain.append('adelay=%d:all=1' % round(track['start'] * 1000))
        parts.append('[%d:a:0]%s[t%d]' % (number, ','.join(chain), number))
        music.append('[t%d]' % number)
    if len(music) > 1:
        parts.append('%samix=inputs=%d:normalize=0:duration=longest[music]' % (''.join(music), len(music)))
    else:
        parts.append('%sanull[music]' % music[0])
    if plan['native_in']:
        parts.append('[0:a:0]aresample=%d,aformat=channel_layouts=stereo,volume=%.2fdB[native]' % (AUDIO_RATE, request['native']['gain_db']))
        if plan['ducking']:
            parts.append('[native]asplit=2[native1][native2]')
            parts.append('[music][native1]%s[ducked]' % DUCKING)
            parts.append('[ducked][native2]amix=inputs=2:normalize=0:duration=longest[mixed]')
        else:
            parts.append('[native][music]amix=inputs=2:normalize=0:duration=longest[mixed]')
    else:
        parts.append('[music]anull[mixed]')
    # alimiter keeps the sum below full scale; apad + -shortest make the audio exactly as long as the video.
    parts.append('[mixed]alimiter=limit=%.2f:level=0,apad[aout]' % LIMIT)
    return ';'.join(parts)


def build_command(ffmpeg, source, output, request, plan):
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-progress', 'pipe:1', '-nostats', '-n', '-i', source]
    for track in request['tracks']:
        command += (['-stream_loop', '-1'] if track['loop'] else []) + ['-i', track['path']]
    return command + ['-filter_complex', build_filter(request, plan), '-map', '0:v:0', '-map', '[aout]', '-c:v', 'copy', '-c:a', 'aac',
                      '-b:a', AUDIO_BITRATE, '-ar', str(AUDIO_RATE), '-ac', '2', '-shortest', '-movflags', '+faststart', output]


def loudness(ffmpeg, path, timeout=300):
    """mean/max volume in dB from volumedetect, or None values for silence."""
    done = subprocess.run([str(ffmpeg), '-hide_banner', '-nostdin', '-i', str(path), '-vn', '-af', 'volumedetect', '-f', 'null', '-'],
                          capture_output=True, timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    text = done.stderr.decode('utf-8', errors='replace')
    found = {name: re.search(r'%s_volume: (-?[\d.]+|-inf) dB' % name, text) for name in ('mean', 'max')}
    return {name: (None if match is None or match.group(1) == '-inf' else float(match.group(1))) for name, match in found.items()}


def _verify(ffmpeg, ffprobe, output, source, plan):
    result = probe(ffprobe, output)
    problems = []
    if not result['has_audio'] or result['audio']['codec'] != 'aac' or (result['audio']['sample_rate'], result['audio']['channels']) != (AUDIO_RATE, 2):
        problems.append('звук не AAC 48 кГц стерео')
    if (result['width'], result['height'], result['video_codec']) != (source['width'], source['height'], source['video_codec']):
        problems.append('видеопоток изменился')
    if abs(result['duration'] - source['duration']) > DURATION_TOLERANCE:
        problems.append('длительность %.3f с вместо %.3f с' % (result['duration'], source['duration']))
    levels = loudness(ffmpeg, output)
    if levels['mean'] is None or levels['mean'] < -70:
        problems.append('звук получился беззвучным')
    if levels['max'] is not None and levels['max'] > 0.0:
        problems.append('пик выше 0 дБ')
    if problems:
        raise DeliveryError('Результат микшера не прошёл проверку: ' + '; '.join(problems))
    return result, levels


def mix_clip(source, directory, request, ffmpeg, ffprobe, cancel=None, progress=None, base=None, timeout=1800):
    """Mix `request` (see validate_request) into a copy of `source`; returns the receipt dictionary."""
    request = validate_request(request)
    source, directory = Path(source), Path(directory)
    if not source.is_file():
        raise ValueError('Исходный файл не найден')
    video = probe(ffprobe, source)
    track_info = [probe_audio(ffprobe, track['path']) for track in request['tracks']]
    plan = plan_mix(video, request, track_info)
    if plan['blocked']:
        raise ValueError(plan['blocked'])
    directory.mkdir(parents=True, exist_ok=True)
    target, receipt_path = _reserve(directory, base or '%s - with music' % source.stem, '.mix.json')
    partial = target.with_name('.%s.%s.partial.mp4' % (target.stem, uuid.uuid4().hex[:8]))
    try:
        started = time.monotonic()
        report = (lambda seconds: progress(max(0.0, min(1.0, seconds / video['duration'])))) if progress else (lambda seconds: None)
        run_tool(build_command(ffmpeg, source, partial, request, plan), timeout, cancel, report)
        delivered, levels = _verify(ffmpeg, ffprobe, partial, video, plan)
        os.replace(partial, target)
        tracks = [dict(label=track['label'], file=Path(track['path']).name, file_duration=round(info['duration'], 3), start=track['start'], span=span['span'],
                       ends=span['ends'], gain_db=track['gain_db'], fade_in=track['fade_in'], fade_out=track['fade_out'], loop=track['loop'])
                  for track, info, span in zip(request['tracks'], track_info, plan['spans'])]
        receipt = dict(schema_version=SCHEMA_VERSION, kind='audio_mix', output_file=target.name, created_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                       elapsed_seconds=round(time.monotonic() - started, 2),
                       source=dict(file=source.name, duration=round(video['duration'], 3), had_audio=video['has_audio']),
                       native=dict(mode=request['native']['mode'], gain_db=request['native']['gain_db'], used=plan['native_in']),
                       ducking=dict(requested=request['ducking'], applied=plan['ducking']), tracks=tracks, notes=plan['notes'],
                       video='stream copied, not re-encoded',
                       delivered=dict(duration=round(delivered['duration'], 3), audio=delivered['audio'], mean_volume_db=levels['mean'], max_volume_db=levels['max']),
                       encoder='aac %s %d Hz stereo, alimiter %.2f' % (AUDIO_BITRATE, AUDIO_RATE, LIMIT))
        temporary = receipt_path.with_name(receipt_path.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, receipt_path)
        return receipt
    except BaseException:
        for leftover in (partial, target, receipt_path):
            leftover.unlink(missing_ok=True)
        raise
