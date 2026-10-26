"""One expiring installer question, shared by the worker and all browser tabs."""
import secrets
import threading
import time
from urllib.parse import urlsplit

from . import net


class Recovery:
    WAIT_SECONDS = 30

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.condition = threading.Condition()
        self.pending = None
        self.answer = None

    def snapshot(self):
        with self.condition:
            if not self.pending:
                return None
            remaining = max(0, self.pending['deadline'] - self.clock())
            return {**{key: value for key, value in self.pending.items() if key != 'deadline'}, 'remaining_seconds': remaining}

    def request(self, item, cancel):
        with self.condition:
            self.pending = dict(item, token=secrets.token_urlsafe(24), deadline=self.clock() + self.WAIT_SECONDS)
            self.answer = None
            try:
                while self.answer is None:
                    if cancel():
                        raise net.Cancelled()
                    remaining = self.pending['deadline'] - self.clock()
                    if remaining <= 0:
                        return None
                    self.condition.wait(min(.2, remaining))
                return self.answer if self.answer.get('kind') != 'skip' else None
            finally:
                self.pending = None
                self.answer = None

    def respond(self, token, kind, value=''):
        with self.condition:
            if not self.pending or self.pending['token'] != token or self.clock() >= self.pending['deadline'] or self.answer is not None:
                raise ValueError('Запрос уже закрыт. Повторите установку модели.')
            if kind not in ('url', 'file', 'skip') or not isinstance(value, str) or len(value) > 4096:
                raise ValueError('Некорректный источник')
            value = value.strip()
            if kind == 'url':
                parsed = urlsplit(value)
                if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                    raise ValueError('Нужна прямая HTTP(S)-ссылка без логина и пароля в адресе')
            if kind == 'file' and not value:
                raise ValueError('Укажите скачанный файл')
            self.answer = dict(kind=kind, value=value)
            self.condition.notify_all()
            return {'accepted': True}

    def wake(self):
        with self.condition:
            self.condition.notify_all()
