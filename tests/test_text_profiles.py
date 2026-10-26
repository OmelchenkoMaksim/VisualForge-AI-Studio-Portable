"""Imported Ollama tuning must survive chat requests; CPU-only, no server."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from text.manager import TextManager


class ModelDefaultsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.manager = TextManager(types.SimpleNamespace(root=root, models=root/'Models', settings=root/'Settings',
            cache=root/'Cache', logs=root/'Logs', modern=False), command=[])
        self.addCleanup(self.manager.close)

    def request(self, options=None):
        captured = []
        def stream(payload, cancel):
            captured.append(json.loads(payload))
            yield b'{"done":true}'
        with patch.object(self.manager, '_stream_lines', stream), patch.object(self.manager, 'can_think', return_value=False):
            list(self.manager.stream_chat('custom:latest', [{'role':'user','content':'Hello'}], True, threading.Event(), options))
        return captured[0]

    def test_native_parameters_do_not_override_imported_tuning(self):
        body = self.request()
        self.assertEqual(body['options'], {})
        self.assertEqual(body['keep_alive'], '10m')
        self.assertEqual(body['messages'], [{'role':'user','content':'Hello'}])

    def test_manual_parameters_remain_available(self):
        self.manager.settings(dict(native_parameters=False, top_k=23, temperature=0.6))
        options = self.request()['options']
        self.assertEqual((options['top_k'], options['temperature'], options['num_ctx']), (23, 0.6, 32768))

    def test_voice_options_override_native_mode(self):
        self.assertEqual(self.request(dict(temperature=0.1, num_predict=64))['options'], dict(temperature=0.1, num_predict=64))

    def test_native_setting_is_validated_and_persisted(self):
        with self.assertRaises(ValueError):
            self.manager.settings(dict(native_parameters='yes'))
        self.manager.settings(dict(native_parameters=False))
        self.assertFalse(self.manager.settings()['native_parameters'])

    def test_tuning_is_read_without_loading_weights(self):
        store = self.manager.models
        folder = store/'manifests/registry.ollama.ai/library/custom/latest'
        folder.parent.mkdir(parents=True)
        (store/'blobs').mkdir()
        payloads = [('config', b'{"model_type":"35B","file_type":"Q6_K"}'), ('model', b'weights'), ('params', b'{"top_k":20,"presence_penalty":1.5,"num_ctx":16384}')]
        layers = []
        for index, (kind, data) in enumerate(payloads):
            digest = 'sha256:%064x' % index
            (store/'blobs'/digest.replace(':','-')).write_bytes(data)
            layers.append(dict(mediaType='application/vnd.ollama.image.'+kind, digest=digest, size=len(data)))
        folder.write_text(json.dumps(dict(config=layers[0], layers=layers[1:])))
        model = self.manager.installed_models()[0]
        self.assertEqual(model['parameters']['top_k'], 20)
        self.assertEqual((model['parameter_size'], model['quantization']), ('35B', 'Q6_K'))
        self.assertEqual(model['disk_size'], folder.stat().st_size + sum(len(data) for _, data in payloads))
        self.assertEqual(self.manager.context_length('custom:latest'), 16384)


if __name__ == '__main__':
    unittest.main()
