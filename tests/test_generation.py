"""Stable four-field identities, real persistence and agent-loop lifetime without inference."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
import generation
from job_history import JobHistory
from text.store import ChatStore
from agents.agent_manager import AgentManager
from speech.speech_manager import SpeechManager

FIELDS = {'type', 'id', 'started_at', 'duration_sec'}


class GenerationTests(unittest.TestCase):
    def setUp(self):
        quiet = patch('builtins.print')
        quiet.start()
        self.addCleanup(quiet.stop)
        self.temporary = tempfile.TemporaryDirectory(prefix='generation-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def assertRecord(self, record, kind, done=True):
        self.assertEqual(set(record), FIELDS)
        self.assertEqual(record['type'], kind)
        self.assertRegex(record['id'], r'^[0-9a-f]{64}$')
        self.assertTrue(record['started_at'].endswith('Z'))
        self.assertIsNotNone(datetime.fromisoformat(record['started_at']).tzinfo)
        if done:
            self.assertIsInstance(record['duration_sec'], (int, float))
            self.assertGreaterEqual(record['duration_sec'], 0)
        else:
            self.assertIsNone(record['duration_sec'])

    def test_shape_and_monotonic_duration_survive_wall_clock_change(self):
        with patch('generation.time.monotonic', side_effect=[100, 103.125]), patch('generation.time.time', return_value=10):
            record = generation.begin('Image')
            self.assertRecord(record, 'Image', done=False)
            generation.finish(record)
        self.assertEqual(record['started_at'], '1970-01-01T00:00:10.000Z')
        self.assertEqual(record['duration_sec'], 3.125)
        generation.finish(record, 900)
        self.assertEqual(record['duration_sec'], 3.125)

    def test_10000_ids_are_random_and_unique_across_threads_and_modules(self):
        def one(number):
            record = generation.begin(tuple(sorted(generation.TYPES))[number % len(generation.TYPES)])
            generation.finish(record)
            return record['id']
        with ThreadPoolExecutor(max_workers=8) as pool:
            identities = list(pool.map(one, range(10000)))
        self.assertEqual(len(set(identities)), len(identities))
        self.assertTrue(all(len(ident) == 64 for ident in identities))

    def test_concurrent_finish_does_not_lose_duration(self):
        record = generation.begin('Music')
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: generation.finish(record), range(100)))
        self.assertRecord(record, 'Music')

    def test_legacy_id_is_stable_and_does_not_invent_a_start_time(self):
        first = generation.legacy('Agent', 'old-message')
        self.assertEqual(first, generation.legacy('Agent', 'old-message'))
        self.assertNotEqual(first['id'], generation.legacy('Text', 'old-message')['id'])
        self.assertEqual(set(first), FIELDS)
        self.assertIsNone(first['started_at'])
        self.assertIsNone(first['duration_sec'])
        self.assertIsNone(generation.legacy('Image', 'bad-date', 'unknown', 'unknown')['started_at'])
        self.assertIsNone(generation.duration_between('unknown', 10))
        self.assertEqual(generation.duration_between(10, 12.125), 2.125)

    def test_identity_card_and_clipboard_object(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is unavailable')
        done = subprocess.run([node, str(Path(__file__).with_name('generation_ui_test.js'))],
                              capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('generation UI ok', done.stdout)

    def test_image_receipt_keeps_exact_id_and_duration_after_reload(self):
        import uuid
        store = JobHistory(self.root / 'history.json', self.root)
        record = generation.begin('Image')
        ident = str(uuid.uuid4())
        store.record(ident, 'done', {'generation': record}, elapsed_sec=99)
        self.assertRecord(record, 'Image')
        self.assertEqual(store.get(ident)['generation'], record)
        self.assertEqual(JobHistory(store.path, self.root).get(ident)['generation'], record)

    def test_text_request_and_answer_share_one_persisted_operation(self):
        store = ChatStore(self.root / 'Chats')
        chat = store.create('', 'fake')
        record = generation.begin('Text')
        store.add_user(chat['id'], 'Question', generation=record)
        generation.finish(record)
        store.add_assistant(chat['id'], 'Answer', generation=record)
        store.update_generation(chat['id'], record)
        saved = ChatStore(self.root / 'Chats').get(chat['id'])
        self.assertEqual([m['generation'] for m in saved['messages']], [record, record])

    def test_speech_record_covers_recognition_and_polishing(self):
        manager = SpeechManager.__new__(SpeechManager)
        manager._recognize = Mock(return_value={'text': 'plain'})
        manager.polisher = SimpleNamespace(polish=Mock(return_value=('polished', '')))
        result = manager.transcribe(b'fixture', 'ru', enhance=True)
        self.assertEqual(result['text'], 'polished')
        self.assertRecord(result['generation'], 'SpeechRecognition')

    def agent(self):
        manager = AgentManager.__new__(AgentManager)
        manager.store = ChatStore(self.root / 'Chats')
        manager.paths = SimpleNamespace(logs=self.root / 'Logs')
        manager.lock, manager.run = threading.RLock(), None
        manager.text = SimpleNamespace(default_model=lambda: 'fake')
        manager.alias_of = lambda model: 'fake-alias'
        manager._models = lambda: {'fake-alias': {'name': 'fake', 'context': 32000}}
        chat = manager.store.create('', 'fake', agent=dict(id='opencode', workspace=str(self.root), permission='read', session=''))
        turn = dict(content='answer', thinking='', stats=None, error='', stopped=False, steps=[], handoff='Status: DONE')
        manager._turn = Mock(return_value=turn)
        return manager, chat, turn

    def test_agent_tasks_get_distinct_ids_and_store_final_metadata(self):
        manager, chat, _ = self.agent()
        first, second = [manager.send(chat['id'], 'Task', lambda event: None, lambda: False)[1] for _ in range(2)]
        self.assertRecord(first['generation'], 'Agent')
        self.assertNotEqual(first['generation']['id'], second['generation']['id'])
        loaded = manager.store.get(chat['id'])
        self.assertEqual(loaded['messages'][0]['generation'], loaded['messages'][1]['generation'])
        self.assertIsNone(manager.run)

    def test_agent_loop_has_one_id_and_final_duration_for_all_iterations(self):
        manager, chat, turn = self.agent()
        manager._turn.side_effect = [dict(turn, handoff='Status: CONTINUE\nNext step: continue'), dict(turn, handoff='Status: DONE')]
        events = []
        chat, last, error = manager.send(chat['id'], 'Task', events.append, lambda: False, dict(threshold=75, iterations=2, check=''))
        self.assertFalse(error)
        self.assertEqual(len(chat['messages']), 3)
        self.assertRecord(last['generation'], 'Agent')
        self.assertEqual([m['generation'] for m in chat['messages']], [last['generation']] * 3)
        self.assertEqual(events[0]['generation']['id'], last['generation']['id'])
        self.assertIn(last['generation']['id'], next((self.root / '.visualforge/loop').rglob('*handoff.md')).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
