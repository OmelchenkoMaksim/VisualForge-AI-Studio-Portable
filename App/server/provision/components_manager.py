"""Runs the installer in a background thread for the Components panel and exposes its state as plain dictionaries."""
from ui_strings import source as ui_text
from collections import deque
import shutil
import threading
import time

from .install_engine import Busy, Engine, NotEnoughSpace, ProvisionError


class ComponentsManager:
    def __init__(self, root, catalog_path=None, gpu=None, engine_factory=Engine):
        self.root = root
        self.catalog_path = catalog_path
        self.gpu = gpu
        self.factory = engine_factory
        self.lock = threading.RLock()
        self.thread = None
        self.engine = None
        self.events = deque(maxlen=60)
        self.progress = {}
        self.current = None
        self.results = None
        self.error = ''
        self.started = None
        self.finished = None
        from .component_recovery import Recovery
        self.recovery = Recovery()
        self.view = self.factory(root, catalog_path)
        from .model_lifecycle import ModelLifecycle
        self.models = ModelLifecycle(self)

    # ---- reading ------------------------------------------------------------------------------------------------
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def snapshot(self):
        with self.lock:
            return dict(running=self.running(), current=self.current, progress=dict(self.progress), results=self.results, error=self.error,
                        started=self.started, finished=self.finished, events=list(self.events)[-12:], recovery=self.recovery.snapshot())

    def describe(self):
        modules = self.view.describe()
        disabled = self.models.policy.disabled()
        for module in modules:
            for row in module['components']:
                if row['kind'] == 'model':
                    row.update(enabled=row['id'] not in disabled, removable=True)
            if module['id'] == 'text':
                module['components'].extend(self.models.custom_models())
        missing_software = [row['id'] for module in modules for row in module['components'] if row['kind'] == 'software' and row['state'] != 'installed']
        try:
            free = shutil.disk_usage(self.root).free
        except OSError:
            free = None
        return dict(modules=modules, install=self.snapshot(), missing_software=missing_software, free_bytes=free, first_run_done=self.view.first_run_done(),
                    presets={name: dict(title=preset['title']) for name, preset in self.view.catalog.get('presets', {}).items()})

    # ---- acting -------------------------------------------------------------------------------------------------
    def start(self, models=(), text_models=(), preset=None, only_required=False):
        """Install the programs that are missing plus the chosen models. Returns the plan."""
        with self.lock:
            if self.running():
                raise Busy(ui_text('components.start_installation_already_running'))
            if self.gpu is not None and self.gpu.snapshot()['busy']:
                raise Busy(ui_text('components.start_generation_running_wait_finish_stop_before'))
            engine = self.factory(self.root, self.catalog_path, emit=self._on_event)
            chosen = list(models) + [engine.text_model_component(tag) for tag in text_models]
            if preset:
                from .setup_cli import resolve_preset
                chosen += resolve_preset(engine, preset)
            engine.recover = self.recovery.request
            ids = chosen if only_required else engine.select(models=chosen)
            plan = engine.plan(ids)
            if not plan['todo']:
                raise ProvisionError(ui_text('components.start_everything_selected_already_installed'))
            engine.check_space(plan)
            self.engine = engine
            self.events.clear()
            self.progress, self.current, self.results, self.error = {}, None, None, ''
            self.started, self.finished = time.time(), None
            self.thread = threading.Thread(target=self._run, args=(engine, ids, chosen), name='components-install', daemon=True)
            self.thread.start()
            return dict(todo=plan['todo'], download_bytes=plan['download_bytes'], disk_bytes=plan['disk_bytes'])

    def repair(self, ident):
        component = self.models.component(ident)
        if ident.startswith('text:'):
            raise ProvisionError(ui_text('components.repair_local_build_use_original_ollama_import'))
        return self.start(models=[component['id']], only_required=True)

    def cancel(self):
        with self.lock:
            if self.engine and self.running():
                self.engine.cancel()
                self.recovery.wake()
                return True
        return False

    def _run(self, engine, ids, chosen):
        try:
            results = engine.install(ids, check_space=False)
            for ident in chosen:
                if results.get(ident) in ('installed', 'skipped'):
                    self.models.policy.set_enabled(ident, True)
                    for step in engine.by_id[ident].get('after', []):
                        if step.get('op') == 'ollama_pull':
                            self.models.policy.set_enabled('text:' + step['model'], True)
            engine.mark_first_run(chosen)
            with self.lock:
                self.results = results
        except (Busy, NotEnoughSpace, ProvisionError) as error:
            with self.lock:
                self.error = str(error)
        except Exception as error:  # the thread must always end in a readable state
            with self.lock:
                self.error = ui_text('components.run_unexpected_installation_error') % error
        finally:
            with self.lock:
                self.finished = time.time()
                self.view = self.factory(self.root, self.catalog_path)

    def _on_event(self, event):
        with self.lock:
            kind = event['type']
            if kind == 'component_start':
                self.current = dict(id=event['id'], title=event['title'], index=event['index'], total=event['total'])
                self.progress = {}
            elif kind == 'progress':
                self.progress = dict(name=event['name'], done=event['done'], total=event['total'], speed=event.get('speed', 0))
                return
            elif kind in ('note', 'component_failed', 'component_done', 'download_start'):
                self.events.append({key: event[key] for key in ('type', 'time', 'text', 'error', 'id', 'name') if key in event})
