"""Stand-in for App/server/voiceover/qwen_tts_worker.py: writes a short tone per fragment, so the manager can be tested without a GPU.

FAKE_VOICEOVER_SLEEP delays every fragment (to test Stop); FAKE_VOICEOVER_FAIL makes the process exit with an error.
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
    cache = Path(spec['cache'])
    cache.mkdir(parents=True, exist_ok=True)
    if os.environ.get('FAKE_VOICEOVER_FAIL'):
        (spec_path.parent / 'error.json').write_text(json.dumps({'error': 'fake worker failure'}), encoding='utf-8')
        raise SystemExit(1)
    total = len(spec['fragments'])
    todo = [item for item in spec['fragments'] if not (cache / (item['key'] + '.wav')).is_file()]
    done = total - len(todo)
    print(json.dumps({'event': 'progress', 'phase': 'loading', 'done': done, 'total': total}), flush=True)
    for item in todo:
        time.sleep(float(os.environ.get('FAKE_VOICEOVER_SLEEP', '0')))
        target = cache / (item['key'] + '.wav')
        temporary = target.with_name(target.name + '.tmp')
        with wave.open(str(temporary), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(24000)
            seconds = 0.4 + len(item['text']) / 100 + 0.05 * int(spec.get('seed_shift', 0))  # a repair round sounds different
            stream.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(2 * math.pi * 220 * i / 24000))) for i in range(int(24000 * seconds))))
        os.replace(temporary, target)
        done += 1
        print(json.dumps({'event': 'progress', 'phase': 'synthesis', 'done': done, 'total': total}), flush=True)
    (spec_path.parent / 'result.json').write_text(json.dumps({'fragments': total, 'reused': total - len(todo), 'flagged': []}), encoding='utf-8')


if __name__ == '__main__':
    main()
