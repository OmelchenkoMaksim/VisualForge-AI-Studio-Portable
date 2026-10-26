"""Text models for the private Ollama: pull one from the public registry, or import one you already have in your own Ollama.

Both put the model into Models/Text/ollama (the private model store used by the Text section). The Ollama installed on the
computer is never started, changed or stopped.
"""
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

from .steps import StepError

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

    def __enter__(self):
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


def pull(context, step):
    name = step['model']
    split_name(name)
    with PrivateServer(context) as server:
        request = Request(server.base + '/api/pull', data=json.dumps({'model': name, 'stream': True}).encode('utf-8'), headers={'Content-Type': 'application/json'})
        started, last = time.monotonic(), (0, time.monotonic())
        try:
            with _OPENER.open(request, timeout=120) as response:
                done_status = None
                for raw in response:
                    if context.cancel():
                        raise StepError('Cancelled')
                    line = json.loads(raw.decode('utf-8'))
                    if 'error' in line:
                        raise StepError('Ollama could not pull %s: %s' % (name, line['error']))
                    done_status = line.get('status', done_status)
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
