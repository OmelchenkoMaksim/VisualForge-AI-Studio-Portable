"""Stand-in for App/server/voiceover/defaults_worker.py: writes a short tone as every requested built-in voice instead of using VoxCPM2.

FAKE_DEFAULTS_FAIL    the process fails like a missing model would
FAKE_DEFAULTS_SLEEP   seconds to wait before answering (to test Stop)
FAKE_DEFAULTS_SKIP    slug of a voice for which no acceptable take is found
"""
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import wave


def main():
    spec_path = Path(sys.argv[1])
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    if os.environ.get('FAKE_DEFAULTS_FAIL'):
        (spec_path.parent / 'error.json').write_text(json.dumps({'error': 'fake voice creation failure'}), encoding='utf-8')
        raise SystemExit(1)
    total = len(spec['voices'])
    print(json.dumps({'event': 'progress', 'phase': 'synthesis', 'done': 0, 'total': total}), flush=True)
    time.sleep(float(os.environ.get('FAKE_DEFAULTS_SLEEP', '0')))
    out = Path(spec['out_dir'])
    out.mkdir(parents=True, exist_ok=True)
    outcome = {}
    for index, voice in enumerate(spec['voices']):
        skipped = voice['slug'] == os.environ.get('FAKE_DEFAULTS_SKIP')
        outcome[voice['slug']] = {'takes': [], 'chosen': None if skipped else 0}
        if not skipped:
            with wave.open(str(out / (voice['id'] + '.wav')), 'wb') as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(24000)
                handle.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(2 * math.pi * (200 + 10 * index) * n / 24000))) for n in range(24000 * 4)))
            (out / (voice['id'] + '.json')).write_text(json.dumps(dict(
                id=voice['id'], slug=voice['slug'], name_key=voice['name_key'], gender=voice['gender'], description=voice['description'], spoken_text=spec['text'],
                duration=4.0, sha256='%064x' % (index + 1), created=time.time(), seed=voice['seed'], engine='voxcpm2', builtin=True)), encoding='utf-8')
        print(json.dumps({'event': 'progress', 'phase': 'listening', 'done': index + 1, 'total': total}), flush=True)
    (spec_path.parent / 'result.json').write_text(json.dumps({'voices': outcome}), encoding='utf-8')


if __name__ == '__main__':
    main()
