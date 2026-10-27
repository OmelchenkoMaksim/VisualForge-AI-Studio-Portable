"""An owned `opencode serve` process and its HTTP API (sessions, prompts, permission replies, the global event stream).

OpenCode is started with a scrubbed environment and an inline config, so it sees only the studio's model gateway: no API keys
inherited from the user's environment (it exposes provider keys it finds through its own API), no other providers, no project
config that could loosen permissions, no update checks, model-catalog fetches or LSP downloads (the studio works offline)."""
import atexit
import base64
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, build_opener, ProxyHandler

DEFAULT_PORT = 8294
# Environment the agent and its shell commands inherit. Everything else (tokens, keys, proxies, other tools' settings) is dropped.
KEEP_ENV = ('SystemRoot', 'SystemDrive', 'WINDIR', 'ComSpec', 'PATHEXT', 'PATH', 'TEMP', 'TMP', 'NUMBER_OF_PROCESSORS', 'OS',
            'PROCESSOR_ARCHITECTURE', 'PROCESSOR_IDENTIFIER', 'PROCESSOR_LEVEL', 'PROCESSOR_REVISION', 'ProgramData', 'ProgramFiles',
            'ProgramFiles(x86)', 'ProgramW6432', 'CommonProgramFiles', 'CommonProgramFiles(x86)', 'CommonProgramW6432', 'PUBLIC',
            # The real profile stays: git reads the user's name/e-mail from it, so the agent's commits are the user's.
            'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'USERNAME', 'USERDOMAIN', 'COMPUTERNAME', 'APPDATA', 'LOCALAPPDATA')
# Rules every chat gets, whatever its mode; per-mode rules are added after them (the last matching rule wins in OpenCode).
BASE_PERMISSION = {'edit': 'ask', 'bash': {'*': 'ask', 'git push': 'deny', 'git push *': 'deny'}, 'webfetch': 'deny', 'websearch': 'deny',
                   'external_directory': 'deny', 'doom_loop': 'ask'}
MODE_RULES = {
    'read': [('edit', '*', 'deny'), ('bash', '*', 'deny')],
    'ask': [],
    'auto': [('edit', '*', 'allow')] + [('bash', pattern, 'allow') for pattern in
             ('git status', 'git status *', 'git diff', 'git diff *', 'git log', 'git log *', 'git add *', 'git commit *')],
}
ALWAYS_DENIED = [('bash', 'git push', 'deny'), ('bash', 'git push *', 'deny')]


def clean_environment():
    # os.environ on Windows reports names upper-cased (SYSTEMROOT); without SystemRoot `opencode serve` dies with "ServeError".
    keep = {name.upper() for name in KEEP_ENV}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    # Python started by an agent's command prints to a pipe in the ANSI code page (cp1251 on Russian Windows), while the agents
    # read command output as UTF-8: a Cyrillic project path or test message reached the model and the page garbled.
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    # git prints non-ASCII file names as octal escapes ("\320\227...") unless core.quotePath is off; the model could not read a
    # Cyrillic file name in `git status`. Set for the agents' processes only, the user's git config is not touched. Not the
    # GIT_CONFIG_COUNT/KEY_0 pair: dsh drops variables named like secrets (*KEY*) from its shell, and git then refused to start.
    env['GIT_CONFIG_PARAMETERS'] = "'core.quotepath=false'"
    return env


def permission_rules(mode):
    return [dict(permission=p, pattern=pattern, action=action) for p, pattern, action in MODE_RULES[mode] + ALWAYS_DENIED]


class OpenCodeError(ValueError):
    """OpenCode refused or failed a request; the message is shown to the user."""


class OpenCodeServer:
    def __init__(self, paths, executable, port=DEFAULT_PORT, command=None):
        self.paths = paths
        self.executable = Path(executable)
        self.port = port
        self.command = command  # tests run a stand-in
        self.state = paths.cache / 'Agents/opencode'
        self.password = secrets.token_urlsafe(24)
        self.process = None
        self.job = None
        self.config_key = None
        self.listeners = {}
        self.lock = threading.RLock()
        self.opener = build_opener(ProxyHandler({}))
        atexit.register(self.close)

    def installed(self):
        return self.command is not None or self.executable.is_file()

    def alive(self):
        return bool(self.process and self.process.poll() is None)

    # ---- process ------------------------------------------------------------------------------------------------
    def config(self, gateway_url, gateway_token, models):
        """`models`: alias -> dict(name, context). The provider is the studio gateway; its key is the gateway's launch token."""
        entries = {alias: dict(name=info['label'], tool_call=True, attachment=False, limit=dict(context=info['context'], output=min(8192, info['context'] // 4)))
                   for alias, info in models.items()}
        first = next(iter(models), None)
        value = {'$schema': 'https://opencode.ai/config.json', 'autoupdate': False, 'share': 'disabled', 'snapshot': True,
                 'enabled_providers': ['visualforge'], 'permission': BASE_PERMISSION,
                 'provider': {'visualforge': {'npm': '@ai-sdk/openai-compatible', 'name': 'VisualForge',
                                              'options': {'baseURL': gateway_url, 'apiKey': gateway_token}, 'models': entries}}}
        if first:
            value.update(model='visualforge/' + first, small_model='visualforge/' + first)
        return value

    def environment(self, config):
        env = clean_environment()
        folders = {name: self.state / name for name in ('data', 'config', 'cache', 'state')}
        for folder in folders.values():
            folder.mkdir(parents=True, exist_ok=True)
        env.update(XDG_DATA_HOME=str(folders['data']), XDG_CONFIG_HOME=str(folders['config']), XDG_CACHE_HOME=str(folders['cache']),
                   XDG_STATE_HOME=str(folders['state']), OPENCODE_CONFIG_CONTENT=json.dumps(config, ensure_ascii=False),
                   OPENCODE_SERVER_PASSWORD=self.password, OPENCODE_DISABLE_AUTOUPDATE='1', OPENCODE_DISABLE_MODELS_FETCH='1',
                   OPENCODE_DISABLE_LSP_DOWNLOAD='1', OPENCODE_DISABLE_DEFAULT_PLUGINS='1', OPENCODE_DISABLE_CLAUDE_CODE='1',
                   OPENCODE_DISABLE_EXTERNAL_SKILLS='1', OPENCODE_DISABLE_PROJECT_CONFIG='1', OPENCODE_DISABLE_EMBEDDED_WEB_UI='1',
                   GIT_TERMINAL_PROMPT='0', NO_COLOR='1')
        git = shutil.which('git')
        if git:
            # Git for Windows puts git.exe in cmd/ or mingw64/bin/ (whichever PATH lists) and bash.exe in bin/ of its root.
            bash = next((folder / 'bin/bash.exe' for folder in Path(git).resolve().parents[:3] if (folder / 'bin/bash.exe').is_file()), None)
            if bash:
                # OpenCode takes its shell from SHELL, else the first of pwsh, powershell, Git Bash, cmd. PowerShell writes a
                # command's output to the pipe in the OEM code page (cp866), which OpenCode read as UTF-8: Cyrillic file names
                # and messages reached the model garbled. Git Bash works in UTF-8.
                env['OPENCODE_GIT_BASH_PATH'] = env['SHELL'] = str(bash)
        return env

    def start(self, gateway_url, gateway_token, models):
        """Start (or restart when the model list changed and nothing runs) the server; returns once it answers."""
        config = self.config(gateway_url, gateway_token, models)
        key = json.dumps(config, sort_keys=True)
        with self.lock:
            if self.alive() and key == self.config_key:
                return
            if self.alive():
                if self.listeners:
                    return  # a task is running: keep the old model list until it ends
                self.close()
            if not self.installed():
                raise OpenCodeError('OpenCode не установлен: нет Runtime/Agents/opencode/opencode.exe. Установите его в «Настройки → Компоненты», раздел Text.')
            with socket.socket() as probe:
                try:
                    probe.bind(('127.0.0.1', self.port))
                except OSError:
                    raise OpenCodeError('Порт OpenCode %d занят другой программой. Измените OpenCodePort в настройке запуска.' % self.port) from None
            log = self.paths.logs / 'agent-opencode.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            if log.is_file() and log.stat().st_size > 4 * 1024 * 1024:
                log.write_bytes(log.read_bytes()[-512 * 1024:])
            command = self.command or [str(self.executable), 'serve', '--port', str(self.port), '--hostname', '127.0.0.1', '--print-logs']
            with log.open('ab') as stream:
                self.process = subprocess.Popen(command, cwd=str(self.state), env=self.environment(config), stdout=stream, stderr=subprocess.STDOUT,
                                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            from video.owned import OwnedJob
            try:
                self.job = OwnedJob(self.process)
            except OSError:
                self.process.terminate()
                raise
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise OpenCodeError('OpenCode не запустился. Проверьте журнал agent-opencode.log.')
                try:
                    self.call('GET', '/global/health', timeout=2)
                    break
                except (OSError, ValueError):
                    time.sleep(0.3)
            else:
                raise OpenCodeError('OpenCode не ответил при запуске')
            self.config_key = key
            threading.Thread(target=self._listen, args=(self.process,), name='opencode-events', daemon=True).start()

    def close(self):
        with self.lock:
            if self.job:
                self.job.close()
                self.job = None
            if self.process and self.process.poll() is None:
                self.process.terminate()
            self.process = None
            self.config_key = None
            for target in list(self.listeners.values()):
                target.put({'type': 'server.closed'})

    # ---- HTTP ---------------------------------------------------------------------------------------------------
    def _headers(self):
        token = base64.b64encode(('opencode:' + self.password).encode()).decode()
        return {'Authorization': 'Basic ' + token, 'Content-Type': 'application/json'}

    def call(self, method, path, data=None, directory=None, timeout=15):
        if directory is not None:
            path += ('&' if '?' in path else '?') + 'directory=' + quote(str(directory), safe='')
        raw = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
        request = Request('http://127.0.0.1:%d%s' % (self.port, path), data=raw, method=method, headers=self._headers())
        try:
            with self.opener.open(request, timeout=timeout) as response:
                body = response.read()
                return json.loads(body) if body else None
        except HTTPError as error:
            raise OpenCodeError(self._error_text(error.read(4000), error.code)) from None

    @staticmethod
    def _error_text(raw, status):
        try:
            value = json.loads(raw)
            message = value.get('message') or value.get('error') or value.get('data', {}).get('message')
            return 'OpenCode: ' + str(message or status)[:500]
        except (ValueError, AttributeError):
            return 'OpenCode: ошибка %s' % status

    def create_session(self, directory, title, mode):
        return self.call('POST', '/session', dict(title=title[:200], permission=permission_rules(mode)), directory=directory)

    def set_mode(self, session_id, directory, mode):
        self.call('PATCH', '/session/' + quote(session_id, safe=''), dict(permission=permission_rules(mode)), directory=directory)

    def messages(self, session_id, directory):
        return self.call('GET', '/session/%s/message' % quote(session_id, safe=''), directory=directory, timeout=30) or []

    def session_exists(self, session_id, directory):
        try:
            self.call('GET', '/session/' + quote(session_id, safe=''), directory=directory)
            return True
        except OpenCodeError:
            return False

    def prompt(self, session_id, directory, text, alias, system=''):
        body = dict(parts=[dict(type='text', text=text)], model=dict(providerID='visualforge', modelID=alias))
        if system.strip():
            body['system'] = system
        self.call('POST', '/session/%s/prompt_async' % quote(session_id, safe=''), body, directory=directory)

    def reply(self, request_id, answer, directory):
        if answer not in ('once', 'always', 'reject'):
            raise ValueError('Ответ на запрос: разрешить один раз, разрешать всегда или отклонить')
        self.call('POST', '/permission/%s/reply' % quote(request_id, safe=''), dict(reply=answer), directory=directory)

    def abort(self, session_id, directory):
        try:
            self.call('POST', '/session/%s/abort' % quote(session_id, safe=''), directory=directory, timeout=10)
        except (OSError, ValueError):
            pass  # the server may already be gone; the caller stops waiting anyway

    # ---- events -------------------------------------------------------------------------------------------------
    def subscribe(self, session_id):
        target = queue.Queue()
        with self.lock:
            self.listeners[session_id] = target
        return target

    def unsubscribe(self, session_id):
        with self.lock:
            self.listeners.pop(session_id, None)

    def _listen(self, process):
        """Read /global/event for the server's lifetime and hand each event to the subscriber of its session."""
        request = Request('http://127.0.0.1:%d/global/event' % self.port, headers=dict(self._headers(), Accept='text/event-stream'))
        while process.poll() is None:
            try:
                with self.opener.open(request, timeout=3600) as response:
                    for line in response:
                        if process.poll() is not None:
                            return
                        if not line.startswith(b'data: '):
                            continue
                        try:
                            event = json.loads(line[6:]).get('payload') or {}
                        except ValueError:
                            continue
                        session = (event.get('properties') or {}).get('sessionID')
                        if not session:
                            part = (event.get('properties') or {}).get('part') or {}
                            session = part.get('sessionID')
                        with self.lock:
                            target = self.listeners.get(session)
                        if target is not None:
                            target.put(event)
            except (OSError, ValueError):
                time.sleep(0.5)  # the stream ended (restart, sleep, timeout): reconnect while the process lives
