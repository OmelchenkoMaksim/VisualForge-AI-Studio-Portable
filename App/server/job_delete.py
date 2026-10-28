"""Delete explicitly owned job artifacts, without traversing directory links."""
from ui_strings import source as ui_text
import json
import os
from pathlib import Path
import stat
import uuid


def write_receipt(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def plain_path(path):
    """Check lexical ancestors before resolving: resolve() would hide a junction escape."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError(ui_text('common.job_delete_plain_path_deletion_through_symbolic_link_junction_prohibited') + str(item))
    return path


class DeletePlan:
    def __init__(self):
        self.files = set()
        self.directories = set()

    def file(self, path):
        path = plain_path(path)
        if path.exists() and not path.is_file():
            raise ValueError(ui_text('common.job_delete_file_folder_found_instead_task') + str(path))
        self.files.add(path)

    def tree(self, path):
        path = plain_path(path)
        if not path.exists():
            return
        if not path.is_dir():
            raise ValueError(ui_text('common.job_delete_tree_file_found_instead_task_folder') + str(path))
        # Inventory first; a link anywhere rejects the entire plan before the first unlink.
        for folder, directories, files in os.walk(path, followlinks=False):
            self.directories.add(plain_path(folder))
            for name in directories:
                plain_path(Path(folder) / name)
            for name in files:
                self.file(Path(folder) / name)

    def sidecar(self, path, key, value):
        path = plain_path(path)
        if not path.exists():
            return
        if path.stat().st_size > 5 * 1024 * 1024:
            raise ValueError(ui_text('common.job_delete_sidecar_metadata_file_too_large') + str(path))
        try:
            document = json.loads(path.read_text(encoding='utf-8-sig'))
        except (ValueError, UnicodeError):
            raise ValueError(ui_text('common.job_delete_sidecar_verify_metadata_ownership_failed') + str(path)) from None
        # A neighbouring JSON may predate this generation. Never delete it by stem alone.
        if isinstance(document, dict) and document.get(key) == value:
            self.file(path)

    def execute(self):
        # Recheck before each operation as the UI and external programs may change files.
        for path in sorted(self.files):
            plain_path(path).unlink(missing_ok=True)
        for path in sorted(self.directories, key=lambda p: len(p.parts), reverse=True):
            try:
                plain_path(path).rmdir()
            except FileNotFoundError:
                pass


def confined_file(root, relative):
    root = plain_path(root)
    if not isinstance(relative, str) or not relative or ':' in relative or Path(relative).is_absolute():
        raise ValueError(ui_text('common.job_delete_confined_file_invalid_task_path'))
    path = plain_path(root / relative)
    if path == root or not path.is_relative_to(root):
        raise ValueError(ui_text('common.job_delete_confined_file_task_outside_folder'))
    return path
