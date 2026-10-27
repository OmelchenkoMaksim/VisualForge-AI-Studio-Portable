"""Atomic metadata and portable output paths for Music."""
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
                # Windows refuses to replace a file another process holds open (the bridge reads progress.json on every
                # status poll while the worker rewrites it each step); the reader closes it within milliseconds.
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
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Путь Music выходит за пределы portable-папки')
    return path

def publish(source, folder, row, root):
    """Copy only a successful WAV with exclusive creation and a unique English name."""
    words = re.findall(r'[A-Za-z0-9]+', row['caption'])[:8]
    title = ' '.join(words)[:70] or ('Instrumental' if row['mode'] == 'instrumental' else 'Song')
    filename = f"{title} - {row['seed']} - {row['id']}.wav"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    created = False
    try:
        with target.open('xb') as output, source.open('rb') as stream:
            created = True
            shutil.copyfileobj(stream, output)
    except Exception:
        # This exact new file belongs to the current job; never remove an existing output.
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
