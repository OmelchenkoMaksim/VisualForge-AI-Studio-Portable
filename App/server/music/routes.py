"""HTTP Music endpoints served through the existing safe-origin loopback bridge."""
import re
from urllib.parse import parse_qs

from gpu import Busy, GpuUnavailable

def serve_audio(handler, path):
    size = path.stat().st_size
    start, end, partial = 0, size - 1, False
    requested = handler.headers.get('Range')
    if requested:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
        if not match or not any(match.groups()):
            return handler.response_json(416, {'error': 'Invalid byte range'})
        first, last = match.groups()
        start = int(first) if first else max(0, size - int(last))
        end = min(size - 1, int(last)) if first and last else size - 1
        if not 0 <= start <= end < size:
            return handler.response_json(416, {'error': 'Invalid byte range'})
        partial = True
    handler.send_response(206 if partial else 200)
    handler.send_header('Content-Type', 'audio/wav')
    handler.send_header('Content-Length', str(end - start + 1))
    handler.send_header('Accept-Ranges', 'bytes')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Content-Disposition', 'inline; filename="Music-' + path.stem.rsplit(' - ', 1)[-1] + '.wav"')
    if partial:
        handler.send_header('Content-Range', f'bytes {start}-{end}/{size}')
    handler.end_headers()
    try:
        with path.open('rb') as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = stream.read(min(65536, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)
    except (BrokenPipeError, ConnectionResetError):
        pass

def handle(handler, manager, gpu, route, data, query):
    """GPU admission (`gpu.claim`) runs under the bridge's existing shared submission lock."""
    if manager is None:
        return handler.response_json(503, {'error': 'Модуль Music недоступен. Проверьте журнал запуска.'})
    ident = parse_qs(query.lstrip('?')).get('id', [''])[0]
    try:
        if route == 'availability' and data is None:
            answer = manager.availability()
        elif route == 'active' and data is None:
            answer = manager.status()
        elif route == 'history' and data is None:
            answer = manager.entries()
        elif route == 'status' and data is None:
            answer = manager.status(ident)
        elif route == 'settings':
            answer = manager.settings(data)
        elif route == 'logs' and data is None:
            answer = manager.log(ident)
        elif route == 'result' and data is None:
            return serve_audio(handler, manager.file(ident))
        elif route == 'cancel' and data is not None:
            answer = manager.cancel(data)
        elif route == 'generate' and data is not None:
            from .registry import claim_vram_mb, validate
            data = validate(data)
            if manager.busy():
                return handler.response_json(409, {'error': 'GPU уже занят задачей Music. Дождитесь завершения или отмените её.'})
            available = manager.availability(data['engine'])
            if not available['available']:
                return handler.response_json(503, {'error': available['reason']})
            try:
                # Refuses while Image/Video/Text work; frees idle Image models and the chat model. Stable Audio states its own estimate.
                gpu.claim('music', claim_vram_mb(available.get('engine', {}), data))
            except Busy as error:
                return handler.response_json(409, {'error': str(error)})
            except GpuUnavailable as error:
                return handler.response_json(503, {'error': str(error)})
            answer = manager.submit(data)
        else:
            return handler.response_json(404, {'error': 'Unknown Music endpoint'})
        return handler.response_json(200, answer)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return handler.response_json(400, {'error': str(exc)[:1000]})
