"""Stdlib bridge manager for voiceover jobs: text, voices, one owned synthesis process, assembly and history.

Layout of a job (all scratch lives under the state root on the system drive, never in the portable folder):
`state/books/<id>.json` prepared texts, `state/cache/<fragment key>.wav` finished fragments shared by every job (this is
what resumes a stopped job), `state/<job id>/` request, fragment list, logs of that job. Voices are user data under
`UserData/Voiceover/Voices`; finished files are published to the chosen output folder.
"""
from ui_strings import source as ui_text
import atexit
import base64
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from generation import begin as generation_begin, ensure as generation_ensure, finish as generation_finish, append_log as generation_log, duration_between, remove as remove_event
from video.owned import OwnedJob
from . import assemble, pronunciation
from .normalize import changes as normalization_changes, normalize as spoken_form
from .registry import DEFAULT_ENGINE, ENGINES, ID, QWEN_ID, VOXCPM_ID, validate
from .storage import publish, read_json, resolve_path, stored_path, write_json
from .text_prep import clean, fragment_key, fragments, parse_input, summary

TERMINAL = ('done', 'error', 'cancelled')
MAX_REPAIR_ROUNDS = 2      # a wrong fragment is synthesized again at most twice; what is still wrong is reported, not hidden
MAX_FLAGGED = 200          # bounded size of the report kept in the history
WHISPER_PREFERENCE = ('whisper-turbo', 'whisper-large-v3', 'whisper-small')
WHISPER_FILES = ('config.json', 'model.safetensors', 'preprocessor_config.json', 'tokenizer.json')


class VerifyUnavailable(RuntimeError):
    """The recognition step could not run; the job continues without verification."""
MAX_VOICE_BYTES = 8 * 1024 * 1024
MAX_BOOK_BYTES = 30 * 1024 * 1024
KEPT_BOOKS = 30
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _decode(value, limit, too_large):
    """Base64 payload of a data URL or a bare base64 string, with a size limit checked before decoding."""
    if not isinstance(value, str):
        raise ValueError(ui_text('voiceover.manager_book_data_invalid_error'))
    value = re.sub(r'^data:[^;,]*;base64,', '', value, count=1)
    if len(value) * 3 // 4 > limit + 4:
        raise ValueError(too_large)
    try:
        return base64.b64decode(value, validate=True)
    except ValueError:
        raise ValueError(ui_text('voiceover.manager_book_data_invalid_error')) from None


