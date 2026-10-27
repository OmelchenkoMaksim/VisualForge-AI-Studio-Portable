"""The only way an agent reaches a model: an OpenAI-compatible loopback endpoint in front of the private Ollama.

Agents speak the OpenAI chat protocol and would happily call Ollama's /v1 directly, but then a chat-model request could start while
Image, Video or Music holds the video card and both would run out of memory. Every request here first passes the studio's GPU
admission (the same `GPU.claim('text')` a chat answer uses) and is marked as a Text job until its response ends, so other modules
wait for it and it waits for them. The response is relayed byte for byte, so streaming and tool calls reach the agent unchanged."""
import json
import secrets
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gpu import Busy, GpuUnavailable

MAX_BODY = 32 * 1024 * 1024  # a long conversation with tool outputs; Ollama's own limit is far above


class ModelGateway:
    def __init__(self, port, upstream, admit, finish, models):
        """`upstream()` starts Ollama when needed and returns its port; `admit()` blocks until the GPU may be used and returns a
        cancel event (raises Busy/GpuUnavailable/ValueError); `finish()` releases it; `models()` maps aliases to Ollama names."""
        self.port = port
        self.upstream, self.admit, self.finish, self.models = upstream, admit, finish, models
        self.token = secrets.token_urlsafe(32)
        self.server = None
        self.lock = threading.Lock()

    @property
    def base_url(self):
        return 'http://127.0.0.1:%d/v1' % self.port

    def start(self):
        with self.lock:
            if self.server is not None:
                return
            gateway = self

            class Handler(GatewayHandler):
                owner = gateway
            try:
                server = ThreadingHTTPServer(('127.0.0.1', self.port), Handler)
            except OSError:
                raise ValueError('Порт шлюза агента %d занят другой программой. Измените AgentGatewayPort в настройке запуска.' % self.port) from None
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, name='agent-gateway', daemon=True).start()
            self.server = server

    def close(self):
        with self.lock:
            if self.server is not None:
                self.server.shutdown()
                self.server.server_close()
                self.server = None


class GatewayHandler(BaseHTTPRequestHandler):
    owner = None
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def fail(self, status, message):
        raw = json.dumps({'error': {'message': message, 'type': 'visualforge_gateway'}}, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def allowed(self):
        # Loopback only, the exact Host (no DNS rebinding) and the per-launch token the studio wrote into the agent's config.
        if self.headers.get('Host') not in ('127.0.0.1:%d' % self.owner.port, 'localhost:%d' % self.owner.port):
            self.fail(403, 'Host not allowed')
            return False
        given = self.headers.get('Authorization', '')
        if not given.startswith('Bearer ') or not secrets.compare_digest(given[7:].encode(), self.owner.token.encode()):
            self.fail(401, 'Неверный ключ шлюза агента')
            return False
        return True

    def do_GET(self):
        if not self.allowed():
            return
        if self.path.rstrip('/') != '/v1/models':
            return self.fail(404, 'Not found')
        data = [{'id': alias, 'object': 'model', 'owned_by': 'visualforge'} for alias in self.owner.models()]
        raw = json.dumps({'object': 'list', 'data': data}).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if not self.allowed():
            return
        if self.path != '/v1/chat/completions':
            return self.fail(404, 'Not found')
        try:
            size = int(self.headers.get('Content-Length', '0') or '0')
            if not 0 < size <= MAX_BODY:
                return self.fail(413, 'Слишком большой запрос к модели')
            body = json.loads(self.rfile.read(size))
            names = self.owner.models()
            if not isinstance(body, dict) or body.get('model') not in names:
                return self.fail(400, 'Модель агента не найдена среди установленных моделей Text')
            body['model'] = names[body['model']]
            payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        except (ValueError, TypeError):
            return self.fail(400, 'Некорректный запрос к модели')
        try:
            port = self.owner.upstream()
            cancel = self.owner.admit()
        except Busy as error:
            return self.fail(409, str(error))
        except (GpuUnavailable, ValueError, OSError) as error:
            return self.fail(503, str(error)[:600])
        try:
            self.relay(port, payload, cancel)
        finally:
            self.owner.finish()

    def relay(self, port, payload, cancel):
        """Pass Ollama's raw HTTP response through; Stop closes both sides even while Ollama is still loading the model (no byte yet)."""
        self.close_connection = True
        try:
            upstream = socket.create_connection(('127.0.0.1', port), timeout=10)
        except OSError:
            return self.fail(503, 'Ollama (Text) не отвечает')
        sent = False
        try:
            upstream.sendall(b'POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\n'
                             b'Accept: */*\r\nConnection: close\r\nContent-Length: %d\r\n\r\n' % (port, len(payload)) + payload)
            while not cancel.is_set():
                if not select.select([upstream], [], [], 0.2)[0]:
                    continue
                piece = upstream.recv(65536)
                if not piece:
                    break
                self.wfile.write(piece)
                self.wfile.flush()
                sent = True
            if cancel.is_set() and not sent:
                self.fail(499, 'Остановлено пользователем')
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            cancel.set()  # the agent dropped the request (its own abort): stop Ollama's generation too
        except OSError:
            if not sent:
                self.fail(502, 'Ollama (Text) прервал ответ')
        finally:
            upstream.close()
