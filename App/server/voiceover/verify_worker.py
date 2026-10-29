"""Recognizes synthesized fragments with Whisper and compares the words with the source text (Music runtime Python).

One owned process per verification round: it loads the model, recognizes the listed fragments in small batches, writes
`verify-<round>.json` (key -> wrong words, words, heard) and exits, so the video card is free again for the next synthesis round.
"""
import json
from math import gcd
import os
from pathlib import Path
import sys
import time
import traceback
import wave

TARGET_RATE = 16000
BATCH = 8
MIN_FREE_MB = 2500  # whisper-turbo in float16 needs about 1.6 GB plus working memory; below this the processor is used


def event(kind, **fields):
    print(json.dumps({'event': kind, **fields}, ensure_ascii=True), flush=True)


def read_wav(path):
    import numpy as np
    with wave.open(str(path), 'rb') as handle:
        rate, channels, raw = handle.getframerate(), handle.getnchannels(), handle.readframes(handle.getnframes())
    samples = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != TARGET_RATE:
        try:
            from scipy.signal import resample_poly
        except ImportError:
            resample_poly = None
        if resample_poly is not None:
            # A polyphase filter: plain interpolation from 48 kHz would fold the high band into the speech band.
            samples = resample_poly(samples, TARGET_RATE // gcd(rate, TARGET_RATE), rate // gcd(rate, TARGET_RATE)).astype(np.float32)
        else:
            target = int(len(samples) * TARGET_RATE / rate)
            samples = np.interp(np.linspace(0, len(samples) - 1, target), np.arange(len(samples)), samples).astype(np.float32)
    return samples


def main():
    spec_path = Path(sys.argv[1])
    directory = spec_path.parent
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    try:
        from generation_identity import operation
        from voiceover.voice_comparison import compare, heard_form, is_suspicious, suspect_words
        from voiceover.voiceover_storage import write_json
        import torch
        from transformers import pipeline
        free_mb = torch.cuda.mem_get_info()[0] // (1024 * 1024) if torch.cuda.is_available() else 0
        device = 'cuda:0' if free_mb >= MIN_FREE_MB else 'cpu'
        recognizer = pipeline('automatic-speech-recognition', model=spec['model'], device=device,
                              dtype=torch.float16 if device != 'cpu' else torch.float32, chunk_length_s=30)
        cache, items, results = Path(spec['cache']), spec['fragments'], {}
        event('loaded', device=device, free_mb=int(free_mb))
        options = {'task': 'transcribe', 'language': spec.get('language', 'russian')}
        started = time.monotonic()
        with operation('SpeechRecognition'):
            for start in range(0, len(items), BATCH):
                group = items[start:start + BATCH]
                heard = recognizer([{'raw': read_wav(cache / (item['key'] + '.wav')), 'sampling_rate': TARGET_RATE} for item in group],
                                   batch_size=BATCH, generate_kwargs=options)
                for item, answer in zip(group, heard):
                    text = answer['text'].strip()
                    russian = spec.get('language') == 'russian'
                    replacements, rules = spec.get('replacements', []), spec.get('rules', [])
                    wrong, words = compare(item['text'], text, expand=russian, replacements=replacements, rules=rules)
                    suspicious = is_suspicious(wrong, words, text)
                    # The words the recognition did not give back, for the page's «add to the dictionary» buttons.
                    suspects = suspect_words(item['text'], heard_form(text, replacements, rules) if russian else text) if suspicious else []
                    results[item['key']] = {'wrong': wrong, 'words': words, 'heard': text, 'suspicious': suspicious, 'suspects': suspects}
                event('progress', done=min(start + BATCH, len(items)), total=len(items))
        write_json(directory / ('verify-%d.json' % spec['round']), results)
        event('verified', seconds=round(time.monotonic() - started, 1), device=device, suspicious=sum(row['suspicious'] for row in results.values()))
    except Exception as exc:
        traceback.print_exc(limit=12)
        try:
            from voiceover.voiceover_storage import write_json
            write_json(directory / 'verify-error.json', {'error': str(exc)[:1000]})
        except Exception:
            pass
        raise SystemExit(1)


if __name__ == '__main__':
    main()
