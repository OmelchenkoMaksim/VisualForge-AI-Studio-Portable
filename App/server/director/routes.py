"""HTTP side of the Director, served through the bridge's safe-origin loopback handler.

    GET  /api/director/draft?id=<draft>     the attachments of a film draft and the running time that suits them
    GET  /api/director/status?id=<run>      the state of a shooting (without an id: the latest one)
    GET  /api/director/planning             where the plan being written has come to (stage, first and last scene, scenes in all)
    GET  /api/director/media?draft=<id>&number=<n>   an attachment as it is kept (the page plays songs and voice samples, shows pictures), with byte ranges
    POST /api/director/attach?draft=<id>&group=<group>&name=<file name>[&consent=1]   raw file bytes (a video or a song streams to disk); a voice needs consent=1
    POST /api/director/detach  {draft, number}
    POST /api/director/label   {draft, number, text}
    POST /api/director/plan    {draft, prompt, seconds, scenes, orientation?, quality?, language?}
    POST /api/director/start   {draft, plan, prompt, seconds, scenes}
    POST /api/director/cancel  {id}
    POST /api/director/resume  {id}    continue a film that failed, was stopped or was interrupted, from the steps that are done
    POST /api/director/discard {id}    give such a film up: its scene clips leave the Video history and its files are removed
"""
from ui_strings import source as ui_text
from urllib.parse import parse_qs, unquote
import uuid

from director import media
from director import plan_rules as rules
from director.studio import DirectorError
from gpu_coordinator import Busy, GpuUnavailable
from range_response import serve_file

BODY_LIMITS = dict(photo=rules.MAX_PHOTO_BYTES, video=rules.MAX_VIDEO_BYTES, audio=rules.MAX_SONG_BYTES, voice=rules.MAX_VOICE_BYTES)


def _answer(handler, work):
    try:
        return handler.response_json(200, work())
    except Busy as error:
        return handler.response_json(409, {'error': str(error)})
    except GpuUnavailable as error:
        return handler.response_json(503, {'error': str(error)})
    except (ValueError, DirectorError) as error:
        return handler.response_json(400, {'error': str(error)[:800]})


def handle_get(handler, manager, route, query):
    if manager is None:
        return handler.response_json(503, {'error': ui_text('director.routes_director_unavailable')})
    params = parse_qs(query.lstrip('?'))
    ident = params.get('id', [''])[0]
    if route == 'draft':
        return _answer(handler, lambda: manager.draft(ident))
    if route == 'status':
        return _answer(handler, lambda: manager.status(ident or None))
    if route == 'planning':
        return _answer(handler, manager.planning_state)
    if route == 'media':
        try:
            path, kind = manager.media_file(params.get('draft', [''])[0], int(params.get('number', ['0'])[0]))
        except ValueError as error:
            return handler.response_json(404, {'error': str(error)[:300]})
        return serve_file(handler, path, kind, path.name, 'no-store')
    return handler.response_json(404, {'error': 'Unknown Director endpoint'})


def handle_post(handler, manager, route, data):
    if manager is None:
        return handler.response_json(503, {'error': ui_text('director.routes_director_unavailable')})
    language = 'English' if data.get('language') == 'English' else 'Russian'
    request = {key: data.get(key) for key in ('prompt', 'seconds', 'scenes', 'orientation', 'quality', 'ultra')}
    request['language'] = language
    if route == 'plan':
        return _answer(handler, lambda: manager.make_plan(data.get('draft'), request, language))
    if route == 'start':
        return _answer(handler, lambda: manager.start(data.get('draft'), data.get('plan'), request))
    if route == 'cancel':
        return _answer(handler, lambda: manager.cancel_run(data.get('id')))
    if route == 'resume':
        return _answer(handler, lambda: manager.resume(data.get('id')))
    if route == 'discard':
        return _answer(handler, lambda: manager.discard(data.get('id')))
    if route == 'detach':
        return _answer(handler, lambda: manager.detach(data.get('draft'), data.get('number')))
    if route == 'label':
        return _answer(handler, lambda: manager.label(data.get('draft'), data.get('number'), data.get('text')))
    return handler.response_json(404, {'error': 'Unknown Director endpoint'})


def attach(handler, manager, query, upload_folder):
    """The raw body is a picture, a video, a song or a voice sample; the name and the group travel in the query because the body is not JSON.

    `kind=photo|video` (the older form of the query) names the first group of that kind."""
    if manager is None:
        return handler.response_json(503, {'error': ui_text('director.routes_director_unavailable')})
    params = {key: values[0] for key, values in parse_qs(query.lstrip('?')).items()}
    name = unquote(params.get('name', 'file'))[:120]
    group = params.get('group') or rules.DEFAULT_GROUP.get(params.get('kind'))
    try:
        size = int(handler.headers.get('Content-Length', '0') or '0')
    except ValueError:
        size = 0

    def work():
        manager.draft(params.get('draft'))              # a draft that does not exist is refused before a gigabyte is read
        if group not in rules.MEDIA_OF:
            raise ValueError(ui_text('director.routes_unknown_kind'))
        kind = rules.MEDIA_OF[group]
        if kind == 'voice' and params.get('consent') != '1':
            raise ValueError(ui_text('director.routes_voice_consent_needed'))
        if kind == 'photo':
            if not 0 < size <= BODY_LIMITS['photo']:
                raise ValueError(ui_text('director.media_body_size', BODY_LIMITS['photo'] // (1024 * 1024)))
            return manager.attach_photo(params.get('draft'), handler.rfile.read(size), name, group)
        upload_folder.mkdir(parents=True, exist_ok=True)
        target = upload_folder / (uuid.uuid4().hex + '.part')
        media.save_body(handler.rfile, size, target, BODY_LIMITS[kind])
        return dict(video=manager.attach_video, audio=manager.attach_song, voice=manager.attach_voice)[kind](params.get('draft'), target, name, group)
    return _answer(handler, work)
