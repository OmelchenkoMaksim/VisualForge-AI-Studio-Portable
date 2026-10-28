"""Atomic metadata, portable paths and publication of finished voiceover files."""
from ui_strings import source as ui_text
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

REPLACE_ATTEMPTS = 25


def write_json(path, value, sleep=time.sleep):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                # Windows refuses to replace a file another process holds open: the bridge reads progress.json on every
                # status poll while the worker rewrites it; the reader lets go within milliseconds.
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                sleep(0.02)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default


def stored_path(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def resolve_path(value, root):
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    path = (root / path).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError(ui_text('voiceover.storage_path_outside_portable_folder_error'))
    return path


def safe_title(title):
    """A readable file name part: letters of any alphabet, digits, spaces and dashes only."""
    words = re.sub(r'[^\w\- ]+', ' ', title or '').split()
    return ' '.join(words)[:60].strip() or 'Voiceover'


def publish(source, folder, row, root):
    """Copy a finished file with exclusive creation (never overwrite) and write its JSON sidecar next to it."""
    source = Path(source)
    filename = f"{safe_title(row.get('title'))} - {row['id']}{source.suffix.lower()}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    created = False
    try:
        with target.open('xb') as output, source.open('rb') as stream:
            created = True
            shutil.copyfileobj(stream, output, 1024 * 1024)
    except Exception:
        # This exact new file belongs to the current job; an existing output is never removed.
        if created and target.exists():
            target.unlink()
        raise
    row.update(filename=filename, output=stored_path(target, root), saved_path=str(target))
    try:
        with target.with_suffix('.json').open('x', encoding='utf-8') as stream:
            json.dump(row, stream, ensure_ascii=False, indent=2)
    except Exception:
        target.unlink()
        raise
    return target
