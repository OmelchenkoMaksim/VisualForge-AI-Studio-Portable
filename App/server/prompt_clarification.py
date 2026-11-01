"""Bounded clarification protocol. Model-specific managers decide what to ask; this code enforces the two-question limit."""
from collections import OrderedDict
from contextlib import contextmanager
import copy
import hashlib
import json
import re
import secrets
import threading
import time

from ui_strings import source as ui_text

MAX_QUESTIONS = 2
MAX_OPTIONS = 8


def selection(answer, count):
    """Single-digit option IDs allow both '1, 3' and '13'; zero delegates the remaining choices to the manager."""
    if not isinstance(answer, str) or not 0 < len(answer) <= 40 or not re.fullmatch(r'[0-8\s,;]+', answer):
        raise ValueError(ui_text('image.clarification_invalid_selection'))
    numbers = []
    for value in re.findall(r'[0-8]', answer):
        number = int(value)
        if number in numbers:
            numbers.remove(number)
        numbers.append(number)  # Keep the last occurrence: '1,2,1' must resolve a final conflict in favor of option 1.
    if not numbers or any(number > count for number in numbers) or (0 in numbers and len(numbers) != 1):
        raise ValueError(ui_text('image.clarification_invalid_selection'))
    return numbers


def question(value):
    """Validate one generated question; display labels stay separate from English instructions sent to the model."""
    if not isinstance(value, dict) or not isinstance(value.get('question'), str) or not 1 <= len(value['question'].strip()) <= 400:
        raise ValueError('Invalid clarification question')
    options = value.get('options')
    if not isinstance(options, list) or not 2 <= len(options) <= MAX_OPTIONS:
        raise ValueError('Invalid clarification options')
    checked = []
    for index, option in enumerate(options, 1):
        if not isinstance(option, dict):
            raise ValueError('Invalid clarification option')
        label, instruction, group = (option.get(key, '') for key in ('label', 'value', 'group'))
        if (not isinstance(label, str) or not 1 <= len(label.strip()) <= 120 or
                not isinstance(instruction, str) or not 1 <= len(instruction.strip()) <= 400 or
                not isinstance(group, str) or (group and not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', group))):
            raise ValueError('Invalid clarification option')
        checked.append(dict(id=index, label=label.strip(), value=instruction.strip(), group=group))
    for field in ('label', 'value'):
        if len({option[field].casefold() for option in checked}) != len(checked):
            raise ValueError('Duplicate clarification options')
    return dict(question=value['question'].strip(), options=checked)


def fingerprint(prompt, engine, mode, context, binding, images):
    digest = hashlib.sha256(json.dumps([prompt, engine, mode, context, binding], sort_keys=True, ensure_ascii=False).encode())
    for raw in images:
        digest.update(len(raw).to_bytes(8, 'big'))
        digest.update(raw)
    return digest.hexdigest()


def last_per_group(options):
    """After the last answer, a conflicting group's last selection wins; compatible choices remain additive."""
    result = []
    for option in options:
        if option['group']:
            result = [old for old in result if old['group'] != option['group']]
        if option not in result:
            result.append(option)
    return result


class Clarifications:
    def __init__(self, *, clock=time.monotonic, ttl=1800, limit=32):
        self.clock, self.ttl, self.limit = clock, ttl, limit
        self.lock = threading.Lock()
        self.pending = OrderedDict()

    def _expire(self):
        now = self.clock()
        for token in list(self.pending):
            if now - self.pending[token]['created'] >= self.ttl:
                del self.pending[token]

    def _put(self, state):
        with self.lock:
            self._expire()
            while len(self.pending) >= self.limit:
                self.pending.popitem(last=False)
            token = secrets.token_hex(32)
            self.pending[token] = copy.deepcopy(state)
        return dict(state='clarification', clarification_token=token, round=state['round'], max_questions=MAX_QUESTIONS,
                    question=state['question'], options=[dict(id=option['id'], label=option['label']) for option in state['options']])

    def start(self, identity, proposal):
        proposal = question(proposal)
        return self._put(dict(**proposal, identity=identity, created=self.clock(), round=1, retained=[], defaults=[], turns=[]))

    def discard(self, token):
        if not isinstance(token, str):
            return False
        with self.lock:
            self._expire()
            return self.pending.pop(token, None) is not None

    @contextmanager
    def answer(self, token, identity, answer):
        """Reserve a continuation once. Failed model admission can retry it; concurrent/replayed answers cannot submit twice."""
        with self.lock:
            self._expire()
            state = self.pending.get(token) if isinstance(token, str) else None
            if state is None or state['identity'] != identity:
                raise ValueError(ui_text('image.clarification_session_expired'))
            ids = selection(answer, len(state['options']))
            del self.pending[token]
        try:
            yield self._resolve(state, ids)
        except BaseException:
            with self.lock:
                if self.clock() - state['created'] < self.ttl and len(self.pending) < self.limit:
                    self.pending[token] = state
            raise

    def _resolve(self, state, ids):
        chosen = [] if ids == [0] else [state['options'][number - 1] for number in ids]
        turns = state['turns'] + [dict(question=state['question'], selected=[option['label'] for option in chosen])]
        groups = {option['group'] for option in chosen if option['group']}
        conflicts = {group for group in groups if sum(option['group'] == group for option in chosen) > 1}
        if state['round'] == 1 and conflicts:
            conflicting = [option for option in chosen if option['group'] in conflicts]
            options = [dict(option, id=index) for index, option in enumerate(conflicting, 1)]
            return self._put(dict(identity=state['identity'], created=state['created'], round=2, turns=turns,
                                  question=ui_text('image.clarification_conflict_message'), options=options, defaults=conflicting,
                                  retained=[option for option in chosen if option['group'] not in conflicts]))
        # The second answer never reaches a question-producing branch, even if it selects incompatible options again.
        choices = last_per_group(state['retained'] + state['defaults'] + chosen)
        return dict(state='ready', choices=choices, clarification=dict(rounds=len(turns), turns=turns,
                    choices=[{key: option[key] for key in ('label', 'value', 'group')} for option in choices],
                    conflict_resolved=state['round'] == MAX_QUESTIONS))
