"""DeepSeek Harness (dsh) tasks: `dsh --profile acp` in the studio's portable Node.js, driven over the Agent Client Protocol.

ACP is JSON-RPC 2.0 over stdio: the studio sends initialize, session/new or session/resume and session/prompt; dsh streams
session/update notifications (committed answers and thoughts, tool calls, context usage) and asks session/request_permission.
One process per task, like Aider; the dsh session persists under UserData/Cache/Agents/dsh/home and is resumed by id, so the
agent keeps its own working conversation across tasks and studio restarts.

The shipped profile talks to DeepSeek's cloud (models, web search, telemetry). A config patch written for every task disables
those plugins, routes the only provider to the studio gateway, keeps dsh's own sandbox off and inserts the studio's
permission plugin (dsh_policy.mjs); dsh_net_guard.cjs, preloaded into Node, refuses every other network connection."""
import difflib
import json
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit

from .opencode import clean_environment

PROVIDER = 'visualforge'
# Plugins of the shipped `acp` profile the studio switches off: DeepSeek account, cloud model routes, web search and fetch,
# telemetry export, and the subagent/workflow tools (more model calls than a local model can afford, no extra value here).
DISABLED = ('otel', 'session-telemetry-otel', 'deepseek-account', 'llm-deepseek', 'llm-deepseek-account', 'web', 'web-search-deepseek',
            'web-deepseek-api', 'web-fetch-http', 'tool-web', 'tool-subagent', 'tool-subagent-fork', 'tool-subagent-control',
            'tool-subagent-list-agents', 'tool-workflow', 'workflow-ptc')
# dsh's own Windows sandbox stays off: for `workspace-write` it permanently rewrites the project folder's security descriptor (a
# capability-SID allow, an Everyone deny of "delete subfolders and files" inherited by subfolders, a Low integrity label) and never
# reverts it. Commands run unconfined, as with OpenCode and Aider; dsh_policy.mjs decides every tool call by the chat's level.
SANDBOX = 'danger-full-access'
TOOLS = {'pwsh': 'bash', 'bash': 'bash'}  # the page labels shell tools as «Команда»
MAX_OUTPUT = 4000
REFUSED = 'Запрещено правами агента в этом чате.'
REJECTED = 'Вы отклонили это действие.'


class DshError(ValueError):
    """dsh failed or could not start; the message is shown to the user."""


def clip(text, limit=MAX_OUTPUT):
    text = str(text or '')
    return text if len(text) <= limit else text[:limit] + '\n… (обрезано: ещё %d симв.)' % (len(text) - limit)


def _text(blocks):
    parts = []
    for block in blocks or []:
        content = block.get('content') if block.get('type') == 'content' else block
        if isinstance(content, dict) and content.get('type') == 'text':
            parts.append(str(content.get('text', '')))
    return '\n'.join(parts)


def _diff(name, old, new):
    return ''.join(difflib.unified_diff(str(old).splitlines(True), str(new).splitlines(True), name, name))


def preview(tool, raw):
    """What an edit will change, from the tool's own arguments (dsh reports only the result text afterwards)."""
    raw = raw or {}
    name = str(raw.get('file_path') or raw.get('path') or '')
    if tool == 'edit' and 'old_string' in raw:
        return clip(_diff(name, raw.get('old_string', ''), raw.get('new_string', '')))
    if tool == 'write':
        return clip(_diff(name, '', raw.get('content', '')))
    return ''


def step_from(call, raw, status, output=''):
    """One dsh tool call as a page step. `call` is the tool name dsh reports, `raw` its arguments."""
    raw = raw if isinstance(raw, dict) else {}
    tool = TOOLS.get(call, call)
    title = raw.get('description') or raw.get('command') or raw.get('file_path') or raw.get('pattern') or call
    step = dict(tool=str(tool)[:60], status=status, title=str(title)[:300])
    if raw.get('command'):
        step['command'] = str(raw['command'])[:2000]
    if raw.get('file_path'):
        step['file'] = str(raw['file_path'])[:500]
    if status == 'completed':
        step['output'] = clip(output)
        change = preview(call, raw)
        if change:
            step['diff'] = change
    elif status == 'error':
        message = str(output or '')
        if 'VisualForge:' in message:
            message = REFUSED
        elif 'the user rejected tool' in message:
            message = REJECTED
        step['error'] = clip(message, 1000)
    return step


