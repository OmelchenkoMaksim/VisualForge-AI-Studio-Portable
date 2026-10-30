"""Model switches and destructive operations use only disposable, owned test files."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
from model_policy import ModelPolicy
from provision.model_lifecycle import ModelLifecycle
from provision.install_engine import Busy


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [0.0]
        self.components = [dict(id='video.model.test', title='Test model', kind='model', files=[dict(path='Models/Video/Test/weight.bin')])]
        self.view = SimpleNamespace(catalog=dict(components=self.components), by_id={row['id']:row for row in self.components}, plan=lambda ids:dict(todo=[]), required_status=lambda exclude=():[])
        self.manager = SimpleNamespace(root=self.root, view=self.view, running=lambda:False, gpu=None)
        self.lifecycle = ModelLifecycle(self.manager, clock=lambda:self.now[0])
        self.put('Models/Video/Test/weight.bin', b'weights')
        self.put('Models/Video/Test/cache/derived.bin', b'cache')
        self.put('UserData/Results/keep.mp4', b'user result')
        self.put('UserData/Prompts/keep.json', b'user prompt')

    def tearDown(self):
        self.temp.cleanup()

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def test_toggle_keeps_weights_and_persists_separately(self):
        self.lifecycle.toggle('video.model.test', False)
        self.assertIn('video.model.test', ModelPolicy(self.root).disabled())
        self.assertTrue((self.root/'Models/Video/Test/weight.bin').is_file())
        self.lifecycle.toggle('video.model.test', True)
        self.assertEqual(ModelPolicy(self.root).disabled(), set())

    def test_delay_word_and_single_use_are_enforced_on_server(self):
        plan = self.lifecycle.prepare('video.model.test')
        with self.assertRaises(ValueError):
            self.lifecycle.remove(plan['token'], 'REMOVE')
        self.now[0] = 10
        with self.assertRaises(ValueError):
            self.lifecycle.remove(plan['token'], 'UNINSTALL')
        result = self.lifecycle.remove(plan['token'], 'REMOVE')
        self.assertTrue(result['removed'])
        self.assertFalse((self.root/'Models/Video/Test').exists())
        self.assertTrue((self.root/'UserData/Results/keep.mp4').exists())
        self.assertTrue((self.root/'UserData/Prompts/keep.json').exists())
        with self.assertRaises(ValueError):
            self.lifecycle.remove(plan['token'], 'REMOVE')

    def test_changed_plan_requires_new_confirmation(self):
        plan = self.lifecycle.prepare('video.model.test')
        self.put('Models/Video/Test/cache/another.bin', b'new cache')
        self.now[0] = 10
        with self.assertRaisesRegex(ValueError, 'изменился'):
            self.lifecycle.remove(plan['token'], 'REMOVE')
        self.assertTrue((self.root/'Models/Video/Test/weight.bin').exists())

    def test_shared_directory_does_not_get_recursive_deletion(self):
        other = dict(id='video.model.shared', title='Shared encoder', kind='model', files=[dict(path='Models/Video/Test/encoder.bin')])
        self.components.append(other)
        self.view.by_id[other['id']] = other
        self.put('Models/Video/Test/encoder.bin', b'shared')
        plan = self.lifecycle.prepare('video.model.test')
        self.now[0] = 10
        self.lifecycle.remove(plan['token'], 'REMOVE')
        self.assertTrue((self.root/'Models/Video/Test/encoder.bin').exists())

    def test_busy_or_program_removal_is_refused(self):
        self.manager.running = lambda:True
        with self.assertRaises(Busy):
            self.lifecycle.prepare('video.model.test')
        with self.assertRaises(Busy):
            self.lifecycle.toggle('video.model.test', False)
        self.manager.running = lambda:False
        with self.assertRaises(ValueError):
            self.lifecycle.prepare('video.video_runtime')

    def test_shared_dependency_cannot_be_removed_while_a_model_needs_it(self):
        other = dict(id='video.model.dependent', title='Dependent model', kind='model', requires=['video.model.test'], files=[dict(path='Models/Video/Dependent/weight.bin')])
        self.components.append(other)
        self.view.by_id[other['id']] = other
        self.put('Models/Video/Dependent/weight.bin', b'weights')
        with self.assertRaisesRegex(ValueError, 'общая зависимость'):
            self.lifecycle.prepare('video.model.test')
        self.assertTrue((self.root/'Models/Video/Test/weight.bin').exists())

    def test_symlink_cannot_redirect_deletion(self):
        link = self.root/'Models/Video/Test/link'
        try:
            link.symlink_to(self.root/'UserData/Results', target_is_directory=True)
        except OSError:
            self.skipTest('symlink privilege unavailable')
        with self.assertRaisesRegex(ValueError, 'junction'):
            self.lifecycle.prepare('video.model.test')

    def test_ollama_removal_keeps_blobs_referenced_by_another_model(self):
        base = self.root/'Models/Text/ollama'
        shared = 'sha256:' + hashlib.sha256(b'shared weights').hexdigest()
        unique = 'sha256:' + hashlib.sha256(b'unique config').hexdigest()
        for digest, content in ((shared, b'shared weights'), (unique, b'unique config')):
            self.put('Models/Text/ollama/blobs/' + digest.replace(':','-'), content)
        first = dict(config=dict(digest=unique), layers=[dict(digest=shared)])
        second = dict(config=dict(digest=shared), layers=[])
        self.put('Models/Text/ollama/manifests/registry.ollama.ai/library/first/latest', json.dumps(first).encode())
        self.put('Models/Text/ollama/manifests/registry.ollama.ai/library/second/latest', json.dumps(second).encode())
        self.lifecycle.text_models = lambda:[dict(name='first:latest',label='First',missing=[])]
        plan = self.lifecycle.prepare('text:first:latest')
        self.assertEqual(plan['shared_files'], 1)
        self.now[0] = 10
        self.lifecycle.remove(plan['token'], 'REMOVE')
        self.assertTrue((base/'blobs'/shared.replace(':','-')).exists())
        self.assertFalse((base/'blobs'/unique.replace(':','-')).exists())

    def test_disabled_dependency_disables_the_engine(self):
        catalog = dict(components=[dict(id='video.model.wan-ti2v-5b', requires=['video.model.wan-text-encoder']), dict(id='video.model.wan-text-encoder')])
        self.put('App/config/component-catalog.json', json.dumps(catalog).encode())
        policy = ModelPolicy(self.root)
        policy.set_enabled('video.model.wan-text-encoder', False)
        self.assertFalse(policy.enabled('video', 'wan22-ti2v-5b'))
        self.assertTrue(policy.enabled('video', 'ltx25-distilled'))


if __name__ == '__main__':
    unittest.main()
