"""The installer engine: status, planning, installation and verification of catalog components.

    engine = Engine(root)                     # root = the installation folder (contains App/, Runtime/, Models/, UserData/)
    engine.status()                           # which components are present
    engine.install(engine.select(models=[...]))   # software is always included
    engine.verify(ids)                        # re-hash everything (slow)

Everything is resumable: finished downloads and finished model files are kept, partial files continue where they stopped.
"""
from ui_strings import source as ui_text
import json
import os
from pathlib import Path
import shutil
import threading
import time

from . import component_catalog as catalog_module
from . import download_transport as net, install_steps as steps
from .install_steps import Context, StepError

STATE_NAME = 'UserData/Settings/components.state.json'
DOWNLOADS = 'UserData/Cache/Downloads'
SPACE_MARGIN = 1.08


class ProvisionError(Exception):
    pass


class NotEnoughSpace(ProvisionError):
    pass


class RecoverySkipped(net.DownloadError):
    """A missing artifact was explicitly skipped or its recovery prompt expired."""


class Busy(ProvisionError):
    """Another installer is running."""


def default_root():
    env = os.environ.get('VISUALFORGE_PORTABLE_ROOT')
    return Path(env) if env else Path(__file__).resolve().parents[3]


def default_catalog(root):
    return Path(root) / 'App/config/component-catalog.json'


class _Lock:
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise Busy('Another VisualForge installer is already running.') from None
        return self

    def __exit__(self, *exc):
        if self.handle:
            try:
                if os.name == 'nt':
                    import msvcrt
                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                self.handle.close()
                self.handle = None


