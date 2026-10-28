"""Explicit model removal plans, shared-blob protection and a server-enforced confirmation delay."""
from ui_strings import source as ui_text
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from model_policy import ModelPolicy
from .engine import Busy, ProvisionError
from .ollama_tools import manifest_path, blob_path


class ModelLifecycle:
    WAIT_SECONDS = 10

    def __init__(self, manager, text_models=None, release=None, clock=time.monotonic):
        self.manager = manager
        self.root = Path(manager.root).resolve()
        self.policy = ModelPolicy(self.root)
        self.text_models = text_models or (lambda: [])
        self.release = release or (lambda: None)
        self.clock = clock
        self.challenges = {}

    def idle(self):
        if self.manager.running() or (self.manager.gpu and self.manager.gpu.snapshot()['busy']):
            raise Busy(ui_text('components.idle_finish_generation_component_installation_first'))

    def custom_models(self):
        disabled = self.policy.disabled()
        catalog_names = {step['model'] for component in self.manager.view.catalog['components'] for step in component.get('after', []) if step.get('op') == 'ollama_pull'}
        return [dict(id='text:' + row['name'], kind='model', title=row['label'], description=ui_text('components.custom_models_local_ollama_build_no_public_source'),
                     state='installed' if not row.get('missing') else 'partial', download_bytes=row.get('disk_size', row.get('size', 0)),
                     disk_bytes=row.get('disk_size', row.get('size', 0)), remaining_bytes=0, enabled='text:' + row['name'] not in disabled,
                     removable=True, local_only=True) for row in self.text_models() if row['name'] not in catalog_names]

    def component(self, ident):
        if not isinstance(ident, str) or len(ident) > 240:
            raise ValueError(ui_text('components.component_invalid_model'))
        if ident.startswith('text:'):
            name = ident[5:]
            if not any(row['name'] == name for row in self.text_models()):
                raise ValueError(ui_text('components.component_local_model_not_found'))
            return dict(id=ident, title=name, kind='model', files=[], after=[dict(op='ollama_pull', model=name)])
        component = self.manager.view.by_id.get(ident)
        if not component or component['kind'] != 'model':
            raise ValueError(ui_text('components.component_models_managed_not_software_runtimes'))
        return component

    def toggle(self, ident, enabled):
        self.idle()
        component = self.component(ident)
        if not isinstance(enabled, bool):
            raise ValueError(ui_text('components.toggle_expected_enable_disable'))
        if enabled and not ident.startswith('text:'):
            if self.manager.view.plan([component['id']])['todo']:
                self.manager.repair(ident)
                return dict(id=ident, enabled=False, repairing=True)
        if not enabled:
            self.release()
        self.policy.set_enabled(ident, enabled)
        return dict(id=ident, enabled=enabled)

    def confined(self, relative, area):
        candidate = self.root / relative
        # resolve() alone would hide a junction that points at another model inside the same drive.
        if not candidate.is_relative_to(self.root) or not candidate.is_relative_to(self.root / area):
            raise ValueError(ui_text('components.confined_deletion_path_outside_model_scope'))
        for part in (candidate, *candidate.parents):
            if part == self.root:
                break
            if part.exists() or part.is_symlink():
                info = part.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                    raise ValueError(ui_text('components.confined_deletion_through_link_junction_prohibited'))
        resolved = candidate.resolve()
        if not resolved.is_relative_to((self.root / area).resolve()):
            raise ValueError(ui_text('components.confined_deletion_path_escaped_model_folder'))
        return resolved

    def _ollama_files(self, name):
        base = self.root / 'Models/Text/ollama'
        manifest = manifest_path(base, name)
        self.confined(manifest.relative_to(self.root), 'Models')
        if not manifest.is_file():
            return set(), set()
        def digests(path):
            self.confined(path.relative_to(self.root), 'Models')
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError(ui_text('components.digests_unexpectedly_large_ollama_manifest'))
            data = json.loads(path.read_text('utf-8'))
            values = [data['config']['digest']] + [row['digest'] for row in data['layers']]
            if not all(re.fullmatch(r'sha256:[0-9a-f]{64}', value) for value in values):
                raise ValueError(ui_text('components.digests_invalid_ollama_digest'))
            return set(values)
        owned, shared = digests(manifest), set()
        manifests = base / 'manifests'
        # Inspect manifest names only; never recurse through the large blob directory.
        for folder, directories, files in os.walk(manifests, followlinks=False):
            for directory in directories:
                self.confined((Path(folder) / directory).relative_to(self.root), 'Models')
            for filename in files:
                other = Path(folder) / filename
                if other != manifest:
                    shared.update(digests(other))
        return {manifest, *(blob_path(base, value) for value in owned - shared)}, owned & shared

    def plan(self, ident):
        component = self.component(ident)
        by_id = self.manager.view.by_id
        def depends(row, seen):
            for required in row.get('requires', []):
                if required == ident:
                    return True
                if required not in seen and required in by_id and depends(by_id[required], seen | {required}):
                    return True
            return False
        for other in by_id.values():
            if other['id'] != ident and other['kind'] == 'model' and depends(other, set()):
                present = [row['path'] for row in other.get('files', [])] + other.get('present', [])
                if any((self.root / path).exists() for path in present):
                    raise ValueError(ui_text('components.plan_model_used_shared_dependency_remove_dependent') % other['title'])
        files, directories, protected = set(), set(), set()
        rows = component.get('files', [])
        for row in rows:
            path = self.confined(row['path'], 'Models')
            files.update((path, path.with_name(path.name + '.part')))
        for step in component.get('after', []):
            if step['op'] == 'ollama_pull':
                extra, shared = self._ollama_files(step['model'])
                files.update(extra)
                protected.update(shared)
            elif step['op'] == 'write_text':
                files.add(self.confined(step['path'], 'Models'))
        if rows:
            common = Path(os.path.commonpath([str(self.root / row['path']) for row in rows]))
            if common.suffix or len(rows) == 1:
                common = common.parent
            if len(common.relative_to(self.root).parts) >= 3:
                other_paths = [self.root / row['path'] for other in self.manager.view.catalog['components'] if other['id'] != ident for row in other.get('files', [])]
                if not any(path.is_relative_to(common) for path in other_paths):
                    directories.add(self.confined(common.relative_to(self.root), 'Models'))
        cache = self.confined('UserData/Cache/Downloads/' + ident, 'UserData/Cache/Downloads') if not ident.startswith('text:') else None
        if cache:
            directories.add(cache)
        empty_dirs = set()
        for directory in directories:
            if not directory.is_dir():
                continue
            area = 'Models' if directory.is_relative_to(self.root / 'Models') else 'UserData/Cache/Downloads'
            for folder, folders, names in os.walk(directory, followlinks=False):
                current = self.confined(Path(folder).relative_to(self.root), area)
                empty_dirs.add(current)
                for child in folders:
                    self.confined((current / child).relative_to(self.root), area)
                for name in names:
                    files.add(self.confined((current / name).relative_to(self.root), area))
        identities = []
        for path in sorted(files):
            area = 'Models' if path.is_relative_to(self.root / 'Models') else 'UserData/Cache/Downloads'
            self.confined(path.relative_to(self.root), area)
            if path.is_file():
                info = path.stat()
                identities.append((path.relative_to(self.root).as_posix(), info.st_size, info.st_mtime_ns))
        fingerprint = hashlib.sha256(json.dumps(identities).encode()).hexdigest()
        return dict(id=ident, title=component['title'], files=identities, directories=sorted(empty_dirs, key=lambda p:len(p.parts), reverse=True),
                    bytes=sum(row[1] for row in identities), shared_files=len(protected), fingerprint=fingerprint)

    def prepare(self, ident):
        self.idle()
        plan = self.plan(ident)
        now = self.clock()
        self.challenges = {key:value for key,value in self.challenges.items() if value['expires'] > now}
        if len(self.challenges) >= 16:
            raise Busy(ui_text('components.prepare_too_many_pending_deletion_confirmations'))
        token = secrets.token_urlsafe(24)
        self.challenges[token] = dict(id=ident, ready=now+self.WAIT_SECONDS, expires=now+300, fingerprint=plan['fingerprint'])
        return dict(id=ident, title=plan['title'], token=token, wait_seconds=self.WAIT_SECONDS, bytes=plan['bytes'],
                    files=len(plan['files']), shared_files=plan['shared_files'], examples=[row[0] for row in plan['files'][:8]],
                    note=ui_text('components.prepare_model_weights_own_cache_deleted_shared'))

    def remove(self, token, confirmation):
        self.idle()
        challenge = self.challenges.get(token) if isinstance(token, str) else None
        if not challenge or self.clock() >= challenge['expires']:
            raise ValueError(ui_text('components.remove_confirmation_expired_open_deletion_again'))
        if self.clock() < challenge['ready'] or confirmation != 'REMOVE':
            raise ValueError(ui_text('components.remove_wait_10_seconds_type'))
        self.release()
        plan = self.plan(challenge['id'])
        if plan['fingerprint'] != challenge['fingerprint']:
            self.challenges.pop(token, None)
            raise ValueError(ui_text('components.remove_file_set_changed_review_new_deletion'))
        self.challenges.pop(token)
        self.policy.set_enabled(plan['id'], False)
        deleted = 0
        try:
            for relative, size, modified in plan['files']:
                area = 'Models' if relative.startswith('Models/') else 'UserData/Cache/Downloads'
                path = self.confined(relative, area)
                info = path.stat()
                if (info.st_size, info.st_mtime_ns) != (size, modified):
                    raise ValueError(ui_text('components.remove_file_changed_during_deletion'))
                path.unlink()
                deleted += 1
            for directory in plan['directories']:
                try:
                    directory.rmdir()  # only empty, previously checked model/cache directories
                except OSError:
                    pass
        except (OSError, ValueError) as error:
            raise ProvisionError(ui_text('components.remove_files_deleted_model_disabled_remaining_preserved') % (deleted, len(plan['files']), error)) from error
        return dict(id=plan['id'], removed=True, files=deleted, bytes=plan['bytes'], shared_files=plan['shared_files'])
