"""Expired prompts, fallback imports and dependency failures use tiny disposable artifacts."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
sys.path.insert(0, str(ROOT / 'tests'))
from fake_web import FakeWeb
from provision import download_transport as net
from provision.install_engine import Engine
from provision.component_recovery import Recovery
from provision.software_bundle import bundled_source
from provision.components_manager import ComponentsManager
from test_provision import tiny_catalog


class RecoveryTests(unittest.TestCase):
    def test_enabling_incomplete_model_repairs_only_its_dependencies(self):
        with tempfile.TemporaryDirectory() as directory, FakeWeb() as web:
            root = Path(directory)
            catalog, _, _ = tiny_catalog(web)
            unrelated = dict(id='unrelated.runtime', kind='software', module='demo', title='Unrelated',
                             requires=[], present=['Runtime/Unrelated/program.exe'])
            catalog['components'].append(unrelated)
            catalog['modules'][0]['components'].append(unrelated['id'])
            path = root/'catalog.json';path.write_text(json.dumps(catalog))
            manager = ComponentsManager(root, path)
            manager.models.toggle('demo.model.a', False)
            result = manager.models.toggle('demo.model.a', True)
            self.assertTrue(result['repairing'])
            manager.thread.join(5)
            self.assertFalse(manager.running())
            self.assertEqual(manager.results['demo.model.a'], 'installed')
            self.assertNotIn('unrelated.runtime', manager.results)
            self.assertNotIn('demo.model.a', manager.models.policy.disabled())

    def test_deadline_is_server_owned_and_late_answer_is_rejected(self):
        now = [10.]
        recovery = Recovery(clock=lambda: now[0])
        result = []
        worker = threading.Thread(target=lambda: result.append(recovery.request({'name':'x'}, lambda:False)))
        worker.start()
        while not recovery.snapshot():
            time.sleep(.001)
        request = recovery.snapshot()
        self.assertEqual(request['remaining_seconds'], 30)
        now[0] = 40
        with self.assertRaises(ValueError):
            recovery.respond(request['token'], 'url', 'https://example.com/file')
        recovery.wake();worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [None])
        self.assertIsNone(recovery.snapshot())

    def test_answer_validation_single_use_and_cancel(self):
        recovery = Recovery()
        cancelled = threading.Event()
        result = []
        def work():
            try:
                result.append(recovery.request({'name':'x'}, cancelled.is_set))
            except net.Cancelled:
                result.append('cancelled')
        worker = threading.Thread(target=work);worker.start()
        while not recovery.snapshot():
            time.sleep(.001)
        token = recovery.snapshot()['token']
        for value in ('file:///bad', 'https://user:password@example.com/file', 'javascript:bad'):
            with self.assertRaises(ValueError):
                recovery.respond(token, 'url', value)
        recovery.respond(token, 'url', 'https://example.com/file')
        with self.assertRaises(ValueError):
            recovery.respond(token, 'skip')
        worker.join(2)
        self.assertEqual(result[0]['kind'], 'url')
        worker = threading.Thread(target=work);worker.start()
        while not recovery.snapshot():
            time.sleep(.001)
        cancelled.set();recovery.wake();worker.join(2)
        self.assertEqual(result[-1], 'cancelled')

    def test_import_keeps_original_and_rejects_wrong_hash_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);source = root/'original';target = root/'target'
            source.write_bytes(b'good');target.write_bytes(b'keep')
            digest = hashlib.sha256(b'good').hexdigest()
            with self.assertRaises(net.ChecksumError):
                net.import_file(source, target, 4, '0'*64)
            self.assertEqual(target.read_bytes(), b'keep')
            self.assertEqual(list(root.glob('*.tmp')), [])
            net.import_file(source, target, 4, digest)
            self.assertEqual(source.read_bytes(), b'good')
            self.assertEqual(target.read_bytes(), b'good')

    def test_installer_uses_manual_url_and_skips_only_failed_dependency_tree(self):
        with tempfile.TemporaryDirectory() as directory, FakeWeb() as web:
            root = Path(directory)
            payload = b'verified artifact';info = web.add('/good', payload)
            def component(ident, requires=()):
                return dict(id=ident,title=ident,kind='model',module='test',requires=list(requires),
                    files=[dict(path='Models/'+ident+'/file.bin', bytes=info['bytes'], sha256=info['sha256'], urls=[web.base+'/missing'])])
            catalog = dict(schema_version=1,modules=[dict(id='test',title='Test',components=['broken','dependent','independent'])],
                components=[component('broken'),component('dependent',['broken']),component('independent')])
            catalog['components'][0]['files'].append(dict(path='Models/broken/next.bin', bytes=info['bytes'], sha256=info['sha256'], urls=[info['url']]))
            path = root/'catalog.json';path.write_text(json.dumps(catalog))
            engine = Engine(root, path)
            asked = []
            def recover(item, cancel):
                asked.append(item['component'])
                return {'kind':'url','value':info['url']} if item['component']=='independent' else None
            engine.recover = recover
            result = engine.install(['dependent','independent'])
            self.assertTrue(result['broken'].startswith('failed'))
            self.assertTrue(result['dependent'].startswith('failed'))
            self.assertEqual(result['independent'], 'installed')
            self.assertEqual((root/'Models/broken/next.bin').read_bytes(), payload)
            self.assertEqual(asked, ['broken','independent'])
            # A second user attempt asks again rather than persisting a skipped dependency as installed.
            engine.install(['dependent'])
            self.assertEqual(asked[-1], 'broken')

    def test_software_mirror_never_includes_models_or_oversized_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);(root/'App/config').mkdir(parents=True)
            path = root/'App/config/software-release.json'
            config = dict(published=False,url='https://github.com/example/studio/releases/download/software')
            path.write_text(json.dumps(config))
            software, model = dict(kind='software'), dict(kind='model')
            self.assertIsNone(bundled_source(root, software, 'x.exe', 3, 'a'*64))
            config['published'] = True;path.write_text(json.dumps(config))
            self.assertIn('/'+'a'*64+'-x.exe', bundled_source(root, software, 'x.exe', 3, 'a'*64))
            self.assertIsNone(bundled_source(root, model, 'x.exe', 3, 'a'*64))
            self.assertIsNone(bundled_source(root, software, 'x.exe', 300000001, 'a'*64))


if __name__ == '__main__':
    unittest.main()
