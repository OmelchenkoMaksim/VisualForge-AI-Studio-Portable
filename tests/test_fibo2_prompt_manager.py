"""FIBO VGL boundary, source wiring and bounded local Gemma lifecycle (CPU only)."""
from contextlib import nullcontext
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import test_image_prompt_manager as fixture
from test_image_engines import object_info
from engines import fibo2_prompts as fibo2
from engines.image_preflight import engine_readiness, graph_errors
from engines.image_prompt_enhancer import ImagePreparationCancelled, PromptEnhancer
from engines.image_registry import ENGINES
from engines.workflow_builder import build_workflow
from provision.install_steps import Context, StepError, run_step

ENGINE = ENGINES[fibo2.ENGINE]


def scene(count=0, lettering=''):
    value = dict(short_description='A fox in a snowy forest.', objects=[dict(description='A red fox', location='center')],
                 background_setting='Conifer forest', lighting=dict(conditions='Winter daylight'),
                 aesthetics=dict(composition='Full animal visible'), photographic_characteristics=dict(focus='Sharp fur'),
                 style_medium='photograph', artistic_style='', context='Wildlife photograph', text_render=[])
    if lettering:
        value['text_render'] = [dict(text=lettering, location='lower right', font='simple sans serif')]
    if count:
        value['edit_instruction'] = 'Keep the face and change only the jacket to blue.' if count == 1 else (
            'Compose the scene using ' + ', '.join('<image_%d>' % (i + 1) for i in range(count)) + ' in their assigned roles.')
    return value


def prose(count=0):
    return fixture.FOX if count < 2 else 'Compose a coherent scene using ' + ', '.join('<image_%d>' % (i + 1) for i in range(count)) + ' in their assigned roles.'


