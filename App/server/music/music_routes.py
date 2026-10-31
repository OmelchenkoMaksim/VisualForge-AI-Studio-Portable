"""HTTP Music endpoints served through the existing safe-origin loopback bridge."""
from ui_strings import source as ui_text
from urllib.parse import parse_qs

from gpu_coordinator import Busy, GpuUnavailable
from range_response import serve_file

def serve_audio(handler, path):
    return serve_file(handler, path, 'audio/wav', 'Music-' + path.stem.rsplit(' - ', 1)[-1] + '.wav', 'no-store')

def handle(handler, manager, gpu, route, data, query):
    """GPU admission (`gpu.claim`) runs under the bridge's existing shared submission lock."""
    if manager is None:
        return handler.response_json(503, {'error': ui_text('music.routes_error_music_module_unavailable_check_startup_log')})
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
        elif route == 'delete' and data is not None:
            answer = manager.delete(data)
        elif route == 'cancel' and data is not None:
            answer = manager.cancel(data)
        elif route == 'generate' and data is not None:
            from .music_registry import claim_vram_mb, validate
            data = validate(data)
            if manager.busy():
                return handler.response_json(409, {'error': ui_text('music.routes_error_gpu_already_busy_music_task_wait')})
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
