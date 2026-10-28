"""Voice input: a Whisper model in a small worker process of the Music runtime, started on demand and stopped when idle.

The worker keeps the model loaded for a few minutes after the last recording (so dictating several phrases is quick) and then exits.
It runs on the video card only when enough memory is free and no other module works; otherwise on the processor. Before another
module claims the GPU the coordinator calls `release_gpu()`, which stops a GPU-resident worker, so voice input never blocks generation.
"""
from ui_strings import source as ui_text
import atexit
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from generation import tracked, parent as generation_parent, scope as generation_scope

from gpu import Busy

MODEL_PREFERENCE = ('whisper-turbo', 'whisper-large-v3', 'whisper-small')
LABELS = {'whisper-turbo': 'Whisper large-v3-turbo', 'whisper-large-v3': 'Whisper large-v3', 'whisper-small': 'Whisper small'}
NEED_VRAM_MB = {'whisper-turbo': 3500, 'whisper-large-v3': 6500, 'whisper-small': 1800}
REQUIRED_FILES = ('config.json', 'model.safetensors', 'preprocessor_config.json', 'tokenizer.json')
DEFAULTS = dict(model='', device='auto', language='ru', keep_alive=5, punctuation=True)
PUNCT_MODEL = 'rupunct-big'
PUNCT_FILES = ('config.json', 'pytorch_model.bin', 'tokenizer.json', 'vocab.txt')
FINAL_WAIT = 60   # a final recording waits this long for a preview that is still running
LANGUAGES = ('ru', 'en', 'uk', 'de', 'fr', 'es', 'it', 'pl', 'tr', 'zh', 'ja', 'ko', 'pt', 'nl', 'cs', 'be', 'auto')
DEVICES = ('auto', 'cpu', 'cuda')
KEEP_ALIVE_CHOICES = (1, 5, 15, 60)
MAX_AUDIO_BYTES = 30 * 1024 * 1024
START_TIMEOUT = 240
REQUEST_TIMEOUT = 180


class SpeechError(ValueError):
    """A problem the person can understand (bad recording, no model...): answered as 400."""


