"""One place that decides whether a module may start GPU work and frees what the idle modules still hold.

Image, Video, Music and Text take the video card one at a time. What stays in video memory after a job differs:
Video and Music run in worker processes that exit when the job ends (nothing is left), ComfyUI (Image) keeps its models
cached until told to drop them, and Ollama (Text) keeps the chat model for the keep-alive time. `claim()` is what every
"start" route calls: it refuses while another module works, asks each idle module to release what it holds and, when the
caller needs a minimum, waits until that much memory is really free.
"""
import time

MODULES = ('image', 'video', 'music', 'text')
# Voice input is not a module that blocks others (it never locks the interface), but its worker may hold video memory: it is released like the rest.
RELEASABLE = MODULES + ('speech',)
NAMES = dict(image='Image', video='Video', music='Music', text='Text')
HINTS = dict(image='Дождитесь завершения или отмените задачу.', video='Дождитесь завершения или отмените задачу Video.',
             music='Дождитесь завершения или отмените задачу Music.', text='Дождитесь окончания ответа или остановите его.')


class Busy(ValueError):
    """Another module works on the GPU; the caller should answer 409."""


class GpuUnavailable(RuntimeError):
    """The free video memory could not be read; the caller should answer 503."""


class GpuCoordinator:
    def __init__(self, probes, releasers=None, free_vram=None, clock=time.monotonic, sleep=time.sleep, held=None):
        self.probes = dict(probes)
        self.releasers = dict(releasers or {})
        self.free_vram = free_vram
        # Memory a module's own process already holds and can reuse for its next job (ComfyUI keeps Image models cached):
        # it counts as available to that module, so a second job of the same engine is not refused for its own cache.
        self.held = dict(held or {})
        self.clock, self.sleep = clock, sleep

    def busy_modules(self, exclude=None):
        """Modules working right now. A probe that fails (a service that is not running) counts as idle."""
        working = []
        for module in MODULES:
            if module == exclude or module not in self.probes:
                continue
            try:
                if self.probes[module]():
                    working.append(module)
            except Exception:
                continue
        return working

    def snapshot(self):
        working = self.busy_modules()
        return dict(busy=bool(working), modules=working, labels=[NAMES[m] for m in working])

    def claim(self, module, need_vram_mb=0, wait=12.0):
        """Let `module` start. Returns the modules that released memory; raises Busy / GpuUnavailable."""
        if module not in MODULES:
            raise ValueError('Неизвестный модуль GPU')
        working = self.busy_modules(exclude=module)
        if working:
            raise Busy('GPU занят %s. %s' % (' и '.join(NAMES[m] for m in working), HINTS[working[0]]))
        released = []
        for other in RELEASABLE:
            release = self.releasers.get(other)
            if other == module or release is None:
                continue
            try:
                release()
                released.append(other)
            except Busy:
                raise
            except Exception:
                continue  # freeing is best effort: an unreachable service holds no memory we could free
        if need_vram_mb:
            self._wait_for(need_vram_mb, wait, module)
        return released

    def _held_mb(self, module):
        try:
            return max(0, int(self.held[module]())) if module in self.held else 0
        except Exception:
            return 0  # an unreachable service holds nothing we could count on

    def _wait_for(self, need_mb, wait, module=None):
        if self.free_vram is None:
            return
        deadline = self.clock() + wait
        free = 0
        while True:
            try:
                free = self.free_vram() + self._held_mb(module)
            except Exception:
                raise GpuUnavailable('Не удалось проверить свободную видеопамять. Подробности — в журнале запуска.') from None
            if free >= need_mb:
                return
            if self.clock() >= deadline:
                raise Busy('Недостаточно свободной видеопамяти: нужно не менее %.1f ГБ, свободно %.1f ГБ. Закройте другие GPU-приложения; чужие процессы студия не завершает.'
                           % (need_mb / 1024, free / 1024))
            self.sleep(0.5)
