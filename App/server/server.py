"""Local-only VisualForge AI Studio UI bridge. Python stdlib; no remote dependencies."""
import base64
import binascii
import zlib
import struct
from functools import lru_cache
import copy
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError
import uuid
import secrets
import socket
import select
import re
from threading import Lock, Thread, RLock
# Embedded Python may omit the adjacent application folder from sys.path.
import sys as _qwen_import_sys
_qwen_adjacent_dir = str(Path(__file__).resolve().parent)
if _qwen_adjacent_dir not in _qwen_import_sys.path:
    _qwen_import_sys.path.insert(0, _qwen_adjacent_dir)
from smart_filenames import build_filename_base, write_unique_output
from branding import optional_icon
from portable_paths import from_server
from engines.registry import DEFAULT_ENGINE, get_engine
from engines.workflow_builder import validate_request, build_workflow, collect_outputs, collect_expansion, resolve_expansion, resolve_workflow_mode
from engines.preflight import all_readiness, engine_readiness, upscale_available
from job_history import JobHistory
from generation import begin as generation_begin, finish as generation_finish, configure as configure_events, history as event_history, remove as remove_event
from gpu import Busy, GpuCoordinator, GpuUnavailable
from model_policy import ModelPolicy, apply_profiles

ROOT = Path(__file__).resolve().parent
PATHS = from_server()
FRONTEND = PATHS.frontend
WORKFLOWS = PATHS.workflows
COMFY_PORT = int(os.environ.get('QWEN_COMFY_PORT', '8188'))
COMFY = 'http://127.0.0.1:' + str(COMFY_PORT)
PORT = int(os.environ.get('QWEN_UI_PORT', '8189'))
if not (1024 <= PORT <= 65535 and 1024 <= COMFY_PORT <= 65535 and PORT != COMFY_PORT):
    raise ValueError('Invalid loopback port configuration')
LIMIT = 320 * 1024 * 1024
IMAGE_LIMIT = 100 * 1024 * 1024
TEMPLATES = {name: json.loads((WORKFLOWS / (name + '.api.json')).read_text(encoding='utf-8')) for name in ('single', 'reference')}
CLIENT_ID = uuid.uuid4().hex
UPSCALE_MODEL = 'RealESRGAN_x2plus.pth'
QUALITY_STEPS = {'low': 40, 'good': 50, 'high': 60}
UPSCALE_FACTORS = (1, 2, 4)
SETTINGS_FILE = PATHS.settings / ('app.settings.json' if PATHS.modern else 'QwenDesktopApp-v3.settings.json')
DEFAULT_SAVE_DIR_NAME = 'Qwen Image Edit Results'

JOB_TOKENS = {}
CANCELLING = set()
JOB_META = {}
SAVE_LOCKS = {}
JOBS_LOCK = Lock()
HISTORY = JobHistory(PATHS.settings / 'image-job-history.json', portable_root=PATHS.root)
GPU_SUBMISSION_LOCK = RLock()
# Keep admission and start atomic so competing modules cannot both claim the GPU.
# Locking every POST previously let a modal folder dialog block Stop.
GPU_START_ROUTES = frozenset(('/api/generate', '/api/video/generate', '/api/music/generate', '/api/components/install', '/api/text/unload',
                              '/api/components/model-toggle', '/api/components/model-remove-plan', '/api/components/model-remove', '/api/components/repair'))
DIALOG_LOCK = Lock()
# The launcher's splash waits for the page it opened (`?launch=<nonce>`) to report that its ComfyUI check finished.
LAUNCHES = {}
LAUNCH_PATTERN = re.compile(r'[A-Za-z0-9_-]{8,64}')
try:
    from video.manager import VideoManager
    VIDEO = VideoManager(PATHS)
    VIDEO_WARNING = ''
except (ImportError, OSError, ValueError) as _video_error:
    VIDEO = None
    VIDEO_WARNING = 'Video недоступен: ' + str(_video_error)
LOCAL_OPENER = build_opener(ProxyHandler({}))
try:
    from music.manager import MusicManager
    MUSIC = MusicManager(PATHS)
except (ImportError, OSError, ValueError) as _music_error:
    MUSIC = None
    print('Music недоступен: ' + str(_music_error), flush=True)
try:
    from speech.manager import SpeechManager
    SPEECH = SpeechManager(PATHS, free_vram=lambda: _gpu_free_mb(), gpu_busy=lambda: bool(GPU.busy_modules()))
except (ImportError, OSError, ValueError) as _speech_error:
    SPEECH = None
    print('Voice input недоступен: ' + str(_speech_error), flush=True)
try:
    from text.manager import TextManager
    from text.store import ChatStore
    TEXT = TextManager(PATHS)
    TEXT_STORE = ChatStore(TEXT.chats_dir)
except (ImportError, OSError, ValueError) as _text_error:
    TEXT = TEXT_STORE = None
    print('Text недоступен: ' + str(_text_error), flush=True)
AGENTS = None  # constructed below, after the GPU coordinator it needs
STATIC = {'/engine-state.js': ('static/engine-state.js', 'application/javascript; charset=utf-8'), '/': ('static/index.html','text/html; charset=utf-8'), '/app.js': ('static/app.js','application/javascript; charset=utf-8'), '/style.css': ('static/style.css','text/css; charset=utf-8'), '/progress-save.css': ('static/progress-save.css','text/css; charset=utf-8')}
STATIC['/prompt-guides.js'] = ('static/prompt-guides.js', 'application/javascript; charset=utf-8')
STATIC['/video.js'] = ('static/video.js', 'application/javascript; charset=utf-8')
STATIC['/video.css'] = ('static/video.css', 'text/css; charset=utf-8')
STATIC['/music.js'] = ('static/music.js', 'application/javascript; charset=utf-8')
STATIC['/music.css'] = ('static/music.css', 'text/css; charset=utf-8')
STATIC['/text.js'] = ('static/text.js', 'application/javascript; charset=utf-8')
STATIC['/text.css'] = ('static/text.css', 'text/css; charset=utf-8')
STATIC['/agent.js'] = ('static/agent.js', 'application/javascript; charset=utf-8')
STATIC['/agent.css'] = ('static/agent.css', 'text/css; charset=utf-8')
STATIC['/event-history.js'] = ('static/event-history.js', 'application/javascript; charset=utf-8')
STATIC['/event-history.css'] = ('static/event-history.css', 'text/css; charset=utf-8')
STATIC['/voice.js'] = ('static/voice.js', 'application/javascript; charset=utf-8')
STATIC['/voice-gate.js'] = ('static/voice-gate.js', 'application/javascript; charset=utf-8')
STATIC['/voice-worklet.js'] = ('static/voice-worklet.js', 'application/javascript; charset=utf-8')
STATIC['/voice.css'] = ('static/voice.css', 'text/css; charset=utf-8')
STATIC['/components.js'] = ('static/components.js', 'application/javascript; charset=utf-8')
STATIC['/components.css'] = ('static/components.css', 'text/css; charset=utf-8')
STATIC['/studio-ui.js'] = ('static/studio-ui.js', 'application/javascript; charset=utf-8')
STATIC['/studio-ui.css'] = ('static/studio-ui.css', 'text/css; charset=utf-8')
STATIC['/hardware.js'] = ('static/hardware.js', 'application/javascript; charset=utf-8')
STATIC['/hardware.css'] = ('static/hardware.css', 'text/css; charset=utf-8')
STATIC['/model-requirements.js'] = ('static/model-requirements.js', 'application/javascript; charset=utf-8')
STATIC['/theme.css'] = ('static/theme.css', 'text/css; charset=utf-8')
STATIC['/theme-init.js'] = ('static/theme-init.js', 'application/javascript; charset=utf-8')
STATIC['/shell.js'] = ('static/shell.js', 'application/javascript; charset=utf-8')
STATIC['/sounds.js'] = ('static/sounds.js', 'application/javascript; charset=utf-8')
REQUIRED = {'LoadImage', 'UNETLoader', 'CLIPLoader', 'VAELoader', 'LoraLoaderModelOnly', 'ModelSamplingAuraFlow', 'CFGNorm', 'FluxKontextImageScale', 'TextEncodeQwenImageEditPlus', 'FluxKontextMultiReferenceLatentMethod', 'VAEEncode', 'KSampler', 'VAEDecode', 'SaveImage'}


def upstream(path, data=None, content_type='application/json', timeout=15):
    headers = {'Content-Type': content_type} if data is not None else {}
    req = Request(COMFY + path, data=data, headers=headers)
    with LOCAL_OPENER.open(req, timeout=timeout) as response:
        return response.read()


def json_upstream(path, data=None, timeout=15):
    b = json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else None
    return json.loads(upstream(path, b, timeout=timeout))


_COMFY_RETRY_AT = [0.0]


def _comfy_call(action: Callable[[], Any]):
    """Run one ComfyUI call; when it is not answering, treat it as idle and do not ask again for 10 s (a refused
    connection takes about 2 s on Windows, which would slow every busy check and every start)."""
    if time.monotonic() < _COMFY_RETRY_AT[0]:
        return None
    try:
        return action()
    except (URLError, TimeoutError, OSError):
        _COMFY_RETRY_AT[0] = time.monotonic() + 10
        return None


def _foreign_queue(queue):
    """ComfyUI queue entries that are not Music's: Stable Audio runs in this ComfyUI too, but it is a Music job, not Image's."""
    owned = MUSIC.owned_comfy_prompt_ids() if MUSIC is not None else set()
    return [entry for key in ('queue_running', 'queue_pending') for entry in queue.get(key, [])
            if not (len(entry) > 1 and entry[1] in owned)]


