"""Decode and bound S2V speech inputs before starting an owned GPU worker."""
import hashlib
import json
from pathlib import Path
import subprocess

def prepare(jobs,data,folder):
    cloning=data.get('speech_mode','clone')=='clone'
    raw=jobs.audio_bytes(data['voice_audio' if cloning else 'source_audio'])
    target=Path(folder)/('voice-reference.input' if cloning else 'speech.input')
    target.write_bytes(raw)
    try:
        probe=subprocess.run([str(jobs.root/'ffmpeg/bin/ffprobe.exe'),'-v','error',
            '-show_format','-show_streams','-of','json',str(target)],capture_output=True,text=True,check=True,timeout=15)
        info=json.loads(probe.stdout);duration=float(info['format']['duration'])
        valid=(2<=duration<=30) if cloning else (.1<=duration<=77/16)
        if not any(s['codec_type']=='audio' for s in info['streams']) or not valid:raise ValueError('duration')
    except (ValueError,KeyError,subprocess.SubprocessError,OSError) as error:
        raise ValueError('Нужен читаемый образец голоса на 2–30 с.' if cloning else 'Нужна читаемая запись речи от 0,1 до 4,8125 с; длинная запись не обрезается автоматически.') from error
    return dict(speech_input_path=str(target),speech_input_sha256=hashlib.sha256(raw).hexdigest(),
                speech_input_duration=duration,audio_conditioning=True,
                lip_sync_accuracy='model_generated_not_guaranteed')
