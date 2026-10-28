"""Persistent and isolated central history; all data stays in user Temp, without inference."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

SERVER = Path(__file__).resolve().parents[1] / 'App/server'
sys.path.insert(0, str(SERVER))
from event_history import EventHistory
import generation


class EventHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='events-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = EventHistory(self.root / 'events.sqlite3')
        configured = patch.object(generation, '_store', self.store)
        configured.start(); self.addCleanup(configured.stop)
        self.quiet = io.StringIO()
        quiet = redirect_stdout(self.quiet)
        quiet.__enter__(); self.addCleanup(quiet.__exit__, None, None, None)

    def test_start_finish_updates_one_row_and_survives_restart(self):
        record = generation.begin('Text')
        row = self.store.page()['entries'][0]
        self.assertEqual(row['state'], 'running')
        self.assertIsNone(row['generation']['duration_sec'])
        generation.finish(record)
        loaded = EventHistory(self.store.path).page()['entries']
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]['generation'], record)
        self.assertEqual(loaded[0]['state'], 'done')
        self.assertEqual(set(record), {'id', 'type', 'started_at', 'duration_sec'})

    def test_technical_events_are_separate_and_never_enter_existing_logs(self):
        with generation.operation('Text') as parent:
            self.quiet.seek(0);self.quiet.truncate()
            with generation.operation('ImageRecognition') as child:
                ordinary = self.root / 'existing.log'
                ordinary.write_text('Existing module content', encoding='utf-8')
                generation.append_log(ordinary, child)
            self.assertEqual(self.quiet.getvalue(), '')
            self.assertEqual(ordinary.read_text(encoding='utf-8'), 'Existing module content')
        technical = self.store.page('scope=technical')['entries']
        self.assertEqual(len(technical), 1)
        self.assertEqual(technical[0]['generation']['type'], 'ImageRecognition')
        self.assertEqual(technical[0]['parent_id'], parent['id'])
        self.assertTrue(technical[0]['technical'])
        self.assertEqual(len(self.store.page('scope=main')['entries']), 1)

    def test_failure_and_cancel_keep_their_final_state(self):
        with self.assertRaisesRegex(ValueError, 'fixture'):
            with generation.operation('SpeechTextPolish') as failed:
                raise ValueError('fixture')
        record = generation.begin('MicrophoneSession')
        generation.finish(record, state='cancelled')
        generation.finish(record)  # Completion bookkeeping must not overwrite cancellation.
        states = {row['generation']['id']: row['state'] for row in self.store.page()['entries']}
        self.assertEqual(states[record['id']], 'cancelled')
        self.assertEqual(states[failed['id']], 'error')

    def populate(self, count=8):
        for number in range(count):
            record = generation.legacy('Image', str(number), 1000 + number, number)
            self.store.record(record, 'running')
            self.store.record(record, 'done')

    def test_keyset_pagination_does_not_skip_rows_when_new_events_arrive(self):
        self.populate()
        first = self.store.page('limit=3')
        record = generation.begin('Text');generation.finish(record)
        second = self.store.page('limit=3&before=' + first['next'])
        third = self.store.page('limit=3&before=' + second['next'])
        records = [r['generation'] for page in (first, second, third) for r in page['entries']]
        self.assertEqual(len(records), 8)
        self.assertEqual(len({r['id'] for r in records}), 8)
        self.assertEqual([r['started_at'] for r in records], sorted([r['started_at'] for r in records], reverse=True))

    def test_filtering_and_validation(self):
        self.populate(2)
        with generation.operation('SpeechModelLoad'):
            pass
        self.assertEqual(len(self.store.page('scope=technical')['entries']), 1)
        self.assertEqual(len(self.store.page('type=Image&state=done')['entries']), 2)
        for query in ('type=bad', 'scope=bad', 'state=bad', 'limit=999999', 'before=bad', 'limit=x', "type=';DROP TABLE events"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.store.page(query)
        self.assertEqual(len(self.store.page()['entries']), 3)

    def test_child_events_are_removed_with_their_generation(self):
        with generation.operation('Video') as parent:
            with generation.operation('VideoModelGeneration'):
                with generation.operation('SpeechSynthesis'):
                    pass
        other = generation.begin('Music');generation.finish(other)
        self.store.remove_generation(parent['id'])
        self.assertEqual([r['generation']['id'] for r in self.store.page()['entries']], [other['id']])

    def test_children_can_be_removed_if_their_parent_record_failed_to_persist(self):
        with generation.operation('SpeechSynthesis', parent_id='a' * 64):
            pass
        self.store.remove_generation('a' * 64)
        self.assertEqual(self.store.page()['entries'], [])

    def test_corrupt_database_is_preserved(self):
        self.store.path.write_bytes(b'not a database')
        before = self.store.path.read_bytes()
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.page()
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_failed_storage_does_not_break_a_generation(self):
        blocker = self.root / 'file'
        blocker.write_bytes(b'x')
        with patch.object(generation, '_store', EventHistory(blocker / 'events.sqlite3')):
            record = generation.begin('SpeechRecognition')
            generation.finish(record)
            self.assertIsNotNone(record['duration_sec'])
            self.assertEqual(generation.history().failed_writes, 2)
            self.assertTrue(generation.history().warning)

    def test_multiple_owned_processes_share_one_history_without_lost_rows(self):
        # Initialize before concurrent writers, as the bridge does before accepting tasks.
        self.store.page()
        script = self.root / 'writer.py'
        script.write_text("import sys\nsys.path.insert(0, sys.argv[1])\nimport generation\ngeneration.configure(sys.argv[2])\nfor _ in range(20):\n    r=generation.begin('SpeechPunctuation')\n    generation.finish(r)\n", encoding='utf-8')
        processes = [subprocess.Popen([sys.executable, '-B', str(script), str(SERVER), str(self.store.path)],
                                     cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
        for process in processes:
            out, err = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, err.decode('utf-8', 'replace'))
            self.assertEqual(out, b'', 'Technical operations must not leak into existing process logs')
        rows = self.store.page('limit=200')['entries']
        self.assertEqual(len(rows), 60)
        self.assertTrue(all(row['state'] == 'done' for row in rows))

    def test_settings_dialog_and_microphone_events(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is unavailable')
        done = subprocess.run([node, str(Path(__file__).with_name('event_history_ui_test.js'))],
                              capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('event history UI ok', done.stdout)

    def test_music_planner_has_its_own_event_without_changing_arguments(self):
        from music.worker import trace_planner
        calls = []

        class Planner:
            def generate_with_stop_condition(self, caption, seed):
                calls.append((caption, seed))
                return {'fixture': True}

        planner = trace_planner(Planner())
        self.assertEqual(self.store.page()['entries'], [])
        with generation.operation('Music') as parent:
            self.assertEqual(planner.generate_with_stop_condition('private fixture input', seed=7), {'fixture': True})
        row = self.store.page('type=MusicPlanning')['entries'][0]
        self.assertEqual(row['parent_id'], parent['id'])
        self.assertEqual(calls, [('private fixture input', 7)])
        self.assertNotIn('private fixture input', json.dumps(self.store.page()))

    def test_agent_image_inputs_are_identified_without_parsing_user_text(self):
        from agents.gateway import has_images
        self.assertFalse(has_images({'messages': [{'content': 'image_url is just a word'}]}))
        self.assertFalse(has_images({'messages': 1}))
        self.assertFalse(has_images({'messages': [{'content': [{'type': 'text', 'text': 'fixture'}]}]}))
        self.assertTrue(has_images({'messages': [{'content': [{'type': 'image_url', 'image_url': {'url': 'fixture'}}]}]}))
        self.assertTrue(has_images({'messages': [{'images': ['fixture']}]}))

    def test_speech_parent_is_forwarded_in_a_header_without_url_logging(self):
        from speech.speech_manager import SpeechManager
        from speech.routes import transcribe
        parent = generation.begin('MicrophoneSession')
        manager = SpeechManager.__new__(SpeechManager)
        manager._recognize = Mock(return_value={'text': 'fixture'})
        body = b'fixture'
        handler = SimpleNamespace(headers={'Content-Length': str(len(body)), 'Content-Type': 'audio/wav',
                                           'X-VisualForge-Session': parent['id']}, rfile=io.BytesIO(body),
                                  response_json=lambda code, value: (code, value))
        try:
            code, result = transcribe(handler, manager, 'lang=ru')
            self.assertEqual(code, 200)
            row = self.store.page('type=SpeechRecognition')['entries'][0]
            self.assertEqual(row['parent_id'], parent['id'])
            self.assertEqual(result['generation'], row['generation'])
        finally:
            generation.finish(parent)


if __name__ == '__main__':
    unittest.main()
