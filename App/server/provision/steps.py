"""Installation steps. Each handler takes (context, step) where `step` is a catalog dictionary with an "op" key.

All paths inside steps are relative to the installation root and are checked to stay inside it.
"""
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

from . import archives
from .catalog import CatalogError, safe_relative
from .net import sha256_file


class StepError(Exception):
    pass


class Context:
    """What a step may touch: the root, the download cache of the running component and the tools installed so far."""

    def __init__(self, root, downloads, cache, emit=None, cancel=None, environ=None, run=None, ollama_command=None):
        self.root = Path(root)
        self.downloads = downloads        # {download id: Path}
        self.cache = Path(cache)
        self.emit = emit or (lambda *a, **k: None)
        self.cancel = cancel or (lambda: False)
        self.environ = dict(os.environ if environ is None else environ)
        self.run = run or subprocess.run
        self.ollama_command = ollama_command  # tests start a stand-in server instead of Runtime/Text/ollama/ollama.exe

    def path(self, relative):
        relative = safe_relative(relative)
        path = (self.root / relative)
        try:
            path.resolve().relative_to(self.root.resolve())
        except ValueError:
            raise StepError('Path leaves the installation root: ' + relative) from None
        return path

    def temp(self):
        folder = self.cache / 'Temp'
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def child_environment(self, extra=None):
        temp = str(self.temp())
        env = {key: value for key, value in self.environ.items() if not key.upper().startswith(('PYTHON', 'PIP_', 'VIRTUAL_ENV'))}
        env.update(TEMP=temp, TMP=temp, TMPDIR=temp, PYTHONNOUSERSITE='1', PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_INPUT='1', PYTHONUTF8='1')
        env.update(extra or {})
        return env

    def seven_zip(self):
        return self.root / 'Runtime/Tools/7zr.exe'


def _download(context, step):
    try:
        return context.downloads[step['download']]
    except KeyError:
        raise StepError('Download %r was not fetched' % step.get('download')) from None


def _skipper(step):
    """Members are kept when they match `keep` (if given) and do not match `skip`."""
    keep, skip = step.get('keep') or [], step.get('skip') or []
    if not keep and not skip:
        return None
    return lambda relative: (bool(keep) and not any(fnmatch.fnmatch(relative, pattern) for pattern in keep)) or any(fnmatch.fnmatch(relative, pattern) for pattern in skip)


def op_extract(context, step):
    target = context.path(step['to'])
    count = archives.extract(_download(context, step), target, step.get('kind'), step.get('strip', 0), _skipper(step), str(context.seven_zip()), context.cancel)
    context.emit('note', text='%s: %d files' % (step['to'], count))


def op_unpack_wheels(context, step):
    """Wheels are zip files: unpacking them into a folder gives an import-only package directory (used for voice packages)."""
    target = context.path(step['to'])
    target.mkdir(parents=True, exist_ok=True)
    for ident in step['downloads']:
        path = context.downloads[ident]
        if str(path).endswith(('.tar.gz', '.tgz')):
            # sdist of a pure-Python package: take only the named package folder
            archives.extract(path, target, 'tar', 1, lambda relative, keep=step.get('sdist_keep', 'sox/'): not relative.startswith(keep))
        else:
            archives.extract(path, target, 'zip')


def op_place(context, step):
    """Put a downloaded plain file (not an archive) at its final place."""
    target = context.path(step['to'])
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_download(context, step), target)


def op_move_tree(context, step):
    source, target = context.path(step['from']), context.path(step['to'])
    if not source.is_dir():
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in sorted(source.iterdir()):
        destination = target / item.name
        if destination.exists():
            if item.is_dir() and destination.is_dir():
                _merge(item, destination)
            continue
        shutil.move(str(item), str(destination))
    shutil.rmtree(source, ignore_errors=True)


def _merge(source, target):
    for item in source.iterdir():
        destination = target / item.name
        if destination.exists():
            if item.is_dir() and destination.is_dir():
                _merge(item, destination)
            continue
        shutil.move(str(item), str(destination))


def op_copy(context, step):
    source, target = context.path(step['from']), context.path(step['to'])
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*step.get('ignore', [])) if step.get('ignore') else None)
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    elif not step.get('optional'):
        raise StepError('Nothing to copy at ' + step['from'])


def op_copy_glob(context, step):
    source, target = context.path(step['from']), context.path(step['to'])
    if not source.is_dir():
        raise StepError('Missing folder ' + step['from'])
    target.mkdir(parents=True, exist_ok=True)
    matches = sorted(source.glob(step['pattern']))
    if not matches and not step.get('optional'):
        raise StepError('No files match %s in %s' % (step['pattern'], step['from']))
    for item in matches:
        if item.is_file():
            shutil.copyfile(item, target / item.name)


def op_write_text(context, step):
    target = context.path(step['path'])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(step['content'].encode('utf-8'))


def op_write_json(context, step):
    target = context.path(step['path'])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(step['value'], ensure_ascii=False, separators=(',', ':')), encoding='utf-8')


