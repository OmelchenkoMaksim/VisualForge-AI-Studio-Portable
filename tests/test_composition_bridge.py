"""Composition -> actual Image route -> model-specific Gemma -> graph, with fake model/ComfyUI responses."""
import json
import unittest
from unittest.mock import Mock, patch

import test_image_prompt_manager as fixture
from test_composition_plan import caps, plan, step
from composition.composition_brain import CompositionBrain
from composition.composition_manager import CompositionManager
from composition.composition_studio import CompositionStudio
from gpu_coordinator import GpuCoordinator


class CompositionBridgeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.BridgeTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.server = fixture.server
        self.gpu = GpuCoordinator({})
        replacement = patch.object(self.server, 'GPU', self.gpu)
        replacement.start(); self.addCleanup(replacement.stop)
        self.server.IMAGE_PROMPT_ENHANCER.gpu = self.gpu
        self.server.engine_readiness.return_value.update(modes=['prompt', 'single', 'reference', 'multi'], quality_steps={'low':40, 'good':40},
                                                       community_lora_available=False, quality_reasons={})
        self.studio = CompositionStudio(123, 'composition-test')
        def request(path, data=None, **kwargs):
            if path == '/api/preflight': return dict(engines={})  # overridden by capabilities below
            if path.startswith('/api/status'): return dict(state='done', save_dir=str(self.fixture.root))
            if path.startswith('/api/result'): return fixture.png()
            self.assertEqual(path, '/api/generate')
            handler = self.server.Handler.__new__(self.server.Handler)
            raw = json.dumps(data).encode()
            from io import BytesIO
            handler.path = path; handler.rfile = BytesIO(raw)
            handler.headers = {'Content-Length':str(len(raw)), 'Content-Type':'application/json', 'X-VisualForge-Run':'composition-test'}
            handler.safe_origin = lambda:True
            handler.response_json = lambda code, value:(code, value)
            with handler.acting(): code, value = handler._post()
            if code != 200: raise ValueError(value['error'])
            return value
        self.studio.request = request
        self.studio.capabilities = caps
        brain = CompositionBrain(self.fixture.text, self.gpu, self.server.GPU_SUBMISSION_LOCK)
        self.manager = CompositionManager(self.fixture.root / 'compositions', brain, self.studio, self.gpu,
                                          self.server.GPU_SUBMISSION_LOCK, 'composition-test')
        current = patch.object(self.server, 'COMPOSITION', self.manager)
        current.start(); self.addCleanup(current.stop)
        self.gpu.probes['composition'] = self.manager.busy
        for target in ('composition.composition_manager.begin', 'composition.composition_manager.finish'):
            current = patch(target, return_value=dict(id='c'*64, duration_sec=None))
            current.start(); self.addCleanup(current.stop)

    def wait(self):
        self.manager.thread.join(10)
        self.assertFalse(self.manager.busy())
        return self.manager.status()

    def test_two_network_chain_preserves_materials_and_uses_researched_routes(self):
        draft = self.manager.draft()
        for group in ('hero', 'reference'):
            draft = self.manager.materials.add(draft['id'], fixture.png(), group, group)
        ids = [row['id'] for row in draft['items']]
        result = plan([step('flux3-klein-int8', 'reference', ids), step('qwen-edit-2511', 'single', ['previous'])])
        edit = 'Refine the clock material with a subtle woven texture. Preserve the scene, subjects, lettering and lighting.'
        self.fixture.text.answers = iter(('Two actual reference pictures.', json.dumps(result), fixture.FOX, edit))
        self.manager.start(dict(draft=draft['id'], prompt='Объедини изображения и доработай материал'))
        value = self.wait()
        self.assertEqual(value['state'], 'done', value.get('error'))
        self.assertEqual(len(self.fixture.graphs), 2)
        first, second = self.fixture.graphs
        self.assertNotIn('26', first)
        self.assertEqual(first['40']['inputs']['text'], fixture.FOX)
        self.assertIn(edit, second['151']['inputs']['prompt'])
        self.assertEqual(second['169']['inputs']['steps'], 40)
        self.assertEqual(second['169']['inputs']['cfg'], 4)
        self.assertNotIn('171', second)
        self.assertEqual(len(self.fixture.text.calls), 4)

    def test_stop_after_a_late_prompt_answer_cannot_enqueue_the_image(self):
        self.manager.brain = Mock(make_plan=Mock(return_value=plan()))
        def late(*args, **kwargs):
            self.manager.cancel.set()
            return dict(prompt=fixture.FOX, model='fake', guide_version='test')
        with patch.object(self.server.IMAGE_PROMPT_ENHANCER, 'enhance', side_effect=late):
            self.manager.start(dict(draft=self.manager.draft()['id'], prompt='Лиса'))
            self.assertEqual(self.wait()['state'], 'cancelled')
        self.assertEqual(self.fixture.graphs, [])


if __name__ == '__main__': unittest.main()
