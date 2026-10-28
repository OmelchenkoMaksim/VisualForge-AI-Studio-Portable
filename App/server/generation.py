"""Four-field operation identities and routing to the independent central event history."""
from datetime import datetime, timezone
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from functools import wraps
import json
import math
import os
import secrets
import threading
import time

from event_history import EventHistory, MAIN_TYPES, TYPES as TYPE_LABELS

TYPES = frozenset(TYPE_LABELS)
_lock = threading.Lock()
_clocks = {}
_parents = ContextVar('generation_parent', default=None)
_store = None


def configure(path, inherit=False):
    global _store
    _store = EventHistory(path)
    _store.prepare()
    if inherit:
        os.environ['VISUALFORGE_EVENTS_DB'] = str(path)
    return _store


def history():
    global _store
    if _store is None and os.environ.get('VISUALFORGE_EVENTS_DB'):
        with _lock:
            if _store is None:
                _store = EventHistory(os.environ['VISUALFORGE_EVENTS_DB'])
    return _store


def _publish(record, state, parent_id=None):
    store = history()
    if store:
        store.safe_record(record, state, parent_id)


def _announce(event, record):
    if record['type'] not in MAIN_TYPES:
        return
    try:
        print('[generation:' + event + '] ' + json.dumps(record, ensure_ascii=False), flush=True)
    except (OSError, ValueError):
        # A closed diagnostic stream must not interrupt the model or leave its admission held.
        pass


def _date(timestamp):
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _seconds(value):
    try:
        return round(max(0.0, float(value)), 3) if value is not None and math.isfinite(float(value)) else None
    except (ValueError, TypeError, OverflowError):
        return None


def begin(kind, started_at=None, monotonic_started=None, *, parent_id=None):
    if kind not in TYPES:
        raise ValueError('Unknown generation type')
    with _lock:
        ident = secrets.token_hex(32)
        while ident in _clocks:
            ident = secrets.token_hex(32)
        _clocks[ident] = time.monotonic() if monotonic_started is None else monotonic_started
    record = dict(type=kind, id=ident, started_at=_date(time.time() if started_at is None else started_at), duration_sec=None)
    _publish(record, 'running', parent_id or _parents.get() or os.environ.get('VISUALFORGE_EVENT_PARENT'))
    _announce('start', record)
    return record


def finish(record, elapsed=None, *, state=None):
    """Freeze duration once; a wall clock adjustment cannot change a running job's measured time."""
    if not record:
        return record
    with _lock:
        if record.get('duration_sec') is not None:
            clock = None
        else:
            clock = _clocks.pop(record['id'], None)
            record['duration_sec'] = _seconds(time.monotonic() - clock if clock is not None else elapsed)
    if clock is not None or state is not None:
        _publish(record, state or 'done')
    if clock is not None:
        _announce('finish', record)
    return record


@contextmanager
def scope(record):
    token = _parents.set(record['id'])
    try:
        yield record
    finally:
        _parents.reset(token)


@contextmanager
def operation(kind, parent_id=None, *, cancel=None):
    record = begin(kind, parent_id=parent_id)
    try:
        with scope(record):
            yield record
    except BaseException:
        finish(record, state='cancelled' if cancel is not None and cancel.is_set() else 'error')
        raise
    else:
        finish(record, state='cancelled' if cancel is not None and cancel.is_set() else None)


def remove(record):
    store = history()
    if store and record:
        store.remove_generation(record['id'])


def parent():
    return _parents.get() or os.environ.get('VISUALFORGE_EVENT_PARENT')


def tracked(kind):
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            with operation(kind):
                return function(*args, **kwargs)
        return run
    return decorate


def legacy(kind, ident, started_at=None, duration_sec=None):
    """Old receipts get stable IDs without rewriting user files or inventing missing timestamps."""
    digest = hashlib.sha256(('VisualForge/generation/v1/' + kind + '/' + str(ident)).encode('utf-8')).hexdigest()
    return dict(type=kind, id=digest, started_at=_date(started_at), duration_sec=_seconds(duration_sec))


def duration_between(started_at, finished_at):
    try:
        return _seconds(float(finished_at) - float(started_at))
    except (TypeError, ValueError, OverflowError):
        return None


def ensure(row, kind, ident, started_at=None, duration_sec=None):
    record = row.get('generation')
    if not isinstance(record, dict) or set(record) != {'type', 'id', 'started_at', 'duration_sec'}:
        row['generation'] = legacy(kind, ident, started_at, duration_sec)
    return row['generation']


def append_log(path, record):
    if not record or record['type'] not in MAIN_TYPES:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as stream:
            stream.write('\n[generation] ' + json.dumps(record, ensure_ascii=False) + '\n')
    except OSError:
        # Diagnostic storage must not turn a completed generation into an error.
        pass