class Engine:
    def __init__(self, root=None, catalog_path=None, emit=None, cancel=None, environ=None, opener=None, run=None, keep_downloads=False, ollama_command=None):
        self.root = Path(root or default_root())
        self.catalog = catalog_module.load(catalog_path or default_catalog(self.root))
        self.by_id = catalog_module.index(self.catalog)
        self._emit = emit or (lambda event: None)
        self.cancel_event = cancel if isinstance(cancel, threading.Event) else threading.Event()
        self.environ = environ
        self.opener = opener
        self.run = run
        self.keep_downloads = keep_downloads
        self.ollama_command = ollama_command
        self.recover = None
        self.active_component = None

    # ---- helpers ------------------------------------------------------------------------------------------------
    def emit(self, type, **data):
        self._emit(dict(type=type, time=time.time(), **data))

    def cancelled(self):
        return self.cancel_event.is_set()

    def cancel(self):
        self.cancel_event.set()

    @property
    def state_path(self):
        return self.root / STATE_NAME

    def read_state(self):
        try:
            data = json.loads(self.state_path.read_text(encoding='utf-8-sig'))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {'schema_version': 1, 'components': {}}

    def write_state(self, state):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(self.state_path.name + '.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(temporary, self.state_path)

    def first_run_done(self):
        return bool(self.read_state().get('first_run_done'))

    def mark_first_run(self, selection=None):
        state = self.read_state()
        state['first_run_done'] = True
        if selection is not None:
            state['selection'] = sorted(selection)
        self.write_state(state)

    # ---- status -------------------------------------------------------------------------------------------------
    def component_status(self, component, state=None):
        """installed / partial / missing with the number of bytes still to fetch (stat only, never hashes)."""
        state = self.read_state() if state is None else state
        present = [self.root / p for p in component.get('present', [])]
        files = component.get('files', [])
        remaining = 0
        have_files = 0
        for item in files:
            path = self.root / item['path']
            if path.is_file() and path.stat().st_size == item['bytes']:
                have_files += 1
            else:
                remaining += item['bytes']
        present_ok = [p.is_file() or p.is_dir() for p in present]
        everything = len(files) + len(present)
        got = have_files + sum(present_ok)
        if everything == 0:
            result = 'installed' if component['id'] in state.get('components', {}) else 'missing'
        elif got == everything:
            result = 'installed'
        elif got == 0:
            result = 'missing'
        else:
            result = 'partial'
        if result == 'installed' and component['id'] in state.get('in_progress', {}):
            # Steps unpack and pip-install straight into the final folders, so the presence markers can exist while a later
            # step never ran (power loss, crash, cancel). Such a component stays partial until its steps finish once.
            result = 'partial'
        if result != 'installed' and not files:
            remaining = catalog_module.download_bytes(component)
        return dict(id=component['id'], state=result, remaining_bytes=0 if result == 'installed' else remaining)

    def status(self):
        state = self.read_state()
        return {component['id']: self.component_status(component, state) for component in self.catalog['components']}

    def describe(self):
        """Modules with their components and current state, for menus and the in-app Components panel."""
        status = self.status()
        modules = []
        for module in self.catalog['modules']:
            rows = []
            for ident in module['components']:
                component = self.by_id[ident]
                row = {key: component.get(key) for key in ('id', 'kind', 'title', 'description', 'license', 'group', 'requires', 'note')}
                row.update(download_bytes=catalog_module.download_bytes(component), disk_bytes=component.get('disk_bytes') or catalog_module.download_bytes(component),
                           state=status[ident]['state'], remaining_bytes=status[ident]['remaining_bytes'])
                rows.append(row)
            modules.append(dict(id=module['id'], title=module['title'], summary=module.get('summary', ''), components=rows))
        return modules

    # ---- selection ----------------------------------------------------------------------------------------------
    def software_ids(self):
        return [c['id'] for c in self.catalog['components'] if c['kind'] == 'software']

    def model_ids(self, module=None):
        return [c['id'] for c in self.catalog['components'] if c['kind'] == 'model' and (module is None or c['module'] == module)]

    def select(self, models=(), all_models=False, modules=(), required=True):
        """Software is always part of the selection; models are opt-in, except the required groups (Gemma) that have no member yet."""
        chosen = list(self.software_ids())
        if all_models:
            chosen += self.model_ids()
        for module in modules:
            chosen += self.model_ids(module)
        for ident in models:
            if ident not in self.by_id:
                raise ProvisionError('Unknown component: ' + ident)
            chosen.append(ident)
        if required:
            for group in self.required_status():
                if not group['satisfied'] and not any(option['id'] in chosen for option in group['options']):
                    chosen.append(group['default'])
        return list(dict.fromkeys(chosen))

    # ---- required models ------------------------------------------------------------------------------------------
    def required_status(self, exclude=()):
        """The catalog's `required` groups (a model family the studio cannot do without) and whether the private store has a member.

        A group counts as satisfied by ANY complete, enabled model of that family in the private Ollama store (an official
        pull, a mirror, a local GGUF or a build copied from the user's own Ollama), not only by the catalog options.
        `exclude` lists model names to ignore, to ask whether a removal would leave the group empty."""
        from . import ollama_tools
        groups = self.catalog.get('required', [])
        if not groups:
            return []
        try:
            from model_policy import ModelPolicy
            policy = ModelPolicy(self.root)
            enabled = lambda name: policy.enabled('text', name)
        except (ImportError, ValueError, OSError, KeyError):
            enabled = lambda name: True
        models = ollama_tools.store_models(self.root / 'Models/Text/ollama')
        state = self.read_state()
        result = []
        for group in groups:
            family = group['family'].lower()
            installed = [row['name'] for row in models if row['complete'] and row['family'].lower().startswith(family) and row['name'] not in exclude and enabled(row['name'])]
            options = []
            for ident in group['options']:
                component = self.by_id[ident]
                options.append(dict(id=ident, title=component['title'], description=component.get('description', ''), download_bytes=catalog_module.download_bytes(component),
                                    state=self.component_status(component, state)['state'], sources=[ollama_tools.source_host(name) for step in component.get('after', [])
                                    if step.get('op') == 'ollama_pull' for name in [step['model']] + list(step.get('alternatives', []))]))
            result.append(dict(id=group['id'], title=group['title'], description=group.get('description', ''), family=group['family'], default=group['default'],
                               satisfied=bool(installed), installed=installed, options=options))
        return result

    def local_gemma_component(self, path, projector=None):
        """A dynamic component that creates a model of the required family from a local GGUF file (and optional mmproj file)."""
        from . import ollama_tools
        path = Path(str(path).strip().strip('"'))
        name = ollama_tools.local_model_name(path)
        size = path.stat().st_size if path.is_file() else 0
        if projector and Path(str(projector).strip().strip('"')).is_file():
            size += Path(str(projector).strip().strip('"')).stat().st_size
        ident = 'text.local.' + name
        step = dict(op='ollama_gguf', model=name + ':latest', file=str(path), projector=str(projector).strip() if projector else None)
        row = dict(id=ident, module='text', kind='model', group='Text', title='%s (local file)' % name, description=str(path), requires=['text.runtime'],
                   download_bytes=size, disk_bytes=size, present=['Models/Text/ollama/manifests/registry.ollama.ai/library/%s/latest' % name], after=[step])
        if ident in self.by_id:
            self.by_id[ident].update(row)
        else:
            self._add(row)
        return ident

    def text_model_component(self, tag):
        """A Text model from the public Ollama registry that is not in the catalog (any tag the registry knows)."""
        from .ollama_tools import split_name
        host, namespace, model, version = split_name(tag)
        ident = 'text.model.' + tag.replace(':', '-').replace('/', '_')
        if ident not in self.by_id:
            self._add(dict(id=ident, module='text', kind='model', group='Text', title=tag, description='Ollama registry model', requires=['text.runtime'],
                           download_bytes=0, present=['Models/Text/ollama/manifests/%s/%s/%s/%s' % (host, namespace, model, version)],
                           after=[dict(op='ollama_pull', model=tag)]))
        return ident

    def ollama_import_component(self, name, source=None):
        """Copy a model out of the user's own Ollama store into the private one."""
        from .ollama_tools import split_name
        host, namespace, model, version = split_name(name)
        source = source or os.environ.get('OLLAMA_MODELS') or str(Path(os.environ.get('USERPROFILE', '~')).expanduser() / '.ollama' / 'models')
        ident = 'text.import.' + name.replace(':', '-').replace('/', '_')
        if ident not in self.by_id:
            self._add(dict(id=ident, module='text', kind='model', group='Text', title='%s (copied from your Ollama)' % name, description=source, requires=['text.runtime'],
                           download_bytes=0, present=['Models/Text/ollama/manifests/%s/%s/%s/%s' % (host, namespace, model, version)],
                           after=[dict(op='ollama_import', model=name, source=source)]))
        return ident

    def _add(self, component):
        self.catalog['components'].append(component)
        self.by_id[component['id']] = component
        for module in self.catalog['modules']:
            if module['id'] == component['module']:
                module['components'].append(component['id'])

    def plan(self, ids, force=False):
        order = catalog_module.closure(self.catalog, ids)
        status = self.status()
        todo = [i for i in order if force or status[i]['state'] != 'installed']
        download = sum(status[i]['remaining_bytes'] if self.by_id[i].get('files') else catalog_module.download_bytes(self.by_id[i]) for i in todo)
        disk = sum(self._disk_need(self.by_id[i], status[i]) for i in todo)
        return dict(order=order, todo=todo, download_bytes=download, disk_bytes=disk)

    def _disk_need(self, component, status):
        if component.get('files'):
            return status['remaining_bytes']
        return max(component.get('disk_bytes') or 0, catalog_module.download_bytes(component)) + catalog_module.download_bytes(component)

    def check_space(self, plan):
        self.root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.root).free
        need = int(plan['disk_bytes'] * SPACE_MARGIN)
        if need > free:
            raise NotEnoughSpace('Not enough free disk space on %s: about %.1f GB needed, %.1f GB free.' % (self.root.drive or self.root, need / 1e9, free / 1e9))

    # ---- installation -------------------------------------------------------------------------------------------
    def install(self, ids, force=False, check_space=True):
        """Install the listed components (and what they require). Returns {id: 'installed'|'skipped'|'failed: reason'}."""
        plan = self.plan(ids, force)
        self.emit('plan', todo=plan['todo'], download_bytes=plan['download_bytes'], disk_bytes=plan['disk_bytes'])
        results = {i: 'skipped' for i in plan['order'] if i not in plan['todo']}
        if not plan['todo']:
            return results
        if check_space:
            self.check_space(plan)
        with _Lock(self.root / 'UserData/Cache/provision.lock'):
            failed = set()
            for number, ident in enumerate(plan['todo'], 1):
                component = self.by_id[ident]
                blocker = next((r for r in component.get('requires', []) if r in failed), None)
                if blocker:
                    results[ident] = 'failed: requires %s which failed' % blocker
                    failed.add(ident)
                    self.emit('component_failed', id=ident, error=results[ident])
                    continue
                if self.cancelled():
                    results[ident] = 'failed: cancelled'
                    failed.add(ident)
                    continue
                self.emit('component_start', id=ident, title=component['title'], index=number, total=len(plan['todo']), kind=component['kind'])
                try:
                    self._install_one(component)
                    results[ident] = 'installed'
                    self._record(component)
                    self.emit('component_done', id=ident)
                except net.Cancelled:
                    results[ident] = 'failed: cancelled'
                    failed.add(ident)
                    self.emit('component_failed', id=ident, error='cancelled')
                except Exception as error:  # one broken component must not stop the others; the message says what happened
                    results[ident] = 'failed: %s' % (error if isinstance(error, (net.DownloadError, StepError, ProvisionError, catalog_module.CatalogError))
                                                     else '%s: %s' % (type(error).__name__, error))
                    failed.add(ident)
                    self.emit('component_failed', id=ident, error=str(error))
        self.emit('finished', results=results)
        return results

    def _record(self, component):
        state = self.read_state()
        state.setdefault('components', {})[component['id']] = dict(installed_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'), catalog_version=self.catalog.get('catalog_version'))
        state.get('in_progress', {}).pop(component['id'], None)
        self.write_state(state)

    def _mark_in_progress(self, component):
        """Written before the first step touches the final folders and cleared by _record, so an interrupted run stays visible."""
        state = self.read_state()
        state.setdefault('in_progress', {})[component['id']] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        self.write_state(state)

    def _progress(self, name):
        started = time.monotonic()
        last = [0, started]

        def report(done, total):
            now = time.monotonic()
            speed = (done - last[0]) / max(now - last[1], 1e-6) if now > last[1] else 0
            last[0], last[1] = done, now
            self.emit('progress', name=name, done=done, total=total, speed=speed)
        return report

    def _fetch(self, urls, destination, size, sha256, name):
        self.emit('download_start', name=name, bytes=size)
        from .software_bundle import bundled_source
        urls = list(urls)
        bundle = bundled_source(self.root, self.active_component, name, size, sha256)
        if bundle:
            urls.insert(0, bundle)
        while True:
            try:
                path = net.download(urls, destination, size, sha256, self._progress(name), self.cancelled, self.environ, self.opener)
                break
            except net.DiskFull:
                raise
            except net.DownloadError:
                if not self.recover:
                    raise
                choice = self.recover(dict(component=self.active_component['id'], title=self.active_component['title'],
                                           name=name, bytes=size, sha256=sha256), self.cancelled)
                if not choice:
                    raise RecoverySkipped(ui_text('components.fetch_no_source_provided_within_30_seconds') + name) from None
                if choice['kind'] == 'file':
                    try:
                        path = net.import_file(choice['value'], destination, size, sha256, self._progress(name), self.cancelled)
                        break
                    except (OSError, net.DownloadError) as error:
                        self.emit('note', text=ui_text('components.fetch_selected_file_unsuitable') + str(error))
                        urls = []
                else:
                    urls = [choice['value']]

        self.emit('download_done', name=name)
        return path

    def _install_one(self, component):
        self.active_component = component
        downloads_dir = self.root / DOWNLOADS / component['id']
        paths, skipped = {}, []
        for item in component.get('downloads', []):
            try:
                paths[item['id']] = self._fetch(item['urls'], downloads_dir / item['filename'], item['bytes'], item['sha256'], item['filename'])
            except RecoverySkipped:
                skipped.append(item['filename'])
        for item in component.get('files', []):
            target = self.root / item['path']
            if target.is_file() and target.stat().st_size == item['bytes']:
                continue
            try:
                self._fetch(item['urls'], target, item['bytes'], item['sha256'], Path(item['path']).name)
            except RecoverySkipped:
                skipped.append(Path(item['path']).name)
        if skipped:
            # Keep fetched artifacts for the next attempt, but never execute an incomplete installation.
            raise ProvisionError(ui_text('components.install_one_dependencies_skipped') + ', '.join(skipped[:8]))
        context = Context(self.root, paths, self.root / 'UserData/Cache', self.emit, self.cancelled, self.environ, self.run, self.ollama_command, self.recover, component)
        if component.get('steps') or component.get('after'):
            self._mark_in_progress(component)
        for step in list(component.get('steps', [])) + list(component.get('after', [])):
            self.emit('step', op=step['op'], id=component['id'])
            steps.run_step(context, step)
        missing = [p for p in component.get('present', []) if not (self.root / p).exists()]
        if missing:
            raise ProvisionError('Installation finished but expected files are missing: ' + ', '.join(missing[:5]))
        if downloads_dir.is_dir() and not self.keep_downloads:
            shutil.rmtree(downloads_dir, ignore_errors=True)

    # ---- verification -------------------------------------------------------------------------------------------
    def verify(self, ids=None):
        """Re-hash every model file (and check the presence list of software). Returns {id: [problems]}."""
        ids = ids or [i for i, s in self.status().items() if s['state'] == 'installed']
        problems = {}
        for ident in catalog_module.closure(self.catalog, ids):
            component = self.by_id[ident]
            found = []
            for path in component.get('present', []):
                if not (self.root / path).exists():
                    found.append('missing: ' + path)
            for item in component.get('files', []):
                path = self.root / item['path']
                if not path.is_file():
                    found.append('missing: ' + item['path'])
                    continue
                if path.stat().st_size != item['bytes']:
                    found.append('size: %s (%d, expected %d)' % (item['path'], path.stat().st_size, item['bytes']))
                    continue
                self.emit('verify', id=ident, path=item['path'])
                if net.sha256_file(path, cancel=self.cancelled) != item['sha256']:
                    found.append('sha256: ' + item['path'])
            problems[ident] = found
        return problems
