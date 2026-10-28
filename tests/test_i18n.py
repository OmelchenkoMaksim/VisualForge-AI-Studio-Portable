"""CPU-only checks for the shared catalog contract and browser localization runtime."""
import importlib.util
import ast
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_strings', ROOT / 'tools/build_strings.py')
assert spec is not None and spec.loader is not None
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class LocalizationTests(unittest.TestCase):
    def test_catalog_keys_languages_placeholders_and_generated_bundle(self):
        entries = builder.catalogs()
        self.assertGreater(len(entries), 1900)
        self.assertFalse(any('.msg_' in key for key in entries), 'Keys must describe their purpose and location')
        self.assertEqual((ROOT / 'App/frontend/strings.js').read_bytes(), builder.bundle())
        self.assertTrue({'image', 'video', 'music', 'text', 'agent', 'speech', 'components', 'settings', 'hardware', 'events'} <= {key.split('.')[0] for key in entries})

    def test_all_frontend_resource_references_exist(self):
        entries = builder.catalogs()
        used = set()
        for file in (ROOT / 'App/frontend').glob('*.js'):
            if file.name == 'strings.js':
                continue
            used.update(re.findall(r'StudioI18n\.(?:t|source)\([\'"]([\w.]+)', file.read_text(encoding='utf-8-sig')))
        page = (ROOT / 'App/frontend/index.html').read_text(encoding='utf-8-sig')
        used.update(re.findall(r'<!--i18n:([\w.]+)-->', page))
        for encoded in re.findall(r'data-i18n-attrs="([^"]+)"', page):
            from html import unescape
            used.update(json.loads(unescape(encoded)).values())
        self.assertFalse(used - entries.keys(), 'Unknown localization keys')
        self.assertLess(page.index('/i18n.js'), page.index('/app.js'))
        self.assertIn('id="studioLanguage"', page)

    def test_server_keys_exist_and_source_messages_preserve_parameters(self):
        entries = builder.catalogs()
        used = set()
        for file in (ROOT / 'App/server').rglob('*.py'):
            if '__pycache__' in file.parts:
                continue
            for node in ast.walk(ast.parse(file.read_text(encoding='utf-8-sig'))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'ui_text':
                    used.add(node.args[0].value)
        self.assertFalse(used - entries.keys())
        import sys
        sys.path.insert(0, str(ROOT / 'App/server'))
        from ui_strings import source
        key = next(key for key, value in entries.items() if value['ru'] == 'Не удалось открыть «{0}»')
        self.assertEqual(source(key, 'Ошибка {1}'), 'Не удалось открыть «Ошибка {1}»')
        with self.assertRaises(ValueError):
            source('../outside')

    def test_duplicate_and_opaque_resource_keys_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='vf-string-keys-') as folder:
            root = Path(folder)
            section = root / 'App/strings/events.json'
            section.parent.mkdir(parents=True)
            value = {'ru': 'Ошибка', 'en': 'Error'}
            for key in ('events.error', 'events.msg_bf22dfeeba8b'):
                section.write_text(json.dumps({key: value}), encoding='utf-8')
                with self.assertRaises(ValueError):
                    builder.catalogs(root)
            key = 'events.history_entry_failed_status'
            pair = json.dumps(key) + ':' + json.dumps(value)
            section.write_text('{' + pair + ',' + pair + '}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Duplicate JSON field'):
                builder.catalogs(root)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for the browser runtime test')
    def test_live_browser_bindings(self):
        result = subprocess.run([shutil.which('node'), str(ROOT / 'tests/i18n_ui_test.js')],
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('i18n UI ok', result.stdout)


if __name__ == '__main__':
    unittest.main()
