"""A tiny stand-in for `ollama serve` used by the Text tests: same endpoints and NDJSON shape, no model."""
import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOADED = {}
LOG = os.environ.get('FAKE_OLLAMA_LOG')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0') or 0)) or b'{}')

    def do_GET(self):
        if self.path == '/api/version':
            return self.reply(200, {'version': '0.0-fake'})
        if self.path == '/api/ps':
            return self.reply(200, {'models': [{'name': name, 'size': 100, 'size_vram': 90, 'context_length': 4096} for name in LOADED]})
        self.reply(404, {'error': 'not found'})

    def do_POST(self):
        data = self.body()
        if LOG:
            with open(LOG, 'a', encoding='utf-8') as stream:
                stream.write(json.dumps({'path': self.path, 'body': data}, ensure_ascii=False) + '\n')
        if self.path == '/api/pull':
            return self.pull(data)
        if self.path == '/api/create':
            return self.create(data)
        if self.path == '/api/show':
            return self.reply(200, {'capabilities': ['completion', 'thinking']})
        if self.path == '/api/generate':
            if data.get('keep_alive') == 0:
                LOADED.pop(data.get('model'), None)
            return self.reply(200, {'done': True})
        if self.path != '/api/chat':
            return self.reply(404, {'error': 'not found'})
        question = [m for m in data['messages'] if m['role'] == 'user'][-1]['content']
        if 'FAIL' in question:
            return self.reply(500, {'error': 'model failed to load: out of memory'})
        if 'LOADSLOW' in question:
            time.sleep(4)  # like a model load: not a single byte, not even headers, until it is done
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.send_header('Connection', 'close')
        self.end_headers()
        LOADED[data['model']] = time.time()
        pictures = len([m for m in data['messages'] if m['role'] == 'user'][-1].get('images') or [])
        pieces = ['Эхо', ': ', question[:40]] if 'SLOW' not in question else ['слово%d ' % i for i in range(60)]
        if pictures:
            pieces.append(' [изображений: %d]' % pictures)
        try:
            if data.get('think'):
                for part in ('Думаю ', 'над ответом'):
                    self.line({'model': data['model'], 'message': {'role': 'assistant', 'content': '', 'thinking': part}, 'done': False})
            for piece in pieces:
                self.line({'model': data['model'], 'message': {'role': 'assistant', 'content': piece}, 'done': False})
                if 'SLOW' in question:
                    time.sleep(0.1)
                if 'MIDERR' in question:
                    return self.line({'error': 'runner crashed'})
            self.line({'model': data['model'], 'message': {'role': 'assistant', 'content': ''}, 'done': True, 'done_reason': 'stop', 'total_duration': 2_000_000_000,
                       'load_duration': 500_000_000, 'prompt_eval_count': 12, 'eval_count': len(pieces), 'eval_duration': 1_000_000_000})
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def pull(self, data):
        name = data['model']
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.send_header('Connection', 'close')
        self.end_headers()
        if name.startswith('missing') or name.startswith('down.test/') or name in os.environ.get('FAKE_OLLAMA_FAIL', '').split(','):
            return self.line({'error': 'pull model manifest: file does not exist'})
        self.line({'status': 'pulling manifest'})
        self.line({'status': 'pulling sha256:abc', 'digest': 'sha256:abc', 'total': 1000})  # a real server starts a layer without a count
        for done in (250, 500, 1000):
            self.line({'status': 'pulling sha256:abc', 'digest': 'sha256:abc', 'total': 1000, 'completed': done})
        path, _, tag = name.partition(':')
        parts = path.split('/')
        host, namespace, model = ['registry.ollama.ai', 'library'][:3 - len(parts)] + parts
        folder = os.path.join(os.environ['OLLAMA_MODELS'], 'manifests', host, namespace, model)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, tag or 'latest'), 'w', encoding='utf-8') as stream:
            json.dump({'config': {'digest': 'sha256:cfg', 'size': 1}, 'layers': []}, stream)
        self.line({'status': 'success'})

    def create(self, data):
        """POST /api/create with `files`: every blob must already be in the store (the installer copies it there); writes a manifest."""
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.send_header('Connection', 'close')
        self.end_headers()
        blobs = os.path.join(os.environ['OLLAMA_MODELS'], 'blobs')
        layers = []
        for filename, digest in (data.get('files') or {}).items():
            blob = os.path.join(blobs, digest.replace(':', '-'))
            if not os.path.isfile(blob):
                return self.line({'error': 'blob missing: ' + digest})
            layers.append({'digest': digest, 'size': os.path.getsize(blob), 'mediaType': 'application/vnd.ollama.image.' + ('projector' if 'mmproj' in filename else 'model')})
        model, _, tag = data['model'].partition(':')
        folder = os.path.join(os.environ['OLLAMA_MODELS'], 'manifests', 'registry.ollama.ai', 'library', model)
        os.makedirs(folder, exist_ok=True)
        config = json.dumps({'model_family': 'gemma4' if data.get('renderer') == 'gemma4' else 'other'}).encode()
        config_digest = 'sha256:' + hashlib.sha256(config).hexdigest()
        with open(os.path.join(blobs, config_digest.replace(':', '-')), 'wb') as stream:
            stream.write(config)
        with open(os.path.join(folder, tag or 'latest'), 'w', encoding='utf-8') as stream:
            json.dump({'config': {'digest': config_digest, 'size': len(config)}, 'layers': layers, 'created_with': {k: data.get(k) for k in ('renderer', 'parser', 'parameters')}}, stream)
        self.line({'status': 'success'})

    def line(self, value):
        self.wfile.write(json.dumps(value).encode() + b'\n')
        self.wfile.flush()


if __name__ == '__main__':
    host, port = os.environ['OLLAMA_HOST'].split(':')
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()
