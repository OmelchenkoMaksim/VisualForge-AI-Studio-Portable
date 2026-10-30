"""Text models for the private Ollama: pull one from the public registry, or import one you already have in your own Ollama.

Both put the model into Models/Text/ollama (the private model store used by the Text section). The Ollama installed on the
computer is never started, changed or stopped.
"""
from ui_strings import source as ui_text
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from urllib.error import URLError
from urllib.request import Request, build_opener, ProxyHandler

from .install_steps import StepError

DEFAULT_HOST = 'registry.ollama.ai'
_OPENER = build_opener(ProxyHandler({}))


_PART = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}')


def split_name(name):
    """'gemma4:e4b' -> ('registry.ollama.ai', 'library', 'gemma4', 'e4b'); 'user/model' keeps the namespace.

    Every part ends up in a file path, so each must be a plain name (letters, digits, dot, dash, underscore)."""
    host, namespace = DEFAULT_HOST, 'library'
    rest = name.strip()
    parts = rest.split('/')
    if len(parts) == 3:
        host, namespace, rest = parts
    elif len(parts) == 2:
        namespace, rest = parts
    model, _, tag = rest.partition(':')
    tag = tag or 'latest'
    if not all(_PART.fullmatch(part) and '..' not in part for part in (host, namespace, model, tag)):
        raise StepError('Invalid model name: ' + name)
    return host, namespace, model, tag


def manifest_path(models_dir, name):
    host, namespace, model, tag = split_name(name)
    return Path(models_dir) / 'manifests' / host / namespace / model / tag


def blob_path(models_dir, digest):
    return Path(models_dir) / 'blobs' / digest.replace(':', '-')


