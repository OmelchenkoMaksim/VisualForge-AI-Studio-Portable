"""HTTP endpoints of the Components panel (served through the loopback bridge):
GET /api/components, POST /api/components/install {models, text_models, preset}, POST /api/components/cancel."""
from .engine import Busy, NotEnoughSpace, ProvisionError


def _strings(value, limit=64):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit or not all(isinstance(item, str) and 0 < len(item) <= 120 for item in value):
        raise ValueError('Ожидается список идентификаторов')
    return value


def handle(handler, manager, route, data):
    if manager is None:
        return handler.response_json(503, {'error': 'Установщик компонентов недоступен: нет App/config/components.json.'})
    try:
        if route == '' and data is None:
            return handler.response_json(200, manager.describe())
        if route == 'recovery' and data is None:
            return handler.response_json(200, dict(running=manager.running(), request=manager.recovery.snapshot()))
        if route == 'recovery-answer' and data is not None:
            return handler.response_json(200, manager.recovery.respond(data.get('token'), data.get('kind'), data.get('value', '')))
        if route == 'repair' and data is not None:
            return handler.response_json(202, manager.repair(data.get('id')))
        if route == 'recovery-file' and data is not None:
            pending = manager.recovery.snapshot()
            if not pending or pending['token'] != data.get('token'):
                raise ValueError('Запрос уже закрыт')
            from .recovery_picker import choose_file
            chosen = choose_file(pending['remaining_seconds'])
            return handler.response_json(200, dict(path=chosen))
        if route == 'install' and data is not None:
            preset = data.get('preset')
            if preset is not None and not isinstance(preset, str):
                raise ValueError('Некорректный набор')
            plan = manager.start(_strings(data.get('models')), _strings(data.get('text_models'), 4), preset)
            return handler.response_json(202, dict(plan, **manager.snapshot()))
        if route == 'cancel' and data is not None:
            return handler.response_json(200, {'cancelled': manager.cancel()})
        if route == 'model-toggle' and data is not None:
            return handler.response_json(200, manager.models.toggle(data.get('id'), data.get('enabled')))
        if route == 'model-remove-plan' and data is not None:
            return handler.response_json(200, manager.models.prepare(data.get('id')))
        if route == 'model-remove' and data is not None:
            return handler.response_json(200, manager.models.remove(data.get('token'), data.get('confirmation')))
        return handler.response_json(404, {'error': 'Not found'})
    except Busy as error:
        return handler.response_json(409, {'error': str(error)})
    except NotEnoughSpace as error:
        return handler.response_json(507, {'error': str(error)})
    except (ProvisionError, ValueError, KeyError) as error:
        return handler.response_json(400, {'error': str(error)[:600]})
