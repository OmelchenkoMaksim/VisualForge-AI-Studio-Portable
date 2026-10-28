"""Agent mode of the Text tab: the model gateway, chat storage and a real OpenCode run against a fake model (no GPU, no network)."""
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from agents import agent_manager as manager_module
from agents.gateway import ModelGateway
from agents.agent_manager import AgentManager, workspace_path
from agents.opencode import OpenCodeServer, permission_rules
from gpu import Busy
from text import routes as text_routes
from text.store import ChatStore

ROOT = Path(__file__).resolve().parents[1]
FAKE_MODEL = Path(__file__).with_name('fake_openai.py')
OPENCODE = ROOT / 'Runtime/Agents/opencode/opencode.exe'
AIDER_PYTHON = ROOT / 'Runtime/Agents/aider/python/python.exe'
DSH_NODE = ROOT / 'Runtime/Agents/dsh/node/node.exe'
MODEL = 'demo-model:latest'
# The agents work in a user's folder with Cyrillic, spaces and '!' in its name: '!' is special to cmd.exe (delayed expansion),
# which Aider's shell commands go through.
PROJECT = 'Мой Супер Проект!'
# A file name mixing Cyrillic, Latin, '№', 'ё' and characters special to cmd.exe, PowerShell or URLs.
NAME = 'Отчёт & план №2 (v1.0) 100% ok!.txt'


def remove_tree(path):
    """rmtree that also removes read-only files: git marks its objects read-only, and ignore_errors left whole test folders behind."""
    def retry(function, name, _):
        os.chmod(name, stat.S_IWRITE)
        function(name)
    shutil.rmtree(path, onexc=retry)


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def start_fake_model(test: unittest.TestCase, log):
    port = free_port()
    process = subprocess.Popen([sys.executable, '-I', str(FAKE_MODEL), str(port)], env=dict(os.environ, FAKE_OPENAI_LOG=str(log)))
    test.addCleanup(process.wait, 10)
    test.addCleanup(process.terminate)
    for _ in range(100):
        try:
            socket.create_connection(('127.0.0.1', port), timeout=0.2).close()
            return port
        except OSError:
            time.sleep(0.05)
    raise RuntimeError('fake model did not start')


class FakeText:
    """The part of TextManager the agent manager uses; `port` is the fake OpenAI server standing in for Ollama."""
    def __init__(self, port):
        self.port, self.active, self.begun, self.ended = port, {}, [], 0
        self.lock = threading.Lock()

    def installed_models(self, include_disabled=False):
        return [dict(name=MODEL, label='demo-model', ok=True)]

    def context_length(self, model):
        return 32768

    def default_model(self):
        return MODEL

    def start(self):
        pass

    def loaded(self):
        return [dict(name=MODEL)] if self.ended else []  # "loaded" once the first request has finished, like Ollama

    def begin(self, key):
        with self.lock:
            if self.active:
                raise Busy('Text уже отвечает')
            self.begun.append(key)
            event = self.active[key] = threading.Event()
            return event

    def end(self, key, expected=None):
        with self.lock:
            if expected is None or self.active.get(key) is expected:
                self.active.pop(key, None)
                self.ended += 1

    def stop_chat(self, key):
        with self.lock:
            event = self.active.get(key)
        if event:
            event.set()
        return dict(stopped=bool(event))


class FakeGpu:
    def __init__(self, refusals=0):
        self.refusals, self.claims = refusals, []

    def claim(self, module, need_vram_mb=0):
        self.claims.append(module)
        if self.refusals:
            self.refusals -= 1
            raise Busy('GPU занят Image. Дождитесь завершения или отмените задачу.')
        return []


