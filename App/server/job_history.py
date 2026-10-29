"""Bounded terminal receipts; generation never depends on history storage."""
from ui_strings import source as ui_text
import copy
import json
import os
from pathlib import Path
import threading
import uuid
from generation_identity import ensure as generation_ensure, finish as generation_finish


class JobHistory:
    LIMIT = 100
    MAX_BYTES = 5 * 1024 * 1024
    FIELDS = ('engine', 'mode', 'selected_mode', 'operation', 'seed', 'quality',
              'steps', 'upscale', 'detail_strength', 'control_type', 'control_strength', 'input_megapixels', 'output_scale',
              'native_megapixels', 'reference_count', 'layout_boxes', 'negative_prompt', 'use_uncensored_lora', 'submitted_at', 'filename_base', 'original_prompt',
              'workflow_sha256', 'workflow_version', 'result_node_ids',
              'graph_sha256', 'sampler_parameters', 'expansion', 'expansion_model', 'expanded_prompt', 'input_files', 'comfy_files', 'generation')

    def __init__(self, path, portable_root=None):
        self.path = Path(path)
        self.portable_root = Path(portable_root).resolve() if portable_root else None
        self.lock = threading.RLock()
        self.rows = {}
        self.deleted = set()  # In-flight status/watcher requests must not resurrect a removed receipt.
        self.warning = ''
        self.writable = True
        try:
            if not self.path.exists():
                return
            if self.path.stat().st_size > self.MAX_BYTES:
                raise ValueError('History exceeds size limit')
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if raw.get('schema_version') != 1 or not isinstance(raw.get('entries'), list) or len(raw['entries']) > self.LIMIT:
                raise ValueError('Invalid history document')
            for row in raw['entries']:
                self._validate(row)
                generation_ensure(row, 'Image', row['prompt_id'], row.get('submitted_at'), row.get('elapsed_sec'))
                self.rows[row['prompt_id']] = row
        except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError):
            self.rows = {}
            self.writable = False
            self.warning = ui_text('common.job_history_init_read_original_file_unchanged_generation_remains_failed')

    def _resolve_file(self, value):
        if not isinstance(value, str) or not value or '\x00' in value or len(value) > 32768 or Path(value).suffix.lower() not in ('.png', '.jpg', '.jpeg'):
            raise ValueError('Invalid output path')
        path = Path(value)
        if path.is_absolute():
            return path
        if self.portable_root is None or ':' in value or '..' in path.parts:
            raise ValueError('Invalid relative output path')
        path = (self.portable_root / path).resolve()
        if not path.is_relative_to(self.portable_root):
            raise ValueError('Output path escapes portable root')
        return path

    def _stored_file(self, value):
        path = self._resolve_file(value).resolve()
        if self.portable_root and path.is_relative_to(self.portable_root):
            return path.relative_to(self.portable_root).as_posix()
        return str(path)

    def _validate(self, row):
        if not isinstance(row, dict) or str(uuid.UUID(row['prompt_id'])) != row['prompt_id']:
            raise ValueError('Invalid receipt ID')
        if row.get('state') not in ('done', 'cancelled'):
            raise ValueError('Invalid terminal state')
        if type(row.get('elapsed_sec')) is not int or row['elapsed_sec'] < 0:
            raise ValueError('Invalid elapsed time')
        files = row.get('saved_files')
        if not isinstance(files, list) or len(files) > 16:
            raise ValueError('Invalid saved outputs')
        for value in files:
            self._resolve_file(value)
        if len(json.dumps(row, ensure_ascii=False).encode('utf-8')) > 40000:
            raise ValueError('Receipt exceeds size limit')

    def record(self, ident, state, metadata, saved_files=(), elapsed_sec=0):
        for detail in metadata.get('_technical', []):
            generation_finish(detail, state=state)
        generation_finish(generation_ensure(metadata, 'Image', ident, metadata.get('submitted_at')), elapsed_sec, state=state)
        row = {k: copy.deepcopy(metadata[k]) for k in self.FIELDS if k in metadata}
        row.update(prompt_id=ident, state=state, saved_files=[self._stored_file(p) for p in saved_files], elapsed_sec=max(0, int(elapsed_sec)))
        self._validate(row)
        with self.lock:
            if ident in self.deleted:
                return
            self.rows.pop(ident, None)
            self.rows[ident] = row
            while len(self.rows) > self.LIMIT:
                self.rows.pop(next(iter(self.rows)))
            if not self.writable:
                return
            temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps({'schema_version': 1, 'entries': list(self.rows.values())}, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(temporary, self.path)
                self.warning = ''
            except OSError:
                self.warning = ui_text('common.job_history_record_available_session_but_not_saved_disk')
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def remove(self, ident):
        from job_delete import write_receipt
        with self.lock:
            if not self.writable:
                raise ValueError(ui_text('common.job_history_remove_not_writable_nothing_deleted'))
            remaining = {key: row for key, row in self.rows.items() if key != ident}
            write_receipt(self.path, {'schema_version': 1, 'entries': list(remaining.values())})
            self.rows = remaining
            self.deleted.add(ident)
            self.warning = ''

    def get(self, ident):
        with self.lock:
            row = copy.deepcopy(self.rows.get(ident))
            if row:
                row['saved_files'] = [str(self._resolve_file(p)) for p in row['saved_files']]
            return row

    def completed(self):
        with self.lock:
            result = []
            for row in reversed(list(self.rows.values())):
                if row['state'] != 'done':
                    continue
                item = copy.deepcopy(row)
                item['image_indices'] = [i for i, value in enumerate(row['saved_files']) if self._resolve_file(value).is_file()]
                item.pop('saved_files', None)
                result.append(item)
            return result
