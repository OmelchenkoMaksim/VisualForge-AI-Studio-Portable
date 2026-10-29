"""HTTP Voiceover endpoints served through the existing safe-origin loopback bridge."""
from ui_strings import source as ui_text
import re
from urllib.parse import parse_qs

from gpu_coordinator import Busy, GpuUnavailable

MIME = {'.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.m4b': 'audio/mp4'}
# Body limits per POST route; the bridge reads them before parsing (an uploaded book or voice arrives as base64 JSON).
POST_LIMITS = {'prepare': 42 * 1024 * 1024, 'voice-add': 34 * 1024 * 1024, 'voice-delete': 4096, 'generate': 128 * 1024,
               'cancel': 4096, 'settings': 8192, 'delete': 4096, 'clear-cache': 4096, 'voices-build': 4096, 'voices-build-cancel': 4096}


def serve_audio(handler, path, name):
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
    handler.send_header('Content-Type', MIME.get(path.suffix.lower(), 'application/octet-stream'))
    handler.send_header('Content-Length', str(end - start + 1))
    handler.send_header('Accept-Ranges', 'bytes')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Content-Disposition', 'inline; filename="' + name + path.suffix.lower() + '"')
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
        return handler.response_json(503, {'error': ui_text('voiceover.routes_module_unavailable_error')})
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
        elif route == 'voices' and data is None:
            answer = manager.voices()
        elif route == 'voice-audio' and data is None:
            return serve_audio(handler, manager.voice_file(ident), 'voice')
        elif route == 'fragment' and data is None:
            return serve_audio(handler, manager.fragment_file(ident, parse_qs(query.lstrip('?')).get('key', [''])[0]), 'fragment')
        elif route == 'result' and data is None:
            return serve_audio(handler, manager.file(ident), 'voiceover')
        elif route == 'prepare' and data is not None:
            answer = manager.prepare(data)
        elif route == 'voice-add' and data is not None:
            answer = manager.add_voice(data)
        elif route == 'voice-delete' and data is not None:
            answer = manager.delete_voice(data)
        elif route == 'voices-build' and data is not None:
            from .voiceover_registry import ENGINES, VOXCPM_ID
            if manager.busy():
                return handler.response_json(409, {'error': ui_text('voiceover.routes_gpu_already_busy_error')})
            state = manager.engine_state(VOXCPM_ID)
            if not state['available']:
                return handler.response_json(503, {'error': state['reason']})
            try:
                # Making the voices uses VoxCPM2, so the card is claimed exactly like for a VoxCPM2 job.
                gpu.claim('voiceover', ENGINES[VOXCPM_ID]['claim_vram_mb'])
            except Busy as error:
                return handler.response_json(409, {'error': str(error)})
            except GpuUnavailable as error:
                return handler.response_json(503, {'error': str(error)})
            answer = manager.build_defaults()
        elif route == 'voices-build-cancel' and data is not None:
            answer = manager.cancel_defaults()
        elif route == 'delete' and data is not None:
            answer = manager.delete(data)
        elif route == 'cancel' and data is not None:
            answer = manager.cancel(data)
        elif route == 'clear-cache' and data is not None:
            answer = manager.clear_cache()
        elif route == 'generate' and data is not None:
            from .voiceover_registry import ENGINES, validate
            request = validate(data)
            if manager.busy():
                return handler.response_json(409, {'error': ui_text('voiceover.routes_gpu_already_busy_error')})
            state = manager.engine_state(request['engine'])
            if not state['available']:
                return handler.response_json(503, {'error': state['reason']})
            try:
                # Refuses while Image/Video/Music/Text work; frees idle Image models and the chat model.
                gpu.claim('voiceover', ENGINES[request['engine']]['claim_vram_mb'])
            except Busy as error:
                return handler.response_json(409, {'error': str(error)})
            except GpuUnavailable as error:
                return handler.response_json(503, {'error': str(error)})
            answer = manager.submit(data)
        else:
            return handler.response_json(404, {'error': 'Unknown Voiceover endpoint'})
        return handler.response_json(200, answer)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return handler.response_json(400, {'error': str(exc)[:1000]})
