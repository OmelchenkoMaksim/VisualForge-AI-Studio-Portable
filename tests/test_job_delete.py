"""Deletion uses throwaway CPU-only artifacts; no installed media or real backend is touched."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from job_delete import DeletePlan, confined_file, write_receipt
from job_history import JobHistory
from music.manager import MusicManager
from video.service import Jobs
import server


class DeleteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='delete-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ident = str(uuid.uuid4())

    def artifact(self, relative, value=b'owned artifact'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def test_ui_confirmation_retry_and_single_post(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is unavailable')
        result = subprocess.run([node, str(Path(__file__).with_name('job_delete_ui_test.js'))],
                                capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('job deletion UI ok', result.stdout)

    def image(self):
        store = JobHistory(self.root / 'image-history.json', portable_root=self.root)
        saved = self.artifact('External results/Результат.png')
        original = self.artifact('ComfyOutput/original.png')
        preview = self.artifact('ComfyTemp/preview.png')
        uploaded = self.artifact('ComfyInput/qwen_local_fixture.png')
        store.record(self.ident, 'done', {'input_files': [uploaded.name]}, [str(saved)])
        handler = object.__new__(server.Handler)
        handler.response_json = lambda code, body: (code, body)
        handler.job_is_queued_or_running = Mock(return_value=False)
        handler.fetch_history = Mock(return_value={'outputs': {'9': {'images': [
            {'filename': original.name, 'type': 'output'}, {'filename': preview.name, 'type': 'temp'}]}}})
        paths = SimpleNamespace(root=self.root, comfy=self.root / 'Comfy', comfy_input=uploaded.parent,
                                comfy_output=original.parent, comfy_temp=preview.parent)
        for name, value in {'PATHS': paths, 'HISTORY': store, 'SAVE_LOCKS': {}, 'JOB_META': {self.ident: {'seed': 9}},
                            'JOB_TOKENS': {self.ident: 'fixture'}, 'CANCELLING': {self.ident}}.items():
            context = patch.object(server, name, value)
            context.start()
            self.addCleanup(context.stop)
        remote_context = patch.object(server, 'json_upstream', return_value={})
        remote = remote_context.start()
        self.addCleanup(remote_context.stop)
        return store, handler, (saved, original, preview, uploaded), remote

    def test_image_removes_files_backend_receipt_and_memory_without_resurrection(self):
        store, handler, artifacts, remote = self.image()
        unrelated = self.artifact('ComfyOutput/unrelated.png')
        self.assertEqual(handler.delete_image({'id': self.ident}), (200, {'deleted': True}))
        self.assertTrue(all(not p.exists() for p in artifacts))
        self.assertTrue(unrelated.exists())
        remote.assert_called_once_with('/history', {'delete': [self.ident]}, timeout=15)
        self.assertNotIn(self.ident, server.JOB_META)
        self.assertNotIn(self.ident, server.JOB_TOKENS)
        self.assertNotIn(self.ident, server.CANCELLING)
        store.record(self.ident, 'cancelled', {})  # An earlier status request finishes after deletion.
        self.assertIsNone(store.get(self.ident))
        self.assertIsNone(JobHistory(store.path).get(self.ident))
        with patch.object(server, 'load_settings', side_effect=AssertionError('must not save again')):
            with self.assertRaises(ValueError):
                handler.maybe_save_outputs(self.ident, [])

    def test_image_busy_or_unknown_id_changes_nothing(self):
        store, handler, artifacts, remote = self.image()
        handler.job_is_queued_or_running.return_value = True
        for ident in (self.ident, str(uuid.uuid4()), '../escape'):
            with self.subTest(ident=ident), self.assertRaises(ValueError):
                handler.delete_image({'id': ident})
        self.assertTrue(all(p.exists() for p in artifacts))
        remote.assert_not_called()

    def test_image_backend_failure_keeps_receipt_for_retry(self):
        store, handler, artifacts, remote = self.image()
        remote.side_effect = OSError('backend unavailable')
        with self.assertRaises(OSError):
            handler.delete_image({'id': self.ident})
        self.assertIsNotNone(store.get(self.ident))
        remote.side_effect = None
        self.assertTrue(handler.delete_image({'id': self.ident})[1]['deleted'])

    def test_image_path_escape_rejected_before_any_delete(self):
        store, handler, artifacts, remote = self.image()
        handler.fetch_history.return_value['outputs']['9']['images'].append({'filename': '../foreign.png', 'type': 'temp'})
        with self.assertRaises(ValueError):
            handler.delete_image({'id': self.ident})
        self.assertTrue(all(p.exists() for p in artifacts))
        remote.assert_not_called()

    def music(self):
        manager = MusicManager.__new__(MusicManager)
        manager.paths = SimpleNamespace(root=self.root)
        manager.state, manager.logs = self.root / 'Scratch', self.root / 'Logs/Music'
        manager.history_file = self.root / 'music-history.json'
        manager.lock = threading.RLock()
        ident = self.ident.replace('-', '')
        output = self.artifact('External Music/Трек - ' + ident + '.wav')
        metadata = output.with_suffix('.json')
        write_receipt(metadata, {'id': ident})
        row = dict(id=ident, output=str(output), state='done')
        manager.history = [row]
        manager.active = dict(row)
        scratch = self.artifact('Scratch/' + ident + '/request.json')
        log = self.artifact('Logs/Music/' + ident + '.log')
        shared = self.artifact('Logs/shared.log')
        write_receipt(manager.history_file, manager.history)
        return manager, ident, (output, metadata, scratch, log), shared

    def test_music_removes_history_result_scratch_and_log(self):
        manager, ident, artifacts, shared = self.music()
        self.assertTrue(manager.delete({'id': ident})['deleted'])
        self.assertEqual(manager.history, [])
        self.assertIsNone(manager.active)
        self.assertEqual(json.loads(manager.history_file.read_text()), [])
        self.assertTrue(all(not p.exists() for p in artifacts))
        self.assertTrue(shared.exists())
        self.assertFalse((manager.state / ident).exists())

    def test_music_running_or_unknown_job_is_not_deleted(self):
        manager, ident, artifacts, _ = self.music()
        manager.history[0]['state'] = 'running'
        for value in (ident, 'f' * 32, '../escape'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                manager.delete({'id': value})
        self.assertTrue(all(p.exists() for p in artifacts))

    def test_music_failed_unlink_keeps_receipt_and_can_be_retried(self):
        manager, ident, artifacts, _ = self.music()
        with patch('job_delete.DeletePlan.execute', side_effect=PermissionError('fixture locked')):
            with self.assertRaises(PermissionError):
                manager.delete({'id': ident})
        self.assertEqual(manager.history[0]['id'], ident)
        self.assertTrue(all(p.exists() for p in artifacts))
        self.assertTrue(manager.delete({'id': ident})['deleted'])

    def test_music_history_write_failure_keeps_receipt_for_retry(self):
        manager, ident, _, _ = self.music()
        with patch('job_delete.write_receipt', side_effect=PermissionError('fixture locked')):
            with self.assertRaises(PermissionError):
                manager.delete({'id': ident})
        self.assertEqual(manager.history[0]['id'], ident)
        self.assertEqual(json.loads(manager.history_file.read_text())[0]['id'], ident)
        self.assertTrue(manager.delete({'id': ident})['deleted'])

    def test_stable_audio_deletes_comfy_flac_and_can_retry_remote_failure(self):
        from music.comfy_backend import ComfyError
        manager, ident, artifacts, _ = self.music()
        prompt_id = str(uuid.uuid4())
        flac = self.artifact('ComfyTemp/audio.flac')
        manager.paths.comfy_temp = flac.parent
        client = Mock()
        item = {'filename': flac.name, 'type': 'temp'}
        client.get.return_value = {prompt_id: {'outputs': {'9': {'audio': [item]}}}}
        client.post.side_effect = ComfyError('fixture offline')
        manager.comfy = SimpleNamespace(client=client)
        write_receipt(manager.state / ident / 'comfy-artifacts.json', {'prompt_id': prompt_id, 'outputs': [item]})
        with self.assertRaises(ValueError):
            manager.delete({'id': ident})
        self.assertEqual(manager.history[0]['id'], ident)
        # A failed/cancelled job can have only the scratch manifest, without completed result metadata.
        # Reload the persisted receipt after scratch was removed, as a studio restart would do.
        manager.history = json.loads(manager.history_file.read_text(encoding='utf-8'))
        self.assertEqual(manager.history[0]['comfy_prompt_id'], prompt_id)
        client.get.return_value = {}
        client.post.side_effect = None
        self.assertTrue(manager.delete({'id': ident})['deleted'])
        self.assertTrue(all(not path.exists() for path in (*artifacts, flac)))
        client.post.assert_called_with('/history', {'delete': [prompt_id]}, timeout=15)

    def video(self):
        args = argparse.Namespace(user=str(self.root / 'UserData'), root=str(self.root / 'Runtime'),
                                  models=str(self.root / 'Models'), state=str(self.root / 'State'))
        jobs = Jobs(args)
        folder = jobs.user / 'Cache/Video/Jobs' / self.ident
        scratch = self.artifact(folder / 'request.json')
        output = self.artifact('External Video/Ролик.mp4')
        metadata = output.with_suffix('.json')
        write_receipt(metadata, {'id': self.ident})
        log = self.artifact(jobs.user / 'Logs/Video/Wan' / (self.ident + '.log'))
        jobs.rows[self.ident] = dict(id=self.ident, state='done', folder=str(folder), output_path=str(output),
                                    log_path=str(log), submitted_at=0, finished_at=1)
        jobs.persist()
        return jobs, (output, metadata, scratch, log)

    def test_video_removes_results_scratch_log_and_persistent_receipt(self):
        jobs, artifacts = self.video()
        unrelated = self.artifact('External Video/unrelated.mp4')
        self.assertTrue(jobs.delete({'id': self.ident})['deleted'])
        self.assertEqual(jobs.rows, {})
        self.assertEqual(Jobs(jobs.args).rows, {})
        self.assertTrue(all(not p.exists() for p in artifacts))
        self.assertTrue(unrelated.exists())

    def test_video_shared_scratch_survives_until_last_derived_job_is_deleted(self):
        jobs, (output, metadata, scratch, log) = self.video()
        derived_id = str(uuid.uuid4())
        derived = self.artifact('External Video/mix.mp4')
        receipt = derived.with_suffix('.mix.json')
        write_receipt(receipt, {'output_file': derived.name})
        row = copy.deepcopy(jobs.rows[self.ident])
        row.update(id=derived_id, output_path=str(derived), derived_kind='mix')
        jobs.rows[derived_id] = row
        jobs.delete({'id': self.ident})
        self.assertFalse(output.exists())
        self.assertTrue(scratch.exists() and log.exists() and derived.exists())
        jobs.delete({'id': derived_id})
        self.assertTrue(all(not p.exists() for p in (scratch, log, derived, receipt)))

    def test_video_active_export_or_generation_blocks_delete(self):
        jobs, artifacts = self.video()
        jobs.active = self.ident
        with self.assertRaises(ValueError):
            jobs.delete({'id': self.ident})
        jobs.active = None
        jobs.exports['fixture'] = dict(state='running')
        with self.assertRaises(ValueError):
            jobs.delete({'id': self.ident})
        self.assertTrue(all(p.exists() for p in artifacts))

    def test_video_persistence_failure_keeps_receipt(self):
        jobs, _ = self.video()
        with patch.object(jobs, 'persist', side_effect=PermissionError('fixture')):
            with self.assertRaises(PermissionError):
                jobs.delete({'id': self.ident})
        self.assertIn(self.ident, jobs.rows)
        self.assertTrue(jobs.delete({'id': self.ident})['deleted'])

    def test_video_export_artifacts_survive_restart_and_are_deleted(self):
        jobs, _ = self.video()
        exported = self.artifact(jobs.user / 'Results/export.mp4')
        receipt = exported.with_suffix('.delivery.json')
        write_receipt(receipt, {'output_file': exported.name})
        jobs.rows[self.ident]['export_files'] = [str(exported)]
        jobs.persist()
        restored = Jobs(jobs.args)
        restored.delete({'id': self.ident})
        self.assertFalse(exported.exists() or receipt.exists())

    def test_foreign_sidecar_is_preserved(self):
        jobs, (output, metadata, _, _) = self.video()
        write_receipt(metadata, {'id': 'another-job'})
        jobs.delete({'id': self.ident})
        self.assertTrue(metadata.exists())
        self.assertFalse(output.exists())

    def test_unknown_technical_folder_is_rejected_before_any_delete(self):
        jobs, artifacts = self.video()
        jobs.rows[self.ident]['folder'] = str(self.root)
        with self.assertRaises(ValueError):
            jobs.delete({'id': self.ident})
        self.assertTrue(all(p.exists() for p in artifacts))

    def test_plan_rejects_traversal_and_junction_without_touching_target(self):
        outside = self.artifact('outside/keep.txt')
        folder = self.root / 'owned'
        folder.mkdir()
        own = self.artifact('owned/own.txt')
        with self.assertRaises(ValueError):
            confined_file(folder, '../outside/keep.txt')
        link = folder / 'junction'
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(outside.parent)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        else:
            link.symlink_to(outside.parent, target_is_directory=True)
            self.addCleanup(lambda: link.unlink(missing_ok=True))
        plan = DeletePlan()
        with self.assertRaises(ValueError):
            plan.tree(folder)
        self.assertTrue(outside.exists() and own.exists())


if __name__ == '__main__':
    unittest.main()
