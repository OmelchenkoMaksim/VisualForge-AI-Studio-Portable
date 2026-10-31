"""HTTP Voiceover endpoints served through the existing safe-origin loopback bridge."""
from ui_strings import source as ui_text
from urllib.parse import parse_qs

from gpu_coordinator import Busy, GpuUnavailable
from range_response import serve_file

MIME = {'.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.m4b': 'audio/mp4'}
# Body limits per POST route; the bridge reads them before parsing (an uploaded book or voice arrives as base64 JSON).
POST_LIMITS = {'prepare': 42 * 1024 * 1024, 'voice-add': 34 * 1024 * 1024, 'voice-delete': 4096, 'generate': 128 * 1024,
               'cancel': 4096, 'settings': 8192, 'delete': 4096, 'clear-cache': 4096, 'voices-build': 4096, 'voices-build-cancel': 4096}


def serve_audio(handler, path, name):
    return serve_file(handler, path, MIME.get(path.suffix.lower(), 'application/octet-stream'), name + path.suffix.lower(), 'no-store')


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
