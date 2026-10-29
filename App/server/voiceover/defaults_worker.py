"""Creates the built-in voices with VoxCPM2 Voice Design, in one owned process, and keeps the best take of each (Video runtime Python).

For every voice description several takes of the same Russian passage are synthesized; then the model is freed and Whisper listens to every
take. A take is offered only when Whisper heard the passage back word for word, the pitch fits the described gender, the length is sensible and
the level is not clipped; among those the calmest pace wins (defaults.choose). The chosen take is trimmed, normalized and stored as a 24 kHz
mono sample, which is what every voice sample is stored as. Writes `result.json` (all measurements) and exits, so the card is free again.
"""
from math import gcd
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

# Run as a script by the manager, so the server folder is put on sys.path by hand before the shared helpers are imported.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voiceover.default_voices import choose, median_pitch  # noqa: E402
from voiceover.qwen_tts_worker import event  # noqa: E402

SAMPLE_RATE = 24000
LISTEN_RATE = 16000


def resample(samples, source, target):
    from scipy.signal import resample_poly
    divisor = gcd(source, target)
    return resample_poly(samples, target // divisor, source // divisor).astype('float32')


def trim(samples, rate):
    """Cuts the silence at both ends (keeping 0.15 s) so the sample starts and ends with speech, not with a model's silence."""
    import numpy as np
    smooth = np.convolve(np.abs(samples), np.ones(int(rate * 0.01)) / int(rate * 0.01), mode='same')
    voiced = np.flatnonzero(smooth > 0.03 * float(smooth.max()))
    if voiced.size == 0:
        return samples
    pad = int(rate * 0.15)
    return samples[max(0, int(voiced[0]) - pad):min(len(samples), int(voiced[-1]) + pad)]


def main():
    spec_path = Path(sys.argv[1])
    directory = spec_path.parent
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    for package in reversed(spec['packages']):
        sys.path.insert(0, package)
    try:
        import numpy as np
        import soundfile as sf
        import torch
        from generation_identity import operation
        from voiceover.voice_comparison import compare
        from voiceover.voiceover_storage import write_json
        if not torch.cuda.is_available():
            raise RuntimeError('Озвучка требует доступную NVIDIA CUDA GPU')
        from voxcpm import VoxCPM
        candidates = directory / 'candidates'
        candidates.mkdir(exist_ok=True)
        voices, takes, text = spec['voices'], int(spec['takes']), spec['text']
        total = len(voices) * takes + len(voices)
        done = 0
        event('progress', phase='loading', done=0, total=total)
        model = VoxCPM(voxcpm_model_path=spec['model'], zipenhancer_model_path=None, enable_denoiser=False, optimize=False, device='cuda')
        rate = model.tts_model.sample_rate
        with operation('SpeechSynthesis'):
            for voice in voices:
                for take in range(takes):
                    target = candidates / ('%s-%d.wav' % (voice['slug'], take))
                    if not target.is_file():
                        torch.manual_seed(int(voice['seed']) + take)
                        wav = model.generate(text='(%s)%s' % (voice['description'], text), cfg_value=2.0, inference_timesteps=10, max_len=int(len(text) * 1.3) + 60)
                        sf.write(str(target), np.asarray(wav, dtype=np.float32).reshape(-1), rate, subtype='PCM_16', format='WAV')
                    done += 1
                    event('progress', phase='synthesis', done=done, total=total)
        del model
        torch.cuda.empty_cache()

        from transformers import pipeline
        recognizer = None
        if spec.get('whisper'):
            recognizer = pipeline('automatic-speech-recognition', model=spec['whisper'], device='cuda:0', dtype=torch.float16, chunk_length_s=30)
        outcome = {}
        letters = sum(character.isalpha() for character in text)
        for voice in voices:
            measured = []
            for take in range(takes):
                samples, source = sf.read(str(candidates / ('%s-%d.wav' % (voice['slug'], take))), dtype='float32')
                samples = trim(samples.reshape(-1), source)
                listen = resample(samples, source, LISTEN_RATE)
                heard = recognizer({'raw': listen, 'sampling_rate': LISTEN_RATE}, generate_kwargs={'task': 'transcribe', 'language': 'russian'})['text'].strip() if recognizer else text
                wrong, words = compare(text, heard, expand=True)
                seconds = len(samples) / source
                measured.append(dict(take=take, wrong=wrong, words=words, heard=heard, seconds=round(seconds, 2), rate=round(letters / seconds, 2),
                                     pitch=round(median_pitch(listen, LISTEN_RATE), 1), peak=round(float(np.max(np.abs(samples))), 3)))
            picked = choose(measured, voice['gender'], {key: voice[key] for key in ('pitch_min', 'pitch_max') if key in voice})
            outcome[voice['slug']] = dict(takes=measured, chosen=picked)
            if picked is not None:
                samples, source = sf.read(str(candidates / ('%s-%d.wav' % (voice['slug'], picked))), dtype='float32')
                samples = resample(trim(samples.reshape(-1), source), source, SAMPLE_RATE)
                samples = samples * (0.7 / float(np.max(np.abs(samples))))  # about -3 dBFS; the level of a reference sample does not matter to cloning
                out = Path(spec['out_dir'])
                out.mkdir(parents=True, exist_ok=True)
                audio = out / (voice['id'] + '.wav')
                temporary = audio.with_suffix('.tmp')
                sf.write(str(temporary), samples, SAMPLE_RATE, subtype='PCM_16', format='WAV')
                os.replace(temporary, audio)
                write_json(out / (voice['id'] + '.json'), dict(
                    id=voice['id'], slug=voice['slug'], name_key=voice['name_key'], gender=voice['gender'], description=voice['description'],
                    spoken_text=text, duration=round(len(samples) / SAMPLE_RATE, 2), sha256=hashlib.sha256(audio.read_bytes()).hexdigest(),
                    created=time.time(), seed=int(voice['seed']) + picked, engine=spec.get('engine', 'voxcpm2'), builtin=True,
                    measured={key: measured[picked][key] for key in ('wrong', 'words', 'seconds', 'rate', 'pitch', 'peak')}))
            done += 1
            event('progress', phase='listening', done=done, total=total)
        write_json(directory / 'result.json', dict(voices=outcome, built=sum(row['chosen'] is not None for row in outcome.values())))
    except Exception as exc:
        traceback.print_exc(limit=12)
        try:
            from voiceover.voiceover_storage import write_json
            write_json(directory / 'error.json', {'error': str(exc)[:1000]})
        except Exception:
            pass
        raise SystemExit(1)


if __name__ == '__main__':
    main()
