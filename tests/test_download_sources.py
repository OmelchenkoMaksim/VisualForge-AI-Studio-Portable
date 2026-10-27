"""Publisher alternatives must preserve the pinned payload; no network or real downloads."""
import copy
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'App/server'))
from provision.sources import enrich


class SourceTests(unittest.TestCase):
    def test_catalog_has_cloud_sources_and_alternatives_without_changing_pins(self):
        catalog = json.loads((ROOT/'App/config/components.json').read_text('utf-8'))
        before = copy.deepcopy(catalog)
        enrich(catalog)
        self.assertEqual(catalog, before, 'catalog generation must retain its reviewed alternatives')
        for component in catalog['components']:
            rows = component.get('files', []) + component.get('downloads', [])
            for row in rows:
                self.assertTrue(row['urls'])
                for url in row['urls']:
                    parsed = urlsplit(url)
                    self.assertEqual(parsed.scheme, 'https')
                    self.assertIsNone(parsed.username)
                    self.assertIsNone(parsed.password)
                if row.get('id') == 'vcredist':
                    self.assertEqual(urlsplit(row['urls'][0]).hostname, 'download.visualstudio.microsoft.com')
                else:
                    self.assertGreaterEqual(len(row['urls']), 2, row.get('path', row.get('id')))
            if not rows:
                self.assertTrue(component.get('cloud_sources'), component['id'])

    def test_enrichment_is_idempotent_and_keeps_size_hash_and_primary(self):
        row = dict(path='Models/test.safetensors', bytes=7, sha256='a'*64,
                   urls=['https://huggingface.co/publisher/model/resolve/'+'b'*40+'/weights.safetensors'])
        catalog = dict(components=[dict(id='test',files=[row])])
        primary = row['urls'][0]
        enrich(catalog, {})
        self.assertEqual(row['urls'], [primary, primary.replace('huggingface.co', 'hf.co')])
        self.assertEqual((row['bytes'], row['sha256']), (7, 'a'*64))
        once = copy.deepcopy(catalog)
        enrich(catalog, {})
        self.assertEqual(catalog, once)

    def test_node_and_npm_alternatives_point_at_the_same_file(self):
        from provision.sources import alternatives
        self.assertEqual(alternatives('https://nodejs.org/dist/v24.21.0/node-v24.21.0-win-x64.zip'),
                         ['https://nodejs.org/download/release/v24.21.0/node-v24.21.0-win-x64.zip'])
        self.assertEqual(alternatives('https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-0.2.1-rc.2.tgz'),
                         ['https://registry.yarnpkg.com/@deepseek-ai/dsh/-/dsh-0.2.1-rc.2.tgz'])


if __name__ == '__main__':
    unittest.main()
