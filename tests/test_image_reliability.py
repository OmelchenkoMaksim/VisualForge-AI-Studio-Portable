"""Focused persistence and terminal-state regression checks."""
import concurrent.futures
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from job_history import JobHistory
import server


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_root = Path(tempfile.gettempdir()) / 'image-history-tests'
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=self.temp_root)
        self.root = Path(self.directory.name)
        self.path = self.root / 'history.json'
        self.store = JobHistory(self.path)
        self.ident = str(uuid.uuid4())
        self.image = self.root / 'result.png'
        self.image.write_bytes(b'image-fixture')
        self.metadata = dict(engine='qwen-image-21-uc-fp8', seed=123, original_prompt='test', cancel_token='must-not-persist')

    def tearDown(self):
        self.root.resolve().relative_to(self.temp_root.resolve())
        self.directory.cleanup()

    def complete(self):
        self.store.record(self.ident, 'done', self.metadata, [str(self.image)], 4)

    def test_result_survives_restart_without_cancel_credentials(self):
        self.complete()
        restored = JobHistory(self.path)
        self.assertEqual(restored.get(self.ident)['seed'], 123)
        self.assertEqual(restored.completed()[0]['image_indices'], [0])
        self.assertNotIn('cancel_token', self.path.read_text())

    def test_corrupt_history_is_not_overwritten(self):
        self.path.write_text('{broken')
        restored = JobHistory(self.path)
        restored.record(self.ident, 'cancelled', {})
        self.assertEqual(self.path.read_text(), '{broken')
        self.assertTrue(restored.warning)
        self.assertEqual(restored.get(self.ident)['state'], 'cancelled')

    def test_cancelled_receipt_survives_restart(self):
        self.store.record(self.ident, 'cancelled', {}, elapsed_sec=8)
        self.assertEqual(JobHistory(self.path).get(self.ident)['state'], 'cancelled')
        self.assertEqual(self.store.completed(), [])

    def test_eviction_keeps_files(self):
        self.complete()
        for _ in range(100):
            self.store.record(str(uuid.uuid4()), 'cancelled', {})
        self.assertEqual(len(JobHistory(self.path).rows), 100)
        self.assertIsNone(self.store.get(self.ident))
        self.assertTrue(self.image.exists())

    def test_concurrent_completions_are_not_lost(self):
        identifiers = [str(uuid.uuid4()) for _ in range(12)]
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda ident:self.store.record(ident, 'done', {}, [str(self.image)]), identifiers))
        self.assertEqual(set(JobHistory(self.path).rows), set(identifiers))

    def test_failed_atomic_write_preserves_previous_file(self):
        self.complete()
        before = self.path.read_bytes()
        with patch('job_history.os.replace', side_effect=OSError('disk unavailable')):
            self.store.record(str(uuid.uuid4()), 'cancelled', {})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.store.warning)

    def test_missing_output_stays_visible_with_no_download_index(self):
        self.complete()
        self.image.unlink()
        self.assertEqual(self.store.completed()[0]['image_indices'], [])

    def test_invalid_receipt_preserves_document(self):
        self.path.write_text(json.dumps({'schema_version':1,'entries':[{'prompt_id':'../../secret'}]}))
        before = self.path.read_bytes()
        restored = JobHistory(self.path)
        self.assertFalse(restored.writable)
        self.assertEqual(before, self.path.read_bytes())

    def handler(self):
        handler = object.__new__(server.Handler)
        handler.response_json = lambda code,body:(code,body)
        handler.reply = lambda code,raw,kind:(code,raw,kind)
        return handler

    def test_cancel_status_is_repeatable_without_backend(self):
        self.store.record(self.ident, 'cancelled', {}, elapsed_sec=8)
        with patch.object(server, 'HISTORY', JobHistory(self.path)), patch.object(server, 'json_upstream', side_effect=AssertionError('backend must not be called')):
            self.assertEqual(self.handler().status(self.ident), self.handler().status(self.ident))
            self.assertEqual(self.handler().status(self.ident)[1]['state'], 'cancelled')

    def test_saved_result_served_without_backend(self):
        self.complete()
        with patch.object(server, 'HISTORY', JobHistory(self.path)), patch.object(server, 'json_upstream', side_effect=AssertionError('backend must not be called')):
            self.assertEqual(self.handler().status(self.ident)[1]['state'], 'done')
            self.assertEqual(self.handler().result(self.ident, '0')[1], self.image.read_bytes())
            self.assertEqual(self.handler().result(self.ident, '-1')[0], 404)
            self.assertEqual(self.handler().result(self.ident, '../../secret')[0], 404)

    def test_missing_saved_result_is_not_requeued(self):
        self.complete()
        self.image.unlink()
        with patch.object(server, 'HISTORY', self.store):
            self.assertEqual(self.handler().status(self.ident)[1]['state'], 'error')
            self.assertEqual(self.handler().result(self.ident, '0')[0], 404)

    def test_autosave_watcher_stops_for_terminal_receipt(self):
        self.store.record(self.ident, 'cancelled', {})
        handler = self.handler()
        handler.fetch_history = lambda ident: self.fail('Terminal watcher must stop')
        with patch.object(server, 'HISTORY', self.store), patch.object(server, 'JOB_META', {self.ident:{'seed':123}}), patch.object(server.time, 'sleep'):
            handler.autosave_watcher(self.ident)

    def test_autosave_watcher_stops_after_a_failed_run(self):
        # A run that ended with an error has a history entry and no outputs: one look is enough, not two hours of polling.
        calls = []
        handler = self.handler()
        handler.fetch_history = lambda ident: calls.append(ident) or {'status': {'status_str': 'error', 'completed': False, 'messages': [['execution_error', {}]]}, 'outputs': {}}
        with patch.object(server, 'HISTORY', self.store), patch.object(server, 'JOB_META', {self.ident:{'seed':123}}), patch.object(server.time, 'sleep'):
            handler.autosave_watcher(self.ident)
        self.assertEqual(len(calls), 1)

    def test_autosave_watcher_logs_an_outage_once(self):
        clock = iter(range(0, 10 ** 6, 60))
        handler = self.handler()
        handler.fetch_history = lambda ident: (_ for _ in ()).throw(server.URLError('refused'))
        with patch.object(server, 'HISTORY', self.store), patch.object(server, 'JOB_META', {self.ident:{'seed':123}}), patch.object(server.time, 'sleep'), \
                patch.object(server.time, 'monotonic', lambda: next(clock)), patch('builtins.print') as printed:
            handler.autosave_watcher(self.ident)
        lines = [call.args[0] for call in printed.call_args_list]
        self.assertEqual(lines.count('[autosave] attempt pending or failed: URLError'), 1)

    def test_queue_cancel_is_persisted_without_browser_polling(self):
        handler = self.handler()
        handler.fetch_history = lambda ident: None
        handler.job_is_queued_or_running = lambda ident: False
        with patch.object(server, 'HISTORY', self.store), patch.object(server, 'JOB_META', {self.ident:{'seed':123}}), patch.object(server, 'CANCELLING', {self.ident}), patch.object(server, 'JOB_TOKENS', {self.ident:'owned-token'}), patch.object(server.time, 'sleep'):
            handler.autosave_watcher(self.ident)
            self.assertEqual(self.store.get(self.ident)['state'], 'cancelled')
            self.assertNotIn(self.ident, server.JOB_TOKENS)

    def test_internal_paths_follow_moved_portable_root(self):
        original = self.root / 'portable-original'
        moved = self.root / 'portable-moved'
        for directory in (original, moved):
            (directory/'UserData/Results').mkdir(parents=True)
            (directory/'UserData/Results/image.png').write_bytes(b'portable-output')
        history = JobHistory(original/'history.json', portable_root=original)
        history.record(self.ident, 'done', {}, [str(original/'UserData/Results/image.png')])
        self.assertEqual(json.loads(history.path.read_text())['entries'][0]['saved_files'], ['UserData/Results/image.png'])
        (moved/'history.json').write_bytes(history.path.read_bytes())
        restored = JobHistory(moved/'history.json', portable_root=moved)
        self.assertEqual(restored.get(self.ident)['saved_files'], [str(moved/'UserData/Results/image.png')])
        self.assertEqual(restored.completed()[0]['image_indices'], [0])

    def test_external_absolute_paths_remain_external(self):
        first = self.root/'first-root'
        second = self.root/'second-root'
        history = JobHistory(self.path, portable_root=first)
        history.record(self.ident, 'done', {}, [str(self.image)])
        restored = JobHistory(self.path, portable_root=second)
        self.assertEqual(restored.get(self.ident)['saved_files'], [str(self.image)])

    def test_full_length_unicode_prompt_survives_history(self):
        prompt = '\U0001f600' * 5000
        self.store.record(self.ident, 'done', {'original_prompt':prompt}, [str(self.image)])
        self.assertEqual(JobHistory(self.path).get(self.ident)['original_prompt'], prompt)

    def test_deeply_nested_corrupt_history_does_not_break_startup(self):
        self.path.write_text('[' * 1200 + '0' + ']' * 1200)
        before=self.path.read_bytes()
        restored=JobHistory(self.path)
        self.assertTrue(restored.warning)
        self.assertFalse(restored.writable)
        self.assertEqual(before,self.path.read_bytes())

if __name__ == '__main__':
    unittest.main()
