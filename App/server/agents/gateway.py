"""The only way an agent reaches a model: an OpenAI-compatible loopback endpoint in front of the private Ollama.

Agents speak the OpenAI chat protocol and would happily call Ollama's /v1 directly, but then a chat-model request could start while
Image, Video or Music holds the video card and both would run out of memory. Every request here first passes the studio's GPU
admission (the same `GPU.claim('text')` a chat answer uses) and is marked as a Text job until its response ends, so other modules
wait for it and it waits for them. The response is relayed byte for byte, so streaming and tool calls reach the agent unchanged.

The gateway also measures how full the model's context is after every request (the usage Ollama reports at the end of a
response, asked for with `stream_options.include_usage`): agents do not know their own context budget, and the studio stops a
loop iteration before it runs out."""
import json
import secrets
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gpu import Busy, GpuUnavailable

MAX_BODY = 32 * 1024 * 1024  # a long conversation with tool outputs; Ollama's own limit is far above
TAIL = 64 * 1024  # maximum HTTP header/chunk header storage
BYTES_PER_TOKEN = 3  # estimate when a response carries no usage (JSON-escaped Cyrillic is longer per token than English)


def usage_from(tail):
    """The last usage record (prompt_tokens, completion_tokens) in a relayed response: SSE `data:` lines or one JSON body."""
    found = None
    for line in tail.split(b'\n'):
        line = line.strip()
        if line.startswith(b'data:'):
            line = line[5:].strip()
        if not line.startswith(b'{') or b'"usage"' not in line:
            continue
        try:
            usage = json.loads(line).get('usage')
        except ValueError:
            continue
        if isinstance(usage, dict) and usage.get('prompt_tokens') is not None:
            try:
                counts = (int(usage.get('prompt_tokens') or 0), int(usage.get('completion_tokens') or 0))
                if min(counts) >= 0:
                    found = counts
            except (ValueError, TypeError, OverflowError):
                pass
    return found


class ResponseUsage:
    """Decode HTTP framing for accounting only; the wire response is relayed unchanged."""
    def __init__(self):
        self.buffer = bytearray()
        self.headers = False
        self.chunked = self.sse = False
        self.remaining = None
        self.separator = False
        self.ended = False
        self.body = bytearray()
        self.body_bytes = 0
        self.usage = None
        self.overflow = False

    def content(self, piece):
        self.body_bytes += len(piece)
        if self.sse:
            # Bound individual event storage, including an unfinished giant tool delta.
            for part in piece.splitlines(keepends=True):
                if len(self.body) + len(part) <= MAX_BODY and not self.overflow:
                    self.body.extend(part)
                else:
                    self.overflow = True
                if part.endswith(b'\n'):
                    if not self.overflow:
                        self.usage = usage_from(bytes(self.body)) or self.usage
                    self.body.clear()
                    self.overflow = False
        elif len(self.body) + len(piece) <= MAX_BODY and not self.overflow:
            self.body.extend(piece)
        else:
            self.body.clear()
            self.overflow = True

    def feed(self, piece):
        if self.ended:
            return
        self.buffer.extend(piece)
        if not self.headers:
            end = self.buffer.find(b'\r\n\r\n')
            if end < 0:
                if len(self.buffer) > TAIL:
                    self.buffer.clear()
                    self.ended = True
                return
            header = bytes(self.buffer[:end]).lower()
            del self.buffer[:end + 4]
            self.headers = True
            self.chunked = b'transfer-encoding: chunked' in header
            self.sse = b'text/event-stream' in header
        if not self.chunked:
            self.content(bytes(self.buffer))
            self.buffer.clear()
            return
        while self.buffer and not self.ended:
            if self.separator:
                if len(self.buffer) < 2:
                    return
                if self.buffer[:2] != b'\r\n':
                    self.ended = True
                    return
                del self.buffer[:2]
                self.separator = False
            if self.remaining is None:
                end = self.buffer.find(b'\r\n')
                if end < 0:
                    if len(self.buffer) > TAIL:
                        self.buffer.clear()
                        self.ended = True
                    return
                try:
                    self.remaining = int(self.buffer[:end].split(b';')[0], 16)
                except ValueError:
                    self.ended = True
                    return
                del self.buffer[:end + 2]
                if self.remaining <= 0:
                    self.ended = True
                    self.buffer.clear()
                    return
            count = min(self.remaining, len(self.buffer))
            self.content(bytes(self.buffer[:count]))
            del self.buffer[:count]
            self.remaining -= count
            if self.remaining == 0:
                self.remaining = None
                self.separator = True

    def result(self):
        if not self.overflow:
            body = bytes(self.body)
            if not self.sse:
                try:
                    body = json.dumps(json.loads(body)).encode('utf-8')
                except (ValueError, UnicodeError):
                    pass
            self.usage = usage_from(body) or self.usage
        return self.usage, self.body_bytes


