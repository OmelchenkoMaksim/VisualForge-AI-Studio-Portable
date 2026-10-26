"""A tiny local web server for the installer tests: Range requests, redirects, 404s and connections that drop halfway."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading


class FakeWeb:
    def __init__(self):
        self.files = {}          # path -> bytes
        self.redirects = {}      # path -> target path
        self.drop_once = {}      # path -> number of bytes to send before breaking the first connection
        self.drop_each = {}      # path -> number of bytes to send before breaking every connection (a flaky line)
        self.requests = []       # (path, Range header)
        self.auth_seen = []      # (path, Authorization header)
        web = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_GET(self):
                path = self.path
                web.requests.append((path, self.headers.get('Range')))
                web.auth_seen.append((path, self.headers.get('Authorization')))
                if path in web.redirects:
                    self.send_response(302)
                    self.send_header('Location', web.redirects[path])
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if path not in web.files:
                    self.send_response(404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                data = web.files[path]
                start = 0
                header = self.headers.get('Range')
                if header and header.startswith('bytes='):
                    start = int(header[6:].split('-')[0])
                    if start >= len(data):
                        self.send_response(416)
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, len(data) - 1, len(data)))
                else:
                    self.send_response(200)
                body = data[start:]
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                limit = web.drop_once.pop(path, None)
                if limit is None and len(body) > web.drop_each.get(path, len(body)):
                    limit = web.drop_each[path]
                if limit is not None:
                    self.wfile.write(body[:limit])
                    self.wfile.flush()
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    @property
    def base(self):
        return 'http://127.0.0.1:%d' % self.server.server_address[1]

    def add(self, path, data):
        self.files[path] = data
        return dict(url=self.base + path, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