def paths_in(root):
    return types.SimpleNamespace(root=root, settings=root / 'UserData/Settings', cache=root / 'UserData/Cache', logs=root / 'UserData/Logs',
                                 ports=root / 'ports.json', modern=True)


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='vf-agent-gw-'))
        self.addCleanup(remove_tree, self.root)
        self.upstream = start_fake_model(self, self.root / 'model.log')
        self.cancel = threading.Event()
        self.finished = []
        self.refuse = None
        self.observed = []
        self.gateway = ModelGateway(free_port(), upstream=lambda: self.upstream, admit=self.admit, finish=lambda: self.finished.append(1),
                                    models=lambda: {'m-demo': MODEL}, observe=lambda tokens, measured: self.observed.append((tokens, measured)))
        self.gateway.start()
        self.addCleanup(self.gateway.close)

    def admit(self):
        if self.refuse:
            raise self.refuse
        return self.cancel

    def post(self, body, token=None, host=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.gateway.port, timeout=10)
        raw = json.dumps(body).encode()
        connection.request('POST', '/v1/chat/completions', raw, {'Authorization': 'Bearer ' + (token or self.gateway.token), 'Content-Type': 'application/json',
                                                                  'Host': host or '127.0.0.1:%d' % self.gateway.port})
        response = connection.getresponse()
        return response.status, response.read().decode('utf-8')

    def test_streams_the_model_answer_and_rewrites_the_alias(self):
        status, text = self.post(dict(model='m-demo', stream=True, messages=[dict(role='user', content='привет')]))
        self.assertEqual(status, 200)
        self.assertIn('Эхо: ', text)
        self.assertIn('[DONE]', text)
        sent = json.loads((self.root / 'model.log').read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(sent['body']['model'], MODEL)  # the agent never learns or sends the Ollama name
        self.assertEqual(self.finished, [1])
        # The gateway asks for the usage record and reports the filled context (prompt + answer) from it.
        self.assertTrue(sent['body']['stream_options']['include_usage'])
        self.assertEqual(self.observed, [(12 + 2, True)])

    def test_optional_event_metadata_failure_does_not_interrupt_or_leak_admission(self):
        def failed_metadata():
            raise RuntimeError('fixture event metadata failure')
        self.gateway.activity = failed_metadata
        status, text = self.post(dict(model='m-demo', stream=True, messages=[dict(role='user', content='fixture')]))
        self.assertEqual(status, 200)
        self.assertIn('[DONE]', text)
        self.assertEqual(self.finished, [1])

    def test_refuses_wrong_token_host_and_model(self):
        self.assertEqual(self.post(dict(model='m-demo', messages=[]), token='wrong')[0], 401)
        self.assertEqual(self.post(dict(model='m-demo', messages=[]), host='evil.example:80')[0], 403)
        self.assertEqual(self.post(dict(model='other', messages=[dict(role='user', content='x')]))[0], 400)
        self.assertEqual(self.finished, [])

    def test_busy_gpu_becomes_409_without_reaching_the_model(self):
        self.refuse = Busy('GPU занят Video.')
        status, text = self.post(dict(model='m-demo', stream=True, messages=[dict(role='user', content='привет')]))
        self.assertEqual(status, 409)
        self.assertIn('GPU занят Video', text)
        self.assertFalse((self.root / 'model.log').exists())

    def test_stop_cuts_a_running_stream(self):
        threading.Timer(0.6, self.cancel.set).start()
        started = time.monotonic()
        status, text = self.post(dict(model='m-demo', stream=True, messages=[dict(role='user', content='SLOW')]))
        self.assertLess(time.monotonic() - started, 4)
        self.assertNotIn('слово59', text)
        self.assertNotIn('[DONE]', text)


class LoopUnitTest(unittest.TestCase):
    def test_options_are_validated(self):
        from agents import loop
        self.assertEqual(loop.options({}), dict(threshold=75, iterations=10, check=''))
        self.assertEqual(loop.options(dict(threshold='60', iterations=3, check=' make test ')), dict(threshold=60, iterations=3, check='make test'))
        for bad in (dict(threshold=20), dict(threshold=99), dict(iterations=0), dict(iterations=101), dict(check='a\nb'), dict(other=1)):
            with self.assertRaises(ValueError):
                loop.options(bad)

    def test_note_status_and_next_step(self):
        from agents import loop
        note = '## Done\n- x\n\n## Next step\n- Write   the parser\n\n## Status\n**DONE**\n'
        self.assertEqual(loop.status_of(note), 'DONE')
        self.assertEqual(loop.next_step_of(note), '- write the parser')
        self.assertEqual(loop.status_of('no sections at all'), 'CONTINUE')

    def test_usage_is_read_from_the_end_of_a_stream_or_a_body(self):
        from agents.gateway import usage_from
        stream = (b'HTTP/1.1 200 OK\r\n\r\n1a\r\ndata: {"choices":[]}\n\n\r\n'
                  b'data: {"choices":[],"usage":{"prompt_tokens":900,"completion_tokens":40}}\n\ndata: [DONE]\n\n')
        self.assertEqual(usage_from(stream), (900, 40))
        self.assertEqual(usage_from(b'{"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":1}}'), (5, 1))
        self.assertIsNone(usage_from(b'data: {"choices":[]}\n'))

    def test_verdicts(self):
        verdict = AgentManager._verdict
        self.assertTrue(verdict('finished', 'DONE', None, False, 1, 5)[1])
        self.assertFalse(verdict('finished', 'DONE', dict(code=1), False, 1, 5)[1])  # the check decides, not the model
        self.assertTrue(verdict('context', 'BLOCKED', None, False, 1, 5)[1])
        self.assertTrue(verdict('context', 'CONTINUE', None, True, 1, 5)[1])  # no change and the same next step: a stall
        self.assertEqual(verdict('context', 'CONTINUE', None, False, 2, 5), ('заполнен контекст — продолжаю', False))
        self.assertTrue(verdict('context', 'CONTINUE', None, False, 5, 5)[1])


class StoreAndRulesTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='vf-agent-store-'))
        self.addCleanup(remove_tree, self.root)
        self.store = ChatStore(self.root / 'Chats')

    def test_agent_chat_is_kept_apart_from_plain_chats(self):
        chat = self.store.create('', MODEL, True, agent=dict(id='opencode', workspace=str(self.root), permission='ask', session=''))
        self.assertEqual(self.store.list()[0]['kind'], 'agent')
        self.assertEqual(self.store.set_agent(chat['id'], session='ses_1')['agent']['session'], 'ses_1')
        plain = self.store.create('', MODEL)
        with self.assertRaises(ValueError):
            self.store.set_agent(plain['id'], session='x')
        with self.assertRaisesRegex(ValueError, 'режиме «Агент»'):
            text_routes.send(None, object(), self.store, dict(chat_id=chat['id'], content='привет'), None)

    def test_workspace_must_be_an_existing_folder_but_not_a_drive_root(self):
        self.assertEqual(workspace_path(str(self.root)), str(self.root.resolve()))
        for bad in ('', 'relative/folder', str(self.root / 'missing'), Path(self.root).anchor):
            with self.assertRaises(ValueError):
                workspace_path(bad)

    def test_push_is_denied_last_in_every_mode(self):
        for mode in ('read', 'ask', 'auto'):
            rules = permission_rules(mode)
            self.assertEqual(rules[-2:], [dict(permission='bash', pattern='git push', action='deny'), dict(permission='bash', pattern='git push *', action='deny')])

    def test_agent_environment_drops_tokens(self):
        os.environ['HF_TOKEN'] = 'hf_secret_for_test'
        self.addCleanup(os.environ.pop, 'HF_TOKEN', None)
        server = OpenCodeServer(paths_in(self.root), self.root / 'opencode.exe')
        env = server.environment(server.config('http://127.0.0.1:1/v1', 'tok', {}))
        self.assertNotIn('HF_TOKEN', env)
        self.assertNotIn('hf_secret_for_test', json.dumps(env))
        self.assertEqual(env['OPENCODE_DISABLE_PROJECT_CONFIG'], '1')
        self.assertEqual(json.loads(env['OPENCODE_CONFIG_CONTENT'])['enabled_providers'], ['visualforge'])

    def test_catalog_installs_opencode_where_the_studio_looks_for_it(self):
        from agents.registry import AGENTS
        from provision import catalog
        data = catalog.load(ROOT / 'App/config/components.json')
        component = catalog.index(data)['text.agent.opencode']
        self.assertEqual((component['kind'], component['requires']), ('software', ['text.runtime']))
        self.assertIn(component['id'], next(m['components'] for m in data['modules'] if m['id'] == 'text'))
        self.assertEqual(component['present'], [AGENTS['opencode']['executable']])
        download = component['downloads'][0]
        self.assertRegex(download['sha256'], '^[0-9a-f]{64}$')
        self.assertTrue(all(url.startswith('https://') and 'anomalyco/opencode' in url for url in download['urls']))
        self.assertIn('v' + AGENTS['opencode']['version'], download['urls'][0])
        self.assertEqual(component['steps'], [dict(op='extract', download='opencode', to=str(Path(AGENTS['opencode']['executable']).parent.as_posix()))])
        aider = catalog.index(data)['text.agent.aider']
        self.assertEqual((aider['kind'], aider['requires']), ('software', ['text.runtime']))
        self.assertIn(AGENTS['aider']['executable'], aider['present'])
        assets = json.loads((ROOT / 'App/config/aider-assets.json').read_text(encoding='utf-8'))
        self.assertEqual(assets['version'], AGENTS['aider']['version'])
        wheels = {d['filename']: d['sha256'] for d in aider['downloads'] if d['id'].startswith('wheel:')}
        self.assertEqual(wheels, {w['filename']: w['sha256'] for w in assets['wheels']})
        self.assertTrue(all(re.fullmatch('[0-9a-f]{64}', d['sha256']) for d in aider['downloads']))
        self.assertIn('aider_chat-%s-py3-none-any.whl' % assets['version'], wheels)
        dsh = catalog.index(data)['text.agent.dsh']
        self.assertEqual((dsh['kind'], dsh['requires']), ('software', ['text.runtime']))
        self.assertIn(AGENTS['dsh']['executable'], dsh['present'])
        assets = json.loads((ROOT / 'App/config/dsh-assets.json').read_text(encoding='utf-8'))
        self.assertEqual(assets['version'], AGENTS['dsh']['version'])
        tarballs = {d['id'][4:]: d['sha256'] for d in dsh['downloads'] if d['id'].startswith('npm:')}
        self.assertEqual(tarballs, {p['path']: p['sha256'] for p in assets['packages']})
        self.assertIn('node_modules/@deepseek-ai/dsh', tarballs)
        self.assertFalse(set(assets['trimmed']) & set(tarballs), 'trimmed packages must not be installed')
        self.assertTrue(all(re.fullmatch('[0-9a-f]{64}', d['sha256']) and d['urls'][0].startswith('https://') for d in dsh['downloads']))
        extracts = {s['download']: s['to'] for s in dsh['steps'] if s['op'] == 'extract'}
        self.assertEqual(extracts['npm:node_modules/@deepseek-ai/dsh'], 'Runtime/Agents/dsh/app/node_modules/@deepseek-ai/dsh')


