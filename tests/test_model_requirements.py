"""Storage budgets must count complete models, shared encoders and installation scratch accurately."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'App/server'))
from model_requirements import storage, enrich, facts
from provision.engine import Engine, SPACE_MARGIN
from provision.catalog import closure
from hardware import profiles


class RequirementTests(unittest.TestCase):
    def test_ltx_all_six_assets_and_clean_installer_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(folder, catalog_path=ROOT/'App/config/components.json')
            status = engine.status()
            info = storage(engine, status, 'video.model.ltx25')
            assets = json.loads((ROOT/'App/config/ltx25-assets.json').read_text(encoding='utf-8'))
            self.assertEqual(info['model_bytes'], sum(asset['bytes'] for asset in assets['files']))
            self.assertEqual(info['model_bytes'], 41162142158)
            self.assertEqual(info['full_download_bytes'], info['remaining_download_bytes'])
            plan = engine.plan(['video.model.ltx25'])
            self.assertEqual(info['clean_install_free_bytes'], int(plan['disk_bytes'] * SPACE_MARGIN))
            self.assertGreater(info['clean_install_free_bytes'], info['full_download_bytes'])

    def test_wan_includes_shared_encoder_but_not_the_other_wan_model(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(folder, catalog_path=ROOT/'App/config/components.json')
            info = storage(engine, engine.status(), 'video.model.wan-ti2v-5b')
            expected = sum(f['bytes'] for ident in ('video.model.wan-ti2v-5b', 'video.model.wan-text-encoder') for f in engine.by_id[ident]['files'])
            self.assertEqual(info['model_bytes'], expected)
            self.assertEqual(len(info['shared_model_components']), 1)

    def test_installed_and_wrong_size_files_change_only_remaining_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            catalog = dict(schema_version=1, modules=[], components=[
                dict(id='runtime', kind='software', title='Runtime', present=['runtime.ok'], disk_bytes=100,
                     downloads=[dict(id='zip', bytes=20, filename='a.zip', sha256='0'*64, urls=['https://example.org/a.zip'])]),
                dict(id='encoder', kind='model', title='Shared encoder', requires=['runtime'], files=[dict(path='encoder.bin', bytes=3, sha256='0'*64, urls=['https://example.org/e'])]),
                dict(id='model', kind='model', title='Model', requires=['runtime','encoder'], files=[dict(path='model.bin', bytes=5, sha256='0'*64, urls=['https://example.org/m'])])])
            path = root/'catalog.json';path.write_text(json.dumps(catalog), encoding='utf-8')
            engine = Engine(root, catalog_path=path)
            self.assertEqual(closure(engine.catalog, ['model']), ['runtime','encoder','model'])
            cold = storage(engine, engine.status(), 'model')
            self.assertEqual((cold['model_bytes'], cold['full_download_bytes']), (8,28))
            (root/'runtime.ok').write_bytes(b'ok');(root/'encoder.bin').write_bytes(b'123');(root/'model.bin').write_bytes(b'1')
            partial = storage(engine, engine.status(), 'model')
            self.assertEqual(partial['remaining_download_bytes'], 5)
            self.assertEqual(partial['model_bytes'], cold['model_bytes'])
            (root/'model.bin').write_bytes(b'12345')
            installed = storage(engine, engine.status(), 'model')
            self.assertEqual((installed['remaining_download_bytes'], installed['additional_free_bytes']), (0,0))
            self.assertTrue(installed['installed'])

    def test_local_ollama_build_does_not_invent_a_public_download_source(self):
        model = dict(name='custom:latest', label='Custom', ok=True, size=10, disk_size=15,
                     quantization='Q6_K', parameter_size='35B', parameters=dict(num_ctx=32768), vision=False)
        rows = enrich(profiles([model]), [model])
        text = rows['text:custom:latest']
        self.assertEqual(text['storage']['model_bytes'], 15)
        self.assertIsNone(text['storage']['model_download_bytes'])
        self.assertEqual(dict(text['characteristics'])['Квантизация'], 'Q6_K')
        self.assertIn('32768', dict(text['characteristics'])['Контекст сборки'])

    def test_capabilities_follow_the_actual_shipped_profiles(self):
        ltx = dict(facts('video', 'ltx25-distilled'))
        self.assertIn('60', ltx['Длительности'])
        self.assertIn('звук', ltx['Звук'])
        self.assertEqual(dict(facts('music','minimax-music3-int8'))['Результат'], 'WAV · 32 кГц')
