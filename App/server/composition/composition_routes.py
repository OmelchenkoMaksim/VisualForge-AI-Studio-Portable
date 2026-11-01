"""Same-origin composition endpoints; raw attachments never enter the JSON request body."""
import json
from urllib.parse import parse_qs

from gpu_coordinator import Busy, GpuUnavailable
from range_response import serve_file
from ui_strings import source as ui_text
from .composition_media import MAX_BYTES


def handle(handler, manager, route, query, post=False):
    if not handler.safe_origin():
        return
    if manager is None:
        return handler.response_json(503, dict(error=ui_text('composition.panel_unavailable_text')))
    params = {key:values[0] for key, values in parse_qs(query).items()}
    try:
        if not post:
            if route == 'profile':
                return handler.response_json(200, manager.profile())
            if route == 'draft':
                return handler.response_json(200, manager.draft(params.get('id')))
            if route == 'status':
                return handler.response_json(200, manager.status(params.get('id')))
            if route == 'history':
                return handler.response_json(200, manager.history())
            if route in ('material', 'result'):
                path = manager.materials.path(params.get('draft'), params.get('id')) if route == 'material' else manager.result_file(params.get('id'), int(params.get('index', '0')))
                mime = 'image/png' if route == 'result' else next(item['mime'] for item in manager.draft(params.get('draft'))['items'] if item['id'] == params.get('id'))
                return serve_file(handler, path, mime, 'composition.png', 'no-store')
        else:
            size = int(handler.headers.get('Content-Length', '0'))
            if not 0 < size <= (MAX_BYTES if route == 'attach' else 32 * 1024):
                return handler.response_json(413, dict(error=ui_text('composition.panel_invalid_attachment_text')))
            raw = handler.rfile.read(size)
            if len(raw) != size:
                raise ValueError(ui_text('composition.panel_invalid_attachment_text'))
            if route == 'attach':
                return handler.response_json(200, manager.materials.add(params.get('draft'), raw, params.get('group'), params.get('name')))
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError(ui_text('composition.panel_invalid_options_text'))
            if route == 'start':
                return handler.response_json(200, manager.start(data))
            if route == 'cancel':
                return handler.response_json(200, manager.cancel_run(data.get('id')))
            if route == 'detach':
                return handler.response_json(200, manager.materials.remove(data.get('draft'), data.get('id')))
            if route == 'label':
                return handler.response_json(200, manager.materials.label(data.get('draft'), data.get('id'), data.get('text')))
        return handler.response_json(404, dict(error=ui_text('composition.panel_invalid_options_text')))
    except Busy as error:
        return handler.response_json(409, dict(error=str(error)))
    except GpuUnavailable as error:
        return handler.response_json(503, dict(error=str(error)))
    except (ValueError, OSError, TypeError) as error:
        return handler.response_json(400, dict(error=str(error)[:800]))