class VoiceoverManager:
    def __init__(self, paths):
        self.paths = paths
        self.runtime = Path(os.environ.get('VISUALFORGE_VOICEOVER_RUNTIME', paths.root / 'Runtime/Video')).resolve()
        # Model folders by engine; the Qwen one keeps its original override name.
        self.models = {QWEN_ID: Path(os.environ.get('VISUALFORGE_VOICEOVER_MODEL', paths.models / ENGINES[QWEN_ID]['model'])).resolve(),
                       VOXCPM_ID: Path(os.environ.get('VISUALFORGE_VOICEOVER_VOXCPM_MODEL', paths.models / ENGINES[VOXCPM_ID]['model'])).resolve()}
        self.python = Path(os.environ.get('VISUALFORGE_VOICEOVER_PYTHON', self.runtime / 'python/python.exe'))
        self.ffmpeg = Path(os.environ.get('VISUALFORGE_VOICEOVER_FFMPEG', self.runtime / 'ffmpeg/bin/ffmpeg.exe'))
        # Tests replace every engine's worker by one fake script.
        self.worker_override = os.environ.get('VISUALFORGE_VOICEOVER_WORKER')
        # torch.compile makes VoxCPM2 2.3 times faster here (0.35 instead of 0.79 s of work per second of audio) after about 110 s for the
        # first compilation, which is cached in the job state afterwards; the worker falls back to no compilation if it fails.
        self.voxcpm_optimize = os.environ.get('VISUALFORGE_VOICEOVER_VOXCPM_OPTIMIZE', '1') == '1'
        # Fragments synthesized together on the GPU: batch 8 was 4.4 times faster than one at a time with the same word accuracy
        # (see docs/Voiceover.md); the worker lowers it to what the free video memory allows.
        self.batch = max(1, min(32, int(os.environ.get('VISUALFORGE_VOICEOVER_BATCH', '8'))))
        # Fragments are checked by speech recognition in the Music runtime (PyTorch and transformers are already there).
        self.verify_python = Path(os.environ.get('VISUALFORGE_VOICEOVER_VERIFY_PYTHON', paths.root / 'Runtime/Music/python/python.exe'))
        self.verify_worker = Path(os.environ.get('VISUALFORGE_VOICEOVER_VERIFY_WORKER', Path(__file__).with_name('verify_worker.py')))
        self.speech_models = Path(os.environ.get('VISUALFORGE_VOICEOVER_SPEECH_MODELS', paths.models / 'Speech')).resolve()
        install_key = hashlib.sha256(str(paths.root).encode('utf-8')).hexdigest()[:12]
        self.state = Path(os.environ.get('VISUALFORGE_VOICEOVER_STATE_ROOT', Path(tempfile.gettempdir()) / 'VisualForgeVoiceover' / install_key)).resolve()
        self.cache = self.state / 'cache'
        self.books = self.state / 'books'
        self.voices_dir = Path(os.environ.get('VISUALFORGE_VOICEOVER_VOICES', paths.settings.parent / 'Voiceover/Voices')).resolve()
        self.logs = paths.logs / 'Voiceover'
        self.settings_file = paths.settings / 'voiceover.settings.json'
        self.history_file = paths.settings / 'voiceover-history.json'
        self.lock = threading.RLock()
        self.active = None
        self.process = None
        self.owned = None
        self.closing = False
        self.history = self._read_history()
        atexit.register(self.close)

    def _read_history(self):
        """A damaged history file is set aside for recovery instead of switching the section off."""
        try:
            history = read_json(self.history_file, [])
            if isinstance(history, list) and all(isinstance(row, dict) and isinstance(row.get('id'), str) for row in history):
                for row in history:
                    generation_ensure(row, 'Voiceover', row['id'], row.get('created'), duration_between(row.get('created'), row.get('finished')))
                return history
        except (OSError, ValueError):
            pass
        try:
            os.replace(self.history_file, self.history_file.with_name('voiceover-history.unreadable-%s.json' % time.strftime('%Y%m%d-%H%M%S')))
        except OSError:
            pass
        return []

    # ---- availability and settings -------------------------------------------------------------------------------

    def _worker_script(self, engine):
        return Path(self.worker_override) if self.worker_override else Path(__file__).with_name(ENGINES[engine]['worker'])

    def engine_state(self, engine):
        """One engine's description for the page, with whether its files are installed and switched on."""
        spec = ENGINES[engine]
        required = [self.python, self.ffmpeg, self.runtime / spec['package_check']] + [self.models[engine] / name for name in spec['model_files']]
        missing = [str(path) for path in required if not path.is_file()]
        reason = ui_text('voiceover.manager_engine_files_missing_error') if missing else ''
        try:
            from model_policy import ModelPolicy
            if spec['component'] in ModelPolicy(self.paths.root).disabled():
                missing, reason = missing or ['disabled'], ui_text('voiceover.manager_engine_disabled_error')
        except (ImportError, OSError, ValueError):
            pass
        shown = {key: spec[key] for key in ('id', 'name', 'license', 'source', 'upstream_revision', 'sample_rate', 'claim_vram_mb', 'batch', 'requirements')}
        return dict(shown, languages=list(spec['languages']), formats=list(spec['formats']), available=not missing, reason=reason, missing=missing)

    def availability(self):
        """`available`, `engine`, `reason` and `missing` describe the default engine (older callers); `engines` lists all of them."""
        states = [self.engine_state(engine) for engine in ENGINES]
        default = next(state for state in states if state['id'] == DEFAULT_ENGINE)
        whisper = self.whisper_model()
        return dict(available=default['available'], engine=default, engines=states, reason=default['reason'], missing=default['missing'], busy=self.busy(),
                    verification=dict(available=whisper is not None, model=whisper.name if whisper else ''))

    def busy(self):
        with self.lock:
            return bool(self.active and self.active['state'] not in TERMINAL)

    def settings(self, data=None):
        with self.lock:
            default = {'save_dir': 'UserData/Results/Voiceover'}
            try:
                current = read_json(self.settings_file, default)
            except (OSError, ValueError):
                current = default  # an unreadable settings file falls back to the portable folder instead of failing every job
            if not isinstance(current, dict) or not isinstance(current.get('save_dir'), str) or not current['save_dir'].strip():
                current = default
            if data is not None:
                value = data.get('save_dir')
                if not isinstance(value, str) or not value.strip() or len(value) > 1000:
                    raise ValueError(ui_text('voiceover.manager_settings_folder_required_error'))
                path = resolve_path(value.strip(), self.paths.root)
                if path.exists() and not path.is_dir():
                    raise ValueError(ui_text('voiceover.manager_settings_path_not_folder_error'))
                current = {'save_dir': stored_path(path, self.paths.root)}
                write_json(self.settings_file, current)
            path = resolve_path(current['save_dir'], self.paths.root)
            return {'save_dir': str(path), 'portable': path.is_relative_to(self.paths.root)}

    # ---- voices --------------------------------------------------------------------------------------------------

    def voices(self):
        rows = []
        for meta in self.voices_dir.glob('*.json') if self.voices_dir.is_dir() else []:
            row = read_json(meta, None)
            if isinstance(row, dict) and ID.fullmatch(str(row.get('id'))) and meta.with_suffix('.wav').is_file():
                rows.append({key: row[key] for key in ('id', 'name', 'created', 'duration', 'transcript') if key in row})
        return {'voices': sorted(rows, key=lambda row: row.get('created', 0), reverse=True)}

    def _voice(self, ident):
        if not isinstance(ident, str) or not ID.fullmatch(ident):
            raise ValueError(ui_text('voiceover.manager_voice_not_found_error'))
        meta = read_json(self.voices_dir / (ident + '.json'), None)
        audio = self.voices_dir / (ident + '.wav')
        if not isinstance(meta, dict) or not audio.is_file():
            raise ValueError(ui_text('voiceover.manager_voice_not_found_error'))
        return meta, audio

    def voice_file(self, ident):
        return self._voice(ident)[1]

    def add_voice(self, data):
        name = data.get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise ValueError(ui_text('voiceover.manager_voice_name_required_error'))
        if data.get('consent') is not True:
            raise ValueError(ui_text('voiceover.manager_voice_consent_required_error'))
        raw = _decode(data.get('audio'), MAX_VOICE_BYTES, ui_text('voiceover.manager_voice_file_too_large_error'))
        transcript = data.get('transcript', '')
        if not isinstance(transcript, str) or len(transcript) > 1000:
            raise ValueError(ui_text('voiceover.manager_voice_transcript_invalid_error'))
        transcript = ' '.join(transcript.split())
        ident = uuid.uuid4().hex
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        scratch = self.state / 'voice-upload'
        scratch.mkdir(parents=True, exist_ok=True)
        source = scratch / (ident + '.bin')
        target = self.voices_dir / (ident + '.wav')
        try:
            source.write_bytes(raw)
            # 24 kHz mono is what the model's speaker encoder uses; thirty seconds are plenty for a timbre.
            done = subprocess.run([str(self.ffmpeg), '-nostdin', '-v', 'error', '-y', '-i', str(source), '-t', '30', '-vn', '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', str(target)],
                                  capture_output=True, timeout=120, creationflags=NO_WINDOW)
            if done.returncode != 0 or not target.is_file():
                raise ValueError(ui_text('voiceover.manager_voice_audio_invalid_error'))
            seconds = assemble.wav_info(target)[2]
            if seconds < 3:
                raise ValueError(ui_text('voiceover.manager_voice_too_short_error'))
            write_json(self.voices_dir / (ident + '.json'), dict(
                id=ident, name=name.strip(), created=time.time(), duration=round(seconds, 2), consent_confirmed=True, transcript=transcript,
                sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            source.unlink(missing_ok=True)
        return {'id': ident, 'name': name.strip(), 'duration': round(seconds, 2)}

    def delete_voice(self, data):
        meta, audio = self._voice(data.get('id'))
        with self.lock:
            if self.active and self.active['state'] not in TERMINAL and self.active.get('voice_id') == meta['id']:
                raise ValueError(ui_text('voiceover.manager_voice_in_use_error'))
            audio.unlink(missing_ok=True)
            (self.voices_dir / (meta['id'] + '.json')).unlink(missing_ok=True)
        return {'deleted': True}

    # ---- texts ---------------------------------------------------------------------------------------------------

    def prepare(self, data):
        """Parses pasted text or an uploaded book and keeps it under a draft id; nothing is synthesized yet."""
        upload = data.get('file')
        raw, name = None, None
        if upload is not None:
            if not isinstance(upload, dict):
                raise ValueError(ui_text('voiceover.manager_book_data_invalid_error'))
            raw = _decode(upload.get('data'), MAX_BOOK_BYTES, ui_text('voiceover.manager_book_file_too_large_error'))
            name = str(upload.get('name', ''))[:200]
        text = data.get('text')
        if raw is None and not isinstance(text, str):
            raise ValueError(ui_text('voiceover.manager_book_data_invalid_error'))
        title = data.get('title')
        book = parse_input(name, raw, text, title.strip()[:200] if isinstance(title, str) else '')
        ident = uuid.uuid4().hex
        self.books.mkdir(parents=True, exist_ok=True)
        write_json(self.books / (ident + '.json'), book)
        for old in sorted(self.books.glob('*.json'), key=lambda path: path.stat().st_mtime, reverse=True)[KEPT_BOOKS:]:
            old.unlink(missing_ok=True)
        places, examples = normalization_changes('\n'.join(chapter['text'] for chapter in book['chapters']), limit=6)
        return dict(book_id=ident, title=book['title'], normalization=dict(places=places, examples=examples), **summary(book['chapters']))

    def _book(self, ident):
        book = read_json(self.books / (ident + '.json'), None) if ID.fullmatch(str(ident)) else None
        if not isinstance(book, dict) or not book.get('chapters'):
            raise ValueError(ui_text('voiceover.manager_book_not_found_error'))
        return book

    # ---- jobs ----------------------------------------------------------------------------------------------------

    def submit(self, request):
        request = validate(request)
        book = self._book(request['book_id'])
        voice, _ = self._voice(request['voice_id'])
        with self.lock:
            if self.closing or self.busy():
                raise ValueError(ui_text('voiceover.manager_busy_or_closing_error'))
            state = self.engine_state(request['engine'])
            if not state['available']:
                raise ValueError(state['reason'])
            ident = uuid.uuid4().hex
            directory = self.state / ident
            directory.mkdir(parents=True, exist_ok=False)
            row = dict(request, id=ident, generation=generation_begin('Voiceover'), state='queued',
                       phase=ui_text('voiceover.job_phase_preparing_text_label'), created=time.time(), token=secrets.token_urlsafe(24),
                       title=book['title'], chapter_count=len(book['chapters']), voice_name=voice['name'], done=0, total=0,
                       upstream_revision=ENGINES[request['engine']]['upstream_revision'], save_dir=stored_path(Path(self.settings()['save_dir']), self.paths.root))
            self.active = row
            threading.Thread(target=self._run, args=(row, directory), daemon=True).start()
            return self.public(row, include_token=True)

    def public(self, row, include_token=False):
        result = {key: value for key, value in row.items() if key not in ('token', 'replacements')}
        result['elapsed_sec'] = round((row.get('finished') or time.time()) - row['created'], 1)
        if row.get('output'):
            result['saved_path'] = str(resolve_path(row['output'], self.paths.root))
        if include_token:
            result['token'] = row['token']
        return result

    def status(self, ident=None):
        with self.lock:
            row = self.active if self.active and (not ident or ident == self.active['id']) else next((r for r in self.history if r['id'] == ident), None)
            if row is None:
                return {'active': False} if not ident else self._missing()
            return self.public(row, include_token=row is self.active and row['state'] not in TERMINAL)

    @staticmethod
    def _missing():
        raise ValueError(ui_text('voiceover.manager_task_not_found_error'))

    def entries(self):
        with self.lock:
            return {'entries': [self.public(row) for row in self.history[:100]]}

    @staticmethod
    def _identifier(ident):
        if not isinstance(ident, str) or not ID.fullmatch(ident):
            raise ValueError(ui_text('voiceover.manager_delete_invalid_identifier_error'))
        return ident

    def delete(self, data):
        from job_delete import DeletePlan, confined_file, write_receipt
        ident = self._identifier(data.get('id'))
        with self.lock:
            row = next((r for r in self.history if r['id'] == ident), None)
            if row is None or row.get('state') not in TERMINAL:
                raise ValueError(ui_text('voiceover.manager_delete_still_running_error'))
            plan = DeletePlan()
            if row.get('output'):
                output = Path(row['output'])
                if not output.is_absolute():
                    output = confined_file(self.paths.root, row['output'])
                if output.suffix.lower() not in ('.wav', '.mp3', '.m4b'):
                    raise ValueError(ui_text('voiceover.manager_file_unavailable_error'))
                plan.file(output)
                plan.sidecar(output.with_suffix('.json'), 'id', ident)
            plan.file(self.logs / (ident + '.log'))
            plan.tree(self.state / ident)
            remaining = [r for r in self.history if r['id'] != ident]
            plan.execute()
            remove_event(row.get('generation'))
            write_receipt(self.history_file, remaining)
            self.history = remaining
            if self.active and self.active['id'] == ident:
                self.active = None
            return {'deleted': True}

    def cancel(self, data):
        with self.lock:
            row = self.active
            if not row or data.get('id') != row['id'] or not secrets.compare_digest(str(data.get('token', '')), row['token']):
                raise ValueError(ui_text('voiceover.manager_cancel_invalid_key_error'))
            if row['state'] not in TERMINAL:
                row['cancel_requested'] = True
                row['phase'] = ui_text('voiceover.job_phase_cancelling_label')
                if self.owned:
                    self.owned.close()
                elif self.process and self.process.poll() is None:
                    self.process.terminate()
            return {'cancelling': row['state'] not in TERMINAL}

    def file(self, ident):
        with self.lock:
            row = next((r for r in self.history if r['id'] == ident and r['state'] == 'done'), None)
            if not row:
                return self._missing()
            path = resolve_path(row['output'], self.paths.root)
            if path.suffix.lower() not in ('.wav', '.mp3', '.m4b') or not path.is_file():
                raise ValueError(ui_text('voiceover.manager_file_unavailable_error'))
            return path

    def fragment_file(self, ident, key):
        """The audio of one reported fragment, so that the page can play it. Only fragments listed in that job's report are served."""
        self._identifier(ident)
        if not isinstance(key, str) or not ID.fullmatch(key):
            raise ValueError(ui_text('voiceover.manager_fragment_not_found_error'))
        with self.lock:
            row = self.active if self.active and self.active['id'] == ident else next((r for r in self.history if r['id'] == ident), None)
            if not row or key not in [item.get('key') for item in row.get('flagged', [])]:
                raise ValueError(ui_text('voiceover.manager_fragment_not_found_error'))
        path = self.cache / (key + '.wav')
        if not path.is_file():
            raise ValueError(ui_text('voiceover.manager_fragment_not_found_error'))
        return path

    def log(self, ident):
        path = self.logs / (self._identifier(ident) + '.log')
        if not path.exists():
            return {'text': ui_text('voiceover.manager_task_log_empty_label')}
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 16000))
            return {'text': stream.read().decode('utf-8', errors='replace')}

    def clear_cache(self):
        """Fragments of finished and stopped jobs stay for a quick re-run; this frees the disk when nothing is running."""
        with self.lock:
            if self.busy():
                raise ValueError(ui_text('voiceover.manager_busy_or_closing_error'))
            size = sum(path.stat().st_size for path in self.cache.glob('*.wav')) if self.cache.is_dir() else 0
            shutil.rmtree(self.cache, ignore_errors=True)
            return {'freed_bytes': size}

    # ---- the job thread ------------------------------------------------------------------------------------------

    def _environment(self):
        env = os.environ.copy()
        temp = self.state / 'RuntimeCache'
        temp.mkdir(parents=True, exist_ok=True)
        env.update(PYTHONUTF8='1', PYTHONNOUSERSITE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   HF_HUB_DISABLE_PROGRESS_BARS='1', TOKENIZERS_PARALLELISM='false', TQDM_DISABLE='1', DO_NOT_TRACK='1',
                   HF_HOME=str(temp / 'huggingface'), TORCH_HOME=str(temp / 'torch'), TORCHINDUCTOR_CACHE_DIR=str(temp / 'inductor'),
                   TEMP=str(temp), TMP=str(temp))
        env.pop('PYTHONHOME', None)
        env.pop('PYTHONPATH', None)
        return env

    def _spawn(self, row, command, env, cwd):
        """Starts a child inside the job object that Stop (and application exit) closes; refuses when Stop already came."""
        with self.lock:
            if row.get('cancel_requested'):
                return None
            self.process = subprocess.Popen(command, env=env, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            try:
                self.owned = OwnedJob(self.process)
            except OSError:
                self.process.terminate()
                self.process.wait(timeout=10)
                raise
            return self.process

    def _release(self):
        with self.lock:
            if self.owned:
                self.owned.close()
            self.owned = None
            self.process = None

    def _pump(self, process, log_path, on_line):
        """Copies the child's output to a bounded log (the tail survives) and hands each line to `on_line`."""
        tail, total = deque(maxlen=160), 0
        with log_path.open('ab') as log:
            for line in iter(process.stdout.readline, b''):
                line = line[-4096:]
                on_line(line)
                tail.append(line)
                total += len(line)
                if total > 192 * 1024:
                    log.seek(0)
                    log.truncate()
                    log.write(b''.join(tail))
                    total = log.tell()
                else:
                    log.write(line)
                log.flush()
        code = process.wait()
        process.stdout.close()
        return code

    def _execute(self, row, command, env, directory, log_path, on_line=lambda line: None):
        """Runs one child process to its end. Returns its exit code, or None when Stop came first."""
        process = self._spawn(row, command, env, directory)
        if process is None:
            return None
        code = self._pump(process, log_path, on_line)
        self._release()
        with self.lock:
            return None if row.get('cancel_requested') else code

    def _progress_reader(self, row, phases):
        def on_line(line):
            try:
                message = json.loads(line)
            except ValueError:
                return
            if isinstance(message, dict) and message.get('event') == 'progress':
                with self.lock:
                    row.update(done=int(message.get('done', 0)), total=int(message.get('total', row['total'])),
                               phase=phases.get(message.get('phase'), row['phase']))
        return on_line

    def _synthesize(self, row, directory, env, log_path, pending, shift):
        """One synthesis worker run over `pending` fragments ({key, text}). None when stopped, else the worker's result.

        `shift` changes the seeds of a repair round, so a fragment that came out wrong is not generated the same way again."""
        engine = row['engine']
        voice, voice_audio = self._voice(row['voice_id'])
        write_json(directory / 'request.json', dict(
            packages=[str(self.runtime / package) for package in ENGINES[engine]['packages']], model=str(self.models[engine]), voice=str(voice_audio),
            language=row['language'], transcript=voice.get('transcript', ''), optimize=self.voxcpm_optimize,
            cache=str(self.cache), fragments=[{'key': item['key'], 'text': item.get('speak', item['text'])} for item in pending],
            attempts=3, batch=self.batch, seed_shift=shift))
        phases = dict(loading=ui_text('voiceover.job_phase_loading_model_label'), synthesis=ui_text('voiceover.job_phase_synthesis_label'))
        code = self._execute(row, [str(self.python), '-u', str(self._worker_script(engine)), str(directory / 'request.json')], env, directory, log_path,
                             self._progress_reader(row, phases))
        if code is None:
            return None
        if code != 0:
            error = read_json(directory / 'error.json', {})
            raise RuntimeError(error.get('error') or ui_text('voiceover.manager_worker_exited_error') % code)
        return read_json(directory / 'result.json', {})

    def whisper_model(self):
        """Folder of the best installed Whisper model that is switched on, or None (then fragments are not verified)."""
        if not self.verify_python.is_file():
            return None
        try:
            from model_policy import ModelPolicy
            policy = ModelPolicy(self.paths.root)
        except (ImportError, OSError, ValueError):
            policy = None
        for name in WHISPER_PREFERENCE:
            folder = self.speech_models / name
            try:
                if policy is not None and not policy.enabled('speech', name):
                    continue
                if all((folder / part).is_file() for part in WHISPER_FILES) and (folder / 'model.safetensors').stat().st_size > 1_000_000:
                    return folder
            except (OSError, ValueError):
                continue
        return None

    def _verify(self, row, directory, env, log_path, model_dir, pending, round_number):
        """One recognition round: {key: verdict}. None when stopped; raises VerifyUnavailable when the checker cannot run."""
        write_json(directory / 'verify-request.json', dict(model=str(model_dir), cache=str(self.cache), fragments=[{'key': item['key'], 'text': item['text']} for item in pending],
                                                           language=row['language'].lower(), round=round_number,
                                                           replacements=row['replacements'], rules=self._rules(row)))
        (directory / 'verify-error.json').unlink(missing_ok=True)
        code = self._execute(row, [str(self.verify_python), '-u', str(self.verify_worker), str(directory / 'verify-request.json')], env, directory, log_path,
                             self._progress_reader(row, {}))
        if code is None:
            return None
        verdicts = read_json(directory / ('verify-%d.json' % round_number), None)
        if code != 0 or not isinstance(verdicts, dict) or any(item['key'] not in verdicts for item in pending):
            raise VerifyUnavailable(read_json(directory / 'verify-error.json', {}).get('error') or 'exit code %s' % code)
        return verdicts

    def _rules(self, row):
        """The built-in pronunciation rules of this job's engine (none when switched off or for a language other than Russian)."""
        if not row.get('dictionary', True) or row['language'] != 'Russian':
            return []
        try:
            return pronunciation.for_engine(pronunciation.load_rules(), row['engine'])
        except (OSError, ValueError) as error:
            # A damaged file must not stop the job; the log says that the rules were skipped.
            print('Voiceover: built-in pronunciation rules skipped: %s' % error, flush=True)
            return []

    def _verdict_file(self, key):
        return self.cache / (key + '.verdict.json')

    def _stamp(self, key):
        """Size and modification time of a fragment's file: a verdict belongs to exactly this take and not to a later one with the same key."""
        stat = (self.cache / (key + '.wav')).stat()
        return [stat.st_size, stat.st_mtime_ns]

    def _known_verdict(self, key):
        """The stored verdict of this take, or None. A suspicious verdict counts only once repairs were exhausted (`final`)."""
        try:
            data = read_json(self._verdict_file(key), None)
            if not isinstance(data, dict) or data.get('stamp') != self._stamp(key):
                return None
        except (OSError, ValueError):
            return None
        verdict = data.get('verdict')
        if not isinstance(verdict, dict) or 'suspicious' not in verdict:
            return None
        return verdict if (not verdict['suspicious'] or data.get('final')) else None

    def _remember(self, key, verdict, final):
        try:
            write_json(self._verdict_file(key), dict(stamp=self._stamp(key), verdict=verdict, final=final))
        except OSError:
            pass  # only a saved check is lost

    @staticmethod
    def _flagged_rows(keys, items, chapters, verdicts):
        first = {}
        for item in items:
            first.setdefault(item['key'], item)
        rows = []
        for key in keys[:MAX_FLAGGED]:
            item, verdict = first[key], verdicts.get(key) or {}
            rows.append(dict(key=key, chapter=chapters[item['chapter']]['title'], text=item['text'], heard=verdict.get('heard'),
                             wrong=verdict.get('wrong'), words=verdict.get('words'), suspects=verdict.get('suspects', [])))
        return rows

    def _check_and_repair(self, row, directory, env, log_path, unique, items, chapters, plausibility):
        """Recognizes every fragment, re-synthesizes the wrong ones (up to MAX_REPAIR_ROUNDS times) and reports what is still wrong.

        Returns (flagged rows, verification summary), or None when stopped. When checking is switched off or impossible the job is
        still finished: `plausibility` (fragments the worker itself found implausible) is reported instead."""
        if not row.get('verify'):
            return self._flagged_rows(plausibility, items, chapters, {}), dict(state='off')
        model_dir = self.whisper_model()
        if model_dir is None:
            return self._flagged_rows(plausibility, items, chapters, {}), dict(state='skipped', reason=ui_text('voiceover.manager_verify_model_missing_label'))
        # What was already checked (and what repairs could not fix) is not checked again: a resumed or repeated job only listens to new takes.
        known = {item['key']: verdict for item in unique if (verdict := self._known_verdict(item['key'])) is not None}
        pending, verdicts, repaired = [item for item in unique if item['key'] not in known], dict(known), 0
        try:
            for round_number in range(MAX_REPAIR_ROUNDS + 1):
                if not pending:
                    break
                with self.lock:
                    row.update(phase=ui_text('voiceover.job_phase_verifying_label'), done=0, total=len(pending))
                outcome = self._verify(row, directory, env, log_path, model_dir, pending, round_number)
                if outcome is None:
                    return None
                verdicts.update(outcome)
                for item in pending:
                    self._remember(item['key'], outcome[item['key']], final=False)
                bad = [item for item in pending if outcome[item['key']]['suspicious']]
                if not bad or round_number == MAX_REPAIR_ROUNDS:
                    for item in bad:
                        self._remember(item['key'], outcome[item['key']], final=True)  # repairs are exhausted: a resume must not try again
                    break
                for item in bad:
                    (self.cache / (item['key'] + '.wav')).unlink(missing_ok=True)  # the worker synthesizes what is missing
                    self._verdict_file(item['key']).unlink(missing_ok=True)
                with self.lock:
                    row.update(phase=ui_text('voiceover.job_phase_resynthesis_label'), done=0, total=len(bad))
                if self._synthesize(row, directory, env, log_path, bad, round_number + 1) is None:
                    return None
                repaired += len(bad)
                pending = bad
        except VerifyUnavailable as error:
            return self._flagged_rows(plausibility, items, chapters, {}), dict(state='skipped', reason=ui_text('voiceover.manager_verify_failed_label') % error)
        still = [key for key, verdict in verdicts.items() if verdict['suspicious']]
        return self._flagged_rows(still, items, chapters, verdicts), dict(state='done', model=model_dir.name, checked=len(unique), repaired=repaired, cached=len(known))

    def _run(self, row, directory):
        log_path = self.logs / (row['id'] + '.log')
        try:
            self.logs.mkdir(parents=True, exist_ok=True)
            book = self._book(row['book_id'])
            voice, voice_audio = self._voice(row['voice_id'])
            # The user's dictionary acts on the text as written; numbers and abbreviations are rewritten into words after it.
            # Language-specific: the rules are Russian, so another language keeps its digits for the model to read.
            spoken = spoken_form if row.get('normalize', True) and row['language'] == 'Russian' else (lambda text: text)
            chapters = [{'title': item['title'], 'text': spoken(clean(item['text'], row['replacements']))} for item in book['chapters']]
            items = fragments(chapters)
            rules = self._rules(row)
            for item in items:
                # What the engine is given. The page, the report and the comparison with the recognized words keep the written text.
                item['speak'] = pronunciation.apply_rules(item['text'], rules)
            spec = ENGINES[row['engine']]
            model = spec['id'] + '@' + spec['upstream_revision'][:12]
            if row['engine'] == VOXCPM_ID:
                # With a transcript of the sample the voice is cloned in a different mode, and the audio differs.
                model += ':continuation' if voice.get('transcript') else ':reference'
            for item in items:
                item['key'] = fragment_key(item['speak'], voice['sha256'], row['language'], row['seed'], model)
            unique = list({item['key']: {'key': item['key'], 'text': item['text'], 'speak': item['speak']} for item in items}.values())
            write_json(directory / 'fragments.json', items)
            with self.lock:
                row.update(state='running', phase=ui_text('voiceover.job_phase_loading_model_label'), total=len(unique), fragments=len(items))
            env = self._environment()
            # The workers record their own operations as children of this job's Voiceover event.
            env['VISUALFORGE_EVENT_PARENT'] = (row.get('generation') or {}).get('id', '')
            result = self._synthesize(row, directory, env, log_path, unique, 0)
            checked = None if result is None else self._check_and_repair(row, directory, env, log_path, unique, items, chapters, result.get('flagged', []))
            if checked is None:
                with self.lock:
                    row.update(state='cancelled', phase=ui_text('voiceover.job_phase_cancelled_label'))
                return
            flagged, verification = checked
            with self.lock:
                row.update(phase=ui_text('voiceover.job_phase_assembling_label'), done=row['total'])
            command, output, chapter_rows, seconds = assemble.prepare(directory, self.cache, items, chapters, book['title'], row['format'], self.ffmpeg)
            process = self._spawn(row, command, env, directory)
            if process is None:
                with self.lock:
                    row.update(state='cancelled', phase=ui_text('voiceover.job_phase_cancelled_label'))
                return
            code = self._pump(process, log_path, lambda line: None)
            self._release()
            with self.lock:
                if row.get('cancel_requested'):
                    row.update(state='cancelled', phase=ui_text('voiceover.job_phase_cancelled_label'))
                    return
            if code != 0 or not output.is_file() or output.stat().st_size == 0:
                tail = log_path.read_bytes()[-600:].decode('utf-8', errors='replace') if log_path.is_file() else ''
                raise RuntimeError(ui_text('voiceover.manager_ffmpeg_failed_error') % tail.strip())
            with self.lock:
                row.update(state='done', phase=ui_text('voiceover.job_phase_completed_label'), finished=time.time(), duration_sec=round(seconds, 2),
                           chapters=chapter_rows, flagged=flagged, verification=verification, reused=result.get('reused', 0))
                generation_finish(row.get('generation'))
                target = publish(output, resolve_path(row['save_dir'], self.paths.root), self.public(row), self.paths.root)
                row.update(filename=target.name, output=stored_path(target, self.paths.root))
            output.unlink(missing_ok=True)  # the published copy is the result; fragments stay cached for a quick re-run
        except Exception as exc:
            with self.lock:
                row.update(state='error', phase=ui_text('voiceover.job_phase_error_label'), error=str(exc)[:2000])
            try:
                with log_path.open('a', encoding='utf-8') as log:
                    log.write('\nVoiceover bridge error: ' + str(exc)[:2000] + '\n')
            except OSError:
                pass
        finally:
            self._release()
            with self.lock:
                row['finished'] = time.time()
                terminal = 'cancelled' if row['state'] == 'cancelled' else 'error' if row['state'] == 'error' else 'done'
                generation_finish(row.get('generation'), state=terminal)
                generation_log(log_path, row.get('generation'))
                self.history.insert(0, {key: value for key, value in row.items() if key not in ('token', 'replacements')})
                self.history = self.history[:200]
                try:
                    write_json(self.history_file, self.history)
                except OSError as exc:
                    row['error'] = ui_text('voiceover.manager_save_history_failed_error') + str(exc)[:300]

    def close(self):
        with self.lock:
            self.closing = True
            if self.active and self.active['state'] not in TERMINAL:
                self.active['cancel_requested'] = True
            if self.owned:
                self.owned.close()
            elif self.process and self.process.poll() is None:
                self.process.terminate()
