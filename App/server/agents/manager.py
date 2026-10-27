"""Agent chats of the Text tab: one agent task at a time, its events translated for the page and its result kept in the chat.

OpenCode keeps the working conversation itself (a session per chat); Aider is started per task and gets the earlier messages of
the chat; dsh is started per task and resumes its own persisted session. The studio's chat file stores what the user saw: the request, the answer, the steps (commands, edits) and their outcome.
The model is reached only through `ModelGateway`, so each model request of the agent waits for the video card like a chat answer
and Image/Video/Music wait for it."""
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import time
from typing import Callable

from gpu import Busy
from .aider import AiderRunner
from .dsh import DshRunner
from .gateway import ModelGateway
from .opencode import OpenCodeError, OpenCodeServer, DEFAULT_PORT as OPENCODE_PORT
from .registry import AGENTS, DEFAULT_AGENT, DEFAULT_PERMISSION, PERMISSION_HINTS, PERMISSION_MODES, get_agent

GATEWAY_PORT = 8293
GPU_WAIT_SEC = 30 * 60  # an agent request waits this long for Image/Video/Music to finish before it fails
MAX_STEP_OUTPUT = 4000
MAX_STEPS = 200
# OpenCode's own prompt is English and local models then answer in English; the studio's interface is Russian.
DEFAULT_SYSTEM = 'Отвечай пользователю на русском языке, если он сам не попросил другой язык.'
AIDER_HISTORY = 20  # earlier messages of the chat Aider gets with a new task (it summarises them when they do not fit)


def _port(paths, key, env, default):
    configured = default
    if paths.modern and paths.ports.is_file() and paths.ports.stat().st_size <= 8192:
        configured = json.loads(paths.ports.read_text(encoding='utf-8-sig')).get(key, default)
    port = int(os.environ.get(env, configured))
    taken = {int(os.environ.get('QWEN_UI_PORT', '8189')), int(os.environ.get('QWEN_COMFY_PORT', '8188'))}
    if not 1024 <= port <= 65535 or port in taken:
        raise ValueError('Invalid agent port %s' % key)
    return port


def workspace_path(value):
    """An existing absolute folder that is not a drive root (an agent confined to C:\\ is not confined at all)."""
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError('Выберите папку проекта для агента')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError('Папка проекта: нужен полный путь')
    try:
        path = path.resolve(strict=True)
    except OSError:
        raise ValueError('Папка проекта не найдена') from None
    if not path.is_dir():
        raise ValueError('Папка проекта: путь указывает не на папку')
    if path.parent == path:
        raise ValueError('Корень диска нельзя отдать агенту: выберите папку проекта')
    return str(path)


def _clip(text, limit):
    text = str(text or '')
    return text if len(text) <= limit else text[:limit] + '\n… (обрезано: ещё %d симв.)' % (len(text) - limit)


def step_from(part):
    """A tool part of OpenCode as a compact step for the page and the chat file."""
    state = part.get('state') or {}
    tool_input = state.get('input') or {}
    title = state.get('title') or tool_input.get('description') or tool_input.get('command') or tool_input.get('filePath') or part.get('tool', '')
    step = dict(id=part.get('id', ''), tool=str(part.get('tool', ''))[:60], status=state.get('status', 'pending'), title=str(title)[:300])
    if tool_input.get('command'):
        step['command'] = str(tool_input['command'])[:2000]
    if tool_input.get('filePath'):
        step['file'] = str(tool_input['filePath'])[:500]
    if state.get('status') == 'completed':
        step['output'] = _clip(state.get('output'), MAX_STEP_OUTPUT)
        diff = (state.get('metadata') or {}).get('diff')
        if isinstance(diff, str) and diff:
            step['diff'] = _clip(diff, MAX_STEP_OUTPUT)
    elif state.get('status') == 'error':
        message = str(state.get('error') or '')
        # OpenCode's own wording when a rule or the user refused the call; the page shows Russian.
        if message.startswith('The user rejected permission'):
            message = 'Вы отклонили это действие.'
        elif 'prevents you from using this specific tool call' in message or message.startswith('The user has specified a rule'):
            message = 'Запрещено правами агента в этом чате.'
        step['error'] = _clip(message, 1000)
    return step


