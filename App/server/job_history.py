"""Bounded terminal receipts; generation never depends on history storage."""
import copy
import json
import os
from pathlib import Path
import threading
import uuid


class JobHistory:
    LIMIT = 100
    MAX_BYTES = 5 * 1024 * 1024
    FIELDS = ('engine', 'mode', 'selected_mode', 'operation', 'seed', 'quality',
              'steps', 'upscale', 'submitted_at', 'filename_base', 'original_prompt',
              'workflow_sha256', 'workflow_version', 'result_node_ids',
              'graph_sha256', 'sampler_parameters')

    def __init__(self, path, portable_root=None):
        self.path = Path(path)
        self.portable_root = Path(portable_root).resolve() if portable_root else None
        self.lock = threading.RLock()
        self.rows = {}
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
                self.rows[row['prompt_id']] = row
        except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError):
            self.rows = {}
            self.writable = False
            self.warning = 'Историю не удалось прочитать. Исходный файл сохранён без изменений; генерация доступна.'

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
        row = {k: copy.deepcopy(metadata[k]) for k in self.FIELDS if k in metadata}
        row.update(prompt_id=ident, state=state, saved_files=[self._stored_file(p) for p in saved_files], elapsed_sec=max(0, int(elapsed_sec)))
        self._validate(row)
        with self.lock:
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
                self.warning = 'История доступна в текущем запуске, но не записана на диск. Проверьте доступ к папке UserData.'
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

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