def _free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class PrivateServer:
    """A throw-away `ollama serve` on a free local port with the private model store; used only while pulling."""

    def __init__(self, context):
        self.context = context
        self.root = context.root
        self.process = None

    def __enter__(self) -> 'PrivateServer':
        executable = self.root / 'Runtime/Text/ollama/ollama.exe'
        if not self.context.ollama_command and not executable.is_file():
            raise StepError('The private Ollama is not installed yet (text.runtime).')
        models = self.root / 'Models/Text/ollama'
        models.mkdir(parents=True, exist_ok=True)
        home = self.root / 'UserData/Cache/Text/home'
        home.mkdir(parents=True, exist_ok=True)
        port = _free_port()
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('OLLAMA') and k.upper() not in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')}
        env.update(OLLAMA_HOST='127.0.0.1:%d' % port, OLLAMA_MODELS=str(models), OLLAMA_NOPRUNE='1', OLLAMA_NO_CLOUD='1', OLLAMA_NOHISTORY='1', USERPROFILE=str(home), HOME=str(home))
        log = self.root / 'UserData/Logs/text-pull.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, 'ab') as stream:
            command = list(self.context.ollama_command) if self.context.ollama_command else [str(executable), 'serve']
            self.process = subprocess.Popen(command, cwd=str(self.root), env=env, stdout=stream, stderr=subprocess.STDOUT,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.base = 'http://127.0.0.1:%d' % port
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise StepError('The private Ollama stopped at start (exit %s); see UserData/Logs/text-pull.log' % self.process.returncode)
            try:
                with _OPENER.open(self.base + '/api/version', timeout=3):
                    return self
            except (URLError, OSError):
                time.sleep(0.4)
        raise StepError('The private Ollama did not become ready in 90 seconds.')

    def __exit__(self, *exc):
        if self.process and self.process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'], capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def source_host(name):
    return split_name(name)[0]


def _pull_one(context, server: PrivateServer, name):
    request = Request(server.base + '/api/pull', data=json.dumps({'model': name, 'stream': True}).encode('utf-8'), headers={'Content-Type': 'application/json'})
    last = (0, time.monotonic())
    try:
        with _OPENER.open(request, timeout=120) as response:
            for raw in response:
                if context.cancel():
                    raise StepError('Cancelled')
                line = json.loads(raw.decode('utf-8'))
                if 'error' in line:
                    raise StepError('Ollama could not pull %s: %s' % (name, line['error']))
                if line.get('total'):
                    now = time.monotonic()
                    completed = line.get('completed', 0)  # the first lines of a layer carry a total but no count yet
                    speed = (completed - last[0]) / max(now - last[1], 1e-6) if completed >= last[0] else 0
                    last = (completed, now)
                    context.emit('progress', name=name, done=completed, total=line['total'], speed=speed)
    except (URLError, OSError) as error:
        raise StepError('Pulling %s failed: %s' % (name, error)) from None
    if not manifest_path(context.root / 'Models/Text/ollama', name).is_file():
        raise StepError('Ollama reported no error but the model %s is not in the private store.' % name)


def _adopt(store, source, target, keep_source):
    """Give a model pulled from a mirror its catalog name.

    Ollama names a model after the place it was pulled from (hf.co/org/repo:tag), but the catalog, the policy file and the
    Components tab know it as `target`. A manifest is just a list of blob digests, so a copy of the file under the other
    name is the whole alias; the blobs are shared and the mirror's own name is dropped unless the user had it before."""
    source_path, target_path = manifest_path(store, source), manifest_path(store, target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    part = target_path.with_name(target_path.name + '.part')
    shutil.copyfile(source_path, part)
    os.replace(part, target_path)
    if keep_source:
        return
    source_path.unlink()
    base = Path(store) / 'manifests'
    for folder in source_path.parents:
        if folder == base:
            break
        try:
            folder.rmdir()
        except OSError:
            break


def pull(context, step):
    """Pull `model`; when its registry fails, try every entry of `alternatives` (other hosts of the same model) in order.

    When all of them fail the person is asked for a local GGUF file (see import_gguf). Raises StepError with the reasons otherwise."""
    target = step['model']
    sources = [target] + [name for name in step.get('alternatives', []) if name != target]
    for name in sources:
        split_name(name)
    store = context.root / 'Models/Text/ollama'
    failures = []
    with PrivateServer(context) as server:
        for name in sources:
            kept = manifest_path(store, name).is_file()
            try:
                _pull_one(context, server, name)
            except StepError as error:
                if context.cancel() or str(error) == 'Cancelled':
                    raise
                failures.append('%s: %s' % (source_host(name), str(error)[:200]))
                if name != sources[-1]:
                    context.emit('note', text=ui_text('components.gemma_source_failed_note') % (source_host(name), str(error)[:120]))
                continue
            if name != target:
                _adopt(store, name, target, kept)
                context.emit('note', text=ui_text('components.gemma_loaded_from_mirror_note') % source_host(name))
            return
        choice = context.recover(dict(component=context.component['id'], title=context.component['title'], name=target, bytes=0, sha256='', purpose='model',
                                      reasons=failures[:6], wait_seconds=600), context.cancel) if context.recover and context.component else None
        if choice and choice.get('kind') == 'file':
            import_gguf(context, server, target, choice['value'], choice.get('extra') or None)
            return
    raise StepError(ui_text('components.gemma_all_sources_failed_error') % '; '.join(failures)[:600])


# ---- local GGUF files ---------------------------------------------------------------------------------------------------
_GGUF_SCALARS = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}


def gguf_architecture(path, limit=512):
    """`general.architecture` read from the header of a GGUF file; '' when the file is not a readable GGUF.

    The key is the first or one of the first entries by convention, so the (large) tokenizer arrays are never reached for real files."""
    import struct
    try:
        with open(path, 'rb') as stream:
            if stream.read(4) != b'GGUF':
                return ''
            version, = struct.unpack('<I', stream.read(4))
            if version < 2:
                return ''
            _tensors, entries = struct.unpack('<QQ', stream.read(16))

            def text():
                size, = struct.unpack('<Q', stream.read(8))
                if size > 1 << 20:
                    raise ValueError
                return stream.read(size).decode('utf-8', 'replace')

            def skip(kind):
                if kind in _GGUF_SCALARS:
                    stream.seek(_GGUF_SCALARS[kind], 1)
                elif kind == 8:
                    stream.seek(struct.unpack('<Q', stream.read(8))[0], 1)
                elif kind == 9:
                    inner, count = struct.unpack('<IQ', stream.read(12))
                    if inner in _GGUF_SCALARS:
                        stream.seek(_GGUF_SCALARS[inner] * count, 1)
                    else:
                        for _ in range(count):
                            skip(inner)
                else:
                    raise ValueError

            for _ in range(min(entries, limit)):
                key = text()
                kind, = struct.unpack('<I', stream.read(4))
                if key == 'general.architecture':
                    return text() if kind == 8 else ''
                skip(kind)
    except (OSError, ValueError, struct.error):
        pass
    return ''


def find_projector(model_file):
    """The vision projector next to a model file (mmproj*.gguf in the same folder), preferring the one named after the model."""
    folder = Path(model_file).parent
    found = sorted(path for path in folder.glob('*.gguf') if 'mmproj' in path.name.lower() and path != Path(model_file))
    if not found:
        return None
    stem = Path(model_file).stem.lower()
    for path in found:
        if any(part and part in path.name.lower() for part in stem.split('-')[:4] if len(part) > 3):
            return path
    return found[0]


def _copy_blob(context, origin, store, label):
    """Copy a file into the blob store under its SHA-256 name in one pass; returns 'sha256:<hex>'."""
    size = origin.stat().st_size
    blobs = Path(store) / 'blobs'
    blobs.mkdir(parents=True, exist_ok=True)
    part = blobs / ('import-%d-%s.part' % (os.getpid(), hashlib.sha1(str(origin).encode('utf-8', 'replace')).hexdigest()[:8]))
    hasher, copied, last, last_bytes = hashlib.sha256(), 0, time.monotonic(), 0
    try:
        with open(origin, 'rb') as reader, open(part, 'wb') as writer:
            while True:
                if context.cancel():
                    raise StepError('Cancelled')
                block = reader.read(8 * 1024 * 1024)
                if not block:
                    break
                writer.write(block)
                hasher.update(block)
                copied += len(block)
                now = time.monotonic()
                if now - last > .3:
                    context.emit('progress', name=label, done=copied, total=size, speed=(copied - last_bytes) / (now - last))
                    last, last_bytes = now, copied
        context.emit('progress', name=label, done=size, total=size, speed=0)
        digest = 'sha256:' + hasher.hexdigest()
        destination = blob_path(store, digest)
        if destination.is_file() and destination.stat().st_size == size:
            part.unlink()
        else:
            os.replace(part, destination)
        return digest
    finally:
        if part.exists():
            part.unlink()


def import_gguf(context, server: PrivateServer, name, model_file, projector=None):
    """Create the model `name` in the private store from a local GGUF file (and optionally its mmproj vision projector).

    The file must be a GGUF of a Gemma architecture; the projector, when given or found beside the model, a CLIP-type GGUF."""
    model_file = Path(str(model_file).strip().strip('"'))
    split_name(name)
    if not model_file.is_file():
        raise StepError(ui_text('components.gemma_file_not_found_error') % model_file)
    architecture = gguf_architecture(model_file)
    if not architecture:
        raise StepError(ui_text('components.gemma_file_not_gguf_error') % model_file.name)
    if not architecture.lower().startswith('gemma'):
        raise StepError(ui_text('components.gemma_file_not_gemma_error') % (model_file.name, architecture))
    explicit = projector is not None and str(projector).strip() != ''
    projector = Path(str(projector).strip().strip('"')) if explicit else find_projector(model_file)
    if projector is not None and (not projector.is_file() or gguf_architecture(projector) != 'clip'):
        if explicit:
            raise StepError(ui_text('components.gemma_projector_invalid_error') % projector)
        projector = None
    store = context.root / 'Models/Text/ollama'
    context.emit('note', text=ui_text('components.gemma_import_started_note') % model_file.name)
    files = {model_file.name: _copy_blob(context, model_file, store, model_file.name)}
    if projector is not None:
        files[projector.name] = _copy_blob(context, projector, store, projector.name)
    else:
        context.emit('note', text=ui_text('components.gemma_import_no_projector_note'))
    body = dict(model=name, files=files, stream=True, parameters=dict(temperature=1.0, top_p=0.95, top_k=64))
    if architecture.lower().startswith('gemma4'):
        # The same renderer/parser pair the official gemma4 manifests name in their config layer: it formats the turns, thinking and tool calls.
        body.update(renderer='gemma4', parser='gemma4')
    request = Request(server.base + '/api/create', data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
    try:
        with _OPENER.open(request, timeout=600) as response:
            for raw in response:
                if context.cancel():
                    raise StepError('Cancelled')
                line = json.loads(raw.decode('utf-8'))
                if 'error' in line:
                    raise StepError(ui_text('components.gemma_create_failed_error') % str(line['error'])[:300])
    except (URLError, OSError) as error:
        raise StepError(ui_text('components.gemma_create_failed_error') % error) from None
    if not manifest_path(store, name).is_file():
        raise StepError(ui_text('components.gemma_create_failed_error') % name)


def local_model_name(model_file):
    """A registry-safe model name derived from a file name: 'gemma-4-E4B-it-Q4_K_M.gguf' -> 'gemma-4-e4b-it-q4_k_m'."""
    stem = re.sub(r'[^a-z0-9._-]+', '-', Path(model_file).stem.lower()).strip('-._')[:60]
    return stem or 'gemma-local'


def op_gguf_import(context, step):
    with PrivateServer(context) as server:
        import_gguf(context, server, step['model'], step['file'], step.get('projector'))


# ---- what is in the store -----------------------------------------------------------------------------------------------
def store_models(models_dir):
    """Models of a private store with their family (from the config layer) and whether every blob is present."""
    base = Path(models_dir) / 'manifests'
    rows = []
    for path in sorted(base.rglob('*')) if base.is_dir() else []:
        parts = path.relative_to(base).parts if path.is_file() else ()
        if len(parts) != 4:
            continue
        host, namespace, model, tag = parts
        name = ('%s:%s' % (model, tag)) if (host, namespace) == (DEFAULT_HOST, 'library') else '%s/%s/%s:%s' % (host, namespace, model, tag)
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
            layers = [document['config']] + list(document['layers'])
            complete = all(blob_path(models_dir, layer['digest']).is_file() and blob_path(models_dir, layer['digest']).stat().st_size == layer['size'] for layer in layers)
            family = ''
            if 0 < document['config']['size'] <= 65536:
                try:
                    details = json.loads(blob_path(models_dir, document['config']['digest']).read_text(encoding='utf-8'))
                    family = str(details.get('model_family') or '') if isinstance(details, dict) else ''
                except (OSError, ValueError):
                    pass
            vision = any(str(layer.get('mediaType', '')).endswith('.projector') for layer in document['layers'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        rows.append(dict(name=name, family=family, complete=complete, vision=vision))
    return rows


def import_model(context, step):
    """Copy one model (its manifest and verified blobs) from another Ollama model store into the private store."""
    name, source = step['model'], Path(step['source'])
    target_store = context.root / 'Models/Text/ollama'
    manifest = manifest_path(source, name)
    if not manifest.is_file():
        raise StepError('Model %s was not found in %s' % (name, source))
    document = json.loads(manifest.read_text(encoding='utf-8'))
    layers = [document['config']] + list(document['layers'])
    for layer in layers:
        digest = layer['digest']
        origin = blob_path(source, digest)
        destination = blob_path(target_store, digest)
        if destination.is_file() and destination.stat().st_size == layer['size']:
            continue
        if not origin.is_file() or origin.stat().st_size != layer['size']:
            raise StepError('The blob %s of %s is missing or incomplete in %s' % (digest[:19], name, source))
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(destination.name + '.part')
        hasher = hashlib.sha256()
        copied = 0
        with open(origin, 'rb') as reader, open(part, 'wb') as writer:
            while True:
                if context.cancel():
                    raise StepError('Cancelled')
                block = reader.read(8 * 1024 * 1024)
                if not block:
                    break
                writer.write(block)
                hasher.update(block)
                copied += len(block)
                context.emit('progress', name=name, done=copied, total=layer['size'], speed=0)
        if 'sha256:' + hasher.hexdigest() != digest:
            part.unlink()
            raise StepError('Blob %s is damaged in the source store.' % digest[:19])
        os.replace(part, destination)
    destination_manifest = manifest_path(target_store, name)
    destination_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manifest, destination_manifest)
