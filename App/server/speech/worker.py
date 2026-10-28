"""Speech-recognition worker: runs in the Music runtime's Python (PyTorch + transformers are already there).

Protocol: one JSON object per line on stdin  {"id": 1, "path": "...wav", "language": "ru"|"en"|"auto"}
          one JSON object per line on stdout {"ready": true, "device": "cuda"} first, then {"id": 1, "ok": true, "text": "...", "seconds": 0.7}
Only the standard library is used for reading the WAV (16-bit PCM); the model is loaded strictly from the local folder.
"""
import argparse
import json
import math
import os
import re
import sys
import time
import wave
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generation import operation

LANGUAGES = {'ru': 'russian', 'en': 'english', 'uk': 'ukrainian', 'de': 'german', 'fr': 'french', 'es': 'spanish', 'it': 'italian', 'pl': 'polish',
             'tr': 'turkish', 'zh': 'chinese', 'ja': 'japanese', 'ko': 'korean', 'pt': 'portuguese', 'nl': 'dutch', 'cs': 'czech', 'be': 'belarusian'}
# What Whisper tends to invent from silence or noise; accepted only when the sound itself was quiet.
PHANTOMS = ('субтитры', 'продолжение следует', 'спасибо за просмотр', 'thanks for watching', 'subtitles by', 'amara.org')
TARGET_RATE = 16000
CYRILLIC = re.compile('[Ѐ-ӿ]')
MARKS = {'O': '', 'PERIOD': '.', 'COMMA': ',', 'QUESTION': '?', 'VOSKL': '!', 'DVOETOCHIE': ':', 'PERIODCOMMA': ';', 'MNOGOTOCHIE': '...', 'QUESTIONVOSKL': '?!',
         'TIRE': ' —', 'DEFIS': '-'}
SENTENCE_END = '.!?…'


def apply_label(word, label):
    """RUPunct label such as UPPER_COMMA or LOWER_O -> the word with its capital letter and the mark after it."""
    mark = MARKS.get(label.rpartition('_')[2], '')
    if label.startswith('UPPER_TOTAL'):
        word = word.upper()
    elif label.startswith('UPPER'):
        word = word[:1].upper() + word[1:]
    return word + mark


def is_punctuated(text):
    """Whisper often punctuates well; then nothing is changed."""
    words = text.split()
    marks = len(re.findall(r'[.,!?;:…]', text))
    return len(words) < 3 or (marks / len(words) >= 0.09 and text[:1].isupper())


def punctuate_text(text, classify):
    """Add punctuation and capitals to Russian words of `text`; Latin words and numbers stay exactly as recognized.

    classify(words_text) -> [(word, label), ...] with exactly one label per word (the RUPunct model). A mismatch returns the text unchanged."""
    if not CYRILLIC.search(text):
        return text
    bare = [word for word in (w.strip('.,!?;:…') for w in text.split()) if word]
    labels = classify(' '.join(bare))
    if len(labels) != len(bare):
        return text
    pieces = []
    for word, (_, label) in zip(bare, labels):
        piece = apply_label(word, label) if CYRILLIC.search(word) else word
        if (not pieces or pieces[-1][-1:] in SENTENCE_END) and piece[:1].islower():
            piece = piece[:1].upper() + piece[1:]   # whatever follows a full stop, question or exclamation mark starts a sentence
        pieces.append(piece)
    result = ' '.join(pieces)
    if result and result[-1] not in SENTENCE_END + ',;:' and CYRILLIC.search(bare[-1]):
        result += '.'
    return result