class DshRunner:
    def __init__(self, paths, node, app, command=None):
        self.paths = paths
        self.node = Path(node)
        self.bin = Path(app) / 'node_modules/@deepseek-ai/dsh/lib/bin.js'
        self.command = command  # tests may run a stand-in
        self.state = paths.cache / 'Agents/dsh'
        self.process = None
        self.job = None
        self.lock = threading.Lock()
        self.pending = {}
        self.always = set()

    def installed(self):
        return self.command is not None or (self.node.is_file() and self.bin.is_file())

    # ---- configuration ------------------------------------------------------------------------------------------
    def patch(self, start):
        """The config overlay for one task (JSON is YAML; dsh applies it after the shipped `acp` profile)."""
        model = dict(id=start['alias'], name=start.get('label') or start['alias'], contextWindow=int(start['context']),
                     maxTokens=int(start['output']), input=['text'])
        persona = 'You are a coding agent powered by the {{model}} model.\n' + start['system']
        return [dict(id=ident, disabled=True) for ident in DISABLED] + [
            dict(id='llm-pi-ai', config=dict(providers={PROVIDER: dict(api='openai-completions', baseURL=start['base_url'], apiKeyEnv='VF_GATEWAY_TOKEN',
                                                                       displayName='VisualForge', models=[model])})),
            dict(id='agent-default-model', config=dict(provider=PROVIDER, model=start['alias'])),
            dict(id='acp', config=dict(provider=PROVIDER, model=start['alias'])),
            dict(id='system-prompt', config=dict(personaPrefix=persona)),
            dict(id='sandbox-policy', config=dict(mode=SANDBOX, workspaceRoot=start['workspace'])),
            dict(id='approval', config=dict(policy='ask')),
            # The shipped presets pair full access with approval `never`; without a matching preset dsh refuses to load.
            dict(id='permission', config=dict(presets=dict(visualforge=dict(sandbox=SANDBOX, approval='ask', name='VisualForge')), defaultPreset='visualforge')),
            dict(insert=[dict(id='visualforge-policy', name='./dsh_policy.mjs', config=dict(mode=start['mode'], workspace=start['workspace']))]),
        ]

    def environment(self, start):
        env = clean_environment()
        parts = urlsplit(start['base_url'])
        env.update(DSH_HOME=str(self.state / 'home'), VF_GATEWAY_TOKEN=start['token'], VF_ALLOWED_ENDPOINT='%s:%d' % (parts.hostname, parts.port),
                   GIT_TERMINAL_PROMPT='0', NO_COLOR='1')
        return env

    def _log(self):
        log = self.paths.logs / 'agent-dsh.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.is_file() and log.stat().st_size > 4 * 1024 * 1024:
            log.write_bytes(log.read_bytes()[-512 * 1024:])
        return log

    # ---- one task -----------------------------------------------------------------------------------------------
    def run(self, start, on_event, cancel, disconnected):
        """Run one task; `on_event` gets the page events. Returns the result, or None when the task was stopped."""
        if not self.installed():
            raise DshError('DeepSeek Harness не установлен: нет Runtime/Agents/dsh. Установите его в «Настройки → Компоненты», раздел Text.')
        folder = self.state / 'task'
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).with_name('dsh_policy.mjs'), folder / 'dsh_policy.mjs')  # the patch names it relative to itself
        (folder / 'patch.json').write_text(json.dumps(self.patch(start), ensure_ascii=False, indent=1), encoding='utf-8')
        command = self.command or [str(self.node), '--require', str(Path(__file__).with_name('dsh_net_guard.cjs')), str(self.bin),
                                   '--profile', 'acp', '--patch', str(folder / 'patch.json')]
        with self._log().open('ab') as stream:
            stream.write(('\n==== %s dsh task in %s (%s)\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), start['workspace'], start['mode'])).encode('utf-8'))
            stream.flush()
            process = subprocess.Popen(command, cwd=start['workspace'], env=self.environment(start), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=stream, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        from video.owned import OwnedJob
        try:
            job = OwnedJob(process)
        except OSError:
            process.kill()
            raise
        messages = queue.Queue()

        def read():
            try:
                for line in process.stdout:
                    try:
                        messages.put(json.loads(line))
                    except ValueError:
                        continue
            finally:
                messages.put(None)

        threading.Thread(target=read, name='dsh-acp', daemon=True).start()
        with self.lock:
            self.process, self.job, self.pending, self.always = process, job, {}, set()
        task = _Task(self, messages, on_event, cancel, disconnected)
        try:
            return task.run(start)
        finally:
            with self.lock:
                self.process = self.job = None
                self.pending = {}
            try:
                process.stdin.close()
            except OSError:
                pass
            job.close()  # kills dsh and every command it started
            if process.poll() is None:
                process.kill()
            process.wait(10)
            process.stdout.close()

    def send(self, message):
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                raise DshError('DeepSeek Harness уже не выполняет задачу')
            try:
                process.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8'))
                process.stdin.flush()
            except OSError:
                raise DshError('DeepSeek Harness уже не выполняет задачу') from None

    def reply(self, request_id, answer):
        if answer not in ('once', 'always', 'reject'):
            raise ValueError('Ответ на запрос: разрешить один раз, разрешать всегда или отклонить')
        with self.lock:
            asked = self.pending.pop(request_id, None)
        if asked is None:
            raise DshError('Этот запрос агента уже не ждёт ответа')
        rpc_id, key, emit = asked
        if answer == 'always':
            self.always.add(key)  # dsh offers one-shot answers only; the studio repeats "allow" for the rest of this task
        self.send(dict(jsonrpc='2.0', id=rpc_id, result=dict(outcome=dict(outcome='selected', optionId='reject-once' if answer == 'reject' else 'allow-once'))))
        emit(dict(type='permission_done', id=request_id, reply=answer))

    def alive(self):
        with self.lock:
            return bool(self.process and self.process.poll() is None)

    def close(self):
        with self.lock:
            job, self.job = self.job, None
        if job:
            job.close()


class _Task:
    """The ACP conversation of one task: requests in order, everything dsh sends in between translated for the page."""
    def __init__(self, runner, messages, on_event, cancel, disconnected):
        self.runner, self.messages, self.on_event, self.cancel, self.disconnected = runner, messages, on_event, cancel, disconnected
        self.counter = 0
        self.calls = {}  # toolCallId -> (tool name, arguments)
        self.content, self.thinking = [], []
        self.message_ids = []
        self.model_calls = self.used = self.size = 0

    def run(self, start):
        self.request('initialize', dict(protocolVersion=1, clientCapabilities=dict(fs=dict(readTextFile=False, writeTextFile=False), terminal=False)))
        session = ''
        if start.get('session'):
            try:
                session = self.request('session/resume', dict(sessionId=start['session'], cwd=start['workspace'], mcpServers=[])).get('sessionId') or start['session']
            except DshError:
                session = ''  # deleted or from another folder: a new session, the chat history stays on the page
        if not session:
            session = self.request('session/new', dict(cwd=start['workspace'], mcpServers=[]))['sessionId']
        self.on_event(dict(type='session', id=session))
        result = self.request('session/prompt', dict(sessionId=session, prompt=[dict(type='text', text=start['task'])]), session=session)
        if result is None:
            return None
        reason = result.get('stopReason')
        error = {'max_tokens': 'Модель упёрлась в лимит длины ответа', 'refusal': 'Модель отказалась отвечать'}.get(reason, '')
        stats = dict(model_calls=self.model_calls, context_tokens=self.used, context=self.size or int(start['context'])) if self.model_calls else None
        return dict(content='\n\n'.join(part for part in (''.join(self.content_by(i)) for i in self.message_ids) if part.strip()).strip(),
                    thinking=''.join(self.thinking).strip(), stats=stats, error=error, session=session)

    def content_by(self, message_id):
        return [text for ident, text in self.content if ident == message_id]

    def request(self, method, params, session=''):
        """Send one request and handle everything dsh sends until its answer; None when the task was stopped during the prompt."""
        self.counter += 1
        ident = self.counter
        self.runner.send(dict(jsonrpc='2.0', id=ident, method=method, params=params))
        stopping = None
        while True:
            if stopping is None and (self.cancel.is_set() or self.disconnected()):
                self.cancel.set()
                if not session:
                    raise DshError('Задача агента остановлена')
                try:
                    self.runner.send(dict(jsonrpc='2.0', method='session/cancel', params=dict(sessionId=session)))
                except DshError:
                    return None
                stopping = time.monotonic() + 5  # let dsh settle the turn so the session stays resumable
            if stopping is not None and time.monotonic() > stopping:
                return None
            try:
                message = self.messages.get(timeout=0.5)
            except queue.Empty:
                continue
            if message is None:
                if stopping is not None:
                    return None
                raise DshError('DeepSeek Harness неожиданно завершился. Подробности — в журнале agent-dsh.log.')
            if message.get('id') == ident and ('result' in message or 'error' in message):
                if 'error' in message:
                    error = message['error'] or {}
                    raise DshError('DeepSeek Harness: ' + clip(error.get('message') or error, 500))
                return None if stopping is not None else (message.get('result') or {})
            if message.get('method') == 'session/update':
                self.update((message.get('params') or {}).get('update') or {})
            elif message.get('method') == 'session/request_permission' and 'id' in message:
                self.permission(message)
            elif 'id' in message and message.get('method'):
                # A client method this studio does not offer (files, terminals): answer so dsh does not wait forever.
                self.runner.send(dict(jsonrpc='2.0', id=message['id'], error=dict(code=-32601, message='not supported by VisualForge')))

    def update(self, update):
        kind = update.get('sessionUpdate')
        if kind == 'agent_message_chunk':
            ident = update.get('messageId', '')
            if ident not in self.message_ids:
                self.message_ids.append(ident)
            text = str((update.get('content') or {}).get('text', ''))
            if self.content and self.content[-1][0] != ident and text:
                self.on_event(dict(type='delta', content='\n\n', thinking=''))
            self.content.append((ident, text))
            self.on_event(dict(type='delta', content=text, thinking=''))
        elif kind == 'agent_thought_chunk':
            text = str((update.get('content') or {}).get('text', ''))
            self.thinking.append(text)
            self.on_event(dict(type='delta', content='', thinking=text))
        elif kind == 'tool_call':
            call = str(update.get('title') or '')
            self.calls[update.get('toolCallId')] = (call, update.get('rawInput'))
            self.on_event(dict(type='step', step=dict(step_from(call, update.get('rawInput'), 'running'), id=update.get('toolCallId'))))
        elif kind == 'tool_call_update':
            call, raw = self.calls.get(update.get('toolCallId'), ('', {}))
            status = {'completed': 'completed', 'failed': 'error'}.get(update.get('status'))
            if status:
                self.on_event(dict(type='step', step=dict(step_from(call, raw, status, _text(update.get('content'))), id=update.get('toolCallId'))))
        elif kind == 'usage_update':
            # One per committed model answer, carrying the context it filled.
            self.model_calls += 1
            self.used, self.size = int(update.get('used') or 0), int(update.get('size') or 0)
            self.on_event(dict(type='status', phase='Модель обдумывает следующий шаг…'))

    def permission(self, message):
        params = message.get('params') or {}
        ident = (params.get('toolCall') or {}).get('toolCallId')
        call, raw = self.calls.get(ident, ('', {}))
        raw = raw if isinstance(raw, dict) else {}
        tool = 'edit' if call in ('edit', 'write', 'str_replace_editor') else TOOLS.get(call, call)
        key = (tool, str(raw.get('command') or '') if tool == 'bash' else '')
        if key in self.runner.always:
            self.runner.send(dict(jsonrpc='2.0', id=message['id'], result=dict(outcome=dict(outcome='selected', optionId='allow-once'))))
            return
        request_id = 'per_%s' % message['id']
        target = str(raw.get('command') or raw.get('file_path') or call)
        request = dict(id=request_id, permission=tool, patterns=[target][:20], always=[target][:20], command=str(raw.get('command') or '')[:2000],
                       file=str(raw.get('file_path') or '')[:500], diff=preview(call, raw))
        with self.runner.lock:
            self.runner.pending[request_id] = (message['id'], key, self.on_event)
        self.on_event(dict(type='permission', request=request))