class Fibo2ManagerTests(unittest.TestCase):
    def setUp(self):
        activity = patch('video.gemma_task.operation', side_effect=lambda *args, **kwargs: nullcontext())
        activity.start()
        self.addCleanup(activity.stop)

    def manager(self, answers):
        text, gpu = fixture.Text(answers), Mock()
        return PromptEnhancer(text, gpu), text, gpu

    def test_all_modes_convert_locally_after_prose_without_a_second_gpu_claim(self):
        self.assertEqual(set(fibo2.MODE_RULES), set(ENGINE.modes))
        for mode, count in (('prompt', 0), ('single', 1), ('reference', 2), ('multi', 3), ('multi', 4)):
            with self.subTest(mode=mode, count=count):
                structured = scene(count)
                manager, text, gpu = self.manager([prose(count), json.dumps(structured)])
                result = manager.enhance('Нужная сцена', ENGINE.id, mode, [fixture.png()] * count)
                expected = dict(structured, edit_instruction=prose(count)) if count else structured
                self.assertEqual(json.loads(result['prompt']), expected)
                self.assertEqual(len(text.calls), 2)
                self.assertTrue(all(not call['think'] for call in text.calls))
                self.assertFalse(text.calls[1]['messages'][1].get('images'))
                gpu.claim.assert_called_once_with('text')
                self.assertEqual(text.owners, [])

    def test_interactive_ready_answer_is_also_converted_and_lettering_survives(self):
        answer = prose() + ' A sign reads "ЛЕС".'
        manager, text, _ = self.manager([json.dumps(dict(state='ready', prompt=answer)), json.dumps(scene(lettering='ЛЕС'))])
        result = manager.prepare('Лиса и надпись «ЛЕС».', ENGINE.id, 'prompt')
        self.assertEqual(json.loads(result['prompt'])['text_render'][0]['text'], 'ЛЕС')
        self.assertIn('required_lettering', text.calls[1]['messages'][1]['content'])

    def test_edit_instruction_is_owned_by_checked_prose_and_the_format_pass_is_text_only(self):
        requested = 'Change only the jacket colour to navy blue. Keep the face, pose and scene unchanged.'
        proposed = scene(1)
        proposed['edit_instruction'] = 'Change the face and make the jacket yellow.'
        manager, text, _ = self.manager([requested, json.dumps(proposed)])
        result = manager.enhance('Сменить только цвет куртки на тёмно-синий', ENGINE.id, 'single', [fixture.png()])
        self.assertEqual(json.loads(result['prompt'])['edit_instruction'], requested)
        self.assertFalse(text.calls[-1]['messages'][1].get('images'))

    def test_composition_advertises_four_sources_full_profile_and_rgb_limits(self):
        from composition.composition_plan import capabilities, image_options
        caps = capabilities(dict(engines={ENGINE.id:dict(ready=True, modes=list(ENGINE.modes))}))
        self.assertEqual(caps['engines'][ENGINE.id]['maximum_inputs'], 4)
        self.assertFalse(caps['engines'][ENGINE.id]['alpha'])
        options = image_options(dict(engine=ENGINE.id, mode='multi', prompt='A coherent scene', upscale=2))
        self.assertEqual(options['quality'], 'low')
        self.assertEqual(ENGINE.steps(options['mode'], options['quality']), 50)

    def test_vgl_rejects_translated_text_wrong_tags_nested_commands_and_invalid_types(self):
        original = scene(2, 'ЛЕС')
        changes = [dict(text_render=[dict(text='FOREST')]), dict(edit_instruction='Use <image1> and <image2>.'),
                   dict(edit_instruction='Use <image_1> and <image_3>.'), dict(lighting=dict(seed=123)),
                   dict(short_description='Лиса в снегу'), dict(objects=[dict(description='Fox', number_of_objects=True)]),
                   dict(objects=[dict(description='Fox', workflow='run this')]), dict(extra_control='injected')]
        for change in changes:
            with self.subTest(change=change):
                self.assertIsNone(fibo2.vgl_caption(json.dumps(dict(original, **change)), 2, ('ЛЕС',)))
        self.assertIsNotNone(fibo2.vgl_caption(json.dumps(original), 2, ('ЛЕС',)))
        self.assertIsNone(fibo2.vgl_caption(json.dumps(original), 0))

    def test_invalid_vgl_has_only_one_retry_and_stop_never_returns_a_prompt(self):
        manager, text, _ = self.manager([prose(), '{}', '{}'])
        with self.assertRaises(ValueError):
            manager.enhance('Лиса в лесу', ENGINE.id, 'prompt')
        self.assertEqual(len(text.calls), 3)
        manager, text, _ = self.manager([prose(), json.dumps(scene())])
        text.on_answer = lambda: text.cancel.set() if len(text.calls) == 2 else None
        with self.assertRaises(ImagePreparationCancelled):
            manager.enhance('Лиса в лесу', ENGINE.id, 'prompt')

    def test_graphs_keep_full_dev_cfg_latent_and_all_four_inputs(self):
        info = object_info(ENGINE)
        self.assertTrue(engine_readiness(ENGINE, info)['ready'])
        for mode, count in (('prompt', 0), ('single', 1), ('reference', 2), ('multi', 3), ('multi', 4)):
            extras = ['third.png', 'fourth.png'][:max(0, count - 2)]
            graph, metadata = build_workflow(ENGINE, mode, json.dumps(scene(count)), 'first.png', 'second.png',
                                            'low', 1, 1234, extra_images=extras, managed_prompt=True)
            self.assertEqual(graph_errors(graph, info), [])
            self.assertEqual(graph['60']['inputs']['cfg'], 5)
            self.assertEqual(graph['60']['inputs']['steps'], 50)
            self.assertEqual(graph['60']['inputs']['latent_image'], ['40', 2])
            self.assertEqual(metadata['reference_count'], count)
            self.assertEqual(len([key for key in graph['40']['inputs'] if key.startswith('images.')]), count)
        with self.assertRaises(ValueError):
            build_workflow(ENGINE, 'multi', '{}', 'a', 'b', 'low', 1, 1234, extra_images=['c', 'd', 'e'])

    def test_catalog_matches_the_reproducible_builder(self):
        import sys
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / 'tools'))
        from build_fibo2_assets import components
        catalog = json.loads((root / 'App/config/component-catalog.json').read_text(encoding='utf-8'))
        rows = {row['id']:row for row in catalog['components']}
        for row in components():
            self.assertEqual(rows[row['id']], row)
        self.assertIn('image.model.qwen3vl4b-flux2', rows['image.model.fibo2-dev']['requires'])
        self.assertIn('image.model.qwen3vl4b-flux2', rows['image.model.flux3-klein']['requires'])

    def test_pinned_source_patch_refuses_drift_without_writing_and_is_idempotent(self):
        import hashlib
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fibo2-vendor.py'
            before, after = b'font = missing\n', b'font = unicode\n'
            path.write_bytes(before)
            step = dict(op='patch_text', path=path.name, before_sha256=hashlib.sha256(before).hexdigest(),
                        after_sha256=hashlib.sha256(after).hexdigest(), replacements=[['missing', 'unicode']])
            context = Context(directory, {}, directory)
            run_step(context, step)
            run_step(context, step)
            self.assertEqual(path.read_bytes(), after)
            path.write_bytes(b'owner edit\n')
            with self.assertRaises(StepError):
                run_step(context, step)
            self.assertEqual(path.read_bytes(), b'owner edit\n')

    def test_vgl_caption_and_format_survive_history_reload(self):
        import tempfile
        import uuid
        from job_history import JobHistory
        with tempfile.TemporaryDirectory() as directory, patch('job_history.generation_finish'):
            path = Path(directory) / 'fibo2-history.json'
            ident = str(uuid.uuid4())
            prepared = json.dumps(scene(), ensure_ascii=False)
            JobHistory(path).record(ident, 'done', dict(engine=ENGINE.id, prompt_format='vgl', enhanced_prompt=prepared))
            restored = JobHistory(path).get(ident)
            self.assertEqual(restored['prompt_format'], 'vgl')
            self.assertEqual(json.loads(restored['enhanced_prompt']), scene())


if __name__ == '__main__':
    unittest.main()
