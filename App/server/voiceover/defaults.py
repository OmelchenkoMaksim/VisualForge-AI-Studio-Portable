"""Built-in voices: the description file, stable ids, and the choice of the best take. Standard library and numpy only.

The voices are made on the user's machine by `defaults_worker.py` (VoxCPM2 Voice Design), not shipped as audio: the repository keeps
no audio files. Each voice is a new voice described in words, so nobody's real voice is copied.
"""
import hashlib
import json
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parents[2] / 'config/default-voices.json'
FEMALE_MIN_HZ, MALE_MAX_HZ = 150.0, 165.0   # a median pitch outside these bounds means the take does not match the described gender
TARGET_CHARS_PER_SECOND = 14.0              # calm narration; a take far from it is rushed or dragging
MIN_SECONDS, MAX_SECONDS = 9.0, 24.0


def voice_id(slug):
    """A fixed 32-hex id (the shape every voice id has), so the same voice keeps its identity and its cached fragments."""
    return hashlib.sha256(('visualforge-default-voice:' + slug).encode('utf-8')).hexdigest()[:32]


def load_config(path=CONFIG_FILE):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    voices = data.get('voices')
    if data.get('version') != 1 or not isinstance(data.get('text'), str) or not isinstance(voices, list) or not voices:
        raise ValueError('default-voices.json: unsupported format')
    takes = data.get('takes', 4)
    if not isinstance(takes, int) or not 1 <= takes <= 8:
        raise ValueError('default-voices.json: takes must be 1..8')
    slugs = set()
    for voice in voices:
        if not (isinstance(voice.get('slug'), str) and voice['slug'] and voice['slug'] not in slugs and voice.get('gender') in ('female', 'male')
                and isinstance(voice.get('description'), str) and voice['description'] and isinstance(voice.get('name_key'), str)
                and isinstance(voice.get('seed'), int)
                and all(isinstance(voice.get(bound, 0), (int, float)) for bound in ('pitch_min', 'pitch_max'))):
            raise ValueError('default-voices.json: invalid voice %r' % (voice,))
        slugs.add(voice['slug'])
    return dict(data, takes=takes)


def median_pitch(samples, rate):
    """Median fundamental frequency in Hz over the voiced frames (autocorrelation), or 0.0 when nothing is voiced."""
    import numpy as np
    frame, hop = int(rate * 0.04), int(rate * 0.02)
    low, high = int(rate / 400), int(rate / 60)
    loud = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0
    found = []
    for start in range(0, len(samples) - frame, hop):
        part = samples[start:start + frame] - np.mean(samples[start:start + frame])
        energy = float(np.dot(part, part))
        if energy < (loud * 0.6) ** 2 * frame:
            continue
        correlation = np.correlate(part, part, mode='full')[frame - 1:]
        peak = low + int(np.argmax(correlation[low:high]))
        if correlation[peak] > 0.45 * correlation[0]:
            found.append(rate / peak)
    return float(np.median(found)) if found else 0.0


def acceptable(take, gender, bounds=None):
    """Whether a measured take can be offered: heard back correctly, the right gender, a sensible length, not clipped.

    `bounds` (optional `pitch_min` / `pitch_max` of the voice description) keep two voices of one gender apart: the same description gives takes
    of very different pitch, and without a bound two voices could end up with nearly the same one."""
    bounds = bounds or {}
    pitch_ok = take['pitch'] >= FEMALE_MIN_HZ if gender == 'female' else 0 < take['pitch'] <= MALE_MAX_HZ
    pitch_ok = pitch_ok and take['pitch'] >= bounds.get('pitch_min', 0) and take['pitch'] <= bounds.get('pitch_max', 10 ** 6)
    return (take['wrong'] == 0 and pitch_ok and MIN_SECONDS <= take['seconds'] <= MAX_SECONDS and take['peak'] < 0.99)


def choose(takes, gender, bounds=None):
    """Index of the best take, or None. Among the acceptable ones the pace closest to calm narration wins."""
    good = [index for index, take in enumerate(takes) if acceptable(take, gender, bounds)]
    if not good:
        return None
    return min(good, key=lambda index: abs(takes[index]['rate'] - TARGET_CHARS_PER_SECOND))