def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def read_wav(path):
    """16-bit PCM WAV -> (float samples at 16 kHz mono as a list-compatible numpy array, rms in dBFS)."""
    import numpy as np
    with wave.open(path, 'rb') as handle:
        channels, width, rate, frames = handle.getnchannels(), handle.getsampwidth(), handle.getframerate(), handle.getnframes()
        if width != 2:
            raise ValueError('Нужен WAV 16 бит')
        if not 0 < frames <= rate * 130:
            raise ValueError('Запись пустая или длиннее двух минут')
        raw = handle.readframes(frames)
    samples = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != TARGET_RATE:
        target = int(len(samples) * TARGET_RATE / rate)
        samples = np.interp(np.linspace(0, len(samples) - 1, target), np.arange(len(samples)), samples).astype(np.float32)
    rms = float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0
    return samples, (20 * math.log10(rms) if rms > 0 else -120.0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--device', choices=('cuda', 'cpu'), default='cpu')
    parser.add_argument('--punct', default='', help='folder of the RUPunct model (punctuation and capitals for Russian); optional')
    arguments = parser.parse_args()
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    import torch
    from transformers import pipeline
    device = 'cuda' if arguments.device == 'cuda' and torch.cuda.is_available() else 'cpu'
    dtype = torch.float16 if device == 'cuda' else torch.float32
    recognizer = pipeline('automatic-speech-recognition', model=arguments.model, device=device, torch_dtype=dtype, chunk_length_s=30)
    classify = None
    if arguments.punct:
        try:
            from transformers import AutoModelForTokenClassification, AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(arguments.punct, strip_accents=False, add_prefix_space=True)
            tagger = AutoModelForTokenClassification.from_pretrained(arguments.punct).to(device).eval()

            def classify(text):
                words = text.split()
                labels = []
                for start in range(0, len(words), 120):   # the model reads at most 512 tokens
                    chunk = words[start:start + 120]
                    encoded = tokenizer(chunk, is_split_into_words=True, return_tensors='pt', truncation=True, max_length=512).to(device)
                    with torch.no_grad():
                        ids = tagger(**encoded).logits[0].argmax(-1).tolist()
                    first = {}
                    for position, word_id in enumerate(encoded.word_ids()):
                        if word_id is not None and word_id not in first:
                            first[word_id] = tagger.config.id2label[ids[position]]
                    labels += [(word, first.get(number, 'LOWER_O')) for number, word in enumerate(chunk)]
                return labels
        except Exception as error:  # dictation still works without punctuation restoration
            classify = None
            sys.stderr.write('RUPunct could not be loaded: %s\n' % error)
    try:  # the first real recognition after loading is slow (kernel set-up); do it now, on one second of silence
        import numpy as np
        with operation('SpeechModelWarmup'):
            recognizer({'raw': np.zeros(TARGET_RATE, dtype=np.float32), 'sampling_rate': TARGET_RATE}, generate_kwargs={'task': 'transcribe', 'language': 'russian'})
    except Exception:
        pass
    send({'ready': True, 'device': device, 'torch': torch.__version__, 'punctuation': classify is not None})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        started = time.monotonic()
        try:
            samples, level = read_wav(request['path'])
            if level < -55:
                send({'id': request['id'], 'ok': True, 'text': '', 'seconds': 0.0, 'level_db': round(level, 1), 'note': 'тишина'})
                continue
            language = request.get('language', 'ru')
            options = {'task': 'transcribe'}
            if language in LANGUAGES:
                options['language'] = LANGUAGES[language]
            result = recognizer({'raw': samples, 'sampling_rate': TARGET_RATE}, generate_kwargs=options)
            text = raw = ' '.join(str(result.get('text', '')).split())
            if classify is not None and request.get('punctuate', True) and not request.get('partial') and text and not is_punctuated(text):
                try:
                    with operation('SpeechPunctuation', request.get('generation_parent')):
                        text = punctuate_text(text, classify)
                except Exception:
                    text = raw
            if level < -42 and any(text.lower().startswith(phantom) for phantom in PHANTOMS):
                text = ''
            send({'id': request['id'], 'ok': True, 'text': text, 'raw': raw, 'seconds': round(time.monotonic() - started, 2), 'level_db': round(level, 1)})
        except Exception as error:  # a bad recording must not end the worker
            send({'id': request.get('id'), 'ok': False, 'error': '%s: %s' % (type(error).__name__, str(error)[:300])})


if __name__ == '__main__':
    main()
