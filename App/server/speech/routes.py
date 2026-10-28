"""HTTP endpoints of voice input (served through the loopback bridge).

GET  /api/speech/availability       models, settings, worker state
POST /api/speech/settings           {model, device, language, keep_alive}
POST /api/speech/unload             stop the worker and free its memory
POST /api/speech/prepare           load the model in the background (the person started to speak); {enhance: true} loads Gemma
POST /api/speech/transcribe?lang=ru[&partial=1][&enhance=1] the body is the WAV recording itself (audio/wav), answered {text, seconds, device, model}
"""
from ui_strings import source as ui_text
from urllib.parse import parse_qs

from gpu import Busy
from .speech_manager import MAX_AUDIO_BYTES, SpeechError


def handle(handler, manager, route, data, query=''):
    if manager is None:
        return handler.response_json(503, {'error': ui_text('speech.routes_error_voice_input_unavailable_check_startup_log')})
    try:
        if route == 'availability' and data is None:
            return handler.response_json(200, manager.availability())
        if route == 'settings':
            return handler.response_json(200, manager.settings(data))
        if route == 'prepare' and data is not None:
            return handler.response_json(200, {'preparing': manager.prepare_enhance() if data.get('enhance') is True else manager.prepare()})
        if route == 'unload' and data is not None:
            manager.unload()
            return handler.response_json(200, {'unloaded': True})
        return handler.response_json(404, {'error': 'Not found'})
    except Busy as error:
        return handler.response_json(409, {'error': str(error)})
    except SpeechError as error:
        return handler.response_json(400, {'error': str(error)})


def transcribe(handler, manager, query=''):
    """The recording is raw audio, not JSON, so the bridge hands the request over before its JSON parsing."""
    if manager is None:
        return handler.response_json(503, {'error': ui_text('speech.routes_error_voice_input_unavailable_check_startup_log')})
    try:
        size = int(handler.headers.get('Content-Length', '0') or '0')
        if not 0 < size <= MAX_AUDIO_BYTES:
            return handler.response_json(413, {'error': ui_text('speech.transcribe_recording_empty_too_long_maximum_two')})
        if not handler.headers.get('Content-Type', '').startswith(('audio/wav', 'audio/x-wav', 'application/octet-stream')):
            return handler.response_json(415, {'error': ui_text('speech.transcribe_expected_audio_wav')})
        audio = handler.rfile.read(size)
        options = parse_qs(query.lstrip('?'))
        language = options.get('lang', [None])[0]
        parent_id = handler.headers.get('X-VisualForge-Session')
        if parent_id is not None:
            from event_history import ID
            if not ID.fullmatch(parent_id):
                raise SpeechError(ui_text('speech.transcribe_invalid_microphone_recording_identifier'))
        extra = {'parent_id': parent_id} if parent_id else {}
        return handler.response_json(200, manager.transcribe(audio, language, **extra, partial=options.get('partial', ['0'])[0] == '1', enhance=options.get('enhance', ['0'])[0] == '1'))
    except Busy as error:
        return handler.response_json(409, {'error': str(error)})
    except SpeechError as error:
        return handler.response_json(400, {'error': str(error)})