def _image_busy():
    queue = _comfy_call(lambda: json_upstream('/queue', timeout=3)) or {}
    return bool(_foreign_queue(queue))


def _comfy_free():
    _comfy_call(lambda: upstream('/free', json.dumps({'unload_models': True, 'free_memory': True}).encode('utf-8'), timeout=5))


def _gpu_free_mb():
    return int(subprocess.check_output(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'], text=True, timeout=3).strip().splitlines()[0])


def _comfy_held_mb():
    """Video memory the ComfyUI process holds through PyTorch (cached models included); ComfyUI reuses it for the next Image job."""
    stats = _comfy_call(lambda: json_upstream('/system_stats', timeout=3)) or {}
    devices = stats.get('devices') or [{}]
    return int(devices[0].get('torch_vram_total', 0)) // (1024 * 1024)


# Every "start" route calls GPU.claim(module): refuse while another module works, free what the idle ones still hold.
GPU = GpuCoordinator(
    probes=dict(image=_image_busy, video=lambda: VIDEO is not None and VIDEO.busy(), music=lambda: MUSIC is not None and MUSIC.busy(), text=lambda: TEXT is not None and TEXT.busy()),
    releasers=dict(image=_comfy_free, text=lambda: TEXT is not None and TEXT.release_gpu(), speech=lambda: SPEECH is not None and SPEECH.release_gpu()),
    free_vram=_gpu_free_mb, held=dict(image=_comfy_held_mb))
if MUSIC is not None:
    from music.comfy_backend import ComfyClient
    MUSIC.comfy.attach(ComfyClient(COMFY))  # Stable Audio uses the Image ComfyUI
if SPEECH is not None and TEXT is not None:
    from speech.polish import Polisher
    SPEECH.polisher = Polisher(TEXT, GPU, GPU_SUBMISSION_LOCK)
if TEXT is not None:
    try:
        from agents.manager import AgentManager
        AGENTS = AgentManager(PATHS, TEXT, TEXT_STORE, GPU, GPU_SUBMISSION_LOCK)
    except (ImportError, OSError, ValueError) as _agents_error:
        print('Режим «Агент» недоступен: ' + str(_agents_error), flush=True)
try:
    from provision.manager import ComponentsManager
    COMPONENTS = ComponentsManager(PATHS.root, PATHS.root / 'App/config/components.json', GPU)
    COMPONENTS.models.text_models = lambda: TEXT.installed_models(include_disabled=True) if TEXT else []
    def _release_managed_models():
        if SPEECH and SPEECH.busy():
            raise Busy('Сначала завершите голосовой ввод.')
        if TEXT:
            TEXT.release_gpu()
        _comfy_free()
        if SPEECH:
            SPEECH.stop()
    COMPONENTS.models.release = _release_managed_models
except (ImportError, OSError, ValueError) as _components_error:
    COMPONENTS = None
    print('Components недоступен: ' + str(_components_error), flush=True)


def busy_state():
    """Who works on the GPU right now; `kind: 'voice'` when the only job is Gemma editing a dictation (the page words it differently)."""
    state = GPU.snapshot()
    if state['modules'] == ['text'] and TEXT is not None and set(TEXT.active) == {'voice-polish'}:
        state.update(kind='voice', labels=['Gemma (голосовой ввод)'])
    return state


def stop_generation():
    """Stop whatever generates right now, whichever module started it (the banner's Stop button)."""
    stopped = []
    if AGENTS is not None and AGENTS.stop()['stopped']:
        stopped.append('agent')
    if TEXT is not None:
        for chat_id in list(TEXT.active):
            TEXT.stop_chat(chat_id)
            stopped.append('text')
    if MUSIC is not None and MUSIC.busy():
        row = MUSIC.status()
        if row.get('token'):
            MUSIC.cancel({'id': row['id'], 'token': row['token']})
            stopped.append('music')
    if VIDEO is not None and VIDEO.busy():
        job = VIDEO.call('/active').get('job') or {}
        if job.get('id') and job.get('cancel_token'):
            VIDEO.call('/cancel', {'id': job['id'], 'cancel_token': job['cancel_token']})
            stopped.append('video')
    try:
        queue = _comfy_call(lambda: json_upstream('/queue', timeout=3)) or {}  # ComfyUI down: answer at once, not after 2 s
        ids =[entry[1] for entry in _foreign_queue(queue) if len(entry) > 1]
        if ids:
            with JOBS_LOCK:
                known = {ident: ident in JOB_TOKENS for ident in ids}
            for ident in ids:
                if known[ident]:
                    json_upstream('/api/jobs/' + ident + '/cancel', {}, timeout=15)
                    with JOBS_LOCK:
                        CANCELLING.add(ident)
            if not all(known.values()):  # started by an earlier session: no token, so interrupt and clear the queue
                upstream('/interrupt', b'{}', timeout=5)
                upstream('/queue', json.dumps({'clear': True}).encode('utf-8'), timeout=5)
            stopped.append('image')
    except (URLError, TimeoutError, OSError, ValueError):
        pass
    return dict(stopped=stopped)


def resolve_mix_tracks(data):
    """The browser names music by Music job id; only the bridge turns an id into a file, so a client can never pass a path."""
    if not isinstance(data, dict) or not isinstance(data.get('tracks'), list):
        raise ValueError('Добавьте дорожки музыки')
    if MUSIC is None:
        raise ValueError('Модуль Music недоступен: дорожки взять негде')
    tracks = []
    for track in data['tracks']:
        if not isinstance(track, dict) or 'path' in track or not isinstance(track.get('music_id'), str):
            raise ValueError('Выберите готовый результат Music для каждой дорожки')
        track = dict(track)
        track['path'] = str(MUSIC.file(track.pop('music_id')))
        tracks.append(track)
    return dict(data, tracks=tracks)


def model_available(info):
    """Check ComfyUI's legacy and modern COMBO input schemas."""
    try:
        spec = info['UpscaleModelLoader']['input']['required']['model_name']
        if not isinstance(spec, (list, tuple)) or not spec:
            return False
        if isinstance(spec[0], (list, tuple)):
            options = spec[0]
        elif spec[0] == 'COMBO' and len(spec) > 1 and isinstance(spec[1], dict):
            options = spec[1].get('options', [])
        else:
            return False
        return isinstance(options, (list, tuple)) and UPSCALE_MODEL in options
    except (KeyError, IndexError, TypeError, AttributeError):
        return False


def add_controls_to_graph(graph, quality, upscale):
    if quality != 'low':
        graph['169']['inputs']['steps'] = QUALITY_STEPS[quality]
    if upscale == 1:
        return
    graph['200'] = {'class_type': 'UpscaleModelLoader', 'inputs': {'model_name': UPSCALE_MODEL}}
    graph['201'] = {'class_type': 'ImageUpscaleWithModel', 'inputs': {'upscale_model': ['200', 0], 'image': ['158', 0]}}
    output = ['201', 0]
    if upscale == 4:
        graph['202'] = {'class_type': 'ImageUpscaleWithModel', 'inputs': {'upscale_model': ['200', 0], 'image': ['201', 0]}}
        output = ['202', 0]
    graph['9']['inputs']['images'] = output
    graph['9']['inputs']['filename_prefix'] = 'Qwen_Edit_2511_' + quality.capitalize() + '_x' + str(upscale)


# This is a BLANK-CANVAS workaround for Qwen-Image-Edit-2511, NOT a dedicated text-to-image model.
PROMPT_CANVASES = {'square': (1024, 1024), 'landscape': (1152, 768), 'portrait': (768, 1152)}


@lru_cache(maxsize=3)
def blank_canvas_png(shape):
    """A deterministic neutral gray RGB PNG, generated offline using stdlib only."""
    width, height = PROMPT_CANVASES[shape]
    row = b'\x00' + b'\xf0\xf0\xf0' * width
    def chunk(tag, data):
        body = tag + data
        return struct.pack('!I', len(data)) + body + struct.pack('!I', binascii.crc32(body) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(row * height, level=6)) + chunk(b'IEND', b''))


def prompt_base_image(shape):
    if shape not in PROMPT_CANVASES:
        raise ValueError('Неверный формат холста')
    # Keep the normal upload path and the known-good image-edit graph unchanged.
    return upload_image({'base64': base64.b64encode(blank_canvas_png(shape)).decode('ascii')})



def default_save_dir():
    return str(PATHS.results.resolve())


def normalize_save_dir(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Укажи папку для автосохранения результата')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        path = PATHS.storage_base / path
    path.mkdir(parents=True, exist_ok=True)
    return str(path.resolve())


def load_settings():
    data = {'save_dir': default_save_dir(), 'auto_save': True}
    warning = ''
    if SETTINGS_FILE.exists():
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
            if not isinstance(raw,dict):raise ValueError('Invalid settings object')
            if isinstance(raw.get('save_dir'),str) and raw['save_dir'].strip():data['save_dir']=raw['save_dir'].strip()
        except (OSError,ValueError,TypeError):warning='Не удалось прочитать настройки. Выберите папку сохранения явно.'
    candidate=Path(data['save_dir']).expanduser()
    if not candidate.is_absolute():candidate=PATHS.storage_base/candidate
    if candidate.resolve().is_relative_to(PATHS.root.resolve()):
        try:candidate.mkdir(parents=True,exist_ok=True)
        except OSError:warning='Внутренняя папка сохранения недоступна.'
    if not candidate.is_dir():warning='Папка сохранения недоступна. Выберите другую папку или внутреннюю Results.'
    data.update(save_dir=str(candidate.resolve()),save_dir_available=not warning,save_dir_warning=warning,internal_save_dir=default_save_dir())
    return data


def save_settings(raw):
    current = load_settings()
    target = {'save_dir':current['save_dir'],'auto_save':True}
    if isinstance(raw, dict) and 'save_dir' in raw:
        target['save_dir'] = normalize_save_dir(raw.get('save_dir', ''))
    target['auto_save'] = True
    SETTINGS_FILE.parent.mkdir(parents=True,exist_ok=True)
    temp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        persisted = target.copy()
        try:
            persisted['save_dir'] = str(Path(target['save_dir']).resolve().relative_to(PATHS.storage_base.resolve()))
        except ValueError:
            pass
        temp.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(str(temp), str(SETTINGS_FILE))
    finally:
        if temp.exists():
            temp.unlink()
    return target


def choose_save_dir(initial_dir=None, description='Выберите папку автосохранения результатов Qwen', normalize=True):
    if os.name != 'nt':
        raise ValueError('Нативный выбор папки поддерживается только на Windows')
    shell = shutil.which('pwsh.exe') or shutil.which('powershell.exe') or 'powershell.exe'
    script = r'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $env:QWEN_DIALOG_TITLE
$dialog.ShowNewFolderButton = $true
if ($env:QWEN_INITIAL_DIR -and (Test-Path -LiteralPath $env:QWEN_INITIAL_DIR)) {
    $dialog.SelectedPath = $env:QWEN_INITIAL_DIR
}
# Give the dialog an explicit temporary topmost owner window.
# This keeps the chooser in front of the Edge/Chrome app window.
$owner = New-Object System.Windows.Forms.Form
$owner.ShowInTaskbar = $false
$owner.ShowIcon = $false
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.Width = 1
$owner.Height = 1
$owner.Opacity = 0.01
$owner.TopMost = $true
try {
    $owner.Show()
    $owner.Activate()
    $result = $dialog.ShowDialog($owner)
    if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::WriteLine($dialog.SelectedPath)
    }
}
finally {
    $dialog.Dispose()
    $owner.Close()
    $owner.Dispose()
}
'''.strip()
    env = os.environ.copy()
    env['QWEN_INITIAL_DIR'] = str(initial_dir or '')
    env['QWEN_DIALOG_TITLE'] = description  # passed by environment: the script text stays a constant, nothing is spliced into it
    proc = subprocess.run([shell, '-NoProfile', '-STA', '-Command', script], text=True, capture_output=True, env=env, timeout=180)
    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout or '').strip()
        raise ValueError('Не удалось открыть диалог выбора папки' + (': ' + details if details else ''))
    chosen = (proc.stdout or '').strip()
    if not chosen:
        return None
    return normalize_save_dir(chosen) if normalize else chosen


def choose_agent_workspace(initial_dir=None):
    """The native folder dialog for the agent's project folder; one dialog at a time, like the save-folder chooser."""
    if not DIALOG_LOCK.acquire(blocking=False):
        raise ValueError('Окно выбора папки уже открыто: найдите его на панели задач.')
    try:
        return choose_save_dir(initial_dir, 'Выберите папку проекта, в которой будет работать агент', normalize=False) or ''
    finally:
        DIALOG_LOCK.release()


def open_existing_directory(path_text):
    # Open a selected/result folder without changing settings or creating directories.
    if not isinstance(path_text, str) or not path_text.strip():
        raise ValueError('Укажите папку')
    path = Path(path_text)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('Папка недоступна. Проверьте подключение диска и путь.')
    if os.name != 'nt':
        raise ValueError('Открытие папки поддерживается только на Windows')
    try:
        os.startfile(str(path.resolve()))
    except OSError as exc:
        raise ValueError('Не удалось открыть папку: ' + str(exc)) from exc
    return str(path.resolve())


def open_directory(path_text):
    path = Path(normalize_save_dir(path_text))
    if os.name == 'nt':
        os.startfile(str(path))
    else:
        raise ValueError('Открытие папки поддерживается только на Windows')
    return str(path)


def check_image(item):
    if not isinstance(item, dict) or not isinstance(item.get('base64'), str):
        raise ValueError('Изображение отсутствует или имеет неверный формат')
    s = item['base64']
    if len(s) > ((IMAGE_LIMIT + 2) // 3) * 4 + 10:
        raise ValueError('Файл слишком большой (максимум 100 МиБ)')
    try:
        data = base64.b64decode(s, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('Некорректные данные изображения') from exc
    if len(data) > IMAGE_LIMIT or len(data) == 0:
        raise ValueError('Файл пустой или больше 100 МиБ')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        suffix, mimetype = '.png', 'image/png'
    elif data.startswith(b'\xff\xd8\xff'):
        suffix, mimetype = '.jpg', 'image/jpeg'
    elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        suffix, mimetype = '.webp', 'image/webp'
    else:
        raise ValueError('Поддерживаются только PNG, JPEG и WebP')
    return data, suffix, mimetype


def upload_image(item):
    data, suffix, mime = check_image(item)
    boundary = '----qwenLocal' + uuid.uuid4().hex
    name = 'qwen_local_' + uuid.uuid4().hex + suffix
    parts = [
        ('image', name, mime, data),
        ('type', None, None, b'input'),
        ('overwrite', None, None, b'false')
    ]
    body = bytearray()
    for field, filename, content_type, value in parts:
        body.extend(('--' + boundary + '\r\n').encode('ascii'))
        disposition = 'Content-Disposition: form-data; name="' + field + '"'
        if filename:
            disposition += '; filename="' + filename + '"'
        body.extend((disposition + '\r\n').encode('ascii'))
        if content_type:
            body.extend(('Content-Type: ' + content_type + '\r\n').encode('ascii'))
        body.extend(b'\r\n')
        body.extend(value)
        body.extend(b'\r\n')
    body.extend(('--' + boundary + '--\r\n').encode('ascii'))
    result = json.loads(upstream('/upload/image', bytes(body), 'multipart/form-data; boundary=' + boundary, timeout=90))
    if not result.get('name'):
        raise ValueError('ComfyUI не вернул имя загруженного файла')
    return (result.get('subfolder', '') + '/' if result.get('subfolder') else '') + result['name']


# QWEN_UX_BUNDLE_V1: Local persistent prompt favorites; no external services.
PROMPT_ARCHIVE_FILE = PATHS.prompts / ('favorites.json' if PATHS.modern else 'QwenDesktopApp-v3.prompts.json')
PROMPT_ARCHIVE_LOCK = Lock()


def _read_prompt_archive():
    if not PROMPT_ARCHIVE_FILE.exists():
        return []
    if PROMPT_ARCHIVE_FILE.stat().st_size > 5 * 1024 * 1024:
        raise ValueError('Архив промптов слишком большой для безопасного чтения')
    raw = json.loads(PROMPT_ARCHIVE_FILE.read_text(encoding='utf-8'))
    if not isinstance(raw, dict) or raw.get('schema_version') != 1 or not isinstance(raw.get('entries'), list):
        raise ValueError('Некорректный формат архива промптов')
    entries = raw['entries']
    if len(entries) > 200:
        raise ValueError('В архиве больше 200 записей')
    for item in entries:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or not isinstance(item.get('prompt'), str) or not isinstance(item.get('created_at'), str)):
            raise ValueError('Некорректная запись в архиве промптов')
    return entries


def _write_prompt_archive(entries):
    # Atomic publish: the old file remains intact on interruption before os.replace.
    PROMPT_ARCHIVE_FILE.parent.mkdir(parents=True,exist_ok=True)
    tmp = PROMPT_ARCHIVE_FILE.with_name(PROMPT_ARCHIVE_FILE.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_text(json.dumps({'schema_version': 1, 'entries': entries}, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(str(tmp), str(PROMPT_ARCHIVE_FILE))
    finally:
        if tmp.exists():
            tmp.unlink()


def modify_prompt_archive(data):
    action = data.get('action')
    with PROMPT_ARCHIVE_LOCK:
        entries = _read_prompt_archive()
        if action == 'add':
            prompt = data.get('prompt')
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 5000:
                raise ValueError('Промпт должен содержать от 1 до 5000 символов')
            prompt = prompt.strip()
            if any(record['prompt'] == prompt for record in entries):
                return {'entries': entries, 'added': False, 'duplicate': True}
            if len(entries) >= 200:
                raise ValueError('Архив содержит 200 записей. Удали ненужные, чтобы сохранить новые.')
            record = {'id': uuid.uuid4().hex, 'prompt': prompt,
                      'created_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}
            entries.insert(0, record)
            _write_prompt_archive(entries)
            return {'entries': entries, 'added': True}
        if action == 'delete':
            ident = data.get('id')
            if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{32}', ident):
                raise ValueError('Некорректный ID сохранённого промпта')
            reduced = [entry for entry in entries if entry['id'] != ident]
            if len(reduced) != len(entries):
                _write_prompt_archive(reduced)
            return {'entries': reduced, 'deleted': len(reduced) != len(entries)}
        raise ValueError('Неизвестная операция с архивом промптов')

class Handler(BaseHTTPRequestHandler):
    server_version = 'LocalQwenUI/3.1'

    def log_message(self, fmt, *args):
        # The independent journal must not feed its reads/session IDs back into the existing HTTP log.
        if urlparse(self.path).path in ('/api/event-history', '/api/event-history/session'):
            return
        if self.command == 'GET' and self.path.startswith('/api/music/') and len(args) > 1 and str(args[1]) in ('200', '206'):
            return  # Music polling belongs in job state, not repetitive access logs.
        print('%s %s' % (self.log_date_time_string(), fmt % args), flush=True)

    def reply(self, code, raw, kind='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob: data:; script-src 'self'; style-src 'self'; connect-src 'self' ws://127.0.0.1:%d; base-uri 'none'; form-action 'self'" % PORT)
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(raw)

    def response_json(self, code, obj):
        self.reply(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'))

    def video_request(self, route, data=None, query=''):
        if VIDEO is None:
            return self.response_json(503, {'available': False, 'error': VIDEO_WARNING})
        if route == 'availability':
            return self.response_json(200, VIDEO.availability())
        try:
            if route == 'generate':
                policy = ModelPolicy(PATHS.root)
                if not policy.enabled('video', data.get('engine', 'freevideo-h3-int8')):
                    raise ValueError('Модель отключена в разделе «Компоненты».')
                uses_voice = (data.get('engine') == 'wan22-s2v-14b' and data.get('speech_mode', 'clone') == 'clone') or any(person.get('speech_text') for person in data.get('characters', []) if isinstance(person, dict))
                if uses_voice and 'video.model.qwen3-tts' in policy.disabled():
                    raise ValueError('Qwen3-TTS отключена в разделе «Компоненты».')
            VIDEO.start()
            if route == 'generate':
                VIDEO.call('/validate', data)
                if VIDEO.busy():
                    return self.response_json(409, {'error': 'GPU уже занят задачей Video.'})
                from video.registry import ENGINES as _video_engines
                # The submission lock spans this claim and the job start; it refuses while Image/Music/Text work and frees what they hold.
                GPU.claim('video', _video_engines[data.get('engine', 'freevideo-h3-int8')].get('minimum_free_vram_mb', 12000))
            if route == 'result':
                answer = VIDEO.call('/file' + query)
                path = Path(answer.get('path') or '')
                if path.suffix.lower() != '.mp4' or not path.is_file():
                    return self.response_json(404, {'error': 'Видео сейчас недоступно'})
                size = path.stat().st_size
                start, end, partial = 0, size - 1, False
                requested = self.headers.get('Range')
                if requested:
                    match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
                    if not match or not any(match.groups()):
                        return self.response_json(416, {'error': 'Invalid byte range'})
                    first, last = match.groups()
                    start = int(first) if first else max(0, size - int(last))
                    end = min(size - 1, int(last)) if first and last else size - 1
                    if not 0 <= start <= end < size:
                        return self.response_json(416, {'error': 'Invalid byte range'})
                    partial = True
                self.send_response(206 if partial else 200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Length', str(end - start + 1))
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Disposition', 'inline; filename="' + path.name.replace('"', '') + '"')
                if partial:
                    self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
                self.end_headers()
                try:
                    with path.open('rb') as stream:
                        stream.seek(start)
                        remaining = end - start + 1
                        while remaining:
                            raw = stream.read(min(65536, remaining))
                            if not raw:
                                break
                            self.wfile.write(raw)
                            remaining -= len(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if route not in ('generate', 'cancel', 'status', 'history', 'settings', 'logs', 'prompts', 'active',
                             'export', 'export-cancel', 'export-status', 'export-preview', 'mix', 'mix-preview', 'delete'):
                return self.response_json(404, {'error': 'Unknown Video endpoint'})
            if route in ('mix', 'mix-preview'):
                try:
                    data = resolve_mix_tracks(data)
                except ValueError as error:
                    return self.response_json(400, {'error': str(error)})
            return self.response_json(200, VIDEO.call('/' + route + query, data))
        except HTTPError as error:
            try:
                answer = json.loads(error.read(4000))
            except ValueError:
                answer = {'error': 'Ошибка Video-backend'}
            return self.response_json(error.code, answer)
        except Busy as error:
            return self.response_json(409, {'error': str(error)})
        except GpuUnavailable as error:
            return self.response_json(503, {'error': str(error)})
        except (OSError, ValueError, TimeoutError) as error:
            return self.response_json(503, {'error': str(error)[:1000]})

    def safe_origin(self):
        origin = self.headers.get('Origin')
        host = self.headers.get('Host', '')
        if host not in ('127.0.0.1:%d' % PORT, 'localhost:%d' % PORT):
            self.response_json(403, {'error': 'Host not allowed'})
            return False
        if origin and origin not in ('http://127.0.0.1:%d' % PORT, 'http://localhost:%d' % PORT):
            self.response_json(403, {'error': 'Origin not allowed'})
            return False
        return True

    def proxy_events(self):
        """Relay websocket bytes to the loopback backend after same-origin checks.

        Forward no cookies, credentials, extensions or arbitrary upstream paths.
        ComfyUI receives no foreign Origin; the bridge enforces its own origin.
        """
        key = self.headers.get('Sec-WebSocket-Key', '')
        if (self.headers.get('Upgrade', '').lower() != 'websocket'
                or self.headers.get('Sec-WebSocket-Version') != '13'):
            return self.response_json(400, {'error':'Expected websocket upgrade'})
        try:
            if len(base64.b64decode(key, validate=True)) != 16:
                raise ValueError('Invalid websocket key')
        except (ValueError, binascii.Error):
            return self.response_json(400, {'error':'Invalid websocket key'})
        # The event stream belongs to this bridge's submitted jobs.
        client_id = parse_qs(urlparse(self.path).query).get('clientId', [''])[0]
        if client_id != CLIENT_ID:
            return self.response_json(403, {'error':'Unknown progress client'})
        with socket.create_connection(('127.0.0.1', COMFY_PORT), timeout=10) as backend:
            request = ('GET /ws?clientId=' + CLIENT_ID + ' HTTP/1.1\r\n'
                       'Host: 127.0.0.1:' + str(COMFY_PORT) + '\r\n'
                       'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                       'Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: ' + key + '\r\n\r\n')
            backend.sendall(request.encode('ascii'))
            headers = bytearray()
            while b'\r\n\r\n' not in headers and len(headers) < 16384:
                part = backend.recv(4096)
                if not part: break
                headers.extend(part)
            if not headers.startswith(b'HTTP/1.1 101 ') or b'\r\n\r\n' not in headers:
                return self.response_json(502, {'error':'ComfyUI websocket unavailable'})
            self.close_connection = True
            self.connection.sendall(headers)
            backend.settimeout(30)
            self.connection.settimeout(30)
            try:
                while True:
                    ready, _, _ = select.select([self.connection, backend], [], [], 1)
                    for source in ready:
                        raw = source.recv(65536)
                        if not raw: return
                        (backend if source is self.connection else self.connection).sendall(raw)
            except (OSError, TimeoutError):
                return

    def queue_info(self, ident):
        q = json_upstream('/queue', timeout=15)
        pending_ids = []
        running_ids = []
        for entry in q.get('queue_pending', []):
            if isinstance(entry, (list, tuple)) and len(entry) > 1:
                pending_ids.append(entry[1])
        for entry in q.get('queue_running', []):
            if isinstance(entry, (list, tuple)) and len(entry) > 1:
                running_ids.append(entry[1])
        if ident in running_ids:
            return {'location': 'running', 'running_total': len(running_ids), 'pending_total': len(pending_ids)}
        if ident in pending_ids:
            return {'location': 'pending', 'queue_position': pending_ids.index(ident) + 1, 'queue_total': len(pending_ids)}
        return {'location': 'absent', 'running_total': len(running_ids), 'queue_total': len(pending_ids)}

    def progress_info(self, ident):
        try:
            info = json_upstream('/progress', timeout=5)
        except Exception:
            return {}
        # ComfyUI may expose global progress. Never attribute another job's
        # progress to this job; omit percent unless the ID is explicit.
        if not isinstance(info, dict) or info.get('prompt_id') != ident:
            return {}
        payload = {}
        value = info.get('value')
        maximum = info.get('max')
        if isinstance(value, (int, float)) and isinstance(maximum, (int, float)) and maximum > 0:
            payload['progress_pct'] = round(float(value) * 100.0 / float(maximum), 1)
        node = info.get('node') or info.get('current_node') or info.get('status')
        if isinstance(node, str) and node:
            payload['node'] = node
        return payload

    def maybe_save_outputs(self, ident, outputs):
        with JOBS_LOCK:
            guard = SAVE_LOCKS.setdefault(ident, Lock())
        with guard:
            return self._save_outputs_locked(ident, outputs)

    def _save_outputs_locked(self, ident, outputs):
        with JOBS_LOCK:
            meta = JOB_META.get(ident) or {}
            existing = meta.get('saved_files')
        if not meta:
            raise ValueError('Задача удалена или недоступна для сохранения')
        if existing is not None:
            return existing, str(meta.get('save_dir') or load_settings()['save_dir'])
        settings = load_settings()
        save_dir = Path(meta.get('save_dir') or settings['save_dir'])
        save_dir.mkdir(parents=True, exist_ok=True)
        submitted_at = meta.get('submitted_at', time.time())
        timestamp = time.strftime('%Y%m%d_%H%M%S', time.localtime(submitted_at))
        quality = str(meta.get('quality', 'low'))
        upscale = int(meta.get('upscale', 1))
        mode = str(meta.get('mode', 'single'))
        meta['comfy_files'] = [{'filename': item['filename'], 'subfolder': item.get('subfolder', ''), 'type': item.get('type', 'output')} for item in outputs]
        saved = []
        for index, item in enumerate(outputs, start=1):
            filename = str(item.get('filename', 'result.png'))
            ext = '.png' if filename.lower().endswith('.png') else '.jpg'
            view_url = '/view?' + urlencode({'filename': filename, 'subfolder': item.get('subfolder', ''), 'type': 'output'})
            raw = upstream(view_url, timeout=120)
            base_prefix = str(meta.get('filename_base') or build_filename_base('', mode, submitted_at))
            image_index = '' if index == 1 else f' {index}'
            base_name = f'{base_prefix}{image_index}{ext}'
            path = write_unique_output(save_dir, base_prefix + image_index, ext, raw)
            saved.append(str(path))
        with JOBS_LOCK:
            if ident in JOB_META:
                JOB_META[ident]['saved_files'] = saved
        try:
            HISTORY.record(ident, 'done', meta, saved, time.time() - submitted_at)
        except (OSError, ValueError, TypeError):
            print('[history] Could not record saved result; output files are preserved.', flush=True)
        return saved, str(save_dir)

    def autosave_watcher(self, ident):
        # Continues monitoring if the browser tab is closed, while the local bridge stays alive.
        # Never submits GPU tasks. The original ComfyUI SaveImage output remains the fallback.
        deadline = time.monotonic() + 2 * 60 * 60
        failing = False
        while time.monotonic() < deadline:
            time.sleep(3)
            with JOBS_LOCK:
                meta = JOB_META.get(ident)
                if not meta or meta.get('saved_files') is not None:
                    return
            if HISTORY.get(ident):
                return
            try:
                history = self.fetch_history(ident)
                outputs = self.job_outputs(ident, history)
                if outputs:
                    self.maybe_save_outputs(ident, outputs)
                    return
                with JOBS_LOCK:
                    cancelled = ident in CANCELLING
                if cancelled and not self.job_is_queued_or_running(ident):
                    HISTORY.record(ident, 'cancelled', meta, elapsed_sec=time.time() - meta.get('submitted_at', time.time()))
                    with JOBS_LOCK:
                        CANCELLING.discard(ident)
                        JOB_TOKENS.pop(ident, None)
                    return
                failing = False
                if not history:
                    continue
                for detail in meta.get('_technical', []):
                    generation_finish(detail, state='error')
                generation_finish(meta.get('generation'), state='error')
                # ComfyUI writes the history entry when the run ends, successful or not: an error has nothing to save either
                # (it used to keep this thread polling every 3 s for two hours).
                return
            except (OSError, ValueError, KeyError, HTTPError, URLError, TimeoutError) as exc:
                if not failing:  # once per outage, not every 3 s
                    print('[autosave] attempt pending or failed: ' + str(type(exc).__name__), flush=True)
                failing = True
        with JOBS_LOCK:
            expired = JOB_META.get(ident) or {}
        for detail in expired.get('_technical', []):
            generation_finish(detail, state='interrupted')
        generation_finish(expired.get('generation'), state='interrupted')
        print('[autosave] job watch timed out', flush=True)

    def list_save_dirs(self, raw_path, initial=False):
        """Browse directories only. No images or file contents are exposed."""
        if not isinstance(raw_path, str) or len(raw_path) > 1024:
            raise ValueError('Слишком длинный путь')
        selected = raw_path.strip() or load_settings()['save_dir']
        path = Path(selected).expanduser()
        if not path.is_absolute():
            raise ValueError('Путь должен быть абсолютным')
        try:
            if initial:
                while not path.exists() and path.parent != path:
                    path = path.parent
                if not path.exists():
                    path = PATHS.root
            path = path.resolve(strict=True)
            if not path.is_dir():
                raise ValueError('Указанный путь не является папкой')
            entries = []
            with os.scandir(path) as listing:
                for entry in listing:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            entries.append({'name': entry.name, 'path': str(path / entry.name)})
                    except OSError:
                        continue
            entries.sort(key=lambda item: item['name'].casefold())
            drives = []
            if os.name == 'nt':
                for char in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
                    drive = char + ':\\'
                    if os.path.isdir(drive):
                        drives.append(drive)
            parent = str(path.parent) if path.parent != path else None
            return self.response_json(200, {
                'path': str(path), 'parent': parent, 'drives': drives,
                'directories': entries[:500], 'truncated': len(entries) > 500
            })
        except OSError as exc:
            raise ValueError('Не удалось открыть папку: ' + str(exc)) from exc

    # QWEN_LOGS_PANEL_V1: read-only, bounded local diagnostics.
    def diagnostic_logs(self, scope):
        import re
        if scope not in ('all', 'errors', 'ui', 'comfy', 'launcher'):
            raise ValueError('Неизвестная категория логов')
        log_dir = PATHS.logs
        if not log_dir.is_dir():
            return self.response_json(200, {
                'scope': scope, 'text': 'Папка logs ещё не создана. Запусти приложение хотя бы один раз.',
                'files': 0, 'truncated': False
            })

        patterns = (
            ('ui', 'UI stderr', 'ui-v3-*.stderr.txt', 3),
            ('ui', 'UI stdout', 'ui-v3-*.stdout.txt', 3),
            ('comfy', 'ComfyUI stderr', 'comfy-v3-*.stderr.txt', 3),
            ('comfy', 'ComfyUI stdout', 'comfy-v3-*.stdout.txt', 3),
            ('launcher', 'Запуск', 'desktop-v3-*.txt', 3),
        )
        sources = []
        base = log_dir.resolve()
        for category, label, pattern, count in patterns:
            if scope not in ('all', 'errors', category):
                continue
            matches = []
            for candidate in log_dir.glob(pattern):
                try:
                    if candidate.is_file() and candidate.resolve().parent == base:
                        matches.append((candidate.stat().st_mtime, candidate))
                except OSError:
                    continue
            matches.sort(key=lambda pair: pair[0], reverse=True)
            for _, candidate in matches[:count]:
                sources.append((category, label, candidate))

        if not sources:
            return self.response_json(200, {
                'scope': scope, 'text': 'Подходящих журналов пока нет.',
                'files': 0, 'truncated': False
            })
        # Do not read unlimited log files or permit filenames supplied by callers.
        remaining = 125000
        pieces = []
        abbreviated = False
        for category, label, file_path in sources:
            if remaining < 150:
                abbreviated = True
                break
            try:
                stat = file_path.stat()
                with file_path.open('rb') as stream:
                    if stat.st_size > 14000:
                        stream.seek(-14000, 2)
                    raw = stream.read(14000)
                content = raw.decode('utf-8', 'replace')
                if content.startswith('\ufffd') and stat.st_size > 14000:
                    content = content.split('\n', 1)[-1]
                lines = content.splitlines()[-140:]
                if scope == 'errors':
                    error_pattern = re.compile(
                        r'(?i)traceback|exception|\berror\b|\bfail(?:ed|ure)?\b|'
                        r'out of memory|\boom\b|\bmissing\b|\bnot found\b|'
                        r'не удалось|ошибка|отказано|не найден|connectionreset'
                    )
                    matches = {index for index, line in enumerate(lines) if error_pattern.search(line)}
                    keep = set()
                    for index in matches:
                        keep.update(range(max(0, index - 3), min(len(lines), index + 5)))
                    lines = [line for index, line in enumerate(lines) if index in keep]
                    if not lines:
                        continue
                content = '\n'.join(lines) if lines else '(пустой журнал)'
                # Mask typical Windows profile paths and common inline credentials.
                profile = os.environ.get('USERPROFILE', '').rstrip('\\/')
                if profile:
                    content = re.sub(re.escape(profile), r'C:\\Users\\<user>', content, flags=re.I)
                content = re.sub(
                    r'(?i)\b(authorization|api[_-]?key|access[_-]?token|password|secret)\b\s*[:=]\s*[^\s,;]+',
                    lambda match: match.group(1) + '=[REDACTED]', content
                )
                content = re.sub(
                    r'''(?i)(["']?(?:prompt|negative_prompt|base64|cancel_token)["']?\s*[:=]\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\r\n]+)''',
                    lambda match: match.group(1) + '[REDACTED]', content)
                content = re.sub(r'(?i)data:image/[^;]+;base64,[A-Za-z0-9+/=]+', '[IMAGE REDACTED]', content)
                stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))
                section = '[{}] {} | {} | {} bytes\n{}\n\n'.format(
                    label, file_path.name, stamp, stat.st_size, content
                )
                if len(section) > remaining:
                    section = section[:remaining] + '\n[Ограничение объёма вывода]\n'
                    abbreviated = True
                pieces.append(section)
                remaining -= len(section)
                if stat.st_size > len(raw):
                    abbreviated = True
            except OSError:
                continue
        result = ''.join(pieces).strip()
        if not result:
            result = 'В последних фрагментах доступных журналов явных ошибок не найдено.'
        if abbreviated:
            result += '\n\n[Показаны только последние фрагменты журналов. Нажми «Обновить» для актуальных данных.]'
        return self.response_json(200, {
            'scope': scope, 'text': result, 'files': len(pieces), 'truncated': abbreviated
        })

    def do_GET(self):
        if not self.safe_origin():
            return
        u = urlparse(self.path)
        try:
            if u.path.startswith('/api/music/'):
                from music.routes import handle
                return handle(self, MUSIC, GPU, u.path.removeprefix('/api/music/'), None, u.query)
            if u.path == '/api/text/image' and TEXT_STORE is not None:
                from text.routes import image as text_image
                return text_image(self, TEXT_STORE, u.query)
            if u.path.startswith('/api/text/'):
                from text.routes import handle as text_handle
                return text_handle(self, TEXT, TEXT_STORE, u.path.removeprefix('/api/text/'), None, u.query)
            if u.path.startswith('/api/agents/'):
                from agents.routes import handle as agents_handle
                return agents_handle(self, AGENTS, u.path.removeprefix('/api/agents/'), None, choose_agent_workspace)
            if u.path.startswith('/api/video/'):
                return self.video_request(u.path.removeprefix('/api/video/'), query=('?' + u.query) if u.query else '')
            if u.path == '/api/hardware':
                from hardware import report as hardware_report
                return self.response_json(200, hardware_report(TEXT.installed_models() if TEXT is not None else (), refresh=parse_qs(u.query).get('refresh') == ['1'], root=PATHS.root))
            if u.path == '/api/busy':
                return self.response_json(200, busy_state())
            if u.path == '/api/launch-ready':
                with JOBS_LOCK:
                    state = LAUNCHES.get(parse_qs(u.query).get('launch', [''])[0])
                return self.response_json(200, {'ready': state is not None, 'ok': bool(state and state['ok'])})
            if u.path.startswith('/api/speech/'):
                from speech.routes import handle as speech_handle
                return speech_handle(self, SPEECH, u.path.removeprefix('/api/speech/'), None, u.query)
            if u.path == '/api/components/recovery':
                from provision.routes import handle as components_handle
                return components_handle(self, COMPONENTS, 'recovery', None)
            if u.path == '/api/components':
                from provision.routes import handle as components_handle
                return components_handle(self, COMPONENTS, '', None)
            if u.path == '/api/events':
                return self.proxy_events()
            if u.path == '/health':
                return self.response_json(200, {'app': 'QwenDesktopApp-v3', 'ready': True})
            if u.path == '/api/preflight':
                return self.preflight()
            if u.path == '/api/status':
                ident = parse_qs(u.query).get('id', [''])[0]
                return self.status(ident)
            if u.path == '/api/result':
                qs = parse_qs(u.query)
                return self.result(qs.get('id', [''])[0], qs.get('index', ['0'])[0])
            if u.path == '/api/settings':
                return self.response_json(200, load_settings())
            if u.path == '/api/event-history':
                import sqlite3
                try:
                    return self.response_json(200, event_history().page(u.query))
                except ValueError as error:
                    return self.response_json(400, {'error': str(error)})
                except (sqlite3.Error, OSError):
                    return self.response_json(503, {'error': 'История событий недоступна. Проверьте доступ к папке UserData и свободное место.'})
            if u.path == '/api/history':
                return self.response_json(200, {'entries': HISTORY.completed(), 'warning': HISTORY.warning})
            if u.path == '/api/favorites':
                with PROMPT_ARCHIVE_LOCK:
                    return self.response_json(200, {'entries': _read_prompt_archive()})
            if u.path == '/api/logs':
                return self.diagnostic_logs(parse_qs(u.query).get('scope', ['errors'])[0])
            if u.path == '/api/list-save-dirs':
                return self.list_save_dirs(parse_qs(u.query).get('path', [''])[0], parse_qs(u.query).get('initial') == ['1'])
            if u.path in ('/visualforge-icon.png', '/favicon.ico'):
                kind = 'png' if u.path.endswith('.png') else 'ico'
                raw, mime = optional_icon(FRONTEND / ('visualforge-icon.' + kind), kind)
                return self.reply(200, raw, mime)
            if u.path in STATIC:
                filename, mime = STATIC[u.path]
                return self.reply(200, ((FRONTEND / filename.removeprefix('static/'))).read_bytes(), mime)
            self.response_json(404, {'error': 'Not found'})
        except (ValueError, KeyError, HTTPError, URLError, TimeoutError) as exc:
            self.response_json(502, {'error': str(exc)[:600]})

    def do_POST(self):
        route = urlparse(self.path).path
        if route == '/api/text/send':
            return self._text_send()  # streams for minutes: only the admission below holds the GPU lock
        if route == '/api/agents/send':
            return self._agent_send()  # runs for minutes; each model request of the agent is admitted by its gateway
        if route == '/api/speech/transcribe':
            return self._speech_transcribe()  # raw audio, not JSON; takes seconds and must not hold the GPU lock
        if route not in GPU_START_ROUTES:
            return self._do_POST()  # Stop, settings, the folder dialog (open for minutes) never wait behind a start
        with GPU_SUBMISSION_LOCK:
            return self._do_POST()

    def _speech_transcribe(self):
        if not self.safe_origin():
            return
        from speech.routes import transcribe
        return transcribe(self, SPEECH, urlparse(self.path).query)

    def _text_send(self):
        if not self.safe_origin():
            return
        try:
            size = int(self.headers.get('Content-Length', '0') or '0')
            if not 0 < size <= 40 * 1024 * 1024:  # up to four screenshots of about 6 MiB each, as base64
                return self.response_json(413, {'error': 'Слишком большой запрос'})
            body = self.rfile.read(size)
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                return self.response_json(415, {'error': 'Expected JSON'})
            data = json.loads(body)
            from text.routes import send

            def admit(chat_id):
                with GPU_SUBMISSION_LOCK:
                    GPU.claim('text')
                    return TEXT.begin(chat_id)

            send(self, TEXT, TEXT_STORE, data, admit)
        except Busy as error:
            return self.response_json(409, {'error': str(error)})
        except GpuUnavailable as error:
            return self.response_json(503, {'error': str(error)})
        except (ValueError, KeyError, TypeError) as error:
            return self.response_json(400, {'error': str(error)[:800]})

    def _agent_send(self):
        if not self.safe_origin():
            return
        try:
            size = int(self.headers.get('Content-Length', '0') or '0')
            if not 0 < size <= 512 * 1024:  # a task of up to 100 000 characters as JSON-escaped UTF-8
                return self.response_json(413, {'error': 'Слишком большой запрос'})
            body = self.rfile.read(size)
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                return self.response_json(415, {'error': 'Expected JSON'})
            from agents.routes import send as agent_send
            agent_send(self, AGENTS, json.loads(body))
        except Busy as error:
            return self.response_json(409, {'error': str(error)})
        except (ValueError, KeyError, TypeError) as error:
            return self.response_json(400, {'error': str(error)[:800]})

    def _do_POST(self):
        if not self.safe_origin():
            return
        try:
            route = urlparse(self.path).path
            is_video = route in ('/api/video/generate', '/api/video/cancel', '/api/video/settings', '/api/video/prompts',
                                 '/api/video/export', '/api/video/export-cancel', '/api/video/mix', '/api/video/mix-preview', '/api/video/delete')
            is_music = route in ('/api/music/generate', '/api/music/cancel', '/api/music/settings', '/api/music/delete')
            is_text = route in ('/api/text/chats', '/api/text/chat-rename', '/api/text/chat-update', '/api/text/chat-delete', '/api/text/settings',
                                '/api/text/stop', '/api/text/unload')
            is_components = route in ('/api/components/install', '/api/components/cancel', '/api/components/model-toggle', '/api/components/model-remove-plan', '/api/components/model-remove', '/api/components/repair', '/api/components/recovery-answer', '/api/components/recovery-file')
            is_speech = route in ('/api/speech/settings', '/api/speech/unload', '/api/speech/prepare')
            is_agents = route in ('/api/agents/chats', '/api/agents/chat-update', '/api/agents/permission', '/api/agents/stop', '/api/agents/settings',
                                  '/api/agents/choose-workspace')
            if not is_video and not is_music and not is_text and not is_components and not is_speech and not is_agents and route not in ('/api/event-history/session', '/api/history/delete', '/api/launch-ready', '/api/stop-generation', '/api/generate', '/api/cancel', '/api/settings', '/api/choose-save-dir', '/api/open-save-dir', '/api/open-output-dir', '/api/favorites'):
                return self.response_json(404, {'error': 'Not found'})
            size = int(self.headers.get('Content-Length', '0') or '0')
            max_size = 4096 if route in ('/api/event-history/session', '/api/history/delete', '/api/launch-ready', '/api/cancel', '/api/stop-generation', '/api/choose-save-dir', '/api/open-save-dir', '/api/open-output-dir') else (32768 if route == '/api/favorites' else (8192 if route == '/api/settings' else LIMIT))
            if is_video:
                max_size = {'generate': 100 * 1024 * 1024, 'cancel': 4096, 'settings': 8192, 'prompts': 32768,
                            'export': 4096, 'export-cancel': 4096, 'mix': 16384, 'mix-preview': 16384, 'delete': 4096}[route.rsplit('/', 1)[-1]]
            if is_music:
                max_size = 40000
            if is_text:
                max_size = 65536
            if is_components:
                max_size = 8192
            if is_speech:
                max_size = 4096
            if is_agents:
                max_size = 16384  # a chat's system instruction (up to 8000 characters) plus a folder path
            if size < 0 or size > max_size:
                return self.response_json(413, {'error': 'Слишком большой запрос'})
            body = self.rfile.read(size) if size > 0 else b'{}'
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                return self.response_json(415, {'error': 'Expected JSON'})
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError('Ожидается JSON-объект')
            if route == '/api/event-history/session':
                return self.microphone_event(data)
            if route == '/api/history/delete':
                try:
                    return self.delete_image(data)
                except OSError as error:
                    return self.response_json(500, {'error': 'Не удалось удалить все файлы генерации. Запись сохранена для повторной попытки: ' + str(error)[:500]})
            if is_text:
                from text.routes import handle as text_handle
                return text_handle(self, TEXT, TEXT_STORE, route.removeprefix('/api/text/'), data, '')
            if is_speech:
                from speech.routes import handle as speech_handle
                return speech_handle(self, SPEECH, route.removeprefix('/api/speech/'), data)
            if is_agents:
                from agents.routes import handle as agents_handle
                return agents_handle(self, AGENTS, route.removeprefix('/api/agents/'), data, choose_agent_workspace)
            if is_components:
                from provision.routes import handle as components_handle
                return components_handle(self, COMPONENTS, route.removeprefix('/api/components/'), data)
            if route == '/api/stop-generation':
                return self.response_json(200, stop_generation())
            if route == '/api/launch-ready':
                launch = data.get('launch')
                if not isinstance(launch, str) or not LAUNCH_PATTERN.fullmatch(launch):
                    raise ValueError('Некорректный номер запуска')
                with JOBS_LOCK:
                    LAUNCHES[launch] = {'ok': data.get('ok') is True, 'at': time.time()}
                    while len(LAUNCHES) > 16:
                        LAUNCHES.pop(next(iter(LAUNCHES)))
                return self.response_json(200, {'ready': True})
            if is_music:
                from music.routes import handle
                return handle(self, MUSIC, GPU, route.removeprefix('/api/music/'), data, '')
            if is_video:
                return self.video_request(route.removeprefix('/api/video/'), data)
            if route == '/api/cancel':
                return self.cancel(data)
            if route == '/api/settings':
                return self.response_json(200, save_settings(data))
            if route == '/api/favorites':
                return self.response_json(200, modify_prompt_archive(data))
            if route == '/api/choose-save-dir':
                if not DIALOG_LOCK.acquire(blocking=False):
                    return self.response_json(409, {'error': 'Окно выбора папки уже открыто: найдите его на панели задач.'})
                try:
                    chosen = choose_save_dir(load_settings()['save_dir'])
                finally:
                    DIALOG_LOCK.release()
                if not chosen:
                    return self.response_json(200, {'cancelled': True, **load_settings()})
                return self.response_json(200, {'cancelled': False, **save_settings({'save_dir': chosen})})
            if route == '/api/open-output-dir':
                return self.response_json(200, {'opened': True, 'opened_path': open_existing_directory(data.get('path'))})
            if route == '/api/open-save-dir':
                settings = load_settings()
                opened = open_directory(settings['save_dir'])
                return self.response_json(200, {'opened': True, **settings, 'opened_path': opened})

            engine = get_engine(data.get('engine'))
            selected_mode, quality, upscale, prompt, fixed_seed, canvas = validate_request(engine, data)
            operation = data.get('operation', 'generate')
            mode = resolve_workflow_mode(engine, selected_mode, operation)
            expand = resolve_expansion(engine, data)
            original_prompt = data.get('original_prompt', prompt) if operation == 'variation' else prompt
            if not isinstance(original_prompt, str) or len(original_prompt) > 5000:
                raise ValueError('Invalid original prompt')
            obj = json_upstream('/object_info', timeout=20)
            readiness = engine_readiness(engine, obj)
            apply_profiles(PATHS.root, 'image', [readiness], 'ready')
            if not readiness['ready']:
                raise ValueError('; '.join(readiness['reasons']))
            if upscale > 1 and (not upscale_available(obj) or 'image.model.upscale' in ModelPolicy(PATHS.root).disabled()):
                raise ValueError('Для AI Upscale нужен RealESRGAN_x2plus.pth и ImageUpscaleWithModel')
            if not load_settings().get('save_dir_available',True):
                raise ValueError(load_settings()['save_dir_warning'])
            # Refuses while Video/Music/Text work; unloads an idle chat model first; waits for the engine's own memory estimate.
            GPU.claim('image', engine.claim_vram_mb)
            # Validate ALL image payloads before uploading either input.
            if mode != 'prompt':
                check_image(data.get('image1'))
                if mode == 'reference':
                    check_image(data.get('image2'))
            input1 = input2 = None
            if mode == 'prompt' and not engine.native_prompt:
                input1 = prompt_base_image(canvas)
            elif mode != 'prompt':
                input1 = upload_image(data['image1'])
                if mode == 'reference':
                    input2 = upload_image(data['image2'])
            seed = fixed_seed if fixed_seed is not None else int.from_bytes(os.urandom(6), 'big')
            graph, engine_meta = build_workflow(engine, mode, prompt, input1, input2,
                                                quality, upscale, seed, canvas, expand)
            engine_meta.update(selected_mode=selected_mode, operation=operation)
            answer = json_upstream('/prompt', {'prompt': graph, 'client_id': CLIENT_ID}, timeout=30)
            if 'prompt_id' not in answer:
                raise ValueError('ComfyUI отклонил граф: ' + str(answer)[:700])
            ident = answer['prompt_id']
            token = secrets.token_urlsafe(32)
            submitted_at = time.time()
            generation_info = generation_begin('Image')
            technical = []
            if expand and engine.expand_nodes:
                technical.append(generation_begin('ImagePromptExpansion', parent_id=generation_info['id']))
            for _ in range({1: 0, 2: 1, 4: 2}[upscale]):
                technical.append(generation_begin('ImageUpscale', parent_id=generation_info['id']))
            filename_base = build_filename_base(original_prompt, selected_mode, submitted_at, operation=operation, upscale=upscale)
            with JOBS_LOCK:
                JOB_TOKENS[ident] = token
                JOB_META[ident] = {
                    **engine_meta,
                    'generation': generation_info,
                    '_technical': technical,
                    'quality': quality,
                    'steps': engine.quality_steps[quality],
                    'upscale': upscale,
                    'seed': seed,
                    'mode': mode,
                    'submitted_at': submitted_at,
                    'saved_files': None,
                    'filename_base': filename_base,
                    'original_prompt': original_prompt,
                    'save_dir': load_settings()['save_dir'],
                    'input_files': [value for value in (input1, input2) if value],
                }
                SAVE_LOCKS[ident] = Lock()
            print('[generation] ' + json.dumps({k: engine_meta[k] for k in ('engine', 'workflow_version', 'seed', 'sampler_parameters', 'workflow_sha256')}, ensure_ascii=True), flush=True)
            Thread(target=self.autosave_watcher, args=(ident,), daemon=True).start()
            return self.response_json(200, {
                **engine_meta,
                'prompt_id': ident,
                'generation': generation_info,
                'cancel_token': token,
                'quality': quality,
                'steps': engine.quality_steps[quality],
                'upscale': upscale,
                'seed': seed,
                'filename_base': filename_base
            })
        except Busy as exc:
            return self.response_json(409, {'error': str(exc)})
        except GpuUnavailable as exc:
            return self.response_json(503, {'error': str(exc)})
        except (ValueError, TypeError, KeyError) as exc:
            return self.response_json(400, {'error': str(exc)[:600]})
        except HTTPError as exc:
            details = exc.read(3000).decode('utf-8', 'replace')
            return self.response_json(502, {'error': ('ComfyUI HTTP ' + str(exc.code) + ': ' + details)[:1500]})
        except (URLError, TimeoutError) as exc:
            return self.response_json(502, {'error': 'ComfyUI недоступен: ' + str(exc)[:500]})

    def cancel(self, data):
        ident = data.get('prompt_id')
        token = data.get('cancel_token')
        try:
            uuid.UUID(ident)
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Некорректный prompt_id')
        if not isinstance(token, str) or not token:
            return self.response_json(403, {'error': 'Отсутствует право на отмену этой задачи'})
        with JOBS_LOCK:
            expected = JOB_TOKENS.get(ident)
        if not expected or not secrets.compare_digest(expected, token):
            return self.response_json(403, {'error': 'Эта задача не была запущена текущей сессией приложения'})
        answer = json_upstream('/api/jobs/' + ident + '/cancel', {}, timeout=15)
        confirmed = answer.get('cancelled') is True
        if confirmed:
            with JOBS_LOCK:
                CANCELLING.add(ident)
        return self.response_json(200, {'cancelled': confirmed})

    def job_is_queued_or_running(self, ident):
        q = json_upstream('/queue', timeout=15)
        for key in ('queue_running', 'queue_pending'):
            for entry in q.get(key, []):
                if len(entry) > 1 and entry[1] == ident:
                    return True
        return False

    def fetch_history(self, ident):
        try:
            uuid.UUID(ident)
        except (ValueError, TypeError):
            raise ValueError('Некорректный prompt_id')
        return json_upstream('/history/' + ident).get(ident)

    def job_outputs(self, ident, history):
        with JOBS_LOCK:
            metadata = JOB_META.get(ident) or {}
        outputs = collect_outputs(history, metadata)
        expanded = collect_expansion(history, metadata)
        if outputs and expanded:
            # Kept with the job so the receipt records the caption the image model actually received.
            with JOBS_LOCK:
                if ident in JOB_META:
                    JOB_META[ident]['expanded_prompt'] = expanded[:6000]
        return outputs

    def preflight(self):
        obj = json_upstream('/object_info', timeout=20)
        engines = all_readiness(obj)
        apply_profiles(PATHS.root, 'image', list(engines.values()), 'ready')
        legacy = engines[DEFAULT_ENGINE]
        settings = load_settings()
        return self.response_json(200, {
            'backend_ready': True,
            'default_engine': DEFAULT_ENGINE,
            'engines': engines,
            'missing_node_types': legacy['missing_node_types'],
            'schema_errors': legacy['schema_errors'],
            'graphs': {**{k: len(v) for k, v in TEMPLATES.items()}, 'prompt': len(TEMPLATES['single'])},
            'upscale_available': upscale_available(obj) and 'image.model.upscale' not in ModelPolicy(PATHS.root).disabled(),
            'progress_client_id': CLIENT_ID,
            'progress_ws_url': 'ws://127.0.0.1:%d/api/events' % PORT,
            'quality_steps': QUALITY_STEPS,
            'upscale_factors': UPSCALE_FACTORS,
            'save_dir': settings['save_dir'],
            'auto_save': True
        })

    def status(self, ident):
        def reply(code, payload):
            receipt = HISTORY.get(ident)
            with JOBS_LOCK:
                metadata = JOB_META.get(ident) or {}
            record = (receipt or metadata).get('generation')
            if record:
                if payload.get('state') in ('done', 'error', 'cancelled') and record.get('duration_sec') is None:
                    for detail in metadata.get('_technical', []):
                        generation_finish(detail, state=payload['state'])
                    generation_finish(record, payload.get('elapsed_sec'), state=payload['state'])
                payload['generation'] = record
            return self.response_json(code, payload)

        try:
            uuid.UUID(ident)
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Некорректный ID задачи')
        receipt = HISTORY.get(ident)
        if receipt:
            if receipt['state'] == 'cancelled':
                return reply(200, {'state': 'cancelled', 'elapsed_sec': receipt['elapsed_sec']})
            files = receipt['saved_files']
            if files and all(Path(p).is_file() for p in files):
                return reply(200, {'state': 'done', 'images': len(files), 'saved_count': len(files), 'elapsed_sec': receipt['elapsed_sec'], 'save_dir': str(Path(files[0]).parent), 'expanded_prompt': receipt.get('expanded_prompt', '')})
            return reply(200, {'state': 'error', 'error': 'Сохранённые файлы сейчас недоступны. Проверьте папку результатов или подключение диска.'})
        with JOBS_LOCK:
            meta = JOB_META.get(ident) or {}
            cancelling = ident in CANCELLING
        elapsed = int(time.time() - meta.get('submitted_at', time.time()))
        if cancelling:
            if self.job_is_queued_or_running(ident):
                return reply(200, {'state': 'cancelling', 'elapsed_sec': elapsed})
            with JOBS_LOCK:
                CANCELLING.discard(ident)
                JOB_TOKENS.pop(ident, None)
            history = self.fetch_history(ident)
            outputs = self.job_outputs(ident, history)
            if outputs:
                saved, save_dir = self.maybe_save_outputs(ident, outputs)
                return reply(200, {'state': 'done', 'images': len(outputs), 'elapsed_sec': elapsed, 'saved_count': len(saved), 'save_dir': save_dir, 'expanded_prompt': meta.get('expanded_prompt', '')})
            HISTORY.record(ident, 'cancelled', meta, elapsed_sec=elapsed)
            return reply(200, {'state': 'cancelled', 'elapsed_sec': elapsed})

        history = self.fetch_history(ident)
        if not history:
            queue = self.queue_info(ident)
            if queue.get('location') == 'running':
                payload = {'state': 'running', 'elapsed_sec': elapsed, **queue}
                payload.update(self.progress_info(ident))
                return reply(200, payload)
            if queue.get('location') == 'pending':
                return reply(200, {'state': 'queued', 'elapsed_sec': elapsed, **queue})
            return reply(200, {'state': 'queued', 'elapsed_sec': elapsed})

        outputs = self.job_outputs(ident, history)
        info = history.get('status', {})
        if outputs:
            try:
                saved, save_dir = self.maybe_save_outputs(ident, outputs)
                return reply(200, {'state': 'done', 'images': len(outputs), 'elapsed_sec': elapsed, 'saved_count': len(saved), 'save_dir': save_dir, 'expanded_prompt': meta.get('expanded_prompt', '')})
            except (OSError, ValueError, HTTPError, URLError, TimeoutError) as exc:
                return reply(200, {'state': 'done', 'images': len(outputs), 'elapsed_sec': elapsed, 'saved_count': 0, 'save_error': str(exc)[:300]})
        if info.get('completed') is False:
            errors = [m for m in info.get('messages', []) if m and m[0] == 'execution_error']
            if errors:
                return reply(200, {'state': 'error', 'error': str(errors[-1][1])[:1800], 'elapsed_sec': elapsed})
        if info.get('completed') is True:
            return reply(200, {'state': 'error', 'error': 'ComfyUI завершил задачу без ожидаемого выходного изображения.', 'elapsed_sec': elapsed})
        queue = self.queue_info(ident)
        state = 'running' if queue.get('location') == 'running' else 'waiting'
        payload = {'state': state, 'elapsed_sec': elapsed, 'status': str(info.get('status_str', 'unknown'))}
        payload.update(queue)
        if state == 'running':
            payload.update(self.progress_info(ident))
        return reply(200, payload)

    def microphone_event(self, data):
        import sqlite3
        try:
            if data.get('action') == 'start' and set(data) == {'action'}:
                return self.response_json(200, {'generation': generation_begin('MicrophoneSession')})
            if data.get('action') == 'finish' and set(data) <= {'action', 'id', 'state'}:
                state = data.get('state', 'done')
                if state not in ('done', 'cancelled', 'error', 'interrupted'):
                    raise ValueError('Некорректное состояние микрофона')
                record = event_history().microphone(data.get('id'))
                generation_finish(record, state=state)
                return self.response_json(200, {'generation': record})
            raise ValueError('Некорректное событие микрофона')
        except (sqlite3.Error, OSError):
            return self.response_json(503, {'error': 'Не удалось сохранить событие микрофона.'})

    def delete_image(self, data):
        from job_delete import DeletePlan, confined_file
        ident = data.get('id')
        if not isinstance(ident, str) or str(uuid.UUID(ident)) != ident:
            raise ValueError('Некорректный идентификатор генерации')
        with JOBS_LOCK:
            guard = SAVE_LOCKS.setdefault(ident, Lock())
        with guard:
            receipt = HISTORY.get(ident)
            if receipt is None or not HISTORY.writable:
                raise ValueError('Запись не найдена или история недоступна для записи')
            if self.job_is_queued_or_running(ident):
                raise ValueError('Сначала остановите генерацию и дождитесь её завершения')
            upstream_history = self.fetch_history(ident)
            plan = DeletePlan()
            for value in receipt['saved_files']:
                plan.file(value)
            files = list(receipt.get('comfy_files') or [])
            inputs = list(receipt.get('input_files') or [])
            if upstream_history:
                for node in upstream_history.get('outputs', {}).values():
                    for item in node.get('images', []):
                        files.append(item)
                # Older receipts did not record upload names; recover only our own uniquely named uploads.
                prompt = upstream_history.get('prompt') or []
                graph = prompt[2] if len(prompt) > 2 and isinstance(prompt[2], dict) else {}
                for node in graph.values():
                    value = node.get('inputs', {}).get('image')
                    if isinstance(value, str) and Path(value).name.startswith('qwen_local_'):
                        inputs.append(value)
            for value in inputs:
                plan.file(confined_file(getattr(PATHS, 'comfy_input', PATHS.comfy / 'input'), value))
            for item in files:
                area = item.get('type', 'output')
                if area not in ('output', 'temp'):
                    raise ValueError('Некорректная область файла ComfyUI')
                base = getattr(PATHS, 'comfy_' + area, PATHS.comfy / area)
                relative = str(Path(item.get('subfolder', '')) / item['filename'])
                plan.file(confined_file(base, relative))
            plan.execute()
            json_upstream('/history', {'delete': [ident]}, timeout=15)
            remove_event(receipt.get('generation'))
            HISTORY.remove(ident)
            with JOBS_LOCK:
                JOB_META.pop(ident, None)
                JOB_TOKENS.pop(ident, None)
                CANCELLING.discard(ident)
            return self.response_json(200, {'deleted': True})

    def result(self, ident, index):
        receipt = HISTORY.get(ident)
        if receipt and receipt['state'] == 'done':
            if not index.isdecimal() or int(index) >= len(receipt['saved_files']):
                return self.response_json(404, {'error': 'Изображение не найдено'})
            path = Path(receipt['saved_files'][int(index)])
            if not path.is_file():
                return self.response_json(404, {'error': 'Файл результата сейчас недоступен'})
            try:
                return self.reply(200, path.read_bytes(), 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg')
            except OSError:
                return self.response_json(404, {'error': 'Файл результата сейчас недоступен'})
        h = self.fetch_history(ident)
        if not h:
            return self.response_json(404, {'error': 'No results'})
        outputs = self.job_outputs(ident, h)
        if not index.isdecimal() or int(index) >= len(outputs):
            return self.response_json(404, {'error': 'Image not found'})
        item = outputs[int(index)]
        if item.get('type') != 'output':
            return self.response_json(400, {'error': 'Unexpected output type'})
        u = '/view?' + urlencode({'filename': item['filename'], 'subfolder': item.get('subfolder', ''), 'type': 'output'})
        raw = upstream(u, timeout=90)
        kind = 'image/png' if item['filename'].lower().endswith('.png') else 'image/jpeg'
        return self.reply(200, raw, kind)


if __name__ == '__main__':
    configure_events(PATHS.settings / 'event-history.sqlite3', inherit=True)
    print('QwenDesktopApp-v3 local UI on http://127.0.0.1:%d' % PORT, flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever(poll_interval=0.25)
