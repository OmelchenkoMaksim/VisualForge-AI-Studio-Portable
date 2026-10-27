"""Aider tasks: one bridge process per task (aider_bridge.py run by Aider's own Python), its JSON-line events relayed to the page.

Aider has no server mode, so the studio starts it for each task and passes the conversation so far; the bridge answers Aider's
questions by the chat's permission level and asks the user through the same permission cards as OpenCode."""
import json
from pathlib import Path
import queue
import subprocess
import threading
import time

from .opencode import clean_environment


class AiderError(ValueError):
    """Aider failed or could not start; the message is shown to the user."""


class AiderRunner:
    def __init__(self, paths, python, command=None):
        self.paths = paths
        self.python = Path(python)
        self.bridge = Path(__file__).with_name('aider_bridge.py')
        self.command = command  # tests run the bridge with another Python and a stand-in package
        self.state = paths.cache / 'Agents/aider'
        self.process = None
        self.job = None
        self.lock = threading.Lock()

    def installed(self):
        return self.command is not None or (self.python.is_file() and (self.python.parent / 'Lib/site-packages/aider/__init__.py').is_file())

    def environment(self):
        env = clean_environment()
        env.update(GIT_TERMINAL_PROMPT='0', NO_COLOR='1')
        return env

    def _log(self):
        log = self.paths.logs / 'agent-aider.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.is_file() and log.stat().st_size > 4 * 1024 * 1024:
            log.write_bytes(log.read_bytes()[-512 * 1024:])
        return log

    def run(self, start, on_event, cancel, disconnected):
        """Run one task; `on_event` gets the page events. Returns the bridge's `done` event, or None when the task was stopped."""
        if not self.installed():
            raise AiderError('Aider не установлен: нет Runtime/Agents/aider. Установите его в «Настройки → Компоненты», раздел Text.')
        self.state.mkdir(parents=True, exist_ok=True)
        # -I: the user's PYTHONPATH or site-packages must not leak into Aider's interpreter; -X utf8 replaces PYTHONUTF8, which -I ignores.
        command = self.command or [str(self.python), '-I', '-X', 'utf8', str(self.bridge)]
        with self._log().open('ab') as stream:
            stream.write(('\n==== %s Aider task in %s (%s)\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), start['workspace'], start['mode'])).encode('utf-8'))
            stream.flush()
            process = subprocess.Popen(command, cwd=str(self.state), env=self.environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=stream, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        from video.owned import OwnedJob
        try:
            job = OwnedJob(process)
        except OSError:
            process.kill()
            raise
        events = queue.Queue()

        def read():
            try:
                for line in process.stdout:
                    try:
                        events.put(json.loads(line))
                    except ValueError:
                        continue
            finally:
                events.put(None)

        threading.Thread(target=read, name='aider-events', daemon=True).start()
        with self.lock:
            self.process, self.job = process, job
        try:
            self._write(dict(start, type='start'))
            while True:
                if cancel.is_set() or disconnected():
                    cancel.set()
                    return None
                try:
                    event = events.get(timeout=0.5)
                except queue.Empty:
                    continue
                if event is None:
                    try:
                        code = process.wait(5)
                    except subprocess.TimeoutExpired:
                        code = None
                    raise AiderError('Aider неожиданно завершился (код %s). Подробности — в журнале agent-aider.log.' % code)
                if event.get('type') == 'done':
                    return event
                if event.get('type') == 'error':
                    raise AiderError('Aider: ' + str(event.get('message') or 'ошибка')[:500])
                on_event(event)
        finally:
            with self.lock:
                self.process = self.job = None
            try:
                process.stdin.close()
            except OSError:
                pass
            job.close()  # kills the bridge and every command it started
            if process.poll() is None:
                process.kill()
            process.wait(10)
            process.stdout.close()

    def _write(self, message):
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                raise AiderError('Aider уже не выполняет задачу')
            try:
                process.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8'))
                process.stdin.flush()
            except OSError:
                raise AiderError('Aider уже не выполняет задачу') from None

    def reply(self, request_id, answer):
        if answer not in ('once', 'always', 'reject'):
            raise ValueError('Ответ на запрос: разрешить один раз, разрешать всегда или отклонить')
        self._write(dict(type='reply', id=request_id, reply=answer))

    def alive(self):
        with self.lock:
            return bool(self.process and self.process.poll() is None)

    def close(self):
        with self.lock:
            job, self.job = self.job, None
        if job:
            job.close()
