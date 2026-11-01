"""CPU checks of the automatic Z-Image preparation, literal text and GPU handoff; no model inference."""
import base64
from contextlib import nullcontext
import copy
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import uuid

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from engines.image_prompt_enhancer import PromptEnhancer, caption, protected_words, vision_image  # noqa: E402
from engines import zimage_prompts as zimage  # noqa: E402
from engines.image_registry import ENGINES  # noqa: E402
from event_history import TECHNICAL_TYPES  # noqa: E402
from job_history import JobHistory  # noqa: E402
import studio_server as server  # noqa: E402

FOX = ('A red fox lowers its nose toward a small mushroom emerging from disturbed snow in a conifer forest. '
       'Its front paw rests beside the shallow opening. Snow-covered fir trunks recede behind the animal. '
       'A low viewpoint keeps the fox and mushroom readable together, with soft winter daylight and warm russet fur against cool snow.')
PROPOSAL = dict(state='clarification', question='Как показать вашу сцену?', options=[
    dict(label='Акварель с мягкими краями', value='Use a watercolor illustration with softly bleeding edges.', group='medium'),
    dict(label='Естественная фотография', value='Use a natural wildlife photograph.', group='medium'),
    dict(label='Сумеречный свет между деревьями', value='Use soft dusk light between the trees.', group='light'),
])


def png(size=(96, 64), color='green', mask=False):
    image = Image.new('RGB', size, 'black' if mask else color)
    if mask:
        ImageDraw.Draw(image).rectangle((20, 10, 65, 55), fill='white')
    buffer = BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