class LoopChecks:
    """A loop of iterations with handoff notes, for every agent (the fake model plays the agent's side, see fake_openai.py)."""
    def run_loop(self, goal, **loop):
        chat = self.manager.create_chat(dict(agent=self.AGENT, workspace=str(self.workspace), permission='auto', model=MODEL))
        events, done = [], []
        worker = threading.Thread(target=lambda: done.append(self.manager.send(chat['id'], goal, events.append, lambda: False, loop)))
        worker.start()
        worker.join(240)
        self.assertFalse(worker.is_alive(), 'the loop did not finish')
        self.assertTrue(done, 'the loop failed: %s' % events[-3:])
        return self.store.get(chat['id']), events, done[0]

    def notes(self):
        return sorted((self.workspace / '.visualforge/loop').glob('*/*-handoff.md'))

    def test_loop_hands_off_and_stops_when_done(self):
        chat, events, (_, message, error) = self.run_loop('LOOP_ сделай большую задачу', threshold=75, iterations=5)
        self.assertEqual(error, '')
        self.assertEqual([m['role'] for m in chat['messages']], ['user', 'assistant', 'assistant'])
        notes = self.notes()
        self.assertEqual([n.name for n in notes], ['01-handoff.md', '02-handoff.md'])
        first = notes[0].read_text(encoding='utf-8')
        for part in ('## Task', 'LOOP_ сделай большую задачу', '## Next step', 'finish the remaining part', '## Changed files (recorded by the studio)'):
            self.assertIn(part, first)
        self.assertEqual([e['number'] for e in events if e['type'] == 'iteration'], [1])
        self.assertIn('задача выполнена (DONE)', message['content'])
        self.assertIn('.visualforge/loop/', message['content'])
        sent = [json.loads(line)['body'] for line in self.log.read_text(encoding='utf-8').splitlines()]
        second = [b for b in sent if 'iteration 2 of at most 5' in json.dumps(b, ensure_ascii=False)]
        self.assertTrue(second and 'finish the remaining part' in json.dumps(second[0], ensure_ascii=False), 'the note reached iteration 2')
        self.assertIn('01-handoff.md', json.dumps(second[0], ensure_ascii=False))
        self.assertTrue(any(e['type'] == 'context' for e in events))
        exclude = subprocess.run(['git', '-C', str(self.workspace), 'rev-parse', '--git-path', 'info/exclude'], capture_output=True, text=True).stdout.strip()
        self.assertIn('/.visualforge/', (self.workspace / exclude).read_text(encoding='utf-8'))
        self.assertNotIn('.visualforge', subprocess.run(['git', '-C', str(self.workspace), 'status', '--short'], capture_output=True, text=True).stdout)

    def test_threshold_ends_an_endless_iteration(self):
        chat, events, (_, message, _) = self.run_loop('LOOP_BIG LOOP_TOOLS работай без конца', threshold=50, iterations=1)
        note = self.notes()[0].read_text(encoding='utf-8')
        self.assertIn('context threshold reached', note)
        self.assertIn('## Next step', note)
        self.assertIn('достигнут лимит итераций', message['content'])
        sent = [json.loads(line)['body'] for line in self.log.read_text(encoding='utf-8').splitlines()]
        work = [b for b in sent if 'Stop working on the task now' not in json.dumps(b, ensure_ascii=False)]
        self.assertLessEqual(len(work), 3, 'the agent kept calling the model after the threshold')
        self.assertTrue(any(e['type'] == 'status' and 'Контекст заполнен' in e['phase'] for e in events))

    def test_check_command_decides_done(self):
        _, _, (_, message, _) = self.run_loop('LOOP_ задача с проверкой', threshold=75, iterations=2, check='python -c "import sys; sys.exit(3)"')
        notes = [n.read_text(encoding='utf-8') for n in self.notes()]
        self.assertEqual(len(notes), 2)
        self.assertIn('exit code 3', notes[1])
        self.assertIn('проверка не прошла', message['content'])
        self.assertIn('достигнут лимит итераций', message['content'])


