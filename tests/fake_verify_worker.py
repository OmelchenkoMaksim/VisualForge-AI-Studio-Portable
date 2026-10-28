"""Stand-in for App/server/voiceover/verify_worker.py: judges fragments by rule instead of by speech recognition.

FAKE_VERIFY_BAD_ROUNDS=N  the fragment containing «Второй абзац» is wrong in the first N rounds, right afterwards
FAKE_VERIFY_ALWAYS_BAD    that fragment is wrong in every round
FAKE_VERIFY_FAIL          the process fails like a missing model would
FAKE_VERIFY_SLEEP         seconds to wait before answering (to test Stop)
"""
import json
import os
from pathlib import Path
import sys
import time


def main():
    spec_path = Path(sys.argv[1])
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    if os.environ.get('FAKE_VERIFY_FAIL'):
        (spec_path.parent / 'verify-error.json').write_text(json.dumps({'error': 'fake recognition failure'}), encoding='utf-8')
        raise SystemExit(1)
    print(json.dumps({'event': 'progress', 'done': 0, 'total': len(spec['fragments'])}), flush=True)
    time.sleep(float(os.environ.get('FAKE_VERIFY_SLEEP', '0')))
    bad_rounds = 10 ** 6 if os.environ.get('FAKE_VERIFY_ALWAYS_BAD') else int(os.environ.get('FAKE_VERIFY_BAD_ROUNDS', '0'))
    results = {}
    for item in spec['fragments']:
        wrong = 'Второй абзац' in item['text'] and spec['round'] < bad_rounds
        results[item['key']] = {'wrong': 5 if wrong else 0, 'words': 6, 'heard': 'шум' if wrong else item['text'], 'suspicious': wrong,
                                'suspects': ['абзац'] if wrong else []}
    (spec_path.parent / ('verify-%d.json' % spec['round'])).write_text(json.dumps(results), encoding='utf-8')
    print(json.dumps({'event': 'progress', 'done': len(spec['fragments']), 'total': len(spec['fragments'])}), flush=True)


if __name__ == '__main__':
    main()
