"""HTTP endpoints of the Text module, served through the loopback bridge after its same-origin checks."""
from ui_strings import source as ui_text
import json
from generation import begin as generation_begin, finish as generation_finish
from urllib.parse import parse_qs

from text.text_manager import Busy
from text.store import MAX_CONTENT, MAX_IMAGES_PER_MESSAGE, decode_image


class Disconnected(Exception):
    """The browser closed the stream (Stop button, reload or closed tab)."""


def _stats(final, settings):
    seconds = (final.get('eval_duration') or 0) / 1e9
    return dict(prompt_tokens=final.get('prompt_eval_count', 0), completion_tokens=final.get('eval_count', 0), eval_sec=round(seconds, 2),
                tokens_per_sec=round(final.get('eval_count', 0) / seconds, 1) if seconds else 0, load_sec=round((final.get('load_duration') or 0) / 1e9, 2),
                total_sec=round((final.get('total_duration') or 0) / 1e9, 2), done_reason=final.get('done_reason', ''), context=settings['num_ctx'])


def handle(handler, manager, store, route, data, query):
    """Everything except the streaming `send`."""
    if manager is None or store is None:
        return handler.response_json(503, {'error': ui_text('text.routes_error_text_module_unavailable_check_startup_log')})
    ident = parse_qs(query.lstrip('?')).get('id', [''])[0]
    try:
        if data is None:
            if route == 'availability':
                answer = dict(manager.availability(), settings=manager.settings(), default_model=manager.default_model())
            elif route == 'status':
                answer = manager.status()
            elif route == 'chats':
                answer = dict(chats=store.list())
            elif route == 'chat':
                answer = store.get(ident)
            else:
                return handler.response_json(404, {'error': 'Unknown Text endpoint'})
        elif route == 'chats':
            if set(data) - {'system', 'model', 'think'}:
                raise ValueError(ui_text('text.routes_request_contains_unsupported_parameters'))
            answer = store.create(data.get('system', ''), data.get('model') or manager.default_model(), data.get('think', True))
        elif route == 'chat-rename':
            answer = store.rename(data.get('id'), data.get('title'))
        elif route == 'chat-update':
            if set(data) - {'id', 'system', 'think', 'model'}:
                raise ValueError(ui_text('text.routes_request_contains_unsupported_parameters'))
            answer = store.update(data.get('id'), data.get('system'), data.get('think'), data.get('model'))
        elif route == 'chat-delete':
            if manager.active.get(data.get('id')) is not None:
                raise Busy(ui_text('text.routes_chat_responding_stop_response_first'))
            store.delete(data.get('id'))
            answer = dict(deleted=True)
        elif route == 'settings':
            answer = manager.settings(data)
        elif route == 'stop':
            answer = manager.stop_chat(data.get('chat_id'))
        elif route == 'unload':
            answer = manager.release_gpu()
        else:
            return handler.response_json(404, {'error': 'Unknown Text endpoint'})
    except Busy as error:
        return handler.response_json(409, {'error': str(error)})
    except ValueError as error:
        return handler.response_json(400, {'error': str(error)[:800]})
    return handler.response_json(200, answer)


def image(handler, store, query):
    """Serve one stored chat image (the browser shows it in the message); ids are looked up in the chat file."""
    params = parse_qs(query.lstrip('?'))
    try:
        path, mime = store.image_path(params.get('chat', [''])[0], params.get('id', [''])[0])
    except (ValueError, OSError) as error:
        return handler.response_json(404, {'error': str(error)})
    raw = path.read_bytes()
    handler.send_response(200)
    handler.send_header('Content-Type', mime)
    handler.send_header('Content-Length', str(len(raw)))
    handler.send_header('Cache-Control', 'private, max-age=3600')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Content-Security-Policy', "default-src 'none'")
    handler.end_headers()
    handler.wfile.write(raw)


