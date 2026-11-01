"""Sequential, cancellable Image compositions with durable plans, variants and normal Image jobs."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import secrets
import threading
import time
import uuid

from director.studio import DirectorCancelled
from generation_identity import begin, finish, scope
from ui_strings import source as ui_text
from .composition_media import IDENT, Materials, atomic_json, prepare, prune_drafts, prune_runs
from .composition_plan import GROUPS, estimate_minutes, request_data

TERMINAL = ('done', 'error', 'cancelled', 'interrupted')


class CompositionManager:
    def __init__(self, root, brain, studio, gpu, submission_lock, token, cancel_preparation=None):
        self.root, self.brain, self.studio, self.gpu = Path(root), brain, studio, gpu
        self.lock, self.token = submission_lock, token
        self.materials = Materials(self.root / 'drafts')
        self.runs = self.root / 'runs'
        self.runs.mkdir(parents=True, exist_ok=True)
        self.run, self.thread, self.cancel = None, None, threading.Event()
        self.cancel_preparation = cancel_preparation
        # A restart cannot resume a half-completed GPU graph safely. Preserve completed variants and mark the rest explicitly.
        for path in self.runs.glob('*/run.json'):
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue                          # a run file that cannot be read never keeps the workspace from opening
            if value.get('state') not in TERMINAL:
                value.update(state='interrupted', finished_at=time.time())
                finish(value.get('generation'), state='interrupted')
                atomic_json(path, value)
        prune_drafts(self.materials.root)
        prune_runs(self.runs)

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def acting(self, header):
        return bool(header) and self.busy() and secrets.compare_digest(str(header), self.token)

    def cancelled_for(self, header):
        return self.acting(header) and self.cancel.is_set()

    @contextmanager
    def request_scope(self):
        with self.gpu.acting_for('composition'), scope(self.run['generation']):
            yield

    def profile(self):
        availability = self.brain.status()
        return dict(available=availability['available'], reason=availability.get('reason', ''), groups=list(GROUPS), max_variants=5)

    def draft(self, ident=None):
        return self.materials.draft(ident)

    def start(self, data):
        request = request_data(data)
        with self.lock:
            if self.busy():
                raise ValueError(ui_text('composition.panel_already_running_text'))
            # A start without a draft would silently make an empty one and shoot a composition without the person's photos.
            if not isinstance(data.get('draft'), str) or not data['draft']:
                raise ValueError(ui_text('composition.panel_invalid_draft_text'))
            draft = self.draft(data['draft'])
            os.utime(self.materials.folder(draft['id']))          # a draft that a composition is made of is in use: the weekly clean-up must not take it
            # Intake and snapshot share the material lock: deleting/uploading in another tab cannot change an in-flight composition.
            with self.materials.lock:
                files = {row['id']: self.materials.path(draft['id'], row['id']).read_bytes() for row in draft['items']}
            self.gpu.claim('composition')
            ident = uuid.uuid4().hex
            folder = self.runs / ident
            folder.mkdir()
            for material, raw in files.items():
                (folder / (material + '.image')).write_bytes(raw)
            self.cancel = threading.Event()
            self.run = dict(id=ident, draft=draft['id'], request=request, materials=draft['items'], state='planning',
                            started_at=time.time(), finished_at=None, plan=None, steps=[], results=[], error='',
                            generation=begin('ImageComposition'))
            self._save()
            self.thread = threading.Thread(target=self._produce, args=(self.run, files, folder, self.cancel), daemon=True)
            self.thread.start()
            return self.status(ident)

    def _save(self):
        with self.lock:
            atomic_json(self.runs / self.run['id'] / 'run.json', self.run)

    def status(self, ident=None):
        with self.lock:
            if ident is None or self.run and ident == self.run['id']:
                value = copy.deepcopy(self.run) if self.run else dict(state='none')
            else:
                if not isinstance(ident, str) or not IDENT.fullmatch(ident) or not (self.runs / ident / 'run.json').is_file():
                    raise ValueError(ui_text('composition.panel_invalid_run_text'))
                value = json.loads((self.runs / ident / 'run.json').read_text(encoding='utf-8'))
            if value.get('started_at'):
                value['elapsed_sec'] = round((value.get('finished_at') or time.time()) - value['started_at'])
            return value

    def history(self):
        paths = sorted(self.runs.glob('*/run.json'), key=lambda path: path.stat().st_mtime, reverse=True)[:30]
        rows = []
        for path in paths:
            value = self.status(path.parent.name)
            rows.append({key:value.get(key) for key in ('id', 'state', 'request', 'started_at', 'results')})
        return dict(entries=rows)

    def result_file(self, ident, index):
        value = self.status(ident)
        if type(index) is not int or not 0 <= index < len(value.get('results', [])):
            raise ValueError(ui_text('composition.panel_invalid_run_text'))
        return self.runs / value['id'] / ('variant-%d.png' % (index + 1))

    def stop(self):
        if not self.busy():
            return False
        self.cancel.set()
        self.brain.stop()
        if self.cancel_preparation:
            self.cancel_preparation()
        return True

    def cancel_run(self, ident):
        if not self.run or self.run['id'] != ident:
            raise ValueError(ui_text('composition.panel_invalid_run_text'))
        return dict(cancelled=self.stop())

    def _produce(self, run, files, folder, cancel):
        try:
            with self.gpu.acting_for('composition'), scope(run['generation']):
                caps = self.studio.capabilities()
                plan = self.brain.make_plan(run['request'], caps, run['materials'], files, cancel)
                with self.lock:
                    run.update(plan=plan, state='running', estimate_minutes=estimate_minutes(plan))
                    self._save()
                seed = secrets.randbelow(2 ** 31)
                for variant in plan['variants']:
                    previous, receipt = None, None
                    for index, step in enumerate(variant['steps'], 1):
                        if cancel.is_set():
                            raise DirectorCancelled()
                        entry = dict(variant=variant['number'], step=index, engine=step['engine'], state='running')
                        with self.lock:
                            run['steps'].append(entry)
                            self._save()
                        raws = [previous if source == 'previous' else files[source] for source in step['sources']]
                        inputs, note = prepare(raws, plan['canvas']) if raws else ([], '')
                        previous, receipt = self.studio.image(step, inputs, plan['canvas'], seed + variant['number'] * 7919 + index, cancel, note=note)
                        if cancel.is_set():
                            raise DirectorCancelled()
                        with self.lock:
                            entry.update(state='done', job=receipt['job'])
                            self._save()
                    (folder / ('variant-%d.png' % variant['number'])).write_bytes(previous)
                    with self.lock:
                        run['results'].append(dict(number=variant['number'], title=variant['title'], **receipt))
                        self._save()
                run['state'] = 'done'
        except Exception as error:
            run['state'] = 'cancelled' if cancel.is_set() or isinstance(error, DirectorCancelled) else 'error'
            if run['state'] == 'error':
                run['error'] = str(error)[:1000]
        finally:
            with self.lock:
                run['finished_at'] = time.time()
                for entry in run['steps']:
                    if entry['state'] == 'running':
                        entry['state'] = run['state']
                finish(run['generation'], state=run['state'])
                self._save()
