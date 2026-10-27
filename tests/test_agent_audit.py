"""Regression checks for audit failures: CPU only, fixtures under the configured TEMP."""
import ast
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from tests.test_agents import ROOT, FakeText, FakeGpu, paths_in, remove_tree
from agents import loop, routes
from agents.gateway import ResponseUsage
from agents.manager import AgentManager
from agents.opencode import clean_environment
from gpu import Busy
from text import store as store_module
from text.store import ChatStore


class AuditRegressionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='vfa-'))
        self.addCleanup(remove_tree, self.root)

    def manager(self):
        manager = AgentManager(paths_in(self.root), FakeText(1), ChatStore(self.root / 'Chats'), FakeGpu(), threading.RLock())
        self.addCleanup(manager.close)
        return manager

    def test_late_finish_releases_original_slot_and_not_replacement(self):
        m = self.manager()
        old = dict(chat='old', generation=object(), phase='work', cancel=threading.Event(), model='demo', emit=lambda e: None)
        m.run = old
        event = m._admit()
        m.run = None
        m._finish()
        self.assertEqual(m.text.active, {})
        replacement = m.text.begin('agent:old')
        m._finish()
        self.assertIs(m.text.active['agent:old'], replacement)
        self.assertIsNot(event, replacement)

    def test_late_usage_and_wrap_cannot_touch_new_iteration(self):
        m = self.manager()
        generation = object()
        m.run = dict(chat='same', generation=object(), phase='work', used=0, cancel=threading.Event())
        m.requests.ticket = (m.run, generation, threading.Event(), 'work')
        m._observe(30000, True)
        self.assertEqual(m.run['used'], 0)
        with patch.object(m, '_wrap_current') as wrap:
            m._wrap(m.run, generation)
            wrap.assert_not_called()

    def test_settings_concurrent_updates_are_atomic(self):
        m = self.manager()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(m.settings, [dict(loop_threshold=60), dict(loop_iterations=7)] * 12))
        self.assertEqual(m.settings()['loop_threshold'], 60)
        self.assertEqual(m.settings()['loop_iterations'], 7)

    def test_active_agent_chat_cannot_be_deleted(self):
        store = ChatStore(self.root / 'chats')
        chat = store.create()
        store.active.add(chat['id'])
        with self.assertRaises(Busy):
            store.delete(chat['id'])
        self.assertEqual(store.get(chat['id'])['id'], chat['id'])

    def test_overflow_preserves_readable_history_and_full_result(self):
        store = ChatStore(self.root / 'chats')
        chat = store.create()
        with patch.object(store_module, 'MAX_FILE_BYTES', 2000):
            with self.assertRaisesRegex(ValueError, 'Полный результат сохранён'):
                store.add_assistant(chat['id'], 'я' * 2000)
            self.assertEqual(store.get(chat['id'])['messages'], [])
        recovery = list((store.directory / (chat['id'] + '.overflow')).glob('*.json'))
        self.assertEqual(len(recovery), 1)
        self.assertEqual(json.loads(recovery[0].read_text(encoding='utf-8'))['messages'][-1]['content'], 'я' * 2000)

    def test_chunked_usage_split_inside_json_and_network_packets(self):
        data = b'data: {"usage":{"prompt_tokens":14000,"completion_tokens":20}}\n\n'
        chunks = [data[:30], data[30:]]
        wire = b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n'
        wire += b''.join(('%x\r\n' % len(c)).encode() + c + b'\r\n' for c in chunks) + b'0\r\n\r\n'
        meter = ResponseUsage()
        for byte in wire:
            meter.feed(bytes([byte]))
        self.assertEqual(meter.result(), ((14000, 20), len(data)))

    def test_nonstream_usage_after_large_answer(self):
        body = json.dumps(dict(content='x' * 100000, usage=dict(prompt_tokens=9000, completion_tokens=4000))).encode()
        meter = ResponseUsage()
        wire = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n' + body
        for i in range(0, len(wire), 1000):
            meter.feed(wire[i:i + 1000])
        self.assertEqual(meter.result(), ((9000, 4000), len(body)))

    def test_status_rejects_prose_negation_ambiguous_and_quoted_fields(self):
        for note in ('Do not mark the task DONE', 'DONE', '## Status\nDONE or CONTINUE',
                     '## Status\nDONE\n## Status\nBLOCKED', '```\n## Status\nDONE\n```'):
            self.assertEqual(loop.status_of(note), 'CONTINUE', note)
        self.assertEqual(loop.status_of('## Status\n**DONE**\n'), 'DONE')

    def test_options_reject_bool_float_and_cr(self):
        for data in (dict(iterations=True), dict(threshold=75.9), dict(check='a\rb')):
            with self.assertRaises(ValueError):
                loop.options(data)

    def test_truncated_utf8_tail_does_not_fall_back_to_oem(self):
        raw = ('界' * 10 + 'конец').encode('utf-8')
        self.assertEqual(loop._decode(raw[1:], truncated=True), '界' * 9 + 'конец')

    def test_aider_guard_refuses_datagrams(self):
        script = '''
import ast, socket, sys
from pathlib import Path
from urllib.parse import urlsplit
tree = ast.parse(Path(sys.argv[1]).read_text(encoding='utf-8'))
node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'guard_network')
exec(compile(ast.Module(body=[node], type_ignores=[]), '<guard>', 'exec'))
guard_network('http://127.0.0.1:1/v1')
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
    try:
        udp.sendto(b'audit', ('127.0.0.1', 2))
    except OSError:
        print('blocked')
'''
        result = subprocess.run([sys.executable, '-I', '-c', script, str(ROOT / 'App/server/agents/aider_bridge.py')],
                                capture_output=True, text=True, check=True, env=clean_environment())
        self.assertEqual(result.stdout.strip(), 'blocked')

    def test_two_runs_in_same_second_have_distinct_notes(self):
        first = loop.prepare(self.root, 'same-second')
        second = loop.prepare(self.root, 'same-second')
        self.assertNotEqual(first, second)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('node'), 'Windows junction and Node')
    def test_junction_is_refused_by_dsh_and_loop_writer(self):
        workspace, outside = self.root / 'project', self.root / 'outside'
        workspace.mkdir()
        outside.mkdir()
        link = workspace / '.visualforge'
        subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(outside)], check=True, capture_output=True)
        with self.assertRaises(ValueError):
            loop.prepare(workspace, 'test')
        self.assertEqual(list(outside.iterdir()), [])
        script = '''
import {apply} from POLICY;
let hook;
apply({on: (_, fn) => {hook = fn;}}, {workspace: ROOT, mode: 'auto'});
const call = async (name, args) => (await hook({name, arguments: args}, async () => ({kind:'allow'}))).kind;
const result = [];
result.push(await call('read', {file_path: '.visualforge/secret.txt'}));
result.push(await call('write', {file_path: '.visualforge/new/secret.txt'}));
result.push(await call('write', {file_path: 'safe/new.txt'}));
for (const command of ['git status', 'git status | Set-Content marker', 'git log $(echo x)', 'git status > marker', 'GIT push']) {
  result.push(await call('pwsh', {command}));
}
console.log(JSON.stringify(result));
'''.replace('POLICY', json.dumps((ROOT / 'App/server/agents/dsh_policy.mjs').as_uri())).replace('ROOT', json.dumps(str(workspace)))
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, encoding='utf-8', check=True, env=clean_environment())
        self.assertEqual(json.loads(result.stdout), ['deny', 'deny', 'allow', 'allow', 'ask', 'ask', 'ask', 'deny'])

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_node_guard_blocks_udp_and_native_dns(self):
        script = '''
const udp = require('node:dgram').createSocket('udp4');
const dns = require('node:dns');
let blocked = 0;
for (const call of [() => udp.send('x', 1, '127.0.0.1'), () => dns.resolve4('invalid.example', () => {}),
                   () => new dns.Resolver().resolve4('invalid.example', () => {})]) {
  try {call();} catch {blocked++;}
}
udp.close(); console.log(blocked);
'''
        result = subprocess.run(['node', '--require', str(ROOT / 'App/server/agents/dsh_net_guard.cjs'), '-e', script],
                                capture_output=True, text=True, check=True, env=dict(clean_environment(), VF_ALLOWED_ENDPOINT='127.0.0.1:1'))
        self.assertEqual(result.stdout.strip(), '3')

    def test_fingerprint_changes_for_untracked_contents(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        file = self.root / 'new.txt'
        file.write_text('first', encoding='utf-8')
        first = loop.facts(self.root, '')['fingerprint']
        file.write_text('other', encoding='utf-8')
        self.assertNotEqual(first, loop.facts(self.root, '')['fingerprint'])

    def loop_result(self, *, cancel_in_check=False, error='', handoff='## Status\nCONTINUE', wrap=False):
        m = self.manager()
        workspace = self.root / 'project'
        workspace.mkdir()
        chat = m.store.create(agent=dict(id='aider', workspace=str(workspace), permission='ask'))
        cancel = threading.Event()
        m.run = dict(chat=chat['id'], cancel=cancel, size=32768, wrap=threading.Event(), phase='work', used=0)
        calls = []
        def turn(*args, **kwargs):
            calls.append(cancel.is_set())
            if wrap:
                m.run['wrap'].set()
            return dict(content='answer', thinking='', steps=[], stats=None, error=error, stopped=False, handoff=handoff)
        def check(*args):
            cancel.set()
            return dict(code=-1, output='(stopped)')
        m._turn = turn
        try:
            with patch.object(loop, 'check', side_effect=check):
                result = m._loop(chat, 'goal', 'model', lambda e: None, cancel, lambda: False,
                                 dict(threshold=75, iterations=2, check='test' if cancel_in_check else ''))
        finally:
            m.run = None
        return result, calls

    def test_stop_in_check_stays_in_current_iteration(self):
        (chat, message, error), calls = self.loop_result(cancel_in_check=True)
        self.assertEqual(calls, [False])
        self.assertTrue(message['stopped'])
        self.assertEqual(len(chat['messages']), 2)

    def test_error_cannot_turn_into_done(self):
        (_, message, error), calls = self.loop_result(error='model failed', handoff='## Status\nDONE')
        self.assertEqual(error, 'model failed')
        self.assertEqual(message['error'], error)
        self.assertEqual(len(calls), 1)

    def test_error_at_threshold_is_not_discarded(self):
        (_, message, error), calls = self.loop_result(error='model failed', handoff='', wrap=True)
        self.assertEqual(error, 'model failed')
        self.assertEqual(len(calls), 1)

    def test_opencode_death_ends_idle_wait(self):
        m = self.manager()
        self.assertIn('неожиданно завершился', m._follow(queue.Queue(), lambda e: None, threading.Event(), lambda: False))

    def test_disconnect_is_detected_without_new_model_events(self):
        server, client = socket.socketpair()
        self.addCleanup(server.close)
        self.addCleanup(client.close)
        started = threading.Event()
        class WaitingManager:
            def busy(self): return False
            def send(self, ident, content, emit, disconnected, options):
                started.set()
                for _ in range(100):
                    if disconnected():
                        return dict(id='a', title='a', updated=0), {}, ''
                    threading.Event().wait(0.02)
                raise AssertionError('disconnect was not detected')
        handler = types.SimpleNamespace(connection=server)
        errors = []
        def run():
            try: routes.send(handler, WaitingManager(), dict(chat_id='a', content='test'))
            except BaseException as e: errors.append(e)
        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(started.wait(1))
        client.close()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def test_aider_auto_requires_permission_for_shell_operators(self):
        # Importing the bridge redirects process stdio; load only the policy method.
        source = ast.parse((ROOT / 'App/server/agents/aider_bridge.py').read_text(encoding='utf-8'))
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'Bridge')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'allow_command')
        constants = [n for n in source.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ('PUSH', 'AUTO_COMMANDS') for t in n.targets)]
        namespace = dict(re=re, REFUSED='refused')
        exec(compile(ast.Module(body=constants + [method], type_ignores=[]), '<policy>', 'exec'), namespace)
        asks = []
        bridge = types.SimpleNamespace(mode='auto', always_commands=set(), next_id=lambda _: 'id', step=lambda **kw: None,
                                       ask=lambda **kw: asks.append(kw) or 'reject')
        allow = lambda command: namespace['allow_command'](bridge, command)
        self.assertTrue(allow('git status'))
        for command in ('git status & echo x', 'git status > x', 'git log $(echo x)', 'git diff | echo x', 'git status %X%'):
            self.assertFalse(allow(command), command)
        count = len(asks)
        self.assertFalse(allow('GIT push'))
        self.assertEqual(len(asks), count)


if __name__ == '__main__':
    unittest.main()
