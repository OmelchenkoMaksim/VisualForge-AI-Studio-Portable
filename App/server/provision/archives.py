"""Safe extraction of zip, tar(.gz) and 7z archives (plus Windows cabinets) into a directory.

Every member path is checked to stay inside the destination; links are refused. `strip` removes leading path components
(like tar --strip-components). `skip(relative_posix_path) -> bool` can leave members out.
"""
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
import zipfile


class ArchiveError(Exception):
    pass


def _target(destination, parts):
    path = destination.joinpath(*parts)
    try:
        path.resolve().relative_to(destination.resolve())
    except ValueError:
        raise ArchiveError('Archive member escapes the destination: ' + '/'.join(parts)) from None
    return path


def _parts(name, strip):
    name = name.replace('\\', '/')
    if name.startswith('/') or (len(name) > 1 and name[1] == ':'):
        raise ArchiveError('Absolute path in archive: ' + name)
    parts = [p for p in PurePosixPath(name).parts if p not in ('', '.')]
    if '..' in parts:
        raise ArchiveError('Unsafe path in archive: ' + name)
    return parts[strip:]


def detect(path):
    name = Path(path).name.lower()
    if name.endswith('.zip') or name.endswith('.whl'):
        return 'zip'
    if name.endswith(('.tar.gz', '.tgz', '.tar')):
        return 'tar'
    if name.endswith('.7z'):
        return '7z'
    if name.endswith('.cab'):
        return 'cab'
    raise ArchiveError('Unknown archive type: ' + name)


def extract(archive, destination, kind=None, strip=0, skip=None, seven_zip=None, cancel=None):
    """Extract `archive` into `destination`; returns the number of files written."""
    archive, destination = Path(archive), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    kind = kind or detect(archive)
    if kind == 'zip':
        return _zip(archive, destination, strip, skip, cancel)
    if kind == 'tar':
        return _tar(archive, destination, strip, skip, cancel)
    if kind == '7z':
        return _seven(archive, destination, strip, skip, seven_zip)
    if kind == 'cab':
        return _cab(archive, destination)
    raise ArchiveError('Unsupported archive type: ' + kind)


def _zip(archive, destination, strip, skip, cancel):
    count = 0
    with zipfile.ZipFile(archive) as handle:
        for info in handle.infolist():
            if cancel and cancel():
                raise ArchiveError('Cancelled')
            parts = _parts(info.filename, strip)
            if not parts:
                continue
            relative = '/'.join(parts)
            if skip and skip(relative):
                continue
            mode = info.external_attr >> 16
            target = _target(destination, parts)
            if mode & 0o170000 == 0o120000:
                # A git symlink stored in a zip: keep it as a small file holding the link text. No link is ever created, and the
                # text must point inside the destination.
                _target(destination, [*parts[:-1], *_parts(handle.read(info).decode('utf-8', 'replace').strip(), 0)])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(handle.read(info))
                count += 1
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(info) as source, open(target, 'wb') as out:
                shutil.copyfileobj(source, out, 1024 * 1024)
            count += 1
    return count


def _tar(archive, destination, strip, skip, cancel):
    count = 0
    with tarfile.open(archive) as handle:
        for member in handle:
            if cancel and cancel():
                raise ArchiveError('Cancelled')
            parts = _parts(member.name, strip)
            if not parts:
                continue
            relative = '/'.join(parts)
            if skip and skip(relative):
                continue
            target = _target(destination, parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.extractfile(member) as source, open(target, 'wb') as out:
                    shutil.copyfileobj(source, out, 1024 * 1024)
                count += 1
            else:
                raise ArchiveError('Link or special file in archive: ' + relative)
    return count


def _seven(archive, destination, strip, skip, seven_zip):
    if not seven_zip or not Path(seven_zip).is_file():
        raise ArchiveError('7zr.exe is needed to unpack ' + Path(archive).name)
    with tempfile.TemporaryDirectory(prefix='vf-7z-', dir=str(destination.parent)) as temp:
        done = subprocess.run([str(seven_zip), 'x', '-y', '-bso0', '-bsp0', '-o' + temp, str(archive)], capture_output=True, text=True, encoding='utf-8', errors='replace',
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if done.returncode:
            raise ArchiveError('7zr failed (%d): %s' % (done.returncode, (done.stderr or done.stdout).strip()[-400:]))
        count = 0
        base = Path(temp)
        for path in sorted(base.rglob('*')):
            if path.is_symlink():
                raise ArchiveError('Symbolic link in archive: ' + str(path.relative_to(base)))
            if not path.is_file():
                continue
            parts = _parts(path.relative_to(base).as_posix(), strip)
            if not parts or (skip and skip('/'.join(parts))):
                continue
            target = _target(destination, parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, target) if path.stat().st_dev == target.parent.stat().st_dev else shutil.move(str(path), str(target))
            count += 1
    return count


def _cab(archive, destination):
    expand = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'expand.exe'
    if not expand.is_file():
        raise ArchiveError('expand.exe (Windows) is needed to unpack ' + Path(archive).name)
    done = subprocess.run([str(expand), '-F:*', str(archive), str(destination)], capture_output=True, text=True, encoding='utf-8', errors='replace',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if done.returncode:
        raise ArchiveError('expand.exe failed (%d): %s' % (done.returncode, (done.stdout or done.stderr).strip()[-300:]))
    return sum(1 for item in Path(destination).iterdir() if item.is_file())