class ProjectPathChecks:
    """Path encoding and compound-command policy checks for every real agent."""
    def test_auto_compound_command_requires_approval(self):
        command = {'opencode': 'git status && echo audit > policy-marker.txt',
                   'aider': 'git status & echo audit > policy-marker.txt',
                   'dsh': 'git status | Set-Content policy-marker.txt'}[self.AGENT]
        _, events, _ = self.run_task('auto', 'RUN_CMD: %s ## audit' % command, reply='reject')
        self.assertTrue(any(e['type'] == 'permission' for e in events))
        self.assertFalse((self.workspace / 'policy-marker.txt').exists())

    def test_commands_with_the_project_path(self):
        _, events, (_, message, _) = self.run_task('ask', 'RUN_CMD: git -C "%s" status ## проверь' % self.workspace)
        self.assertEqual([e['request']['command'] for e in events if e['type'] == 'permission'], ['git -C "%s" status' % self.workspace])
        bash = [s for s in message['steps'] if s['tool'] == 'bash' and s['status'] == 'completed']
        self.assertTrue(bash and 'On branch' in bash[-1]['output'], message['steps'])
        _, _, (_, message, _) = self.run_task('ask', 'RUN_CMD: python -c "import os; print(os.getcwd())" ## где мы')
        bash = [s for s in message['steps'] if s['tool'] == 'bash' and s['status'] == 'completed']
        self.assertTrue(bash and PROJECT in bash[-1]['output'], message['steps'])
        (self.workspace / NAME).write_text('привет\n', encoding='utf-8')
        listing = {'OpenCodeRunTest': 'ls', 'AiderRunTest': 'dir /b', 'DshRunTest': 'Get-ChildItem -Name'}[type(self).__name__]
        for command in ('git status --short', listing):
            _, _, (_, message, _) = self.run_task('ask', 'RUN_CMD: %s ## покажи' % command)
            bash = [s for s in message['steps'] if s['tool'] == 'bash' and s['status'] == 'completed']
            self.assertTrue(bash and NAME in bash[-1]['output'], (command, message['steps']))


