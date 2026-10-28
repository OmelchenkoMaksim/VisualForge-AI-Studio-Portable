"""Stdlib-only bridge client. The private backend starts only on Video use."""
from ui_strings import source as ui_text
import atexit
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler
from .delivery import public_presets
from .registry import ENGINES
from .wan_support import engine_missing

class VideoManager:
    def __init__(self, paths):
        self.paths = paths
        self.root = Path(os.environ.get('VISUALFORGE_VIDEO_ROOT', paths.root / 'Runtime/Video')).resolve()
        self.models = Path(os.environ.get('VISUALFORGE_VIDEO_MODELS', paths.models / 'Video/H3')).resolve()
        self.user = paths.settings.parent
        configured = 8290
        port_file = paths.ports if paths.modern else None
        if port_file and port_file.is_file() and port_file.stat().st_size <= 8192:
            configured = json.loads(port_file.read_text(encoding='utf-8-sig')).get('VideoPort', configured)
        self.port = int(os.environ.get('VISUALFORGE_VIDEO_PORT', configured))
        if not 1024 <= self.port <= 65535 or self.port in (int(os.environ.get('QWEN_UI_PORT', '8189')), int(os.environ.get('QWEN_COMFY_PORT', '8188'))):
            raise ValueError('Invalid private video port')
        self.token = secrets.token_urlsafe(32)
        self.process = None
        self.job = None
        self.last_busy = False
        self.lock = threading.RLock()
        self.opener = build_opener(ProxyHandler({}))
        atexit.register(self.close)

    def availability(self):
        engines = []
        for engine in ENGINES.values():
            missing = engine_missing(self.root,self.models,engine['id'])
            info = dict(engine,available=not missing,missing=missing,
                reason=ui_text('video.availability_engine_files_not_installed_unavailable_download') if missing else '')
            if engine['id'] in ('wan22-ti2v-5b','wan22-s2v-14b'):
                is_s2v=engine['id']=='wan22-s2v-14b'
                evidence = Path(os.environ.get('VISUALFORGE_S2V_VERIFICATION' if is_s2v else 'VISUALFORGE_WAN_VERIFICATION',self.root/('s2v-verification.json' if is_s2v else 'wan-verification.json')))
                if evidence.is_file() and evidence.stat().st_size < 32768:
                    try:
                        info['verification'] = json.loads(evidence.read_text(encoding='utf-8-sig'))
                    except (OSError,ValueError):
                        info['verification'] = {'summary_ru':ui_text('video.availability_read_wan_verification_data_failed')}
            if engine['id']=='ltx25-distilled':
                evidence=Path(os.environ.get('VISUALFORGE_LTX_VERIFICATION',self.paths.settings.parent/'Settings/ltx25-verification.json'))
                if evidence.is_file() and evidence.stat().st_size<32768:
                    try:
                        info['verification']=json.loads(evidence.read_text(encoding='utf-8-sig'))
                    except (OSError,ValueError):
                        pass
            engines.append(info)
        from model_policy import apply_profiles
        apply_profiles(self.paths.root, 'video', engines)
        available = any(engine['available'] for engine in engines)
        return {'available': available, 'engines': engines, 'delivery': public_presets(), 'missing': [path for engine in engines for path in engine['missing']],
                'voice_available': (self.root / 'voice-packages/qwen_tts/__init__.py').is_file() and (self.models.parent / 'Qwen3TTS/config.json').is_file(),
                'reason': ui_text('video.availability_video_runtime_missing_damaged_setup_visual') if not available else '',
                'running': bool(self.process and self.process.poll() is None)}

    def call(self, route, data=None, timeout=15):
        raw = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
        request = Request('http://127.0.0.1:%d%s' % (self.port, route), data=raw,
                          headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=timeout) as response:
            return json.loads(response.read())

    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                return
            if self.job:
                self.job.close()
                self.job = None
            status = self.availability()
            if not status['available']:
                raise ValueError(status['reason'])
            with socket.socket() as probe:
                try:
                    probe.bind(('127.0.0.1', self.port))
                except OSError:
                    raise ValueError(ui_text('video.start_video_port_occupied_another_application_change'))
            from .runtime import environment
            state = Path(os.environ.get('VISUALFORGE_VIDEO_STATE_ROOT', self.paths.cache / 'Video/State'))
            env = environment(self.root, self.models, state)
            env['VISUALFORGE_VIDEO_TOKEN'] = self.token
            log = self.paths.logs / 'video-backend.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('ab') as stream:
                self.process = subprocess.Popen([str(self.root / 'python/python.exe'), '-u', str(Path(__file__).with_name('service.py')),
                    '--root', str(self.root), '--models', str(self.models), '--user', str(self.user), '--state', str(state),
                    '--port', str(self.port), '--parent', str(os.getpid())], env=env, stdout=stream, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            from .owned import OwnedJob
            try:
                self.job = OwnedJob(self.process)
            except OSError:
                self.process.terminate()
                raise
            for _ in range(60):
                if self.process.poll() is not None:
                    raise ValueError(ui_text('video.start_video_backend_did_not_check_log'))
                try:
                    if self.call('/health').get('ready'):
                        return
                except OSError:
                    time.sleep(.1)
            raise ValueError(ui_text('video.start_video_backend_did_not_respond_during'))

    def busy(self):
        if not self.process or self.process.poll() is not None:
            self.last_busy = False  # start() drops the old job object of an exited service
            return False
        try:
            self.last_busy = bool(self.call('/active', timeout=5).get('busy', False))
        except OSError:
            # A live service that is slow to answer may be in the middle of a job. Closing the job object here (as an earlier
            # version did) kills the service and the running generation, so keep the last known answer instead.
            pass
        return self.last_busy

    def close(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                try:
                    self.call('/shutdown', {})
                    self.process.wait(timeout=15)
                except (OSError, subprocess.TimeoutExpired):
                    # Only our exact Popen child, never a process selected by name.
                    self.process.terminate()
            if self.job:
                self.job.close()
                self.job = None
