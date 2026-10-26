"""A private, owned Ollama process and streaming chat. Nothing is shared with a user's own Ollama installation:
own port, own model store (Models/Text/ollama), own home folder, killed with the application's job object."""
import atexit
import itertools
import json
import math
import os
from pathlib import Path
import select
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from gpu import Busy  # re-exported: the chat raises it for "already answering"

DEFAULT_PORT = 8292
DEFAULTS = dict(model='', num_ctx=32768, temperature=1.0, top_p=0.95, top_k=64, num_predict=8192, keep_alive='10m', native_parameters=True)
LIMITS = dict(num_ctx=(2048, 131072), temperature=(0.0, 2.0), top_p=(0.05, 1.0), top_k=(1, 200), num_predict=(16, 32768))
KEEP_ALIVE_CHOICES = ('2m', '10m', '30m', '60m')
REGISTRY = 'registry.ollama.ai'


def _number(value, name, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high or (integer and value != int(value)):
        raise ValueError('%s: %s от %g до %g' % (name, 'целое число' if integer else 'число', low, high))
    return int(value) if integer else float(value)


class TextManager:
    def __init__(self, paths, command=None):
        self.paths = paths
        self.root = Path(os.environ.get('VISUALFORGE_TEXT_ROOT', paths.root / 'Runtime/Text')).resolve()
        self.models = Path(os.environ.get('VISUALFORGE_TEXT_MODELS', paths.models / 'Text/ollama')).resolve()
        self.state = Path(os.environ.get('VISUALFORGE_TEXT_STATE_ROOT', paths.cache / 'Text'))
        self.settings_path = paths.settings / 'text.settings.json'
        self.chats_dir = paths.settings.parent / 'Text/Chats'
        configured = DEFAULT_PORT
        port_file = paths.ports if paths.modern else None
        if port_file and port_file.is_file() and port_file.stat().st_size <= 8192:
            configured = json.loads(port_file.read_text(encoding='utf-8-sig')).get('TextPort', configured)
        self.port = int(os.environ.get('VISUALFORGE_TEXT_PORT', configured))
        if not 1024 <= self.port <= 65535 or self.port in (int(os.environ.get('QWEN_UI_PORT', '8189')), int(os.environ.get('QWEN_COMFY_PORT', '8188'))):
            raise ValueError('Invalid private text port')
        self.command = command
        self.process = None
        self.job = None
        self.active = {}
        self.capabilities = {}
        self.lock = threading.RLock()
        self.opener = build_opener(ProxyHandler({}))
        atexit.register(self.close)

    # ---- installation -------------------------------------------------------------------------------------------
    @property
    def executable(self):
        return self.root / 'ollama/ollama.exe'

    def installed_models(self, include_disabled=False):
        """Models whose manifest and every blob are present (names as the Ollama API knows them)."""
        found = []
        base = self.models / 'manifests'
        for path in sorted(base.rglob('*')) if base.is_dir() else []:
            if not path.is_file() or len(path.relative_to(base).parts) != 4:
                continue
            host, namespace, model, tag = path.relative_to(base).parts
            name = ('%s:%s' % (model, tag)) if (host, namespace) == (REGISTRY, 'library') else '%s/%s/%s:%s' % (host, namespace, model, tag)
            try:
                manifest = json.loads(path.read_text(encoding='utf-8'))
                layers = [manifest['config']] + list(manifest['layers'])
                missing = [layer['digest'] for layer in layers if not self._blob(layer['digest']).is_file() or self._blob(layer['digest']).stat().st_size != layer['size']]
                weights = sum(layer['size'] for layer in manifest['layers'] if layer.get('mediaType', '').endswith(('.model', '.projector')))
                vision = any(layer.get('mediaType', '').endswith('.projector') for layer in manifest['layers'])
                parameters = {}
                details = {}
                config = manifest['config']
                if 0 < config['size'] <= 65536:
                    try:
                        details = json.loads(self._blob(config['digest']).read_text(encoding='utf-8'))
                        if not isinstance(details, dict):
                            details = {}
                    except (OSError, ValueError):
                        pass
                disk_size = path.stat().st_size + sum({layer['digest']: layer['size'] for layer in layers}.values())
                for layer in manifest['layers']:
                    if layer.get('mediaType', '').endswith('.params') and 0 < layer['size'] <= 65536:
                        value = json.loads(self._blob(layer['digest']).read_text(encoding='utf-8'))
                        if isinstance(value, dict):
                            parameters.update(value)
            except (OSError, ValueError, KeyError, TypeError):
                found.append(dict(name=name, label=model, ok=False, size=0, vision=False, missing=['manifest']))
                continue
            found.append(dict(name=name, label=model, ok=not missing, size=weights, vision=vision, missing=missing, parameters=parameters,
                disk_size=disk_size, parameter_size=details.get('model_type'), quantization=details.get('file_type')))
        from model_policy import ModelPolicy
        policy = ModelPolicy(self.paths.root)
        for row in found:
            row['enabled'] = policy.enabled('text', row['name'])
            if not row['enabled'] and not include_disabled:
                row['ok'] = False
        return found

    def _blob(self, digest):
        return self.models / 'blobs' / digest.replace(':', '-')

    def availability(self):
        models = self.installed_models()
        usable = [m for m in models if m['ok']]
        problems = []
        if self.command is None and not self.executable.is_file():
            problems.append('Не найден Runtime/Text/ollama/ollama.exe')
        if not usable:
            problems.append('Нет установленной текстовой модели в Models/Text/ollama. Скачайте её во вкладке «Компоненты».')
        return dict(available=not problems, reason='; '.join(problems), models=models, running=self.alive(), port=self.port)

    # ---- settings -----------------------------------------------------------------------------------------------
    def settings(self, data=None):
        stored = {}
        if self.settings_path.is_file() and self.settings_path.stat().st_size <= 8192:
            try:
                stored = json.loads(self.settings_path.read_text(encoding='utf-8'))
            except ValueError:
                stored = {}
        current = {**DEFAULTS, **{key: value for key, value in stored.items() if key in DEFAULTS}}
        if data is None:
            return current
        if not isinstance(data, dict) or set(data) - set(DEFAULTS):
            raise ValueError('Настройки Text содержат неподдерживаемые параметры')
        names = {m['name'] for m in self.installed_models() if m['ok']}
        for key, value in data.items():
            if key == 'model':
                if value != '' and value not in names:
                    raise ValueError('Эта модель не установлена')
            elif key == 'native_parameters':
                if not isinstance(value, bool):
                    raise ValueError('Настройки из Ollama: включено или выключено')
            elif key == 'keep_alive':
                if value not in KEEP_ALIVE_CHOICES:
                    raise ValueError('Выгружать модель из памяти через: 2, 10, 30 или 60 минут')
            else:
                value = _number(value, key, *LIMITS[key], integer=key in ('num_ctx', 'top_k', 'num_predict'))
            current[key] = value
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_name(self.settings_path.name + '.tmp')
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(temporary, self.settings_path)
        return current

    def context_length(self, model):
        settings = self.settings()
        if settings['native_parameters']:
            for item in self.installed_models():
                if item['name'] == model:
                    value = item.get('parameters', {}).get('num_ctx')
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        return value
        return settings['num_ctx']

    def default_model(self):
        names = [m['name'] for m in self.installed_models() if m['ok']]
        chosen = self.settings()['model']
        return chosen if chosen in names else (names[0] if names else '')

    # ---- process ------------------------------------------------------------------------------------------------
    def alive(self):
        return bool(self.process and self.process.poll() is None)

    def environment(self):
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith('OLLAMA') and key.upper() not in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')}
        settings = self.settings()
        home = self.state / 'home'
        home.mkdir(parents=True, exist_ok=True)
        env.update(OLLAMA_HOST='127.0.0.1:%d' % self.port, OLLAMA_MODELS=str(self.models), OLLAMA_NOPRUNE='1', OLLAMA_NO_CLOUD='1', OLLAMA_NOHISTORY='1',
                   OLLAMA_FLASH_ATTENTION='1', OLLAMA_KV_CACHE_TYPE='f16', OLLAMA_NUM_PARALLEL='1', OLLAMA_MAX_LOADED_MODELS='1',
                   OLLAMA_KEEP_ALIVE=settings['keep_alive'], OLLAMA_LOAD_TIMEOUT='10m', OLLAMA_CONTEXT_LENGTH=str(settings['num_ctx']),
                   OLLAMA_DEBUG='0', USERPROFILE=str(home), HOME=str(home), OLLAMA_TMPDIR=str(self.state / 'tmp'), CUDA_CACHE_PATH=str(self.state / 'cuda'))
        (self.state / 'tmp').mkdir(parents=True, exist_ok=True)
        return env

    def start(self):
        with self.lock:
            if self.alive():
                return
            status = self.availability()
            if not status['available']:
                raise ValueError(status['reason'] or 'Text недоступен')
            if self.job:
                self.job.close()
                self.job = None
            with socket.socket() as probe:
                try:
                    probe.bind(('127.0.0.1', self.port))
                except OSError:
                    raise ValueError('Порт Text занят другой программой. Измените TextPort в настройке запуска.') from None
            log = self.paths.logs / 'text-backend.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            if log.is_file() and log.stat().st_size > 4 * 1024 * 1024:
                log.write_bytes(log.read_bytes()[-512 * 1024:])
            command = self.command or [str(self.executable), 'serve']
            with log.open('ab') as stream:
                self.process = subprocess.Popen(command, cwd=str(self.root if self.root.is_dir() else self.paths.root), env=self.environment(), stdout=stream,
                                                stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            from video.owned import OwnedJob
            try:
                self.job = OwnedJob(self.process)
            except OSError:
                self.process.terminate()
                raise
            self.capabilities = {}
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise ValueError('Ollama (Text) не запустился. Проверьте журнал text-backend.log.')
                try:
                    self.call('/api/version', timeout=2)
                    return
                except (OSError, ValueError):
                    time.sleep(0.2)
            raise ValueError('Ollama (Text) не ответил при запуске')

    def close(self):
        with self.lock:
            for event in self.active.values():
                event.set()
            if self.job:
                self.job.close()
                self.job = None
            if self.process and self.process.poll() is None:
                self.process.terminate()
            self.process = None

    def call(self, path, data=None, timeout=15):
        raw = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
        request = Request('http://127.0.0.1:%d%s' % (self.port, path), data=raw, headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return json.loads(response.read() or b'{}')
        except HTTPError as error:
            raise ValueError(self._error_text(error)) from None

    @staticmethod
    def _error_from(raw, status):
        try:
            return str(json.loads(raw).get('error') or 'Ошибка Ollama')[:600]
        except (ValueError, AttributeError):
            return 'Ошибка Ollama (%s)' % status

    @classmethod
    def _error_text(cls, error):
        return cls._error_from(error.read(4000), error.code)

    # ---- state --------------------------------------------------------------------------------------------------
    def loaded(self):
        if not self.alive():
            return []
        try:
            return [dict(name=m.get('name'), size=m.get('size', 0), size_vram=m.get('size_vram', 0), context=m.get('context_length'), expires=m.get('expires_at'))
                    for m in self.call('/api/ps', timeout=3).get('models', [])]
        except (OSError, ValueError):
            return []

    # The lock guards `active` only: Ollama calls (/api/ps, an unload of up to 50 s) run outside it, or the busy poll and Stop would wait.
    def status(self):
        with self.lock:
            generating = sorted(self.active)
        return dict(running=self.alive(), loaded=self.loaded(), generating=generating, settings=self.settings(), default_model=self.default_model())

    def busy(self):
        with self.lock:
            return bool(self.active)

    def begin(self, chat_id):
        with self.lock:
            if self.active:
                raise Busy('Text уже отвечает. Дождитесь окончания ответа или остановите его.')
            event = self.active[chat_id] = threading.Event()
            return event

    def end(self, chat_id):
        with self.lock:
            self.active.pop(chat_id, None)

    def stop_chat(self, chat_id):
        with self.lock:
            event = self.active.get(chat_id)
            if event is None:
                return dict(stopped=False)
            event.set()
            return dict(stopped=True)

    def unload(self):
        """Free the GPU: ask Ollama to drop the loaded model (the small server process stays)."""
        if not self.alive():
            return dict(unloaded=[])
        names = [m['name'] for m in self.loaded()]
        for name in names:
            try:
                self.call('/api/generate', dict(model=name, keep_alive=0), timeout=30)
            except (OSError, ValueError):
                pass
        deadline = time.monotonic() + 20
        while names and time.monotonic() < deadline and self.loaded():
            time.sleep(0.3)
        return dict(unloaded=names)

    def release_gpu(self):
        """Called before an Image/Video/Music job: refuse while Text answers, otherwise unload the model. Callers hold the bridge's
        submission lock, which also admits chat answers, so no answer can start between the check and the unload."""
        with self.lock:
            if self.active:
                raise Busy('GPU занят Text: идёт ответ модели. Дождитесь окончания или остановите ответ.')
        return self.unload()

    # ---- chat ---------------------------------------------------------------------------------------------------
    def can_think(self, model):
        if model not in self.capabilities:
            try:
                self.capabilities[model] = list(self.call('/api/show', dict(model=model), timeout=30).get('capabilities') or [])
            except (OSError, ValueError):
                return None
        return 'thinking' in self.capabilities[model]

    def stream_chat(self, model, messages, think, cancel, options=None):
        """Yield Ollama /api/chat NDJSON objects until done or `cancel` is set. `options` override the chat settings (voice input edits with its own)."""
        settings = self.settings()
        # An empty options object preserves every imported Modelfile parameter, including ones absent from our controls.
        manual = {} if settings['native_parameters'] else {key: settings[key] for key in LIMITS}
        options = {**manual, **(options or {})}
        body = dict(model=model, messages=messages, stream=True, options=options, keep_alive=settings['keep_alive'])
        if think is not None and self.can_think(model):
            body['think'] = bool(think)
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        try:
            for line in self._stream_lines(payload, cancel):
                if line.strip():
                    row = json.loads(line)
                    if row.get('error'):
                        raise ValueError(str(row['error'])[:600])
                    yield row
        except OSError:
            if cancel.is_set():
                return
            raise

    # Ollama sends no byte (not even the HTTP headers) while it loads the model, which takes minutes from a hard disk.
    # Windows socket shutdown did not reliably wake a blocking recv during this phase.
    # Poll every 0.2 s so Stop can close the request even before response headers arrive.
    @staticmethod
    def _socket_pieces(sock, cancel):
        while not cancel.is_set():
            if select.select([sock], [], [], 0.2)[0]:
                piece = sock.recv(65536)
                if not piece:
                    return
                yield piece

    @staticmethod
    def _unchunk(initial, pieces):
        buffer, source = initial, iter(pieces)

        def more():
            nonlocal buffer
            piece = next(source, None)
            if piece is None:
                return False
            buffer += piece
            return True

        while True:
            while b'\r\n' not in buffer:
                if not more():
                    return
            line, _, buffer = buffer.partition(b'\r\n')
            size = int(line.split(b';')[0], 16)
            if size == 0:
                return
            while len(buffer) < size + 2:
                if not more():
                    return
            yield buffer[:size]
            buffer = buffer[size + 2:]

    def _stream_lines(self, payload, cancel):
        """POST /api/chat and yield the response body line by line; ends quietly when `cancel` is set."""
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=10)
        try:
            sock.sendall(b'POST /api/chat HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: %d\r\n\r\n' % len(payload) + payload)
            pieces = self._socket_pieces(sock, cancel)
            head = b''
            for piece in pieces:
                head += piece
                if b'\r\n\r\n' in head:
                    break
            else:
                return
            header, _, rest = head.partition(b'\r\n\r\n')
            status = int(header.split(b' ', 2)[1])
            data = self._unchunk(rest, pieces) if b'transfer-encoding: chunked' in header.lower() else itertools.chain([rest], pieces)
            if status != 200:
                raise ValueError(self._error_from(b''.join(itertools.islice(data, 8))[:4000], status))
            pending = b''
            for piece in data:
                pending += piece
                while b'\n' in pending:
                    line, _, pending = pending.partition(b'\n')
                    yield line
            if pending.strip():
                yield pending
        finally:
            sock.close()