@unittest.skipUnless(OPENCODE.is_file(), 'OpenCode is not installed in Runtime/Agents/opencode')
class OpenCodeRunTest(LoopChecks, ProjectPathChecks, unittest.TestCase):
    """A real `opencode serve` with the studio's gateway in front of a fake model: tool call, permission, saved answer."""
    AGENT = 'opencode'

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='vf-agent-oc-'))
        self.addCleanup(remove_tree, self.root)
        self.workspace = self.root / PROJECT
        self.workspace.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.workspace)], check=True)
        (self.workspace / 'readme.txt').write_text('hello\n', encoding='utf-8')
        self.log = self.root / 'model.log'
        self.text = FakeText(start_fake_model(self, self.log))
        self.gpu = FakeGpu()
        os.environ['VISUALFORGE_AGENT_GATEWAY_PORT'] = str(free_port())
        os.environ['VISUALFORGE_OPENCODE_PORT'] = str(free_port())
        self.addCleanup(os.environ.pop, 'VISUALFORGE_AGENT_GATEWAY_PORT', None)
        self.addCleanup(os.environ.pop, 'VISUALFORGE_OPENCODE_PORT', None)
        paths = paths_in(self.root)
        paths.root = ROOT  # the real executable under Runtime/Agents; state, settings and logs stay in the temporary folder
        self.store = ChatStore(self.root / 'Chats')
        self.manager = AgentManager(paths, self.text, self.store, self.gpu, threading.RLock())
        self.manager.opencode.state = self.root / 'UserData/Cache/Agents/opencode'
        self.manager.settings_path = self.root / 'UserData/Settings/agents.settings.json'
        self.addCleanup(self.manager.close)

    def run_task(self, mode, content, reply='once'):
        chat = self.manager.create_chat(dict(agent='opencode', workspace=str(self.workspace), permission=mode, model=MODEL))
        events = []

        def emit(event):
            events.append(event)
            if event['type'] == 'permission' and reply:
                threading.Thread(target=self.manager.reply, args=(dict(chat_id=chat['id'], request_id=event['request']['id'], reply=reply),)).start()
        done = []
        worker = threading.Thread(target=lambda: done.append(self.manager.send(chat['id'], content, emit, lambda: False)))
        worker.start()
        worker.join(90)
        self.assertFalse(worker.is_alive(), 'the agent task did not finish')
        return chat, events, done[0]

    def test_permission_then_command_then_saved_answer(self):
        chat, events, (saved, message, error) = self.run_task('ask', 'TOOL проверь репозиторий')
        self.assertEqual(error, '')
        asked = [e['request'] for e in events if e['type'] == 'permission']
        self.assertEqual([r['permission'] for r in asked], ['bash'])
        self.assertEqual(asked[0]['command'], 'git status')
        self.assertIn('Готово: ', message['content'])
        self.assertEqual([(s['tool'], s['status']) for s in message['steps']], [('bash', 'completed')])
        self.assertIn('No commits yet', message['steps'][0]['output'])
        stored = self.store.get(chat['id'])
        self.assertEqual([m['role'] for m in stored['messages']], ['user', 'assistant'])
        self.assertTrue(stored['agent']['session'].startswith('ses'))
        self.assertTrue(self.text.begun and all(key == 'agent:' + chat['id'] for key in self.text.begun))
        self.assertEqual(self.text.active, {})
        self.assertTrue(all(module == 'text' for module in self.gpu.claims))
        sent = [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(all(row['body']['model'] == MODEL for row in sent))
        system = ' '.join(str(m.get('content')) for m in sent[-1]['body']['messages'] if m['role'] == 'system')
        self.assertIn('на русском языке', system)  # the studio's default instruction reaches the model
        self.assertEqual(message['stats']['model_calls'], 2)  # the tool call, then the answer
        phases = [e['phase'] for e in events if e['type'] == 'status']
        self.assertIn('Загрузка модели в видеопамять: первая загрузка с диска может занять несколько минут…', phases)
        self.assertIn('Модель обдумывает следующий шаг…', phases)

    def test_read_mode_and_push_never_reach_the_shell(self):
        for mode, content in (('read', 'TOOL проверь'), ('auto', 'PUSH отправь')):
            _, events, (_, message, _) = self.run_task(mode, content, reply=None)
            self.assertEqual([e for e in events if e['type'] == 'permission'], [], mode)
            self.assertEqual([(s['status'], s.get('error')) for s in message.get('steps', [])], [('error', 'Запрещено правами агента в этом чате.')], mode)

    def test_rejected_request_is_reported_in_russian(self):
        _, events, (_, message, error) = self.run_task('ask', 'TOOL проверь', reply='reject')
        self.assertEqual([e['reply'] for e in events if e['type'] == 'permission_done'], ['reject'])
        self.assertEqual([(s['status'], s.get('error')) for s in message['steps']], [('error', 'Вы отклонили это действие.')])

    def test_waits_for_the_gpu_instead_of_failing(self):
        self.gpu.refusals = 1
        original = manager_module.GPU_WAIT_SEC
        _, events, (_, message, error) = self.run_task('auto', 'привет')
        self.assertEqual(manager_module.GPU_WAIT_SEC, original)
        phases = [e['phase'] for e in events if e['type'] == 'status']
        self.assertTrue(any(p.startswith('Агент ждёт видеокарту') for p in phases), phases)
        self.assertIn('Эхо', message['content'])


@unittest.skipUnless(AIDER_PYTHON.is_file(), 'Aider is not installed in Runtime/Agents/aider')
class AiderRunTest(LoopChecks, ProjectPathChecks, unittest.TestCase):
    """The real Aider in its own Python, driven by the bridge, with the studio's gateway in front of a fake model."""
    AGENT = 'aider'

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='vf-agent-ai-'))
        self.addCleanup(remove_tree, self.root)
        self.workspace = self.root / PROJECT
        self.workspace.mkdir()
        git = lambda *a: subprocess.run(['git', '-C', str(self.workspace), *a], check=True, capture_output=True)
        git('init', '-q')
        git('config', 'user.name', 'Studio Test')
        git('config', 'user.email', 'studio-test@localhost')
        (self.workspace / 'calc.py').write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')
        git('add', '.')
        git('commit', '-q', '-m', 'Initial')
        self.git = git
        self.log = self.root / 'model.log'
        self.text = FakeText(start_fake_model(self, self.log))
        self.gpu = FakeGpu()
        os.environ['VISUALFORGE_AGENT_GATEWAY_PORT'] = str(free_port())
        self.addCleanup(os.environ.pop, 'VISUALFORGE_AGENT_GATEWAY_PORT', None)
        paths = paths_in(self.root)
        paths.root = ROOT
        self.store = ChatStore(self.root / 'Chats')
        self.manager = AgentManager(paths, self.text, self.store, self.gpu, threading.RLock())
        self.manager.aider.state = self.root / 'UserData/Cache/Agents/aider'
        self.manager.settings_path = self.root / 'UserData/Settings/agents.settings.json'
        self.addCleanup(self.manager.close)

    def run_task(self, mode, content, reply='once', chat=None):
        chat = chat or self.manager.create_chat(dict(agent='aider', workspace=str(self.workspace), permission=mode, model=MODEL))
        events = []

        def emit(event):
            events.append(event)
            if event['type'] == 'permission' and reply:
                threading.Thread(target=self.manager.reply, args=(dict(chat_id=chat['id'], request_id=event['request']['id'], reply=reply),)).start()
        done = []
        worker = threading.Thread(target=lambda: done.append(self.manager.send(chat['id'], content, emit, lambda: False)))
        worker.start()
        worker.join(120)
        self.assertFalse(worker.is_alive(), 'the agent task did not finish')
        self.assertTrue(done, 'the agent task failed: %s' % events[-3:])
        return chat, events, done[0]

    def calc(self):
        return (self.workspace / 'calc.py').read_text(encoding='utf-8')

    def test_edit_is_asked_applied_and_left_uncommitted(self):
        chat, events, (saved, message, error) = self.run_task('ask', 'AIDER_EDIT исправь сложение')
        self.assertEqual(error, '')
        asked = [e['request'] for e in events if e['type'] == 'permission']
        self.assertEqual([(r['permission'], r['patterns']) for r in asked], [('edit', ['calc.py'])])
        self.assertIn('+    return a + b', asked[0]['diff'])
        self.assertIn('return a + b', self.calc())
        self.assertIn('Исправляю сложение', message['content'])
        edits = [s for s in message['steps'] if s['tool'] == 'edit']
        self.assertEqual([(s['status'], s['file']) for s in edits], [('completed', 'calc.py')])
        self.assertIn('-    return a - b', edits[0]['diff'])
        self.assertEqual(self.git('log', '--oneline').stdout.decode().count('\n'), 1)  # `ask` leaves the change for the user to commit
        self.assertEqual([(s['tool'], s['file']) for s in message['steps'] if s['tool'] == 'read'], [('read', 'calc.py')])
        self.assertEqual(message['stats']['model_calls'], 2)  # Aider adds the named file, then the model repeats the edit
        stored = self.store.get(chat['id'])
        self.assertEqual([m['role'] for m in stored['messages']], ['user', 'assistant'])
        self.assertTrue(self.text.begun and all(key == 'agent:' + chat['id'] for key in self.text.begun))
        self.assertEqual(self.text.active, {})
        sent = [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(all(row['body']['model'] == MODEL for row in sent))
        # Reasoning off and the model's own sampling: see the comment at models.Model in aider_bridge.py.
        self.assertTrue(all(row['body'].get('reasoning_effort') == 'none' and 'temperature' not in row['body'] for row in sent), sent[0]['body'].keys())
        self.assertIn('Russian', ' '.join(str(m.get('content')) for m in sent[0]['body']['messages'] if m['role'] == 'system'))
        self.assertFalse(list(self.workspace.glob('.aider*')), 'Aider must keep its caches out of the project')

    def test_file_the_model_asks_for_is_added_and_sent(self):
        _, events, (_, message, _) = self.run_task('ask', 'AIDER_ASK посмотри код')
        self.assertEqual([(s['tool'], s['status'], s['file']) for s in message['steps']], [('read', 'completed', 'calc.py')])
        sent = [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(sent), 2)  # the request for the file, then the same question with the file's content
        self.assertIn('return a - b', json.dumps(sent[1]['body']['messages'], ensure_ascii=False))

    def test_auto_commits_with_the_users_identity(self):
        _, events, (_, message, error) = self.run_task('auto', 'AIDER_EDIT исправь сложение', reply=None)
        self.assertEqual([e for e in events if e['type'] == 'permission'], [])
        self.assertIn('return a + b', self.calc())
        log = self.git('log', '-1', '--format=%an|%cn|%s%n%b').stdout.decode('utf-8')
        self.assertTrue(log.startswith('Studio Test|Studio Test|fix: make add return the sum'), log)
        self.assertNotIn('aider', log.lower())

    def test_rejected_edit_and_read_mode_change_nothing(self):
        for mode, reply in (('ask', 'reject'), ('read', None)):
            _, events, (_, message, _) = self.run_task(mode, 'AIDER_EDIT исправь сложение', reply=reply)
            self.assertIn('return a - b', self.calc(), mode)
            self.assertEqual(len([e for e in events if e['type'] == 'permission']), 1 if reply else 0, mode)

    def test_commands_ask_push_and_outside_files_are_refused(self):
        _, events, (_, message, _) = self.run_task('ask', 'AIDER_RUN покажи состояние')
        self.assertEqual([e['request']['command'] for e in events if e['type'] == 'permission'], ['git status'])
        bash = [s for s in message['steps'] if s['tool'] == 'bash']
        self.assertEqual(bash[-1]['status'], 'completed')
        self.assertIn('nothing to commit', bash[-1]['output'])
        for content in ('AIDER_PUSH отправь', 'AIDER_OUT поправь'):
            _, events, (_, message, _) = self.run_task('auto', content, reply=None)
            self.assertEqual([e for e in events if e['type'] == 'permission'], [], content)
            self.assertIn(('error', 'Запрещено правами агента в этом чате.'), [(s['status'], s.get('error')) for s in message['steps']], content)
        self.assertFalse((self.root / 'outside.txt').exists())

    def test_stop_keeps_the_partial_answer(self):
        chat = self.manager.create_chat(dict(agent='aider', workspace=str(self.workspace), permission='ask', model=MODEL))
        done, first = [], threading.Event()

        def emit(event):
            if event['type'] == 'delta' and event['content']:
                first.set()
        worker = threading.Thread(target=lambda: done.append(self.manager.send(chat['id'], 'SLOW расскажи', emit, lambda: False)))
        worker.start()
        self.assertTrue(first.wait(60))
        self.assertEqual(self.manager.stop(chat['id']), dict(stopped=True))
        worker.join(30)
        self.assertFalse(worker.is_alive())
        message = done[0][1]
        self.assertTrue(message['stopped'])
        self.assertIn('слово0', message['content'])
        self.assertNotIn('слово59', message['content'])
        self.assertFalse(self.manager.aider.alive())

    def test_second_task_gets_the_earlier_conversation(self):
        chat, _, _ = self.run_task('ask', 'первое задание')
        self.run_task('ask', 'второе задание', chat=chat)
        last = json.loads(self.log.read_text(encoding='utf-8').splitlines()[-1])['body']['messages']
        self.assertTrue(any('первое задание' in str(m.get('content')) for m in last))


@unittest.skipUnless(DSH_NODE.is_file(), 'DeepSeek Harness is not installed in Runtime/Agents/dsh')
class DshRunTest(LoopChecks, ProjectPathChecks, unittest.TestCase):
    """The real dsh (ACP profile) in the portable Node.js, the studio's policy plugin and gateway in front of a fake model."""
    AGENT = 'dsh'

    def setUp(self):
        # Not mkdtemp: on Windows it gives the folder an owner-only, explicit DACL (Python 3.12.4+); a real project folder inherits
        # its ACL, which is what explicit_aces() checks dsh leaves alone. (With dsh's sandbox on, its restricted token could not even
        # enter a mkdtemp folder.)
        self.root = Path(tempfile.gettempdir()) / ('vf-agent-ds-%s' % os.urandom(4).hex())
        self.root.mkdir()
        self.addCleanup(remove_tree, self.root)
        self.workspace = self.root / PROJECT
        self.workspace.mkdir()
        git = lambda *a: subprocess.run(['git', '-C', str(self.workspace), *a], check=True, capture_output=True)
        git('init', '-q')
        git('config', 'user.name', 'Studio Test')
        git('config', 'user.email', 'studio-test@localhost')
        (self.workspace / 'calc.py').write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')
        git('add', '.')
        git('commit', '-q', '-m', 'Initial')
        self.log = self.root / 'model.log'
        self.text = FakeText(start_fake_model(self, self.log))
        self.gpu = FakeGpu()
        os.environ['VISUALFORGE_AGENT_GATEWAY_PORT'] = str(free_port())
        self.addCleanup(os.environ.pop, 'VISUALFORGE_AGENT_GATEWAY_PORT', None)
        paths = paths_in(self.root)
        paths.root = ROOT
        self.store = ChatStore(self.root / 'Chats')
        self.manager = AgentManager(paths, self.text, self.store, self.gpu, threading.RLock())
        self.manager.dsh.state = self.root / 'UserData/Cache/Agents/dsh'
        self.manager.settings_path = self.root / 'UserData/Settings/agents.settings.json'
        self.addCleanup(self.manager.close)

    def run_task(self, mode, content, reply='once', chat=None):
        chat = chat or self.manager.create_chat(dict(agent='dsh', workspace=str(self.workspace), permission=mode, model=MODEL))
        events = []

        def emit(event):
            events.append(event)
            if event['type'] == 'permission' and reply:
                threading.Thread(target=self.manager.reply, args=(dict(chat_id=chat['id'], request_id=event['request']['id'], reply=reply),)).start()
        done = []
        worker = threading.Thread(target=lambda: done.append(self.manager.send(chat['id'], content, emit, lambda: False)))
        worker.start()
        worker.join(120)
        self.assertFalse(worker.is_alive(), 'the agent task did not finish')
        self.assertTrue(done, 'the agent task failed: %s' % events[-3:])
        return chat, events, done[0]

    def calc(self):
        return (self.workspace / 'calc.py').read_text(encoding='utf-8')

    def explicit_aces(self):
        # dsh's own sandbox would leave explicit ACEs on the project folder; with it off, everything stays inherited.
        script = "(Get-Acl -LiteralPath $env:VF_PATH).Access | Where-Object { -not $_.IsInherited } | Measure-Object | ForEach-Object Count"
        # Windows PowerShell must discover its own Security module, not inherit PS7's module paths.
        env = {key: value for key, value in os.environ.items() if key.upper() != 'PSMODULEPATH'}
        done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True,
                              env=dict(env, VF_PATH=str(self.workspace)), timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.strip().isdigit(), done.stderr)
        return int(done.stdout.strip())

    def errors(self, message):
        return [(s['tool'], s['status'], s.get('error')) for s in message['steps'] if s['status'] == 'error']

    def test_edit_is_asked_and_applied(self):
        chat, events, (saved, message, error) = self.run_task('ask', 'DSH_EDIT исправь сложение')
        self.assertEqual(error, '')
        asked = [e['request'] for e in events if e['type'] == 'permission']
        self.assertEqual([(r['permission'], r['file']) for r in asked], [('edit', 'calc.py')])
        self.assertIn('+return a + b', asked[0]['diff'])
        self.assertIn('return a + b', self.calc())
        self.assertEqual([(s['tool'], s['status']) for s in message['steps']], [('read', 'completed'), ('edit', 'completed')])
        self.assertIn('Готово', message['content'])
        self.assertEqual(message['stats']['model_calls'], 3)  # read, edit, answer
        self.assertNotIn('completion_tokens', message['stats'])
        stored = self.store.get(chat['id'])
        self.assertTrue(stored['agent']['session'])
        self.assertTrue(self.text.begun and all(key == 'agent:' + chat['id'] for key in self.text.begun))
        self.assertEqual(self.text.active, {})
        sent = [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(all(row['body']['model'] == MODEL for row in sent))
        system = ' '.join(str(m.get('content')) for m in sent[0]['body']['messages'] if m['role'] == 'system')
        self.assertIn('на русском языке', system)
        tools = {t['function']['name'] for t in sent[0]['body']['tools']}
        self.assertTrue({'read', 'edit', 'write', 'pwsh'} <= tools and not tools & {'web_search', 'web_fetch', 'subagent'}, tools)
        log = (self.root / 'UserData/Logs/agent-dsh.log').read_text(encoding='utf-8', errors='replace')
        self.assertNotIn('refused a network connection', log)

    def test_auto_runs_git_status_without_asking(self):
        _, events, (_, message, _) = self.run_task('auto', 'DSH_RUN покажи состояние', reply=None)
        self.assertEqual([e for e in events if e['type'] == 'permission'], [])
        self.assertEqual([(s['tool'], s['status'], s['command']) for s in message['steps']], [('bash', 'completed', 'git status')])
        self.assertIn('nothing to commit', message['steps'][0]['output'])
        self.assertEqual(self.explicit_aces(), 0)

    def test_read_level_push_and_outside_files_are_refused(self):
        for mode, content in (('read', 'DSH_EDIT исправь'), ('auto', 'DSH_PUSH отправь'), ('ask', 'DSH_OUT запиши')):
            _, events, (_, message, _) = self.run_task(mode, content, reply='once')
            self.assertEqual([e for e in events if e['type'] == 'permission'], [], mode)
            self.assertEqual([e[2] for e in self.errors(message)], ['Запрещено правами агента в этом чате.'], mode)
        self.assertIn('return a - b', self.calc())
        self.assertFalse((self.root / 'outside.txt').exists())

    def test_rejected_edit_changes_nothing(self):
        _, events, (_, message, _) = self.run_task('ask', 'DSH_EDIT исправь', reply='reject')
        self.assertEqual([e['reply'] for e in events if e['type'] == 'permission_done'], ['reject'])
        self.assertIn('return a - b', self.calc())
        self.assertEqual(self.errors(message), [('edit', 'error', 'Вы отклонили это действие.')])

    def test_second_task_resumes_the_session(self):
        chat, _, _ = self.run_task('ask', 'первое задание')
        session = self.store.get(chat['id'])['agent']['session']
        self.run_task('ask', 'второе задание', chat=chat)
        self.assertEqual(self.store.get(chat['id'])['agent']['session'], session)
        last = json.loads(self.log.read_text(encoding='utf-8').splitlines()[-1])['body']['messages']
        self.assertTrue(any('первое задание' in str(m.get('content')) for m in last))

    def test_stop_ends_the_task_and_the_process(self):
        chat = self.manager.create_chat(dict(agent='dsh', workspace=str(self.workspace), permission='ask', model=MODEL))
        done, started = [], threading.Event()

        def emit(event):
            if event['type'] == 'status' and event['phase'].startswith('Загрузка модели'):
                started.set()
        worker = threading.Thread(target=lambda: done.append(self.manager.send(chat['id'], 'SLOW расскажи', emit, lambda: False)))
        worker.start()
        self.assertTrue(started.wait(60))
        time.sleep(1)
        self.assertEqual(self.manager.stop(chat['id']), dict(stopped=True))
        worker.join(30)
        self.assertFalse(worker.is_alive())
        self.assertTrue(done[0][1]['stopped'])
        self.assertFalse(self.manager.dsh.alive())


if __name__ == '__main__':
    unittest.main()