def send(handler, manager, store, data, admit):
    """Stream one answer as NDJSON events: status, delta, done, error. Raises Busy/ValueError before any byte is sent."""
    if manager is None or store is None:
        raise ValueError(ui_text('text.send_text_module_unavailable'))
    if not isinstance(data, dict) or set(data) - {'chat_id', 'content', 'regenerate', 'images'}:
        raise ValueError(ui_text('text.routes_request_contains_unsupported_parameters'))
    chat_id, regenerate = data.get('chat_id'), data.get('regenerate') is True
    chat = store.get(chat_id)
    if chat.get('kind') == 'agent':
        raise ValueError(ui_text('text.send_agent_chat_tasks_mode'))
    pictures = data.get('images') or []
    if regenerate and (pictures or data.get('content')):
        raise ValueError(ui_text('text.send_regeneration_does_not_accept_new_text'))
    if not isinstance(pictures, list) or len(pictures) > MAX_IMAGES_PER_MESSAGE:
        raise ValueError(ui_text('text.send_up_images_attached_message') % MAX_IMAGES_PER_MESSAGE)
    images = [decode_image(item.get('data'), item.get('name')) if isinstance(item, dict) else decode_image(None) for item in pictures]
    question = data.get('content', '')
    if not regenerate and (not isinstance(question, str) or len(question) > MAX_CONTENT or not (question.strip() or images)):
        raise ValueError(ui_text('text.send_message_up_characters_least_one_image') % MAX_CONTENT)
    model = chat.get('model') or manager.default_model()
    availability = manager.availability()
    usable = {m['name']: m for m in availability['models'] if m['ok']}
    if not availability['available'] or model not in usable:
        raise ValueError(availability['reason'] or ui_text('text.send_selected_model_not_installed'))
    if images and not usable[model].get('vision'):
        raise ValueError(ui_text('text.attach_selected_model_does_not_support_images'))
    cancel = admit(chat_id)
    generation_info = generation_begin('Text')
    content, thinking, final = [], [], None

    def emit(event):
        try:
            handler.wfile.write(json.dumps(event, ensure_ascii=False).encode('utf-8') + b'\n')
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            cancel.set()
            raise Disconnected from None

    try:
        chat = store.prepare_regenerate(chat_id) if regenerate else store.add_user(chat_id, question, images)
        chat = store.update_generation(chat_id, generation_info, request=True)
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Connection', 'close')
        handler.end_headers()
        error = ''
        try:
            emit(dict(type='status', phase=ui_text('text.send_preparing_response'), generation=generation_info))
            if not manager.alive():
                emit(dict(type='status', phase=ui_text('text.send_starting_ollama')))
            manager.start()
            if model not in [m['name'] for m in manager.loaded()]:
                emit(dict(type='status', phase=ui_text('text.send_loading_model_into_memory_first_load')))
            history = [dict(role='system', content=chat['system'])] if chat.get('system', '').strip() else []
            for m in chat['messages']:
                if m['content'].strip() or m.get('images'):
                    history.append(dict(role=m['role'], content=m['content'], **({'images': store.images_b64(chat_id, m)} if m.get('images') else {})))
            for row in manager.stream_chat(model, history, chat.get('think', True), cancel, generation_id=generation_info['id']):
                message = row.get('message') or {}
                piece, thought = message.get('content') or '', message.get('thinking') or ''
                if piece or thought:
                    content.append(piece)
                    thinking.append(thought)
                    emit(dict(type='delta', content=piece, thinking=thought))
                if row.get('done'):
                    final = row
        except Disconnected:
            pass
        except (OSError, ValueError, KeyError, TypeError) as failure:
            error = str(failure)[:500] or ui_text('text.send_get_model_response_failed')
        text, thought = ''.join(content), ''.join(thinking)
        stopped = cancel.is_set() and final is None
        generation_finish(generation_info, state='error' if error else 'cancelled' if stopped else 'done' if text.strip() or thought.strip() else 'error')
        if text.strip() or thought.strip():
            chat, saved = store.add_assistant(chat_id, text, thought, model, _stats(final, dict(num_ctx=manager.context_length(model))) if final else None, stopped, error, generation=generation_info)
            result = dict(type='done', message=saved, chat=dict(id=chat['id'], title=chat['title'], updated=chat['updated']))
        else:
            result = dict(type='error', error=error or (ui_text('text.send_response_stopped_before_first_words') if stopped else ui_text('text.send_model_returned_empty_response')), generation=generation_info)
        try:
            emit(dict(result, error=error) if result['type'] == 'done' and error else result)
        except Disconnected:
            pass
    finally:
        if generation_info.get('duration_sec') is None:
            generation_finish(generation_info, state='cancelled' if cancel.is_set() else 'error')
        try:
            store.update_generation(chat_id, generation_info)
        finally:
            manager.end(chat_id)
