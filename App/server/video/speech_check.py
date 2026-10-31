"""Hear the speech of a generated scene and compare it with the words the prompt asked for.

LTX 2.5 draws the voice with the picture and sometimes mumbles or says other words (2026-10-31: a speaker without quoted words came out as an
unintelligible murmur; with quoted words Whisper heard the exact sentence). A scene whose prompt has spoken lines is therefore listened to by the
studio's own Whisper model (the worker of `speech/speech_worker.py` in the Music runtime, started here as an owned child) and regenerated with
another seed when too little of the requested text is heard. Nothing is sent anywhere: the model is read from `Models/Speech`.
"""
import difflib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading

# The speech manager's models in the order of preference (speech/speech_manager.py) and the files that make one usable.
MODELS = ('whisper-turbo', 'whisper-large-v3', 'whisper-small')
REQUIRED = ('config.json', 'model.safetensors', 'preprocessor_config.json', 'tokenizer.json')
START_TIMEOUT = 240
HEAR_TIMEOUT = 180
ACCEPT = 0.8          # share of the requested characters that must be heard, in order

SPEECH_VERB = re.compile(r'\b(?:says?|saying|said|speaks?|speaking|whispers?|whispering|asks?|asking|replies|replying|shouts?|calls?|announces?|exclaims?|'
                         r'sings?|singing|tells?|telling|greets?|greeting|mutters?|murmurs?|continues|continuing|concludes?|concluding|finish(?:es)?|begins?|'
                         r'adds?|explains?|states?|declares?|answers?|responds?|remarks?|narrates?|recites?|repeats?|utters?|pronounces?|sentence|words)\b', re.I)
QUOTED = re.compile(r'"([^"]{4,400})"|«([^»]{4,400})»|“([^”]{4,400})”')
LANGUAGES = dict(russian='ru', english='en', ukrainian='uk', german='de', french='fr', spanish='es', italian='it', polish='pl', portuguese='pt')
CYRILLIC = re.compile('[Ѐ-ӿ]')


def spoken_lines(prompt):
    """[(words, language code)] of the quoted lines of a prompt that somebody says; other quotes (a sign, a title) are not speech."""
    result = []
    for match in QUOTED.finditer(prompt):
        text = next(group for group in match.groups() if group).strip()
        # the words in front of the quote that belong to the same sentence ("... and says in Russian, ")
        before = re.split(r'[.!?…]["”»]?\s+', prompt[max(0, match.start()-100):match.start()])[-1]
        named = re.search(r'\bin (' + '|'.join(LANGUAGES) + r')\b', before, re.I)    # "in Russian" is a sure sign of speech
        if len(text.split()) < 2 or not (named or SPEECH_VERB.search(before)):
            continue
        language = LANGUAGES[named[1].lower()] if named else ('ru' if CYRILLIC.search(text) else 'en')
        result.append((text, language))
    return result


def words_of(text):
    return ' '.join(re.sub(r'[^\w]+', ' ', text.lower().replace('ё', 'е')).split())


def heard_share(expected, heard):
    """How much of the expected text (letters, in order) is found in what was heard; 1.0 when the text was not asked for."""
    wanted, got = words_of(expected), words_of(heard)
    if not wanted:
        return 1.0
    if not got:
        return 0.0
    matcher = difflib.SequenceMatcher(None, wanted, got, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks())/len(wanted)


def reseed(seed, attempt):
    """The seed of the next try of a scene; keeps the type of the original (the request stores it as text)."""
    value = int(seed)+attempt*7919
    return str(value) if isinstance(seed, str) else value


class Listener:
    """The speech worker as an owned child process: start it, ask for texts, stop it. `available()` is False when no model or no Music runtime."""

    def __init__(self, install, ffmpeg, scratch):
        self.python = Path(os.environ.get('VISUALFORGE_SPEECH_PYTHON', Path(install)/'Runtime/Music/python/python.exe'))
        self.models = Path(os.environ.get('VISUALFORGE_SPEECH_MODELS', Path(install)/'Models/Speech'))
        self.worker = Path(__file__).resolve().parents[1]/'speech/speech_worker.py'
        self.ffmpeg, self.scratch = Path(ffmpeg), Path(scratch)
        self.process = None
        self.replies = queue.Queue()
        self.counter = 0

    def model(self):
        for name in MODELS:
            folder = self.models/name
            if all((folder/file).is_file() for file in REQUIRED):
                return folder
        return None

    def available(self):
        return self.python.is_file() and self.worker.is_file() and self.ffmpeg.is_file() and self.model() is not None

    def start(self, device='cuda'):
        if self.process and self.process.poll() is None:
            return
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith(('PYTHON', 'VIRTUAL_ENV'))}
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONNOUSERSITE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        self.process = subprocess.Popen([str(self.python), '-u', str(self.worker), '--model', str(self.model()), '--device', device], env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.replies = queue.Queue()
        threading.Thread(target=self._read, args=(self.process, self.replies), daemon=True).start()
        ready = self.replies.get(timeout=START_TIMEOUT)
        if not ready.get('ready'):
            self.stop()
            raise RuntimeError('The speech worker did not start')

    @staticmethod
    def _read(process, replies):
        for line in process.stdout:
            try:
                replies.put(json.loads(line))
            except ValueError:
                continue
        replies.put({'closed': True})

    def hear(self, sound, language, seconds):
        """The text heard in the first `seconds` of a sound file (any format ffmpeg reads)."""
        self.counter += 1
        clip = self.scratch/('hear-%d.wav' % self.counter)
        subprocess.run([str(self.ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(sound), '-t', '%.3f' % seconds, '-ac', '1', '-ar', '16000',
                        '-c:a', 'pcm_s16le', str(clip)], check=True, timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            self.process.stdin.write((json.dumps(dict(id=self.counter, path=str(clip), language=language, punctuate=False))+'\n').encode('utf-8'))
            self.process.stdin.flush()
            while True:
                reply = self.replies.get(timeout=HEAR_TIMEOUT)
                if reply.get('closed'):
                    raise RuntimeError('The speech worker stopped')
                if reply.get('id') == self.counter:
                    return reply.get('text', '') if reply.get('ok') else ''
        finally:
            clip.unlink(missing_ok=True)

    def stop(self):
        process, self.process = self.process, None
        if process and process.poll() is None:
            try:
                process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
