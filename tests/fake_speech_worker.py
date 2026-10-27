"""Stand-in for App/server/speech/worker.py: same line protocol, no model. Usage: fake_speech_worker.py <model dir> <device>."""
import json
import os
import sys
import time
import wave

device = sys.argv[2] if len(sys.argv) > 2 else 'cpu'
if os.environ.get('FAKE_SPEECH_FAIL_START'):
    sys.exit(3)
print(json.dumps({'ready': True, 'device': device}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    with wave.open(request['path'], 'rb') as handle:
        seconds = handle.getnframes() / handle.getframerate()
    if seconds > 5 and os.environ.get('FAKE_SPEECH_CRASH_ON_LONG'):
        os._exit(7)
    if seconds > 3:
        time.sleep(1.5)  # a long recording takes a moment: used to test "one recording at a time"
    print(json.dumps({'id': request['id'], 'ok': True, 'text': 'распознано %.1f с (%s, %s)' % (seconds, request['language'], device), 'seconds': 0.1}), flush=True)