class Text:
    def __init__(self, answers=(FOX,), lock=None):
        self.answers = iter(answers)
        self.lock = lock
        self.cancel = threading.Event()
        self.calls = []
        self.owners = []
        self.on_answer = None

    def availability(self):
        return dict(available=True)

    def gemma_model(self):
        return 'installed-gemma'

    def begin(self, owner):
        self.owners.append(owner)
        return self.cancel

    def start(self):
        pass

    def end(self, owner):
        self.owners.remove(owner)

    def stream_chat(self, model, messages, think, cancel, options=None):
        if self.lock and self.lock._is_owned():
            raise AssertionError('The global submission lock must be released while Gemma answers')
        self.calls.append(dict(model=model, messages=copy.deepcopy(messages), think=think, options=options))
        if self.on_answer:
            self.on_answer()
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        yield dict(message=dict(content=answer))


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.activity = patch('video.gemma_task.operation', side_effect=lambda *args, **kwargs: nullcontext())
        self.activity.start()
        self.addCleanup(self.activity.stop)

    def manager(self, answers=(FOX,)):
        text, gpu = Text(answers), Mock()
        return PromptEnhancer(text, gpu), text, gpu

    def test_only_the_researched_five_modes_are_active(self):
        manager, text, gpu = self.manager()
        self.assertEqual(set(zimage.MODE_RULES), set(ENGINES[zimage.ENGINE].modes))
        self.assertEqual(set(zimage.CLARIFICATION_RULES), set(zimage.MODE_RULES))
        self.assertIn(manager.OPERATION, TECHNICAL_TYPES)
        self.assertTrue(manager.status()['available'])
        for engine, modes in manager.MODE_RULES.items():
            for mode in modes:
                self.assertEqual(manager.supported(engine, mode), engine == zimage.ENGINE)
        self.assertFalse(text.calls)
        gpu.assert_not_called()

    def test_one_automatic_rewrite_with_distinct_guides_and_ordered_vision(self):
        systems = set()
        source, mask = png((2048, 1024)), png((2048, 1024), mask=True)
        for mode in zimage.MODE_RULES:
            manager, text, gpu = self.manager()
            images = [] if mode == 'prompt' else [source, mask] if mode == 'inpaint' else [source]
            result = manager.enhance('Нарисуй лису в лесу', zimage.ENGINE, mode, images, {'control_type':'canny', 'canvas':'landscape'})
            self.assertEqual(result, dict(prompt=FOX, model='installed-gemma', guide_version=zimage.VERSION))
            gpu.claim.assert_called_once_with('text')
            self.assertEqual(text.owners, [])
            call = text.calls[0]
            self.assertIs(call['think'], False)
            self.assertEqual(json.loads(call['messages'][1]['content'])['request'], 'Нарисуй лису в лесу')
            system = call['messages'][0]['content']
            self.assertNotIn(system, systems)
            systems.add(system)
            self.assertIn(zimage.MODE_RULES[mode], system)
            pictures = call['messages'][1].get('images', [])
            self.assertEqual(len(pictures), len(images))
            for picture in pictures:
                with Image.open(BytesIO(base64.b64decode(picture))) as image:
                    self.assertEqual(image.size, (1024, 512))

    def test_each_control_kind_explains_the_actual_preprocessing(self):
        for kind, rule in zimage.CONTROL_RULES.items():
            manager, text, _ = self.manager()
            manager.enhance('Лиса', zimage.ENGINE, 'control', [png()], {'control_type':kind})
            self.assertIn(rule, text.calls[0]['messages'][0]['content'])

    def test_literal_russian_and_uppercase_unquoted_labels_survive(self):
        request = 'Открытка с надписью «ТЁПЛЫЙ ЛЕС» и текст СНЕГ'
        good = 'A winter postcard with the exact heading "ТЁПЛЫЙ ЛЕС" and the smaller label "СНЕГ" below it.'
        self.assertEqual(protected_words(request), ('ТЁПЛЫЙ ЛЕС', 'СНЕГ'))
        manager, text, _ = self.manager(('A winter postcard with the heading "WARM FOREST" and snow.', good))
        self.assertEqual(manager.enhance(request, zimage.ENGINE, 'prompt')['prompt'], good)
        self.assertEqual(len(text.calls), 2)
        self.assertIn(request, text.calls[1]['messages'][1]['content'])

    def test_caption_cleanup_and_rejection(self):
        good = 'A wooden sign carrying the exact Cyrillic text "СНЕГ" in white painted letters.'
        self.assertEqual(caption('```text\nPrompt: ' + good + '\n```', 'Надпись «СНЕГ»', False), good)
        self.assertEqual(caption(good.replace('"СНЕГ"', '«СНЕГ»'), 'Надпись «СНЕГ»', False), good)
        for bad in ('', 'Short', '{"prompt": "' + FOX + '"}', 'Русское описание вместо английского. ' + FOX,
                    FOX + '\x00', '<think>Some reasoning</think>' + FOX, good.replace('СНЕГ', 'Снег')):
            self.assertIsNone(caption(bad, 'Надпись «СНЕГ»', False))
        self.assertIsNone(caption(good, 'Нарисуй лес', False), 'new Cyrillic lettering must not appear in text-only expansion')
        self.assertEqual(caption(good, 'Улучши детализацию', True), good, 'source lettering may be absent from the typed request')

    def test_removing_or_replacing_old_lettering_does_not_require_rendering_it_again(self):
        removed = 'An uninterrupted plain wooden panel with matching wood grain and warm side lighting.'
        self.assertEqual(protected_words('Удали надпись «СТАРОЕ»'), ())
        self.assertEqual(protected_words('Замени надпись «СТАРОЕ» на «НОВОЕ»'), ('НОВОЕ',))
        self.assertEqual(protected_words('Удали текст OLD; напиши NEW'), ('NEW',))
        self.assertEqual(caption(removed, 'Удали надпись «СТАРОЕ»', True), removed)
        replaced = 'A wooden panel carrying the new heading "НОВОЕ" in large painted letters.'
        self.assertEqual(caption(replaced, 'Замени надпись «СТАРОЕ» на «НОВОЕ»', True), replaced)

    def test_invalid_answers_retry_once_and_never_fall_back_to_user_text(self):
        manager, text, _ = self.manager(('Русский текст вместо промпта.', 'Still too short.'))
        with self.assertRaises(ValueError):
            manager.enhance('Лиса', zimage.ENGINE, 'prompt')
        self.assertEqual(len(text.calls), 2)
        self.assertEqual(text.owners, [])

    def test_cancellation_and_model_failure_release_owner_without_retry(self):
        for failure in ('stop', OSError('model failed')):
            manager, text, _ = self.manager((failure,) if isinstance(failure, Exception) else (FOX,))
            if failure == 'stop':
                text.on_answer = text.cancel.set
            with self.assertRaises(ValueError):
                manager.enhance('Лиса', zimage.ENGINE, 'prompt')
            self.assertEqual(text.owners, [])
            self.assertEqual(len(text.calls), 1)

    def test_bad_inputs_are_rejected_before_any_model_or_gpu_call(self):
        cases = [('text', zimage.ENGINE, 'detail', [], {}), ('text', zimage.ENGINE, 'prompt', [png()], {}),
                 ('text', zimage.ENGINE, 'inpaint', [png()], {}), ('text', zimage.ENGINE, 'detail', [b'broken'], {}),
                 ('text', zimage.ENGINE, 'control', [png()], {'control_type':'depth'}),
                 ('', zimage.ENGINE, 'prompt', [], {}), ('x' * 5001, zimage.ENGINE, 'prompt', [], {}),
                 ('text', 'flux3-klein-int8', 'prompt', [], {}), ('text', zimage.ENGINE, 'single', [], {})]
        for request, engine, mode, images, context in cases:
            manager, text, gpu = self.manager()
            with self.subTest(engine=engine, mode=mode), self.assertRaises(ValueError):
                manager.enhance(request, engine, mode, images, context)
            self.assertEqual(text.calls, [])
            self.assertEqual(gpu.mock_calls, [])

    def test_vision_mask_has_no_resampling_gray_edges(self):
        encoded = vision_image(png((2048, 1024), mask=True), mask=True)
        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            self.assertEqual({value for _, value in image.getchannel('R').getcolors(maxcolors=3)}, {0, 255})


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='image-prompts-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.ident = str(uuid.uuid4())
        self.text = Text(lock=server.GPU_SUBMISSION_LOCK)
        self.gpu = Mock()
        self.graphs = []
        self.lock_states = []

        def upstream(route, data=None, **kwargs):
            if route == '/object_info':
                return {}
            self.assertEqual(route, '/prompt')
            self.assertTrue(server.GPU_SUBMISSION_LOCK._is_owned())
            self.graphs.append(data['prompt'])
            return dict(prompt_id=self.ident)

        def claim(*args):
            self.lock_states.append(server.GPU_SUBMISSION_LOCK._is_owned())

        self.gpu.claim.side_effect = claim
        patches = {
            'GPU':self.gpu, 'IMAGE_PROMPT_ENHANCER':PromptEnhancer(self.text, self.gpu, server.GPU_SUBMISSION_LOCK),
            'json_upstream':Mock(side_effect=upstream), 'apply_profiles':Mock(), 'Thread':Mock(),
            'ModelPolicy':Mock(return_value=Mock(disabled=lambda: [])),
            'load_settings':Mock(return_value=dict(save_dir=str(self.root), save_dir_available=True)),
            'engine_readiness':Mock(return_value=dict(ready=True, quality_steps={'low':8}, modes=list(zimage.MODE_RULES), expansion_reasons={})),
            'upscale_available':Mock(return_value=True), 'upload_image':Mock(return_value='input.png'),
            'generation_begin':Mock(side_effect=lambda kind, **kwargs: dict(type=kind, id=uuid.uuid4().hex * 2,
                                                                          started_at=None, duration_sec=None)),
        }
        for name, value in patches.items():
            current = patch.object(server, name, value)
            current.start()
            self.addCleanup(current.stop)
        for target in ('video.gemma_task.operation', 'job_history.generation_finish'):
            current = patch(target, side_effect=lambda *args, **kwargs: nullcontext())
            current.start()
            self.addCleanup(current.stop)
        for mapping in (server.JOB_META, server.JOB_TOKENS, server.SAVE_LOCKS):
            self.addCleanup(mapping.pop, self.ident, None)

    def generate(self, **overrides):
        data = dict(engine=zimage.ENGINE, mode='prompt', prompt='Нарисуй лису в хвойном лесу', canvas='landscape', seed=123)
        data.update(overrides)
        handler = server.Handler.__new__(server.Handler)
        body = json.dumps(data).encode()
        handler.path = '/api/generate'
        handler.headers = {'Content-Length':str(len(body)), 'Content-Type':'application/json'}
        handler.rfile = BytesIO(body)
        handler.safe_origin = lambda: True
        handler.response_json = lambda status, payload: (status, payload)
        return handler._post()

    def test_server_rewrites_automatically_then_submits_actual_caption_with_atomic_admission(self):
        status, result = self.generate(image1={'base64':'unused invalid upload'}, original_prompt='forged provenance')
        self.assertEqual(status, 200, result)
        self.assertEqual(self.graphs[0]['40']['inputs']['text'], FOX)
        self.assertEqual(self.gpu.claim.call_args_list[0].args, ('text',))
        self.assertEqual(self.gpu.claim.call_args_list[1].args, ('image', 11000))
        self.assertEqual(self.lock_states, [True, True])
        self.assertEqual(len(self.text.calls), 1)
        self.assertNotIn('images', self.text.calls[0]['messages'][1], 'Prompt must ignore unrelated selected uploads')
        metadata = server.JOB_META[self.ident]
        self.assertEqual(metadata['original_prompt'], 'Нарисуй лису в хвойном лесу')
        self.assertEqual(metadata['enhanced_prompt'], FOX)
        self.assertEqual(metadata['seed'], 123)
        self.assertEqual(metadata['prompt_enhancement']['guide_version'], zimage.VERSION)
        store = JobHistory(self.root / 'history.json')
        store.record(self.ident, 'done', metadata)
        receipt = JobHistory(store.path).get(self.ident)
        self.assertEqual(receipt['enhanced_prompt'], FOX)
        self.assertEqual(receipt['original_prompt'], metadata['original_prompt'])
        self.assertEqual(receipt['prompt_enhancement'], metadata['prompt_enhancement'])

    def test_source_modes_pass_validated_images_and_keep_controls(self):
        for mode in ('detail', 'control', 'inpaint', 'tile'):
            self.text.answers = iter((FOX,))
            source = png()
            data = dict(mode=mode, image1={'base64':base64.b64encode(source).decode()})
            if mode == 'inpaint':
                data['image2'] = {'base64':base64.b64encode(png(mask=True)).decode()}
            status, result = self.generate(**data)
            self.assertEqual(status, 200, result)
            pictures = self.text.calls[-1]['messages'][1]['images']
            self.assertEqual(len(pictures), 2 if mode == 'inpaint' else 1)
            self.assertEqual(result['mode'], mode)
            if mode == 'detail':
                self.assertEqual(result['detail_strength'], 0.33)
                self.assertEqual(result['steps'], 5)
            if mode == 'control':
                self.assertEqual(result['control_type'], 'canny')
                self.assertEqual(result['control_strength'], 0.85)
            if mode == 'inpaint':
                self.assertEqual(result['control_strength'], 1.0)
                self.assertEqual(result['upscale'], 1)

    def test_bad_mask_or_unavailable_mode_never_loads_gemma_or_submits(self):
        source = {'base64':base64.b64encode(png()).decode()}
        bad_mask = {'base64':base64.b64encode(png((32, 32), mask=True)).decode()}
        status, _ = self.generate(mode='inpaint', image1=source, image2=bad_mask)
        self.assertEqual(status, 400)
        server.engine_readiness.return_value = dict(ready=False, reasons=['disabled model'])
        status, _ = self.generate()
        self.assertEqual(status, 400)
        self.assertEqual(self.text.calls, [])
        self.assertEqual(self.gpu.mock_calls, [])
        self.assertEqual(self.graphs, [])

    def test_stopped_preparation_cannot_submit_an_image(self):
        self.text.on_answer = self.text.cancel.set
        status, _ = self.generate()
        self.assertEqual(status, 400)
        self.assertEqual(self.graphs, [])
        self.gpu.claim.assert_called_once_with('text')
        self.assertEqual(self.text.owners, [])

    def test_other_models_keep_their_existing_prompt_path(self):
        status, result = self.generate(engine='qwen-image-21-uc-fp8')
        self.assertEqual(status, 200, result)
        self.assertEqual(self.graphs[0]['40']['inputs']['prompt'], 'Нарисуй лису в хвойном лесу')
        self.assertEqual(self.text.calls, [])
        self.gpu.claim.assert_called_once_with('image', 0)
        self.assertNotIn('prompt_enhancement', result)

    def test_interactive_sufficient_request_submits_without_a_question_or_second_rewrite(self):
        self.text.answers = iter((json.dumps(dict(state='ready', prompt=FOX)),))
        status, result = self.generate(clarify=True)
        self.assertEqual(status, 200, result)
        self.assertIn('prompt_id', result)
        self.assertEqual(len(self.text.calls), 1)
        self.assertEqual(self.graphs[0]['40']['inputs']['text'], FOX)

    def test_numbered_choices_suspend_generation_and_then_survive_history(self):
        self.text.answers = iter((json.dumps(PROPOSAL), FOX))
        status, first = self.generate(clarify=True)
        self.assertEqual((status, first['state'], first['round']), (200, 'clarification', 1))
        self.assertEqual(self.graphs, [])
        self.assertEqual(self.text.owners, [])
        status, result = self.generate(clarify=True, clarification_token=first['clarification_token'], clarification_answer='1,3')
        self.assertEqual(status, 200, result)
        self.assertEqual(len(self.graphs), 1)
        info = result['prompt_enhancement']['clarification']
        self.assertEqual(info['rounds'], 1)
        self.assertEqual([choice['group'] for choice in info['choices']], ['medium', 'light'])
        metadata = server.JOB_META[self.ident]
        store = JobHistory(self.root / 'interactive-history.json')
        store.record(self.ident, 'done', metadata)
        persisted = JobHistory(store.path).get(self.ident)
        self.assertEqual(persisted['prompt_enhancement']['clarification'], info)
        self.assertNotIn(first['clarification_token'], store.path.read_text(encoding='utf-8'))

    def test_explicit_conflict_allows_one_last_question_then_always_submits(self):
        self.text.answers = iter((json.dumps(PROPOSAL), FOX))
        _, first = self.generate(clarify=True)
        status, second = self.generate(clarify=True, clarification_token=first['clarification_token'], clarification_answer='123')
        self.assertEqual((status, second['round']), (200, 2))
        self.assertEqual(len(self.text.calls), 1, 'a mechanical conflict must not ask Gemma to invent more questions')
        self.assertEqual(self.graphs, [])
        status, result = self.generate(clarify=True, clarification_token=second['clarification_token'], clarification_answer='12')
        self.assertEqual(status, 200, result)
        self.assertIn('prompt_id', result)
        info = result['prompt_enhancement']['clarification']
        self.assertEqual(info['rounds'], 2)
        self.assertTrue(info['conflict_resolved'])
        self.assertEqual({choice['value'] for choice in info['choices']}, {PROPOSAL['options'][1]['value'], PROPOSAL['options'][2]['value']})
        self.assertEqual(len(self.graphs), 1)
        status, _ = self.generate(clarify=True, clarification_token=second['clarification_token'], clarification_answer='1')
        self.assertEqual(status, 400)
        self.assertEqual(len(self.graphs), 1, 'replaying a clarification must not generate another picture')

    def test_changed_prompt_seed_or_engine_cannot_reuse_choices(self):
        self.text.answers = iter((json.dumps(PROPOSAL),))
        _, first = self.generate(clarify=True)
        for changed in (dict(prompt='Другой сюжет'), dict(seed=124), dict(engine='qwen-image-21-uc-fp8')):
            status, _ = self.generate(clarify=True, clarification_token=first['clarification_token'], clarification_answer='1', **changed)
            self.assertEqual(status, 400)
        self.assertEqual(len(self.text.calls), 1)
        self.assertEqual(self.graphs, [])

    def test_cancel_route_invalidates_waiting_question_without_gpu_work(self):
        self.text.answers = iter((json.dumps(PROPOSAL),))
        _, first = self.generate(clarify=True)
        handler = server.Handler.__new__(server.Handler)
        body = json.dumps(dict(clarification_token=first['clarification_token'])).encode()
        handler.path = '/api/image/clarification-cancel'
        handler.headers = {'Content-Length':str(len(body)), 'Content-Type':'application/json'}
        handler.rfile = BytesIO(body)
        handler.safe_origin = lambda: True
        handler.response_json = lambda status, data: (status, data)
        self.assertEqual(handler._post(), (200, dict(cancelled=True)))
        status, _ = self.generate(clarify=True, clarification_token=first['clarification_token'], clarification_answer='1')
        self.assertEqual(status, 400)
        self.gpu.claim.assert_called_once_with('text')


if __name__ == '__main__':
    unittest.main()
