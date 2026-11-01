"""Persistent composition orchestration, snapshots, cancellation and route boundaries without models."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from test_composition_plan import caps, plan, step
import test_image_prompt_manager as fixture
from composition.composition_manager import CompositionManager
from composition.composition_media import Materials, PAD_NOTE, TRANSPARENT_NOTE, prepare, prepare_inputs
from composition.composition_routes import handle
from composition.composition_studio import CompositionStudio
from director.studio import StudioBusy
from engines.image_registry import ENGINES
from gpu_coordinator import GpuCoordinator
from PIL import Image


class CompositionManagerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='composition-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.brain = Mock()
        self.brain.make_plan.return_value = plan()
        self.studio = Mock()
        self.studio.capabilities.return_value = caps()
        self.studio.image.return_value = (fixture.png(), dict(job='image-1', save_dir=str(self.root)))
        self.gpu = GpuCoordinator({})
        self.lock = threading.RLock()
        self.manager = CompositionManager(self.root, self.brain, self.studio, self.gpu, self.lock, 'private-token')
        self.gpu.probes['composition'] = self.manager.busy
        event = patch('composition.composition_manager.begin', return_value=dict(type='ImageComposition', id='a'*64, duration_sec=None))
        event.start(); self.addCleanup(event.stop)
        finish = patch('composition.composition_manager.finish')
        finish.start(); self.addCleanup(finish.stop)

    def wait(self):
        self.manager.thread.join(10)
        self.assertFalse(self.manager.busy())
        return self.manager.status()

    def test_plain_prompt_produces_multiple_variants_and_durable_history(self):
        self.brain.make_plan.return_value = plan(count=3)
        started = self.manager.start(dict(draft=self.manager.draft()['id'], prompt='Лиса', variants=3))
        self.assertIn(started['state'], ('planning', 'running', 'done'))
        result = self.wait()
        self.assertEqual(result['state'], 'done', result.get('error'))
        self.assertEqual(len(result['results']), 3)
        self.assertEqual(self.studio.image.call_count, 3)
        self.assertEqual(len({call.args[3] for call in self.studio.image.call_args_list}), 3)
        self.assertTrue(all(call.args[1] == [] for call in self.studio.image.call_args_list))
        self.assertTrue(self.manager.result_file(result['id'], 2).is_file())
        self.assertEqual(self.manager.history()['entries'][0]['id'], result['id'])
        restored = CompositionManager(self.root, self.brain, self.studio, self.gpu, self.lock, 'another-token')
        self.assertEqual(restored.status(result['id'])['state'], 'done')

    def test_material_snapshot_survives_another_tab_removing_the_draft_image(self):
        draft = self.manager.draft()
        draft = self.manager.materials.add(draft['id'], fixture.png(), 'hero', 'Алексей')
        original = draft['items'][0]['id']
        entered, release = threading.Event(), threading.Event()

        def brain(request, capabilities, items, files, cancel):
            entered.set(); release.wait(5)
            self.assertEqual(files[original], fixture.png())
            return plan([step('flux3-klein-int8', 'single', [original]), step('qwen-edit-2511', 'single', ['previous'])])

        self.brain.make_plan.side_effect = brain
        self.manager.start(dict(draft=draft['id'], prompt='Сохрани героя'))
        self.assertTrue(entered.wait(3))
        self.assertTrue(self.manager.acting('private-token'))
        self.assertFalse(self.manager.acting('forged-token'))
        self.manager.materials.remove(draft['id'], original)
        release.set()
        value = self.wait()
        self.assertEqual(value['state'], 'done', value.get('error'))
        self.assertEqual([call.args[0]['engine'] for call in self.studio.image.call_args_list], ['flux3-klein-int8', 'qwen-edit-2511'])
        self.assertTrue((self.root / 'runs' / value['id'] / (original + '.image')).is_file())

    def test_stop_during_planning_prevents_every_image_and_signals_preparation(self):
        entered, release = threading.Event(), threading.Event()
        def brain(*args):
            entered.set(); release.wait(5)
            return plan()
        self.brain.make_plan.side_effect = brain
        self.manager.cancel_preparation = Mock()
        draft = self.manager.draft()['id']
        value = self.manager.start(dict(draft=draft, prompt='Лиса'))
        self.assertTrue(entered.wait(3))
        with self.assertRaises(ValueError): self.manager.start(dict(draft=draft, prompt='Ещё'))
        self.manager.cancel_run(value['id']); release.set()
        self.assertEqual(self.wait()['state'], 'cancelled')
        self.studio.image.assert_not_called()
        self.brain.stop.assert_called_once()
        self.manager.cancel_preparation.assert_called_once()

    def test_partial_results_remain_after_a_later_failure(self):
        self.brain.make_plan.return_value = plan(count=2)
        self.studio.image.side_effect = [(fixture.png(), dict(job='one', save_dir=str(self.root))), ValueError('Second image failed')]
        self.manager.start(dict(draft=self.manager.draft()['id'], prompt='Лиса', variants=2))
        value = self.wait()
        self.assertEqual(value['state'], 'error')
        self.assertEqual(len(value['results']), 1)
        self.assertTrue(self.manager.result_file(value['id'], 0).is_file())

    def test_a_start_without_a_known_draft_is_refused_instead_of_shooting_without_the_photos(self):
        for data in (dict(prompt='Лиса'), dict(prompt='Лиса', draft=''), dict(prompt='Лиса', draft=None), dict(prompt='Лиса', draft='f' * 32), dict(prompt='Лиса', draft=5)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.manager.start(data)
        self.assertEqual(sorted(path.name for path in (self.root / 'runs').iterdir()), [])
        self.assertEqual(self.studio.image.call_count, 0)

    def test_the_estimate_is_kept_with_the_plan(self):
        self.brain.make_plan.return_value = plan(count=2)
        self.manager.start(dict(draft=self.manager.draft()['id'], prompt='Лиса', variants=2))
        value = self.wait()
        self.assertEqual(value['state'], 'done', value.get('error'))
        self.assertGreaterEqual(value['estimate_minutes'], 1)

    def test_a_broken_run_file_does_not_keep_the_workspace_from_opening(self):
        folder = self.root / 'runs' / ('c' * 32); folder.mkdir()
        (folder / 'run.json').write_text('{"id": ', encoding='utf-8')
        CompositionManager(self.root, self.brain, self.studio, self.gpu, self.lock, 'another-token')

    def test_old_drafts_and_runs_beyond_the_thirty_listed_are_removed_at_start(self):
        import os, time
        old = self.manager.draft()['id']; fresh = self.manager.draft()['id']
        folder = self.root / 'drafts' / old
        for path in (folder, folder / 'materials.json'):
            os.utime(path, (time.time() - 9 * 86400,) * 2)
        runs = self.root / 'runs'
        for number in range(34):
            run = runs / ('%032x' % number); run.mkdir()
            (run / 'run.json').write_text(json.dumps(dict(id=run.name, state='done')), encoding='utf-8')
            os.utime(run / 'run.json', (time.time() - 1000 + number,) * 2)
        CompositionManager(self.root, self.brain, self.studio, self.gpu, self.lock, 'another-token')
        self.assertFalse(folder.exists())
        self.assertTrue((self.root / 'drafts' / fresh).exists())
        left = sorted(path.name for path in runs.iterdir())
        self.assertEqual(len(left), 30)
        self.assertNotIn('%032x' % 0, left)                      # the oldest went
        self.assertIn('%032x' % 33, left)                        # the newest stayed

    def test_running_state_is_marked_interrupted_on_restart_without_losing_results(self):
        folder = self.root / 'runs' / ('b'*32); folder.mkdir()
        (folder / 'run.json').write_text(json.dumps(dict(id='b'*32, state='running', generation=None, results=[dict(number=1)])), encoding='utf-8')
        other = CompositionManager(self.root, self.brain, self.studio, self.gpu, self.lock, 'new-token')
        self.assertEqual(other.status('b'*32)['state'], 'interrupted')
        with self.assertRaises(ValueError): other.status('../escape')


class CompositionMediaTests(unittest.TestCase):
    def test_six_heroes_incremental_limits_and_labels_preserve_originals(self):
        with tempfile.TemporaryDirectory(prefix='composition-media-') as folder:
            store = Materials(folder)
            draft = store.draft()
            for count in range(6):
                draft = store.add(draft['id'], fixture.png(), 'hero', 'Герой %d' % count)
                self.assertEqual(len(draft['items']), count + 1)
            with self.assertRaises(ValueError): store.add(draft['id'], fixture.png(), 'hero', 'Seventh')
            ident = draft['items'][0]['id']
            self.assertEqual(store.label(draft['id'], ident, 'Лена')['items'][0]['label'], 'Лена')
            self.assertEqual(store.path(draft['id'], ident).read_bytes(), fixture.png())
            self.assertEqual(len(store.remove(draft['id'], ident)['items']), 5)
            with self.assertRaises(ValueError): store.path(draft['id'], '../escape')

    def test_full_portrait_is_padded_to_landscape_without_cropping_or_losing_alpha(self):
        output = BytesIO(); image = Image.new('RGBA', (20, 100), (255, 0, 0, 255)); image.save(output, 'PNG')
        raws = prepare_inputs([output.getvalue(), fixture.png()], 'landscape')
        with Image.open(BytesIO(raws[0])) as result:
            self.assertGreater(result.width, result.height)
            self.assertEqual(result.getpixel((result.width//2, result.height//2)), (255, 0, 0, 255))
            self.assertEqual(result.getchannel('A').getextrema(), (255, 255))

    def test_a_picture_of_the_shape_of_the_canvas_fills_it_and_one_of_another_shape_sits_on_its_own_blur(self):
        def photo(size, left=(220, 30, 30), right=(30, 30, 220)):
            image = Image.new('RGBA', size, left); image.paste(Image.new('RGBA', (size[0] - size[0] // 2, size[1]), right), (size[0] // 2, 0))
            output = BytesIO(); image.save(output, 'PNG'); return output.getvalue()

        def grey(png):
            with Image.open(BytesIO(png)).convert('RGB') as image:
                cells = [image.getpixel((x, y)) for x in range(0, image.width, 32) for y in range(0, image.height, 32)]
            return sum(cell == (127, 127, 127) for cell in cells) / len(cells)

        wide, note = prepare([photo((1152, 768))], 'landscape')                      # the shape of the canvas: nothing is padded, it was 69 % grey once
        self.assertEqual((Image.open(BytesIO(wide[0])).size, note, grey(wide[0])), ((2048, 1365), '', 0))
        tall, note = prepare([photo((768, 1152))], 'landscape')
        self.assertEqual((Image.open(BytesIO(tall[0])).size, note, grey(tall[0])), ((2048, 1365), PAD_NOTE, 0))
        with Image.open(BytesIO(tall[0])).convert('RGB') as image:
            self.assertTrue(image.getpixel((1024 - 20, 600))[0] > 180 and image.getpixel((1024 + 20, 600))[2] > 180)        # the photo itself fills the height, untouched in the middle
            self.assertTrue(image.getpixel((20, 600))[0] > 100 or image.getpixel((20, 600))[2] > 100)                      # and its blur, not grey, is at the edge
        square, note = prepare([photo((1000, 1000))], 'square')
        self.assertEqual((Image.open(BytesIO(square[0])).size, note), ((2048, 2048), ''))
        near, note = prepare([photo((1100, 768))], 'landscape')                      # 4 % off the shape: stretched to it, a sliver of the edge is cropped
        self.assertEqual((Image.open(BytesIO(near[0])).size, note), ((2048, 1365), ''))
        clear = Image.new('RGBA', (400, 800), (255, 0, 0, 0)); clear.paste(Image.new('RGBA', (200, 400), (255, 0, 0, 255)), (100, 200)); out = BytesIO(); clear.save(out, 'PNG')
        kept, note = prepare([out.getvalue()], 'landscape')                           # transparency keeps a transparent frame so that the alpha survives
        self.assertEqual(note, TRANSPARENT_NOTE)
        self.assertEqual(Image.open(BytesIO(kept[0])).getchannel('A').getextrema()[0], 0)
        later, note = prepare([photo((1152, 768)), photo((5000, 4000))], 'landscape')
        self.assertLessEqual(max(Image.open(BytesIO(later[1])).size), 2048)           # the others are only shrunk to the route's budget

    def test_a_lookup_of_a_material_never_makes_a_draft(self):
        with tempfile.TemporaryDirectory(prefix='composition-media-') as folder:
            store = Materials(folder)
            for ident in (None, '', 'f' * 32):
                with self.subTest(ident=ident), self.assertRaises(ValueError):
                    store.path(ident, 'a' * 32)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_a_busy_card_is_waited_out_and_the_padding_sentence_goes_only_with_a_padded_picture(self):
        studio = CompositionStudio(123, 'token', pause=lambda _: None)
        studio.request = Mock(side_effect=[StudioBusy('another module holds the card'), dict(prompt_id='one', cancel_token='secret'), dict(state='done', save_dir='C:/output'), fixture.png()])
        raw, receipt = studio.image(step('flux3-klein-int8', 'single', ['m1']), [fixture.png()], 'square', 1, threading.Event(), note=PAD_NOTE)
        self.assertEqual(raw, fixture.png())
        generate = studio.request.call_args_list[1]
        self.assertEqual(generate.args[0], '/api/generate')
        self.assertTrue(generate.args[1]['prompt'].endswith(PAD_NOTE))
        self.assertEqual(generate.kwargs['timeout'], 1800)                               # the call may include a slow Gemma pass
        studio.request = Mock(side_effect=[dict(prompt_id='two', cancel_token='secret'), dict(state='done'), fixture.png()])
        studio.image(step('flux3-klein-int8', 'single', ['m1']), [fixture.png()], 'square', 2, threading.Event())
        self.assertNotIn('canvas', studio.request.call_args_list[0].args[1]['prompt'])
        self.assertEqual(studio.request.call_args_list[0].args[1]['prompt'], step('flux3-klein-int8', 'single', ['m1'])['prompt'])

    def test_route_refuses_cross_origin_and_invalid_raw_body_before_mutation(self):
        handler, manager = Mock(), Mock()
        handler.safe_origin.return_value = False
        handle(handler, manager, 'start', '', post=True)
        manager.start.assert_not_called()
        handler.safe_origin.return_value = True
        handler.headers = {'Content-Length':'10000000000'}
        handle(handler, manager, 'attach', 'group=hero', post=True)
        self.assertEqual(handler.response_json.call_args.args[0], 413)
        manager.materials.add.assert_not_called()

    def test_studio_uses_actual_image_routes_quality_and_cancellation(self):
        studio = CompositionStudio(123, 'token', pause=lambda _:None)
        for ident in ENGINES:
            value = step(ident)
            value.update(native_megapixels=4)
            studio.request = Mock(side_effect=[dict(prompt_id='one', cancel_token='secret'), dict(state='done', save_dir='C:/output'), fixture.png()])
            raw, receipt = studio.image(value, [], 'square', 123, threading.Event())
            self.assertEqual(raw, fixture.png())
            call = studio.request.call_args_list[0]
            self.assertEqual(call.args[0], '/api/generate')
            self.assertEqual(call.args[1]['quality'], 'good' if ident == 'qwen-image-21-uc-fp8' else 'low')
            self.assertNotIn('clarify', call.args[1])
            self.assertNotIn('cancel_token', receipt)


if __name__ == '__main__':
    unittest.main()
