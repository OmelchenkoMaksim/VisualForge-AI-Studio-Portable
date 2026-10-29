"""HTTP endpoints of the Text tab's agent mode, served through the loopback bridge after its same-origin checks."""
from ui_strings import source as ui_text
import json
import select
import socket
import threading

from gpu_coordinator import Busy


class Disconnected(Exception):
    """The page closed the stream (Stop, reload or closed tab)."""


def handle(handler, manager, route, data, choose_folder):
    """Everything except the streaming `send`. `choose_folder(initial)` opens the native folder dialog and returns a path or ''."""
    if manager is None:
        return handler.response_json(503, {'error': ui_text('agent.routes_error_agent_mode_unavailable_check_startup_log')})
    try:
        if data is None:
            if route != 'availability':
                return handler.response_json(404, {'error': 'Unknown agent endpoint'})
            answer = manager.availability()
        elif route == 'chats':
            answer = manager.create_chat(data)
        elif route == 'chat-update':
            answer = manager.update_chat(data)
        elif route == 'permission':
            answer = manager.reply(data)
        elif route == 'stop':
            answer = manager.stop(data.get('chat_id'))
        elif route == 'settings':
            answer = manager.settings(data)
        elif route == 'choose-workspace':
            if set(data) - {'initial'}:
                raise ValueError(ui_text('agent.create_chat_request_contains_unsupported_parameters'))
            chosen = choose_folder(data.get('initial') or manager.settings()['workspace'] or None)
            answer = dict(cancelled=not chosen, workspace=chosen or '')
        else:
            return handler.response_json(404, {'error': 'Unknown agent endpoint'})
    except Busy as error:
        return handler.response_json(409, {'error': str(error)})
    except (ValueError, OSError) as error:
        return handler.response_json(400, {'error': str(error)[:800]})
    return handler.response_json(200, answer)


def send(handler, manager, data):
    """Stream one agent task (or loop) as NDJSON events: status, context, delta, step, permission, permission_done, iteration,
    done, error."""
    if manager is None:
        raise ValueError(ui_text('agent.api_request_agent_mode_unavailable'))
    if not isinstance(data, dict) or set(data) - {'chat_id', 'content', 'loop'}:
        raise ValueError(ui_text('agent.create_chat_request_contains_unsupported_parameters'))
    if manager.busy():
        raise Busy(ui_text('agent.send_agent_already_running_task_wait_completion'))
    state = dict(gone=False, started=False)
    finished = threading.Event()

    def watch_disconnect():
        # Permission waits emit nothing. Detect TCP EOF without consuming requests.
        while not finished.wait(0.25):
            try:
                if select.select([handler.connection], [], [], 0)[0] and not handler.connection.recv(1, socket.MSG_PEEK):
                    state['gone'] = True
                    return
            except OSError:
                state['gone'] = True
                return

    def start():
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Connection', 'close')
        handler.end_headers()
        state['started'] = True

    def emit(event):
        if state['gone']:
            raise Disconnected
        try:
            if not state['started']:
                start()
            handler.wfile.write(json.dumps(event, ensure_ascii=False).encode('utf-8') + b'\n')
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            state['gone'] = True
            raise Disconnected from None

    watcher = threading.Thread(target=watch_disconnect, name='agent-client', daemon=True)
    watcher.start()
    try:
        chat, message, error = manager.send(data.get('chat_id'), data.get('content'), emit, lambda: state['gone'], data.get('loop'))
        result = dict(type='done', message=message, chat=dict(id=chat['id'], title=chat['title'], updated=chat['updated']))
        if error:
            result['error'] = error
    except Busy as failure:
        if not state['started']:
            raise
        result = dict(type='error', error=str(failure))
    except ValueError as failure:
        if not state['started']:
            raise
        result = dict(type='error', error=str(failure))
    finally:
        finished.set()
        watcher.join(1)
    try:
        emit(result)
    except Disconnected:
        pass