def op_mkdir(context, step):
    context.path(step['path']).mkdir(parents=True, exist_ok=True)


def op_remove(context, step):
    target = context.path(step['path'])
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    elif target.exists():
        target.unlink()


def op_verify_files(context, step):
    for item in step['files']:
        path = context.path(item['path'])
        if not path.is_file():
            raise StepError('Expected file is missing: ' + item['path'])
        actual = sha256_file(path)
        if actual != item['sha256']:
            raise StepError('Unexpected content of %s (sha256 %s, expected %s). The source changed or the step did not apply cleanly.' % (item['path'], actual, item['sha256']))


def _python(context, step):
    python = context.path(step['python'])
    if not python.is_file():
        raise StepError('Python is not installed yet: ' + step['python'])
    return python


def _run(context, command, cwd=None, extra_env=None, what='command'):
    context.emit('note', text='$ ' + ' '.join(str(c) for c in command[:6]) + (' ...' if len(command) > 6 else ''))
    done = context.run([str(c) for c in command], cwd=str(cwd) if cwd else None, env=context.child_environment(extra_env), capture_output=True, text=True,
                       encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if done.returncode:
        tail = ((done.stderr or '') + (done.stdout or '')).strip()[-1500:]
        raise StepError('%s failed with exit code %d:\n%s' % (what, done.returncode, tail))
    return done.stdout


def op_pip_bootstrap(context, step):
    """Install pip itself from its wheel with the target interpreter (needed for the embeddable Python, which has no ensurepip)."""
    python, wheel = _python(context, step), _download(context, step)
    code = ('import sys; sys.path.insert(0, %r); from pip._internal.cli.main import main; sys.exit(main(%r))'
            % (str(wheel), ['install', '--no-index', '--no-deps', '--no-warn-script-location', '--force-reinstall', str(wheel)]))
    _run(context, [python, '-c', code], what='pip bootstrap')


def op_ensurepip(context, step):
    _run(context, [_python(context, step), '-m', 'ensurepip', '--default-pip'], what='ensurepip')


def op_pip_install(context, step):
    python = _python(context, step)
    wheels = [_download(context, {'download': ident}) for ident in step['downloads']]
    folder = sorted({str(w.parent) for w in wheels})
    command = [python, '-m', 'pip', 'install', '--no-index', '--no-deps', '--no-warn-script-location', '--no-cache-dir']
    for item in folder:
        command += ['--find-links', item]
    command += list(step.get('args', []))
    command += [str(w) for w in wheels]
    _run(context, command, what='pip install')


def op_pip_install_source(context, step):
    """Install a pinned source tree (setup.py / pyproject) with the target Python, offline and without isolation."""
    python, source = _python(context, step), context.path(step['path'])
    if not source.is_dir():
        raise StepError('Source folder is missing: ' + step['path'])
    command = [python, '-m', 'pip', 'install', '--no-index', '--no-deps', '--no-build-isolation', '--no-warn-script-location', '--no-cache-dir', str(source)]
    _run(context, command, cwd=source, what='pip install ' + step['path'])


def op_pip_check(context, step):
    out = _run(context, [_python(context, step), '-m', 'pip', 'check'], what='pip check')
    context.emit('note', text=out.strip()[:300] or 'pip check: ok')


def op_git_apply(context, step):
    git = shutil.which('git', path=context.environ.get('PATH'))
    if not git:
        raise StepError('Git is required to apply the pinned source patches. Install Git for Windows (https://git-scm.com) and run the setup again.')
    cwd = context.path(step['cwd'])
    for relative in step['patches']:
        patch = context.path(relative)
        # Outside any repository: GIT_CEILING_DIRECTORIES stops Git from picking up the checkout that contains this installation.
        env = {'GIT_CEILING_DIRECTORIES': str(cwd.parent), 'GIT_DIR': str(cwd / '.vf-no-git')}
        _run(context, [git, '-c', 'core.autocrlf=false', 'apply', '--whitespace=nowarn', '--unsafe-paths', str(patch)], cwd=cwd, extra_env=env, what='git apply ' + patch.name)


def op_crt_from_vcredist(context, step):
    """Take the app-local Microsoft C++ runtime DLLs out of the official vc_redist.x64.exe without running it."""
    from . import vcredist
    vcredist.extract(_download(context, step), context.path(step['to']), step['files'], context.temp(), context.seven_zip(), context.emit, step.get('license'))


def op_ollama_import(context, step):
    from . import ollama_tools
    ollama_tools.import_model(context, step)


def op_ollama_pull(context, step):
    from . import ollama_tools
    ollama_tools.pull(context, step)


OPS = {name[3:]: function for name, function in globals().items() if name.startswith('op_')}


def run_step(context, step):
    handler = OPS.get(step['op'])
    if handler is None:
        raise CatalogError('Unknown step op: ' + step['op'])
    if context.cancel():
        raise StepError('Cancelled')
    handler(context, step)
