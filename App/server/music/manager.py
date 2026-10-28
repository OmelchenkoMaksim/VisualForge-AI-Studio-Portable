"""Stdlib bridge manager for isolated, cancellable local Music jobs."""
import atexit
from collections import deque
import json
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
import tempfile
import uuid
from generation import begin as generation_begin, ensure as generation_ensure, finish as generation_finish, append_log as generation_log, duration_between, remove as remove_event
from .comfy_backend import Cancelled, ComfyBackend, ComfyError
from .registry import ENGINE, ENGINES, get_engine, validate
from .storage import publish, read_json, resolve_path, stored_path, write_json
from video.owned import OwnedJob

TERMINAL = ('done', 'error', 'cancelled')

class MusicManager:
    def __init__(self, paths):
        self.paths = paths
        self.root = Path(os.environ.get('VISUALFORGE_MUSIC_ROOT', paths.root / 'Runtime/Music')).resolve()
        self.models = Path(os.environ.get('VISUALFORGE_MUSIC_MODELS', paths.models / 'Music/ACE-Step-1.5')).resolve()
        install_key = hashlib.sha256(str(paths.root).encode('utf-8')).hexdigest()[:12]
        self.state = Path(os.environ.get('VISUALFORGE_MUSIC_STATE_ROOT', Path(tempfile.gettempdir()) / 'VisualForgeMusic' / install_key)).resolve()
        self.logs = paths.logs / 'Music'
        self.settings_file = paths.settings / 'music.settings.json'
        self.history_file = paths.settings / 'music-history.json'
        self.lock = threading.RLock()
        self.active = None
        self.process = None
        self.owned = None
        self.comfy = ComfyBackend(paths)  # Stable Audio runs in the Image ComfyUI; the bridge attaches the connection
        self.backend_active = False
        self.closing = False
        self.history = self._read_history()
        atexit.register(self.close)

    def _read_history(self):
        """A damaged history file used to switch the whole Music section off; it is set aside instead and kept for recovery."""
        try:
            history = read_json(self.history_file, [])
            if isinstance(history, list) and all(isinstance(row, dict) and isinstance(row.get('id'), str) for row in history):
                for row in history:
                    generation_ensure(row, 'Music', row['id'], row.get('created'), duration_between(row.get('created'), row.get('finished')))
                return history
        except (OSError, ValueError):
            pass
        kept = self.history_file.with_name('music-history.unreadable-%s.json' % time.strftime('%Y%m%d-%H%M%S'))
        try:
            os.replace(self.history_file, kept)
            print('История Music не читается; файл сохранён как %s, начата новая история.' % kept.name, flush=True)
        except OSError as error:
            print('История Music не читается и не может быть отложена: %s' % error, flush=True)
        return []

    def engine_paths(self, ident):
        if ident == 'minimax-music3-int8':
            return (Path(os.environ.get('VISUALFORGE_MINIMAX_RUNTIME',self.paths.root/'Runtime/Video')).resolve(),
                    Path(os.environ.get('VISUALFORGE_MINIMAX_MODELS',self.paths.models/'Music/MiniMax-Music3')).resolve(),
                    'minimax_worker.py')
        return self.root,self.models,'worker.py'

    def availability(self, ident=None):
        ace = self._ace_availability()
        root,models,_ = self.engine_paths('minimax-music3-int8')
        required=[root/name for name in ('python/python.exe','ffmpeg/bin/ffmpeg.exe','vendor/ComfyUI/comfy_extras/nodes_minimax_music.py','vendor/ComfyUI/comfy/ldm/minimax_music/ar.py')]
        required += [models/name for name in ('diffusion_models/minimax_music3_dit_int8_convrot.safetensors','text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors','vae/minimax_music3_dav.safetensors','LICENSE')]
        missing=[str(path) for path in required if not path.is_file()]
        mini=dict(ENGINES['minimax-music3-int8'],available=not missing,missing=missing,
                  reason='Файлы MiniMax Music3 не установлены или недоступны.' if missing else '')
        evidence=read_json(Path(os.environ.get('VISUALFORGE_MINIMAX_VERIFICATION',self.paths.root/'Support/Diagnostics/MiniMaxMusicVerification.json')),{})
        mini['evidence']=evidence.get('summary_ru','Работа MiniMax Music3 на этой GPU ещё не проверена.')
        sa3=dict(ENGINES['stable-audio-3-medium'],**self.comfy.availability())
        evidence=read_json(Path(os.environ.get('VISUALFORGE_SA3_VERIFICATION',self.paths.root/'Support/Diagnostics/StableAudio3Verification.json')),{})
        sa3['evidence']=evidence.get('summary_ru','Работа Stable Audio 3 Medium на этой GPU ещё не проверена.')
        profiles=[dict(ace['engine'],available=ace['available'],missing=ace['missing'],reason=ace['reason']),mini,sa3]
        from model_policy import apply_profiles
        apply_profiles(self.paths.root, 'music', profiles)
        chosen=next((row for row in profiles if row['id']==ident),profiles[0])
        available=chosen['available'] if ident else any(row['available'] for row in profiles)
        return dict(available=available,engine=chosen,engines=profiles,busy=self.busy(),
                    reason=chosen['reason'] if ident else ('' if available else 'Среда или модели Music недоступны.'),missing=chosen['missing'])

    def _ace_availability(self):
        required = [self.root / name for name in ('python/python.exe', 'python/msvcp140.dll', 'vendor/ACE-Step/acestep/handler.py', 'requirements-lock.txt')]
        required += [self.models / name for name in (
            'acestep-v15-turbo/model.safetensors', 'vae/diffusion_pytorch_model.safetensors',
            'Qwen3-Embedding-0.6B/model.safetensors', 'acestep-5Hz-lm-0.6B/model.safetensors')]
        missing = [str(path) for path in required if not path.is_file()]
        engine = dict(ENGINE)
        evidence = read_json(self.root / 'verification.json', {})
        engine['evidence'] = evidence.get('summary_ru', 'Работа этой сборки на GPU ещё не проверена.')
        return {'available': not missing, 'engine': engine,
                'reason': 'Среда или модели Music ещё не установлены. Модели можно скачать во вкладке «Компоненты».' if missing else '',
                'busy': self.busy(), 'missing': missing}

    def busy(self):
        with self.lock:
            return bool(self.active and self.active['state'] not in TERMINAL)

    def settings(self, data=None):
        with self.lock:
            default = {'save_dir': 'UserData/Results/Music'}
            try:
                current = read_json(self.settings_file, default)
            except (OSError, ValueError):
                current = default  # an unreadable settings file falls back to the portable folder instead of failing every job
            if not isinstance(current, dict) or not isinstance(current.get('save_dir'), str) or not current['save_dir'].strip():
                current = default
            if data is not None:
                value = data.get('save_dir')
                if not isinstance(value, str) or not value.strip() or len(value) > 1000:
                    raise ValueError('Укажите папку результатов Music')
                path = resolve_path(value.strip(), self.paths.root)
                if path.exists() and not path.is_dir():
                    raise ValueError('Путь Music не является папкой')
                current = {'save_dir': stored_path(path, self.paths.root)}
                write_json(self.settings_file, current)
            path = resolve_path(current['save_dir'], self.paths.root)
            return {'save_dir': str(path), 'portable': path.is_relative_to(self.paths.root)}

    def submit(self, request):
        request = validate(request)
        backend = get_engine(request['engine'])['backend']
        if backend == 'comfy':
            self.comfy.preflight(request)  # one HTTP call to ComfyUI, kept outside the manager lock
        with self.lock:
            if self.closing or self.busy():
                raise ValueError('Music уже занят или студия закрывается')
            availability=self.availability(request['engine'])
            if not availability['available']:
                raise ValueError(availability['reason'])
            ident = uuid.uuid4().hex
            directory = self.state / ident
            directory.mkdir(parents=True, exist_ok=False)
            row = dict(request, id=ident, generation=generation_begin('Music'), state='queued', phase='Подготовка Music',
                       created=time.time(), token=secrets.token_urlsafe(24),
                       upstream_revision=get_engine(request['engine'])['upstream_revision'], backend=backend,
                       save_dir=stored_path(Path(self.settings()['save_dir']), self.paths.root))
            if backend == 'worker':
                root,models,_=self.engine_paths(request['engine'])
                write_json(directory / 'request.json', {'runtime': str(root), 'models': str(models), 'request': request})
            self.active = row
            threading.Thread(target=self._run, args=(row, directory), daemon=True).start()
            return self.public(row, include_token=True)

    def public(self, row, include_token=False):
        result = {key: value for key, value in row.items() if key != 'token'}
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
            if row['state'] == 'running':
                progress = read_json(self.state / row['id'] / 'progress.json', {})
                row.update({key: progress[key] for key in ('phase', 'progress') if key in progress})
            return self.public(row, include_token=row is self.active and row['state'] not in TERMINAL)

    @staticmethod
    def _missing():
        raise ValueError('Задача Music не найдена')

    def entries(self):
        with self.lock:
            return {'entries': [self.public(row) for row in self.history[:100]]}

    def delete(self, data):
        from job_delete import DeletePlan, confined_file, plain_path, write_receipt
        ident = data.get('id')
        if not isinstance(ident, str) or len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError('Неверный идентификатор Music')
        with self.lock:
            # History is published only after the worker has closed its log and output handles.
            row = next((r for r in self.history if r['id'] == ident), None)
            if row is None or row.get('state') not in TERMINAL:
                raise ValueError('Задача не найдена или ещё выполняется. Сначала остановите генерацию.')
            plan = DeletePlan()
            if row.get('output'):
                output = Path(row['output'])
                if not output.is_absolute():
                    output = confined_file(self.paths.root, row['output'])
                if output.suffix.lower() != '.wav':
                    raise ValueError('Некорректный файл результата Music')
                plan.file(output)
                plan.sidecar(output.with_suffix('.json'), 'id', ident)
            # Stable Audio owns a separate ComfyUI history entry and a temporary FLAC as well.
            manifest = read_json(plain_path(self.state / ident / 'comfy-artifacts.json'), {})
            prompt_id = row.get('comfy_prompt_id') or manifest.get('prompt_id') or (row.get('metadata') or {}).get('comfy_prompt_id')
            if prompt_id:
                if str(uuid.UUID(prompt_id)) != prompt_id or self.comfy.client is None:
                    raise ValueError('Не удалось проверить данные Stable Audio в ComfyUI')
                try:
                    entry = (self.comfy.client.get('/history/' + prompt_id, timeout=15) or {}).get(prompt_id) or {}
                    outputs = list(row.get('comfy_files') or manifest.get('outputs') or [])
                    for node in entry.get('outputs', {}).values():
                        outputs.extend(node.get('audio', []))
                    for item in outputs:
                        area = item.get('type', 'temp')
                        if area not in ('output', 'temp'):
                            raise ValueError('Некорректная область файла Stable Audio')
                        relative = str(Path(item.get('subfolder', '')) / item['filename'])
                        plan.file(confined_file(getattr(self.paths, 'comfy_' + area), relative))
                except ComfyError as error:
                    raise ValueError('ComfyUI недоступен. Повторите удаление после его запуска: ' + str(error)[:300]) from None
            plan.file(self.logs / (ident + '.log'))
            plan.tree(self.state / ident)
            remaining = [r for r in self.history if r['id'] != ident]
            if prompt_id:
                # Preserve remote ownership facts before removing scratch; a failed HTTP delete must be retryable after restart.
                row.update(comfy_prompt_id=prompt_id, comfy_files=outputs)
                write_receipt(self.history_file, self.history)
            plan.execute()
            if prompt_id:
                try:
                    self.comfy.client.post('/history', {'delete': [prompt_id]}, timeout=15)
                except ComfyError as error:
                    raise ValueError('Файлы удалены, но история ComfyUI пока недоступна. Повторите удаление: ' + str(error)[:300]) from None
            # Keep the receipt on any failure, including persistence, so deletion can be retried.
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
                raise ValueError('Неверная задача или ключ отмены Music')
            if row['state'] not in TERMINAL:
                row['cancel_requested'] = True
                row['phase'] = 'Отмена Music'
                if self.owned:
                    self.owned.close()
                elif self.process and self.process.poll() is None:
                    self.process.terminate()
                # A Stable Audio job notices row['cancel_requested'] on its own thread and cancels only its ComfyUI prompt.
            return {'cancelling': row['state'] not in TERMINAL}

    def file(self, ident):
        with self.lock:
            row = next((r for r in self.history if r['id'] == ident and r['state'] == 'done'), None)
            if not row:
                return self._missing()
            path = resolve_path(row['output'], self.paths.root)
            if path.suffix.lower() != '.wav' or not path.is_file():
                raise ValueError('Аудиофайл Music недоступен')
            return path

    def log(self, ident):
        if not isinstance(ident, str) or len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError('Неверный идентификатор Music')
        path = self.logs / (ident + '.log')
        if not path.exists():
            return {'text': 'Журнал пока пуст.'}
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 16000))
            return {'text': stream.read().decode('utf-8', errors='replace')}

    def _environment(self):
        env = os.environ.copy()
        temp = self.state / 'RuntimeCache'
        temp.mkdir(parents=True, exist_ok=True)
        env.update(PYTHONUTF8='1', PYTHONNOUSERSITE='1', HF_HUB_OFFLINE='1',
            TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_PROGRESS_BARS='1',
            TOKENIZERS_PARALLELISM='false', TQDM_DISABLE='1', DO_NOT_TRACK='1',
            ACESTEP_CHECKPOINTS_DIR=str(self.models), ACESTEP_PROJECT_ROOT=str(self.root / 'vendor/ACE-Step'),
            HF_HOME=str(temp / 'huggingface'), TORCH_HOME=str(temp / 'torch'),
            TORCHINDUCTOR_CACHE_DIR=str(temp / 'inductor'), TEMP=str(temp), TMP=str(temp))
        env.pop('PYTHONHOME', None)
        env.pop('PYTHONPATH', None)
        return env

    def _run(self, row, directory):
        engine_record = generation_begin('MusicModelGeneration', parent_id=(row.get('generation') or {}).get('id'))
        try:
            if row.get('backend') == 'comfy':
                self._run_comfy(row, directory)
                return
            env = self._environment()
            env['VISUALFORGE_EVENT_PARENT'] = engine_record['id']
            self.logs.mkdir(parents=True, exist_ok=True)
            with self.lock:
                if row.get('cancel_requested'):
                    row.update(state='cancelled', phase='Отменено')
                    return
                root,_,worker=self.engine_paths(row['engine'])
                self.process = subprocess.Popen([str(root / 'python/python.exe'), '-u', str(Path(__file__).with_name(worker)), str(directory / 'request.json')],
                    env=env, cwd=str(directory), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try:
                    self.owned = OwnedJob(self.process)
                except OSError:
                    self.process.terminate()
                    self.process.wait(timeout=10)
                    raise
                row.update(state='running', phase='Загрузка Music')
            tail = deque(maxlen=160)
            total = 0
            path = self.logs / (row['id'] + '.log')
            with path.open('wb') as log:
                for line in iter(self.process.stdout.readline, b''):
                    line = line[-4096:]
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
            code = self.process.wait()
            with self.lock:
                if row.get('cancel_requested'):
                    row.update(state='cancelled', phase='Отменено')
                elif code != 0:
                    error = read_json(directory / 'error.json', {})
                    raise RuntimeError(error.get('error') or f'Music завершился с кодом {code}. Откройте журнал задачи.')
                else:
                    result = read_json(directory / 'result.json', {})
                    source = Path(result.get('path', '')).resolve()
                    if not source.is_relative_to(directory.resolve()) or source.suffix.lower() != '.wav' or not source.is_file():
                        raise RuntimeError('Music не создал готовый WAV')
                    row.update(state='done', phase='Готово', metadata=result['metadata'], finished=time.time())
                    generation_finish(row.get('generation'))
                    target = publish(source, resolve_path(row['save_dir'], self.paths.root), self.public(row), self.paths.root)
                    row.update(filename=target.name, output=stored_path(target, self.paths.root))
        except Exception as exc:
            with self.lock:
                row.update(state='error', phase='Ошибка Music', error=str(exc)[:2000])
            try:
                self.logs.mkdir(parents=True, exist_ok=True)
                with (self.logs / (row['id'] + '.log')).open('a', encoding='utf-8') as log:
                    log.write('\nMusic bridge error: ' + str(exc)[:2000] + '\n')
            except OSError:
                pass
        finally:
            with self.lock:
                if self.owned:
                    self.owned.close()
                self.owned = None
                self.process = None
                self.backend_active = False
                row['finished'] = time.time()
                terminal_state = 'cancelled' if row['state'] == 'cancelled' else 'error' if row['state'] == 'error' else 'done'
                generation_finish(engine_record, state=terminal_state)
                generation_finish(row.get('generation'), state=terminal_state)
                generation_log(self.logs / (row['id'] + '.log'), row.get('generation'))
                self.history.insert(0, {key: value for key, value in row.items() if key != 'token'})
                self.history = self.history[:200]
                try:
                    write_json(self.history_file, self.history)
                except OSError as exc:
                    row['error'] = 'Не удалось сохранить историю Music: ' + str(exc)[:300]
                # Only the current job's reproducible generated audio and tensors, after publication.
                # Keep the small request, progress and error metadata for diagnosis.
                if row['state'] == 'done':
                    import shutil
                    scratch = (directory / 'audio').resolve()
                    if directory.resolve().is_relative_to(self.state) and scratch.is_relative_to(directory.resolve()):
                        shutil.rmtree(scratch, ignore_errors=True)

    def _run_comfy(self, row, directory):
        """A Stable Audio job: same states, log, history and publication as a worker job, but the work happens in the Image ComfyUI."""
        self.logs.mkdir(parents=True, exist_ok=True)
        log_path = self.logs / (row['id'] + '.log')

        def log(line):
            with log_path.open('a', encoding='utf-8') as stream:
                print(time.strftime('%H:%M:%S'), str(line)[:2000], file=stream)

        def progress(phase):
            with self.lock:
                row['phase'] = phase

        with self.lock:
            if row.get('cancel_requested'):
                row.update(state='cancelled', phase='Отменено')
                return
            row.update(state='running', phase='Подготовка Stable Audio')
            self.backend_active = True
        try:
            result = self.comfy.run(row, directory, progress=progress, log=log, cancelled=lambda: bool(row.get('cancel_requested')))
        except Cancelled:
            with self.lock:
                row.update(state='cancelled', phase='Отменено')
            return
        with self.lock:
            if row.get('cancel_requested'):
                row.update(state='cancelled', phase='Отменено')
                return
            source = Path(result.get('path', '')).resolve()
            if not source.is_relative_to(directory.resolve()) or source.suffix.lower() != '.wav' or not source.is_file():
                raise RuntimeError('Music не создал готовый WAV')
            row.update(state='done', phase='Готово', metadata=result['metadata'], finished=time.time())
            generation_finish(row.get('generation'))
            target = publish(source, resolve_path(row['save_dir'], self.paths.root), self.public(row), self.paths.root)
            row.update(filename=target.name, output=stored_path(target, self.paths.root))

    def owned_comfy_prompt_ids(self):
        """ComfyUI prompt ids that Music started; the bridge keeps them out of Image's busy check and Stop."""
        return self.comfy.owned_prompt_ids()

    def close(self):
        with self.lock:
            self.closing = True
            if self.active and self.active['state'] not in TERMINAL:
                self.active['cancel_requested'] = True
            if self.owned:
                self.owned.close()
            elif self.process and self.process.poll() is None:
                self.process.terminate()
