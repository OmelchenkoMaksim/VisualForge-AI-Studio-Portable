"""Gemma as a required component: mirrors, a local GGUF file, the required group and the guard against removing the last Gemma.

Everything runs against a stand-in `ollama serve` (tests/fake_ollama.py) and tiny hand-made GGUF headers; nothing is downloaded."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
from model_policy import ModelPolicy
from provision import ollama_tools, setup_cli as cli
from provision.component_recovery import Recovery
from provision.install_engine import Engine
from provision.model_lifecycle import ModelLifecycle

FAKE = [sys.executable, str(ROOT / 'tests/fake_ollama.py')]


def gguf(architecture, padding=b'x' * 64):
    """A GGUF header with two entries (a number, then general.architecture) followed by filler instead of tensors."""
    def text(value):
        raw = value.encode()
        return struct.pack('<Q', len(raw)) + raw
    body = text('general.alignment') + struct.pack('<I', 4) + struct.pack('<I', 32)
    body += text('general.architecture') + struct.pack('<I', 8) + text(architecture)
    return b'GGUF' + struct.pack('<I', 3) + struct.pack('<QQ', 0, 2) + body + padding


def catalog(**extra):
    models = [dict(id='text.model.gemma-small', module='text', kind='model', title='Gemma small', requires=['text.runtime'], download_bytes=10,
                   present=['Models/Text/ollama/manifests/registry.ollama.ai/library/gemma4/small'],
                   after=[dict(op='ollama_pull', model='gemma4:small', alternatives=['down.test/org/gemma:q4', 'mirror.test/org/gemma:q4'])]),
              dict(id='text.model.gemma-big', module='text', kind='model', title='Gemma big', requires=['text.runtime'], download_bytes=20,
                   present=['Models/Text/ollama/manifests/registry.ollama.ai/library/gemma4/big'], after=[dict(op='ollama_pull', model='gemma4:big')])]
    runtime = dict(id='text.runtime', module='text', kind='software', title='Ollama', present=['Runtime/Text/ollama/ollama.exe'])
    return dict(schema_version=1, catalog_version='t', presets={}, components=[runtime] + models,
                modules=[dict(id='text', title='Text', components=['text.runtime', 'text.model.gemma-small', 'text.model.gemma-big'])],
                required=[dict(id='gemma', family='gemma', title='Gemma', default='text.model.gemma-small', options=['text.model.gemma-small', 'text.model.gemma-big'])], **extra)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'Runtime/Text/ollama').mkdir(parents=True)
        (self.root / 'Runtime/Text/ollama/ollama.exe').write_bytes(b'MZ')
        self.catalog_path = self.root / 'App/config/component-catalog.json'
        self.catalog_path.parent.mkdir(parents=True)
        self.catalog_path.write_text(json.dumps(catalog()), encoding='utf-8')
        self.events = []

    def tearDown(self):
        self.temp.cleanup()

    def engine(self, **kwargs):
        return Engine(self.root, self.catalog_path, emit=self.events.append, ollama_command=FAKE, **kwargs)

    def put_model(self, name='gemma4:small', family='gemma4', complete=True):
        """A model in the private store the way Ollama writes it: manifest, config layer naming the family, one weights blob."""
        store = self.root / 'Models/Text/ollama'
        config = json.dumps(dict(model_family=family)).encode()
        weights = b'weights-' + name.encode()
        digests = {}
        for label, data in (('config', config), ('weights', weights)):
            digests[label] = 'sha256:' + hashlib.sha256(data).hexdigest()
            blob = ollama_tools.blob_path(store, digests[label])
            blob.parent.mkdir(parents=True, exist_ok=True)
            if complete or label == 'config':
                blob.write_bytes(data)
        path = ollama_tools.manifest_path(store, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(config=dict(digest=digests['config'], size=len(config)),
                                        layers=[dict(digest=digests['weights'], size=len(weights), mediaType='application/vnd.ollama.image.model')])), encoding='utf-8')
        return path


class GgufTests(unittest.TestCase):
    def test_architecture_is_read_from_the_header(self):
        with tempfile.TemporaryDirectory() as folder:
            model, clip, junk = (Path(folder) / name for name in ('m.gguf', 'p.gguf', 'x.gguf'))
            model.write_bytes(gguf('gemma4'))
            clip.write_bytes(gguf('clip'))
            junk.write_bytes(b'not a gguf file at all')
            self.assertEqual(ollama_tools.gguf_architecture(model), 'gemma4')
            self.assertEqual(ollama_tools.gguf_architecture(clip), 'clip')
            self.assertEqual(ollama_tools.gguf_architecture(junk), '')
            self.assertEqual(ollama_tools.gguf_architecture(Path(folder) / 'missing.gguf'), '')
            model.write_bytes(gguf('gemma4')[:30])  # cut inside the header
            self.assertEqual(ollama_tools.gguf_architecture(model), '')

    def test_projector_is_found_beside_the_model(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'gemma-4-E4B-it-Q4_K_M.gguf').write_bytes(gguf('gemma4'))
            self.assertIsNone(ollama_tools.find_projector(base / 'gemma-4-E4B-it-Q4_K_M.gguf'))
            (base / 'mmproj-other-model-F16.gguf').write_bytes(gguf('clip'))
            (base / 'mmproj-gemma-4-E4B-it-F16.gguf').write_bytes(gguf('clip'))
            self.assertEqual(ollama_tools.find_projector(base / 'gemma-4-E4B-it-Q4_K_M.gguf').name, 'mmproj-gemma-4-E4B-it-F16.gguf')

    def test_local_names_are_registry_safe(self):
        self.assertEqual(ollama_tools.local_model_name('C:/x/Gemma 4 E4B (it) Q4_K_M.gguf'), 'gemma-4-e4b-it-q4_k_m')
        ollama_tools.split_name(ollama_tools.local_model_name('///Ünïcode имя.gguf') + ':latest')


class RequiredGroupTests(Base):
    def test_a_missing_gemma_adds_the_default_to_every_selection(self):
        engine = self.engine()
        self.assertEqual(engine.select(), ['text.runtime', 'text.model.gemma-small'])
        self.assertEqual(engine.select(models=['text.model.gemma-big']), ['text.runtime', 'text.model.gemma-big'])  # the person's own pick replaces the default
        self.assertEqual(engine.select(required=False), ['text.runtime'])

    def test_any_complete_gemma_satisfies_the_group_but_other_families_do_not(self):
        self.put_model('qwen3:8b', family='qwen3')
        self.assertFalse(self.engine().required_status()[0]['satisfied'])
        self.put_model('my-build:latest', family='gemma4')  # a custom import, not from the catalog
        group = self.engine().required_status()[0]
        self.assertTrue(group['satisfied'])
        self.assertEqual(group['installed'], ['my-build:latest'])
        self.assertEqual(self.engine().select(), ['text.runtime'])

    def test_an_incomplete_or_disabled_gemma_does_not_count(self):
        self.put_model('gemma4:small', complete=False)
        self.assertFalse(self.engine().required_status()[0]['satisfied'])
        self.put_model('gemma4:small')
        self.assertTrue(self.engine().required_status()[0]['satisfied'])
        ModelPolicy(self.root).set_enabled('text:gemma4:small', False)
        self.assertFalse(self.engine().required_status()[0]['satisfied'])

    def test_options_describe_sources_for_the_window(self):
        option = self.engine().required_status()[0]['options'][0]
        self.assertEqual(option['sources'], ['registry.ollama.ai', 'down.test', 'mirror.test'])

    def test_the_real_catalog_is_consistent(self):
        real = Engine(ROOT, ROOT / 'App/config/component-catalog.json')
        groups = real.catalog['required']
        self.assertEqual([group['id'] for group in groups], ['gemma'])
        self.assertIn(groups[0]['default'], groups[0]['options'])
        for ident in groups[0]['options']:
            pull = real.by_id[ident]['after'][0]
            self.assertEqual(pull['op'], 'ollama_pull')
            hosts = [ollama_tools.source_host(name) for name in [pull['model']] + pull['alternatives']]
            self.assertEqual(hosts[0], 'registry.ollama.ai')
            self.assertGreaterEqual(len(set(hosts)), 3, ident)  # registry plus two independent mirrors, not only hf.co aliases
        self.assertEqual(real.catalog['presets']['software-only']['models'], [])


class MirrorTests(Base):
    def test_a_failing_registry_falls_through_to_the_next_source_and_gets_the_catalog_name(self):
        engine = self.engine()
        with mock.patch.dict(os.environ, {'FAKE_OLLAMA_FAIL': 'gemma4:small'}):
            result = engine.install(engine.select(), check_space=False)
        self.assertEqual(result['text.model.gemma-small'], 'installed', result)
        store = self.root / 'Models/Text/ollama'
        names = [row['name'] for row in ollama_tools.store_models(store)]
        self.assertEqual(names, ['gemma4:small'], 'the mirror name is dropped after it is aliased to the catalog name')
        notes = [e.get('text') for e in self.events if e['type'] == 'note']
        self.assertTrue(any('registry.ollama.ai' in (note or '') for note in notes), notes)
        self.assertTrue(any('down.test' in (note or '') for note in notes), notes)
        self.assertTrue(any('mirror.test' in (note or '') for note in notes), notes)

    def test_primary_registry_failure_is_not_fatal_but_cancelling_stops_the_chain(self):
        engine = self.engine()
        engine.cancel_event.set()
        result = engine.install(engine.select(), check_space=False)
        self.assertTrue(result['text.model.gemma-small'].startswith('failed'), result)
        self.assertFalse(any(row['complete'] for row in ollama_tools.store_models(self.root / 'Models/Text/ollama')))

    def test_when_every_source_fails_the_person_is_asked_for_a_file(self):
        model = self.root / 'download' / 'gemma-4-E2B-it-Q4_K_M.gguf'
        model.parent.mkdir()
        model.write_bytes(gguf('gemma4'))
        (model.parent / 'mmproj-gemma-4-E2B-it-F16.gguf').write_bytes(gguf('clip'))
        asked = []

        def answer(item, cancel):
            asked.append(item)
            return dict(kind='file', value=str(model), extra='')
        engine = self.engine()
        engine.recover = answer
        with mock.patch.dict(os.environ, {'FAKE_OLLAMA_FAIL': 'gemma4:small,mirror.test/org/gemma:q4'}):
            result = engine.install(['text.model.gemma-small'], check_space=False)
        self.assertEqual(result['text.model.gemma-small'], 'installed', result)
        self.assertEqual(asked[0]['purpose'], 'model')
        self.assertEqual(len(asked[0]['reasons']), 3)
        self.assertGreaterEqual(asked[0]['wait_seconds'], 300)
        created = ollama_tools.manifest_path(self.root / 'Models/Text/ollama', 'gemma4:small')
        self.assertTrue(created.is_file())
        layers = json.loads(created.read_text())['layers']
        self.assertEqual(sorted(layer['mediaType'].split('.')[-1] for layer in layers), ['model', 'projector'])
        self.assertEqual(json.loads(created.read_text())['created_with']['renderer'], 'gemma4')

    def test_skipping_the_question_fails_with_the_reasons(self):
        document = catalog()
        document['components'][1]['after'][0].update(alternatives=[])
        self.catalog_path.write_text(json.dumps(document), encoding='utf-8')
        engine = self.engine()
        engine.recover = lambda item, cancel: None
        with mock.patch.dict(os.environ, {'FAKE_OLLAMA_FAIL': 'gemma4:small'}):
            result = engine.install(['text.model.gemma-small'], check_space=False)
        self.assertTrue(result['text.model.gemma-small'].startswith('failed') and 'registry.ollama.ai' in result['text.model.gemma-small'], result)


class LocalFileTests(Base):
    def test_a_local_file_becomes_a_gemma_component(self):
        model = self.root / 'my' / 'weights.gguf'
        model.parent.mkdir()
        model.write_bytes(gguf('gemma4'))
        engine = self.engine()
        ident = engine.local_gemma_component(model)
        result = engine.install([ident], check_space=False)
        self.assertEqual(result[ident], 'installed', result)
        self.assertTrue(engine.required_status()[0]['satisfied'])
        self.assertTrue(model.is_file(), 'the original file stays where it was')

    def test_foreign_architectures_and_bad_projectors_are_refused(self):
        folder = self.root / 'files'
        folder.mkdir()
        llama, fine = folder / 'llama.gguf', folder / 'gemma.gguf'
        llama.write_bytes(gguf('llama'))
        fine.write_bytes(gguf('gemma3'))
        (folder / 'wrong.gguf').write_bytes(gguf('llama'))
        for model, projector, fragment in ((llama, None, 'llama'), (fine, folder / 'wrong.gguf', 'wrong.gguf'), (folder / 'none.gguf', None, 'none.gguf')):
            engine = self.engine()
            ident = engine.local_gemma_component(model, str(projector) if projector else None)
            result = engine.install([ident], check_space=False)
            self.assertTrue(result[ident].startswith('failed') and fragment in result[ident], result)

    def test_the_command_line_checks_the_file_first(self):
        junk = self.root / 'junk.gguf'
        junk.write_bytes(b'nope')
        out = __import__('io').StringIO()
        code = cli.run(['--root', str(self.root), '--catalog', str(self.catalog_path), '--lang', 'en', '--gemma-file', str(junk), '--dry-run'], out=out, interactive=False)
        self.assertEqual(code, 2)
        self.assertIn('rejected', out.getvalue())


class GuardTests(Base):
    def lifecycle(self):
        manager = type('Manager', (), dict(root=self.root, view=self.engine(), running=lambda self: False, gpu=None))()
        store = self.root / 'Models/Text/ollama'
        return ModelLifecycle(manager, text_models=lambda: ollama_tools.store_models(store))

    def test_the_last_gemma_cannot_be_disabled_or_deleted_but_a_second_one_frees_the_first(self):
        self.put_model('gemma4:small')
        lifecycle = self.lifecycle()
        with self.assertRaises(ValueError):
            lifecycle.toggle('text.model.gemma-small', False)
        with self.assertRaises(ValueError):
            lifecycle.prepare('text.model.gemma-small')
        self.put_model('gemma4:big')
        lifecycle = self.lifecycle()
        self.assertEqual(lifecycle.toggle('text.model.gemma-small', False)['enabled'], False)
        with self.assertRaises(ValueError):
            lifecycle.toggle('text.model.gemma-big', False)  # now this one is the last

    def test_a_model_of_another_family_can_always_be_disabled(self):
        self.put_model('gemma4:small')
        self.put_model('qwen3:8b', family='qwen3')
        lifecycle = self.lifecycle()
        self.assertEqual(lifecycle.toggle('text:qwen3:8b', False)['enabled'], False)


class RecoveryTests(unittest.TestCase):
    def test_a_model_question_takes_a_file_and_a_projector_but_never_a_link(self):
        recovery = Recovery()
        item = dict(component='x', title='X', name='gemma4:e4b', bytes=0, sha256='', purpose='model', wait_seconds=600)
        import threading
        out = []
        thread = threading.Thread(target=lambda: out.append(recovery.request(item, lambda: False)))
        thread.start()
        while not recovery.snapshot():
            pass
        snapshot = recovery.snapshot()
        self.assertGreater(snapshot['remaining_seconds'], 300)
        with self.assertRaises(ValueError):
            recovery.respond(snapshot['token'], 'url', 'https://example.test/model.gguf')
        recovery.respond(snapshot['token'], 'file', 'C:/models/gemma.gguf', 'C:/models/mmproj.gguf')
        thread.join(5)
        self.assertEqual(out, [dict(kind='file', value='C:/models/gemma.gguf', extra='C:/models/mmproj.gguf')])


if __name__ == '__main__':
    unittest.main()