def error_text(error):
    if not isinstance(error, dict):
        return 'Агент завершился с ошибкой'
    name, message = error.get('name', ''), (error.get('data') or {}).get('message', '')
    if name == 'MessageAbortedError':
        return ''
    if name == 'ContextOverflowError':
        return 'Разговор не помещается в контекст модели: начните новый чат агента или увеличьте контекст'
    return _clip(message or name or 'Агент завершился с ошибкой', 500)


class AgentManager:
    def __init__(self, paths, text, store, gpu, gpu_lock, opencode=None, aider=None, dsh=None):
        self.paths, self.text, self.store, self.gpu, self.gpu_lock = paths, text, store, gpu, gpu_lock
        self.settings_path = paths.settings / 'agents.settings.json'
        self.gateway = ModelGateway(_port(paths, 'AgentGatewayPort', 'VISUALFORGE_AGENT_GATEWAY_PORT', GATEWAY_PORT),
                                    upstream=self._upstream, admit=self._admit, finish=self._finish, models=self._aliases)
        self.opencode = opencode or OpenCodeServer(paths, paths.root / AGENTS['opencode']['executable'],
                                                   _port(paths, 'OpenCodePort', 'VISUALFORGE_OPENCODE_PORT', OPENCODE_PORT))
        self.aider = aider or AiderRunner(paths, paths.root / AGENTS['aider']['executable'])
        self.dsh = dsh or DshRunner(paths, paths.root / AGENTS['dsh']['executable'], paths.root / 'Runtime/Agents/dsh/app')
        self.run = None
        self.lock = threading.RLock()

    # ---- models and settings ------------------------------------------------------------------------------------
    def _models(self):
        """Installed Text models under short aliases (Ollama names contain ':' and '/', which OpenCode splits on)."""
        result = {}
        for model in self.text.installed_models():
            if model['ok']:
                alias = 'm-' + hashlib.sha1(model['name'].encode('utf-8')).hexdigest()[:10]
                result[alias] = dict(name=model['name'], label=model['label'], context=self.text.context_length(model['name']))
        return result

    def _aliases(self):
        return {alias: info['name'] for alias, info in self._models().items()}

    def alias_of(self, name):
        for alias, info in self._models().items():
            if info['name'] == name:
                return alias
        raise ValueError('Модель этого чата не установлена или отключена')

    def settings(self, data=None):
        current = dict(agent=DEFAULT_AGENT, workspace='', permission=DEFAULT_PERMISSION)
        if self.settings_path.is_file() and self.settings_path.stat().st_size <= 8192:
            try:
                stored = json.loads(self.settings_path.read_text(encoding='utf-8'))
                current.update({key: value for key, value in stored.items() if key in current})
            except ValueError:
                pass
        if data is None:
            return current
        if not isinstance(data, dict) or set(data) - set(current):
            raise ValueError('Настройки агента содержат неподдерживаемые параметры')
        if 'agent' in data:
            get_agent(data['agent'])
        if 'permission' in data and data['permission'] not in PERMISSION_MODES:
            raise ValueError('Неизвестный уровень прав агента')
        if data.get('workspace'):
            data = dict(data, workspace=workspace_path(data['workspace']))
        current.update(data)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_name(self.settings_path.name + '.tmp')
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(temporary, self.settings_path)
        return current

    def availability(self):
        agents = []
        runners = dict(opencode=self.opencode, aider=self.aider, dsh=self.dsh)
        for agent in AGENTS.values():
            installed = not agent['planned'] and agent['id'] in runners and runners[agent['id']].installed()
            agents.append(dict(agent, installed=installed, ready=installed))
        with self.lock:
            run = dict(chat=self.run['chat'], waiting=self.run.get('waiting', '')) if self.run else None
        return dict(agents=agents, modes=PERMISSION_MODES, hints=PERMISSION_HINTS, settings=self.settings(), running=run,
                    server=self.opencode.alive(), offline=True)

    # ---- GPU admission for the gateway --------------------------------------------------------------------------
    def _key(self, run):
        return 'agent:' + run['chat']

    def _upstream(self):
        self.text.start()
        return self.text.port

    def _admit(self):
        """Block until this agent request may use the GPU: Image/Video/Music jobs and a chat answer finish first."""
        deadline = time.monotonic() + GPU_WAIT_SEC
        while True:
            with self.lock:
                run = self.run
            if run is None:
                raise Busy('Агент сейчас не выполняет задачу')
            if run['cancel'].is_set():
                raise ValueError('Задача агента остановлена')
            try:
                with self.gpu_lock:
                    self.gpu.claim('text')
                    event = self.text.begin(self._key(run))
                run['waiting'] = ''
                # A cold load from a hard disk takes minutes with no output at all: say so instead of a silent start.
                loaded = run['model'] in [m['name'] for m in self.text.loaded()]
                run['emit'](dict(type='status', phase='Модель обдумывает следующий шаг…' if loaded else
                                 'Загрузка модели в видеопамять: первая загрузка с диска может занять несколько минут…'))
                return event
            except Busy as error:
                if time.monotonic() > deadline:
                    raise
                if run.get('waiting') != str(error):
                    run['waiting'] = str(error)
                    run['emit'](dict(type='status', phase='Агент ждёт видеокарту: ' + str(error)))
                run['cancel'].wait(2)

    def _finish(self):
        with self.lock:
            run = self.run
        if run is not None:
            self.text.end(self._key(run))

    # ---- chats --------------------------------------------------------------------------------------------------
    def create_chat(self, data):
        if not isinstance(data, dict) or set(data) - {'agent', 'workspace', 'permission', 'model', 'system'}:
            raise ValueError('Запрос содержит неподдерживаемые параметры')
        agent = get_agent(data.get('agent') or DEFAULT_AGENT)
        if agent['planned']:
            raise ValueError('%s пока не подключён' % agent['name'])
        permission = data.get('permission') or DEFAULT_PERMISSION
        if permission not in PERMISSION_MODES:
            raise ValueError('Неизвестный уровень прав агента')
        workspace = workspace_path(data.get('workspace'))
        model = data.get('model') or self.text.default_model()
        self.settings(dict(agent=agent['id'], workspace=workspace, permission=permission))
        return self.store.create(data.get('system', ''), model, True, agent=dict(id=agent['id'], workspace=workspace, permission=permission, session=''))

    def update_chat(self, data):
        if not isinstance(data, dict) or set(data) - {'id', 'workspace', 'permission'}:
            raise ValueError('Запрос содержит неподдерживаемые параметры')
        chat = self.store.get(data.get('id'))
        if chat.get('kind') != 'agent':
            raise ValueError('Это обычный чат, а не чат агента')
        with self.lock:
            if self.run and self.run['chat'] == chat['id']:
                raise Busy('Агент работает в этом чате: сначала дождитесь окончания или остановите его')
        changes = {}
        if 'permission' in data:
            if data['permission'] not in PERMISSION_MODES:
                raise ValueError('Неизвестный уровень прав агента')
            changes['permission'] = data['permission']
            # dsh reads the level from the config patch of every task, so its session simply continues.
            if chat['agent'].get('session') and chat['agent']['id'] == 'opencode' and self.opencode.alive():
                try:
                    self.opencode.set_mode(chat['agent']['session'], chat['agent']['workspace'], data['permission'])
                except (OSError, OpenCodeError):
                    changes['session'] = ''  # the next task opens a new session with the new rules
        if 'workspace' in data:
            workspace = workspace_path(data['workspace'])
            if workspace != chat['agent']['workspace']:
                changes.update(workspace=workspace, session='')  # an OpenCode session belongs to one folder
        self.settings({key: value for key, value in changes.items() if key in ('workspace', 'permission')})
        return self.store.set_agent(chat['id'], **changes)

    # ---- one task -----------------------------------------------------------------------------------------------
    def busy(self):
        with self.lock:
            return self.run is not None

    def send(self, chat_id, content, emit, disconnected):
        """Run one agent task and stream its events through `emit`; returns when the agent is idle, stopped or failed."""
        if not isinstance(content, str) or not content.strip() or len(content) > 100_000:
            raise ValueError('Задание агенту: текст до 100 000 символов')
        chat = self.store.get(chat_id)
        if chat.get('kind') != 'agent':
            raise ValueError('Это обычный чат: включите режим «Агент» и создайте чат агента')
        agent, directory = chat['agent'], chat['agent']['workspace']
        workspace_path(directory)
        alias = self.alias_of(chat.get('model') or self.text.default_model())
        cancel, gone, writing = threading.Event(), threading.Event(), threading.Lock()
        raw_emit = emit

        def emit(event):
            # The gateway thread reports GPU waiting while this thread relays events: one writer at a time, and a closed
            # page only marks the task as abandoned instead of raising inside the gateway.
            with writing:
                if gone.is_set():
                    return
                try:
                    raw_emit(event)
                except Exception:
                    gone.set()

        abandoned = disconnected
        disconnected = lambda: gone.is_set() or abandoned()
        with self.lock:
            if self.run is not None:
                raise Busy('Агент уже выполняет задачу. Дождитесь окончания или остановите её.')
            self.run = dict(chat=chat_id, cancel=cancel, emit=emit, waiting='', session='', directory=directory, pending={},
                            model=self._models()[alias]['name'], agent=agent['id'])
        session = ''
        try:
            history = [m for m in chat['messages'] if m.get('content') and not m.get('error')][-AIDER_HISTORY:]
            self.store.add_user(chat_id, content)
            if agent['id'] in ('aider', 'dsh'):
                return self._send_process(agent['id'], chat, content, alias, history, emit, cancel, disconnected)
            emit(dict(type='status', phase='Запуск OpenCode…' if not self.opencode.alive() else 'Агент получил задание'))
            self.gateway.start()
            self.opencode.start(self.gateway.base_url, self.gateway.token, self._models())
            session = agent.get('session', '')
            if not session or not self.opencode.session_exists(session, directory):
                session = self.opencode.create_session(directory, chat['title'] if not chat.get('title_auto') else content, agent['permission'])['id']
                self.store.set_agent(chat_id, session=session)
            self.run['session'] = session
            events = self.opencode.subscribe(session)
            try:
                self.opencode.prompt(session, directory, content, alias, chat.get('system', '').strip() or DEFAULT_SYSTEM)
                error = self._follow(events, emit, cancel, disconnected)
            finally:
                self.opencode.unsubscribe(session)
            return self._save(chat_id, session, directory, chat.get('model', ''), error, cancel.is_set())
        except (OSError, OpenCodeError, ValueError) as failure:
            if session:
                self.opencode.abort(session, directory)
            message = str(failure)[:500] or 'Агент не смог выполнить задание'
            self.store.add_assistant(chat_id, '', '', chat.get('model', ''), None, False, message)
            raise ValueError(message) from None
        finally:
            with self.lock:
                self.run = None

    def _send_process(self, kind, chat, content, alias, history, emit, cancel, disconnected):
        """One task of an agent started per task (Aider, dsh): its runner speaks the page's events; keep the steps and text."""
        runner = self.aider if kind == 'aider' else self.dsh
        steps, text, thinking = {}, [], []

        def relay(event):
            kind = event.get('type')
            if kind == 'step' and isinstance(event.get('step'), dict):
                if len(steps) < MAX_STEPS or event['step'].get('id') in steps:
                    steps[event['step'].get('id')] = event['step']
            elif kind == 'delta':
                text.append(event.get('content') or '')
                thinking.append(event.get('thinking') or '')
            elif kind == 'permission' and isinstance(event.get('request'), dict):
                self.run['pending'][event['request'].get('id')] = event['request']
            elif kind == 'permission_done':
                self.run['pending'].pop(event.get('id'), None)
            elif kind == 'session':  # dsh keeps its own conversation; the next task resumes it
                self.run['session'] = event.get('id', '')
                self.store.set_agent(chat['id'], session=self.run['session'])
                return
            elif kind != 'status':
                return
            emit(event)

        emit(dict(type='status', phase='Запуск Aider…' if kind == 'aider' else 'Запуск DeepSeek Harness…'))
        self.gateway.start()
        info = self._models()[alias]
        start = dict(workspace=chat['agent']['workspace'], task=content, mode=chat['agent']['permission'], alias=alias, label=info['label'],
                     context=info['context'], output=min(8192, info['context'] // 4), base_url=self.gateway.base_url, token=self.gateway.token)
        if kind == 'aider':
            start.update(system=chat.get('system', ''), history=[dict(role=m['role'], content=m['content']) for m in history], state=str(self.aider.state))
        else:
            start.update(system=chat.get('system', '').strip() or DEFAULT_SYSTEM, session=chat['agent'].get('session', ''))
        done = runner.run(start, relay, cancel, disconnected)
        model = chat.get('model', '')
        if done is None:  # stopped: keep what the page already showed
            body, reasoning, stats, error = ''.join(text).strip(), ''.join(thinking).strip(), None, ''
        else:
            body, reasoning, stats, error = done.get('content', ''), done.get('thinking', ''), done.get('stats'), done.get('error', '')
            if stats:
                stats = dict(stats, context=self.text.context_length(model) if model else stats.get('context', 0))
        chat, message = self.store.add_assistant(chat['id'], body, reasoning, model, stats, done is None, error, steps=list(steps.values()))
        return chat, message, error

    def _follow(self, events, emit: Callable[[dict], None], cancel, disconnected: Callable[[], bool]):
        """Translate OpenCode events until the session is idle; returns an error text ('' when it finished or was stopped)."""
        kinds, assistant_messages, error = {}, set(), ''
        while True:
            if cancel.is_set() or disconnected():
                cancel.set()
                self.opencode.abort(self.run['session'], self.run['directory'])
                # Let OpenCode write the aborted message before it is read back for the chat file.
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        if events.get(timeout=0.5).get('type') == 'session.idle':
                            break
                    except queue.Empty:
                        continue
                return ''
            try:
                event = events.get(timeout=0.5)
            except queue.Empty:
                continue
            kind, props = event.get('type'), event.get('properties') or {}
            if kind == 'server.closed':
                return 'OpenCode неожиданно завершился. Подробности — в журнале agent-opencode.log.'
            if kind == 'message.updated':
                info = props.get('info') or {}
                if info.get('role') == 'assistant':
                    assistant_messages.add(info.get('id'))
                    if info.get('error'):
                        error = error_text(info['error']) or error
            elif kind == 'message.part.updated':
                part = props.get('part') or {}
                kinds[part.get('id')] = part.get('type')
                if part.get('type') == 'tool':
                    emit(dict(type='step', step=step_from(part)))
            elif kind == 'message.part.delta':
                if props.get('messageID') in assistant_messages and props.get('field') == 'text':
                    if kinds.get(props.get('partID')) == 'reasoning':
                        emit(dict(type='delta', content='', thinking=props.get('delta', '')))
                    elif kinds.get(props.get('partID')) == 'text':
                        emit(dict(type='delta', content=props.get('delta', ''), thinking=''))
            elif kind == 'permission.asked':
                request = dict(id=props.get('id'), permission=props.get('permission'), patterns=list(props.get('patterns') or [])[:20],
                               always=list(props.get('always') or [])[:20], command=str((props.get('metadata') or {}).get('command', ''))[:2000],
                               file=str((props.get('metadata') or {}).get('filepath', '') or (props.get('metadata') or {}).get('filePath', ''))[:500],
                               diff=_clip((props.get('metadata') or {}).get('diff', ''), MAX_STEP_OUTPUT))
                self.run['pending'][request['id']] = request
                emit(dict(type='permission', request=request))
            elif kind == 'permission.replied':
                self.run['pending'].pop(props.get('requestID'), None)
                emit(dict(type='permission_done', id=props.get('requestID'), reply=props.get('reply')))
            elif kind == 'session.status':
                status = props.get('status') or {}
                if status.get('type') == 'retry':
                    emit(dict(type='status', phase='Повтор запроса к модели: ' + _clip(status.get('message'), 300)))
            elif kind == 'session.error':
                error = error_text(props.get('error')) or error
            elif kind == 'session.idle':
                return error

    def _save(self, chat_id, session, directory, model, error, stopped):
        """Store the answer of this task as the user saw it: OpenCode's own messages after the last request are the source."""
        content, thinking, steps, stats = [], [], [], None
        calls = output = context = 0
        try:
            rows = self.opencode.messages(session, directory)
        except (OSError, OpenCodeError):
            rows = []
        last_user = max((index for index, row in enumerate(rows) if (row.get('info') or {}).get('role') == 'user'), default=-1)
        for row in rows[last_user + 1:]:
            info = row.get('info') or {}
            if info.get('role') != 'assistant':
                continue
            for part in row.get('parts') or []:
                if part.get('type') == 'text' and part.get('text'):
                    content.append(part['text'])
                elif part.get('type') == 'reasoning' and part.get('text'):
                    thinking.append(part['text'])
                elif part.get('type') == 'tool' and len(steps) < MAX_STEPS:
                    steps.append(step_from(part))
            tokens = info.get('tokens') or {}
            if tokens:
                # One assistant message per model call; its input excludes the part OpenCode counts as cached.
                cache = tokens.get('cache') or {}
                calls += 1
                output += int(tokens.get('output') or 0)
                context = int(tokens.get('input') or 0) + int(cache.get('read') or 0) + int(cache.get('write') or 0)
            if info.get('error') and not error:
                error = error_text(info['error'])
        if calls:
            stats = dict(model_calls=calls, completion_tokens=output, context_tokens=context, context=self.text.context_length(model) if model else 0)
        chat, message = self.store.add_assistant(chat_id, '\n\n'.join(content), '\n\n'.join(thinking), model, stats, stopped, error, steps=steps)
        return chat, message, error

    def reply(self, data):
        if not isinstance(data, dict) or set(data) - {'chat_id', 'request_id', 'reply'}:
            raise ValueError('Запрос содержит неподдерживаемые параметры')
        with self.lock:
            run = self.run
            if run is None or run['chat'] != data.get('chat_id') or data.get('request_id') not in run['pending']:
                raise ValueError('Этот запрос агента уже не ждёт ответа')
        if run['agent'] == 'aider':
            self.aider.reply(data['request_id'], data.get('reply'))
        elif run['agent'] == 'dsh':
            self.dsh.reply(data['request_id'], data.get('reply'))
        else:
            self.opencode.reply(data['request_id'], data.get('reply'), run['directory'])
        return dict(replied=True)

    def stop(self, chat_id=None):
        with self.lock:
            run = self.run
        if run is None or (chat_id is not None and run['chat'] != chat_id):
            return dict(stopped=False)
        run['cancel'].set()
        self.text.stop_chat(self._key(run))
        return dict(stopped=True)

    def close(self):
        self.stop()
        self.aider.close()
        self.dsh.close()
        self.opencode.close()
        self.gateway.close()