def has_images(body):
    messages = body.get('messages') or []
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get('images'):
            return True
        content = message.get('content')
        if isinstance(content, list) and any(isinstance(part, dict) and part.get('type') in ('image', 'image_url', 'input_image') for part in content):
            return True
    return False


class ModelGateway:
    def __init__(self, port, upstream, admit, finish, models, observe=None, activity=None):
        """`upstream()` starts Ollama when needed and returns its port; `admit()` blocks until the GPU may be used and returns a
        cancel event (raises Busy/GpuUnavailable/ValueError); `finish()` releases it; `models()` maps aliases to Ollama names;
        `observe(tokens, measured)` gets the context filled by each answered request (prompt + answer), `measured` False for an
        estimate from the request and response sizes."""
        self.port = port
        self.upstream, self.admit, self.finish, self.models = upstream, admit, finish, models
        self.observe = observe or (lambda tokens, measured: None)
        self.activity = activity or (lambda: ('AgentModelCall', None))
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
            if body.get('stream'):
                body['stream_options'] = dict(body.get('stream_options') or {}, include_usage=True)
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
        from contextlib import nullcontext
        from generation import operation, finish as finish_event, TYPES as EVENT_TYPES
        try:
            # Optional journal metadata must not leak an admitted GPU slot or interrupt model streaming.
            try:
                kind, parent_id = self.owner.activity()
                if not isinstance(kind, str) or kind not in EVENT_TYPES:
                    raise ValueError('Unknown event type')
            except Exception:
                kind, parent_id = 'AgentModelCall', None
            with operation(kind, parent_id, cancel=cancel) as record:
                with operation('ImageRecognition', cancel=cancel) if has_images(body) else nullcontext() as vision:
                    tail = self.relay(port, payload, cancel)
                    if cancel.is_set() or not tail:
                        for entry in (record, vision):
                            finish_event(entry, state='cancelled' if cancel.is_set() else 'error')
        finally:
            self.owner.finish()
        usage, output_bytes = tail if tail else (None, 0)
        if usage:
            self.owner.observe(sum(usage), True)
        elif output_bytes:
            self.owner.observe((len(payload) + output_bytes) // BYTES_PER_TOKEN, False)

    def relay(self, port, payload, cancel):
        """Pass Ollama's raw HTTP response through; Stop closes both sides even while Ollama is still loading the model (no byte yet).
        Returns (usage, decoded body bytes), or b'' when the response was interrupted."""
        self.close_connection = True
        try:
            upstream = socket.create_connection(('127.0.0.1', port), timeout=10)
        except OSError:
            self.fail(503, 'Ollama (Text) не отвечает')
            return b''
        sent = False
        accounting = ResponseUsage()
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
                accounting.feed(piece)
            if cancel.is_set():
                if not sent:
                    self.fail(499, 'Остановлено пользователем')
                return b''
            return accounting.result()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            cancel.set()  # the agent dropped the request (its own abort): stop Ollama's generation too
        except OSError:
            if not sent:
                self.fail(502, 'Ollama (Text) прервал ответ')
        finally:
            upstream.close()
        return b''
