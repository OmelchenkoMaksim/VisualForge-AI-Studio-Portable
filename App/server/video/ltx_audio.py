"""Decode and bound the recording of LTX audio-to-video before an owned GPU worker starts."""
from ui_strings import source as ui_text
import hashlib
import json
from pathlib import Path
import subprocess


def prepare(jobs, data, folder, frames, fps):
    """The recording must not be longer than the clip; shorter ones are padded with silence (never cut).

    The model hears 48 kHz stereo PCM of exactly the clip's length, the same format it returns for generated audio."""
    raw = jobs.audio_bytes(data['source_audio'])
    source = Path(folder) / 'a2v-source.input'
    source.write_bytes(raw)
    length = frames / fps
    try:
        probe = subprocess.run([str(jobs.root / 'ffmpeg/bin/ffprobe.exe'), '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(source)],
                               capture_output=True, text=True, check=True, timeout=15)
        info = json.loads(probe.stdout)
        duration = float(info['format']['duration'])
        if not any(s['codec_type'] == 'audio' for s in info['streams']) or not .1 <= duration <= length + 1e-3:
            raise ValueError('duration')
        target = Path(folder) / 'a2v-audio.wav'
        subprocess.run([str(jobs.root / 'ffmpeg/bin/ffmpeg.exe'), '-nostdin', '-v', 'error', '-n', '-i', str(source), '-vn', '-ac', '2', '-ar', '48000',
                        '-af', 'apad', '-t', str(length), '-c:a', 'pcm_s16le', str(target)], check=True, timeout=60)
    except (ValueError, KeyError, subprocess.SubprocessError, OSError) as error:
        raise ValueError(ui_text('video.prepare_ltx_recording_up_to_clip_length') % round(length, 2)) from error
    return dict(audio_path=str(target), audio_input_sha256=hashlib.sha256(raw).hexdigest(), audio_input_duration=duration)