class SpeechManager:
    def __init__(self, paths, command=None, free_vram=None, gpu_busy=None):
        self.paths = paths
        self.models = Path(os.environ.get('VISUALFORGE_SPEECH_MODELS', paths.models / 'Speech')).resolve()
        self.python = Path(os.environ.get('VISUALFORGE_SPEECH_PYTHON', paths.root / 'Runtime/Music/python/python.exe'))
        self.state = Path(os.environ.get('VISUALFORGE_SPEECH_STATE_ROOT', paths.cache / 'Speech'))
        self.settings_path = paths.settings / 'speech.settings.json'
        self.worker_script = Path(__file__).with_name('worker.py')
        self.command = command          # tests replace the whole worker command: command(model_dir, device) -> list
        self.free_vram = free_vram
        self.gpu_busy = gpu_busy
        self.lock = threading.RLock()
        self.working = threading.Lock()
        self.process = None
        self.job = None
        self.replies = queue.Queue()
        self.model_name = None
        self.device = None
        self.requests = 0
        self.timer = None
        self.polisher = None   # set by the bridge: edits the text with the local Gemma when the person asks for the enhanced mode
        atexit.register(self.close)

    # ---- installation -------------------------------------------------------------------------------------------
    def installed_models(self):
        found = []
        for name in MODEL_PREFERENCE:
            folder = self.models / name
            if not folder.is_dir():
                continue
            missing = [f for f in REQUIRED_FILES if not (folder / f).is_file()]
            weights = (folder / 'model.safetensors').stat().st_size if (folder / 'model.safetensors').is_file() else 0
            found.append(dict(name=name, label=LABELS[name], ok=not missing and weights > 1_000_000, size=weights, missing=missing))
        from model_policy import ModelPolicy
        policy = ModelPolicy(self.paths.root)
        for row in found:
            row['enabled'] = policy.enabled('speech', row['name'])
            row['ok'] = row['ok'] and row['enabled']
        return found

    def punct_dir(self):
        """The RUPunct model (punctuation and capitals for Russian) when installed and switched on, else None."""
        from model_policy import ModelPolicy
        if not ModelPolicy(self.paths.root).enabled('speech', PUNCT_MODEL):
            return None
        folder = self.models / PUNCT_MODEL
        if self.settings()['punctuation'] and all((folder / name).is_file() for name in PUNCT_FILES):
            return folder
        return None

    def availability(self):
        usable = [m for m in self.installed_models() if m['ok']]
        problems = []
        if self.command is None and not self.python.is_file():
            problems.append(ui_text('speech.availability_music_runtime_python_required_recognition_not'))
        if not usable:
            problems.append(ui_text('speech.availability_speech_recognition_model_missing_download_components'))
        punct = all((self.models / PUNCT_MODEL / name).is_file() for name in PUNCT_FILES)
        enhance = self.polisher.status() if self.polisher else dict(available=False, reason=ui_text('speech.availability_gemma_unavailable'), model='')
        return dict(available=not problems, reason=' '.join(problems), models=self.installed_models(), languages=list(LANGUAGES), settings=self.settings(), punctuation_installed=punct, enhance=enhance, **self.status())

    def status(self):
        alive = self.alive()
        return dict(running=alive, device=self.device if alive else None, model=self.model_name if alive else None, transcribing=self.busy())

    # ---- settings -----------------------------------------------------------------------------------------------
    def settings(self, data=None):
        stored = {}
        if self.settings_path.is_file() and self.settings_path.stat().st_size <= 4096:
            try:
                stored = json.loads(self.settings_path.read_text(encoding='utf-8'))
            except ValueError:
                stored = {}
        current = {**DEFAULTS, **{key: value for key, value in stored.items() if key in DEFAULTS}}
        if data is None:
            return current
        if not isinstance(data, dict) or set(data) - set(DEFAULTS):
            raise SpeechError(ui_text('speech.settings_voice_input_contain_unsupported_parameters'))
        names = {m['name'] for m in self.installed_models() if m['ok']}
        for key, value in data.items():
            if key == 'model' and value != '' and value not in names:
                raise SpeechError(ui_text('speech.settings_recognition_model_not_installed'))
            if key == 'device' and value not in DEVICES:
                raise SpeechError(ui_text('speech.settings_device_auto_cpu_cuda'))
            if key == 'language' and value not in LANGUAGES:
                raise SpeechError(ui_text('speech.settings_unknown_recognition_language'))
            if key == 'keep_alive' and value not in KEEP_ALIVE_CHOICES:
                raise SpeechError(ui_text('speech.settings_keep_model_memory_1_5_15'))
            if key == 'punctuation' and not isinstance(value, bool):
                raise SpeechError(ui_text('speech.settings_punctuation_enable_disable'))
            current[key] = value
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_name(self.settings_path.name + '.tmp')
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(temporary, self.settings_path)
        if 'model' in data or 'device' in data or 'punctuation' in data:
            self.stop()
        return current

    def chosen_model(self):
        usable = [m['name'] for m in self.installed_models() if m['ok']]
        wanted = self.settings()['model']
        return wanted if wanted in usable else (usable[0] if usable else '')

    # ---- worker -------------------------------------------------------------------------------------------------
    def alive(self):
        return bool(self.process and self.process.poll() is None)

    def busy(self):
        if self.working.acquire(blocking=False):
            self.working.release()
            return False
        return True

    def _decide_device(self, model):
        setting = self.settings()['device']
        if setting in ('cpu', 'cuda'):
            return setting
        try:
            if self.gpu_busy is not None and self.gpu_busy():
                return 'cpu'
            if self.free_vram is not None and self.free_vram() >= NEED_VRAM_MB[model] + 600:
                return 'cuda'
        except Exception:
            pass
        return 'cpu'

    def _reader(self, process):
        for line in process.stdout:
            try:
                self.replies.put(json.loads(line))
            except ValueError:
                continue
        self.replies.put({'closed': True})

    @tracked('SpeechModelLoad')
    def _start(self, model):
        device = self._decide_device(model)
        folder = self.models / model
        punct = self.punct_dir()
        command = self.command(folder, device) if self.command else [str(self.python), '-u', str(self.worker_script), '--model', str(folder), '--device', device] + (['--punct', str(punct)] if punct else [])
        log = self.paths.logs / 'speech-worker.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.is_file() and log.stat().st_size > 2 * 1024 * 1024:
            log.write_bytes(log.read_bytes()[-256 * 1024:])
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / 'tmp').mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('PYTHON', 'VIRTUAL_ENV'))}
        if generation_parent():
            env['VISUALFORGE_EVENT_PARENT'] = generation_parent()
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONNOUSERSITE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8', TEMP=str(self.state / 'tmp'), TMP=str(self.state / 'tmp'))
        with log.open('ab') as errors:
            self.process = subprocess.Popen(command, cwd=str(self.paths.root), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            from video.owned import OwnedJob
            self.job = OwnedJob(self.process)
        except OSError:
            self.process.terminate()
            self.process = None
            raise SpeechError(ui_text('speech.start_speech_recognition_failed')) from None
        self.replies = queue.Queue()
        threading.Thread(target=self._reader, args=(self.process,), daemon=True, name='speech-reader').start()
        try:
            ready = self.replies.get(timeout=START_TIMEOUT)
        except queue.Empty:
            self.stop()
            raise SpeechError(ui_text('speech.start_recognition_model_did_not_load_within') % START_TIMEOUT) from None
        if not ready.get('ready'):
            self.stop()
            raise SpeechError(ui_text('speech.start_speech_recognition_did_not_details_user'))
        self.model_name, self.device = model, ready.get('device', device)

    def stop(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None
            if self.job:
                self.job.close()
                self.job = None
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            for pipe in (self.process.stdin, self.process.stdout) if self.process else ():
                try:
                    pipe.close()
                except OSError:
                    pass
            self.process = None
            self.model_name = self.device = None

    close = stop
    unload = stop

    def release_gpu(self):
        """Called before another module takes the video card: a GPU-resident worker goes away, a processor worker stays."""
        if self.busy() and self.device == 'cuda':
            raise Busy(ui_text('speech.release_gpu_busy_recognizing_speech_wait_few_seconds'))
        if self.alive() and self.device == 'cuda':
            self.stop()

    def prepare(self):
        """Load the model in the background (called when the person starts speaking) so that the answer comes quickly."""
        if self.alive() or not self.availability()['available']:
            return False

        def load():
            try:
                with self.lock:
                    if not self.alive():
                        self._start(self.chosen_model())
                        self._arm_timer()
            except Exception:
                pass
        threading.Thread(target=load, daemon=True, name='speech-prepare').start()
        return True

    def _arm_timer(self):
        if self.timer:
            self.timer.cancel()
        minutes = self.settings()['keep_alive']
        self.timer = threading.Timer(minutes * 60, self.stop)
        self.timer.daemon = True
        self.timer.start()

    # ---- recognition --------------------------------------------------------------------------------------------
    def transcribe(self, audio, language=None, partial=False, enhance=False, parent_id=None):
        """Recognition, then (enhance=True, final recordings only) an edit by the local Gemma. The recognition lock is released before the
        edit, because Gemma needs the video card and the coordinator would otherwise see the voice worker as busy."""
        from generation import begin, finish
        started_at, clock = time.time(), time.monotonic()
        generation_info = None
        kind = 'SpeechRecognitionPreview' if partial else 'SpeechRecognition'
        terminal_state = 'done'

        def admitted():
            nonlocal generation_info
            generation_info = begin(kind, started_at, clock, parent_id=parent_id)
            return generation_info

        try:
            result = self._recognize(audio, language, partial, admitted=admitted)
            # A skipped live preview performs no inference and keeps its existing small response.
            if result.get('skipped'):
                return result
            if generation_info is None:
                admitted()
            if enhance and not partial and result.get('text') and self.polisher is not None:
                with generation_scope(generation_info):
                    polished, note = self.polisher.polish(result['text'], language or self.settings()['language'])
                result.update(enhanced=not note, enhance_note=note, plain=result['text'], text=polished)
            result['generation'] = generation_info
            return result
        except BaseException:
            terminal_state = 'error'
            if generation_info is None:
                admitted()
            raise
        finally:
            finish(generation_info, state=terminal_state)

    def prepare_enhance(self):
        """Load Gemma in the background when the person switches the enhanced mode on (the first load can take a minute)."""
        if self.polisher is None or not self.polisher.status()['available']:
            return False
        threading.Thread(target=self.polisher.warm, daemon=True, name='speech-enhance-warm').start()
        return True

    def _recognize(self, audio, language=None, partial=False, admitted=None):
        """WAV bytes (16-bit PCM) -> dict(text, seconds, device, model). One recording at a time.

        partial=True is the live preview while someone is still speaking: it is answered only if the model already sits on the video card and
        nothing else is being recognized (otherwise {text: None, skipped: True} at once), so it never loads a model or waits."""
        if not isinstance(audio, (bytes, bytearray)) or len(audio) < 1000 or audio[:4] != b'RIFF' or audio[8:12] != b'WAVE':
            raise SpeechError(ui_text('speech.recognize_expected_wav_recording'))
        if len(audio) > MAX_AUDIO_BYTES:
            raise SpeechError(ui_text('speech.recognize_recording_too_long_maximum_two_minutes'))
        if partial and (not self.alive() or self.device != 'cuda' or self.busy()):
            return dict(text=None, skipped=True)
        status = self.availability()
        if not status['available']:
            raise SpeechError(status['reason'])
        language = language or self.settings()['language']
        if language not in LANGUAGES:
            raise SpeechError(ui_text('speech.settings_unknown_recognition_language'))
        # A final recording waits for a running preview instead of being refused (that refusal once made a whole dictation vanish).
        if not self.working.acquire(blocking=False) and (partial or not self.working.acquire(timeout=FINAL_WAIT)):
            if partial:
                return dict(text=None, skipped=True)
            raise Busy(ui_text('speech.recognize_recognition_taking_longer_than_usual_record'))
        try:
            active_generation = admitted() if admitted else None
            model = self.chosen_model()
            with self.lock:
                if self.timer:
                    self.timer.cancel()
                if not self.alive() or self.model_name != model:
                    self.stop()
                    if active_generation:
                        with generation_scope(active_generation):
                            self._start(model)
                    else:
                        self._start(model)
                process = self.process
            self.state.mkdir(parents=True, exist_ok=True)
            path = self.state / ('recording-%d.wav' % os.getpid())
            path.write_bytes(bytes(audio))
            self.requests += 1
            identifier = self.requests
            try:
                process.stdin.write((json.dumps(dict(id=identifier, path=str(path), language=language, partial=partial, generation_parent=(active_generation or {}).get('id')), ensure_ascii=False) + '\n').encode('utf-8'))
                process.stdin.flush()
            except OSError:
                self.stop()
                raise SpeechError(ui_text('speech.recognize_speech_recognition_interrupted_record_again')) from None
            deadline = time.monotonic() + REQUEST_TIMEOUT
            while True:
                try:
                    reply = self.replies.get(timeout=max(0.1, deadline - time.monotonic()))
                except queue.Empty:
                    self.stop()
                    raise SpeechError(ui_text('speech.recognize_recognition_took_too_long_stopped')) from None
                if reply.get('closed'):
                    self.stop()
                    raise SpeechError(ui_text('speech.recognize_speech_recognition_interrupted_e_g_starting'))
                if reply.get('id') == identifier:
                    break
            if not reply.get('ok'):
                raise SpeechError(reply.get('error') or ui_text('speech.recognize_recognition_error'))
            self._arm_timer()
            return dict(text=reply.get('text', ''), raw=reply.get('raw'), seconds=reply.get('seconds'), device=self.device, model=model, level_db=reply.get('level_db'), note=reply.get('note'))
        finally:
            try:
                (self.state / ('recording-%d.wav' % os.getpid())).unlink()
            except OSError:
                pass
            self.working.release()
