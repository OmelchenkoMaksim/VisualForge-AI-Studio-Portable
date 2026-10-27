"""The speech gate of the voice input (auto-stop on a pause) is JavaScript; Node runs its checks."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'node is not installed')
class VoiceGateTests(unittest.TestCase):
    def test_gate_starts_on_speech_and_stops_on_a_pause(self):
        done = subprocess.run([shutil.which('node'), str(ROOT / 'tests/voice_gate_test.js')], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('voice gate ok', done.stdout)

    def test_the_bar_cannot_hide_while_waiting_for_the_microphone(self):
        # Regression: the browser's permission dialog takes the focus away from the field, which used to hide the bar (and its stop button)
        # while the recording had already started. Every non-idle state must keep it visible.
        source = (ROOT / 'App/frontend/voice.js').read_text(encoding='utf-8')
        self.assertIn("if (next !== 'idle') {clearTimeout(hideTimer);bar.hidden = false;}", source)
        self.assertIn("setState('starting'", source)


if __name__ == '__main__':
    unittest.main()
