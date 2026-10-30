"""Gemma prompt enhancement for video models: hidden guides, answer cleaning and request rules; no GPU, the text model is a stub."""
from pathlib import Path
import base64
import io
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gpu_coordinator import Busy
from test_scene_planner import FakeGpu, FakeText
from video import prompt_enhancer
from video.prompt_enhancer import DEFAULT_VARIANT, GUIDES, PromptEnhancer, clean, guide, supported
from video.video_registry import ENGINES

PARAGRAPH = 'A red fox trots through knee-deep snow between pines at dawn, its breath steaming, while the camera stays low and static.'


def picture():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (64, 48), (200, 80, 20)).save(buffer, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


class GuideTests(unittest.TestCase):
    def test_every_advertised_mode_has_a_guide_and_a_default_that_exists(self):
        for engine_id, engine in ENGINES.items():
            advertised = set(engine.get('prompt_enhancer', []))
            self.assertEqual(advertised, set(GUIDES.get(engine_id, {})), engine_id)
            self.assertTrue(advertised <= set(engine['modes']), engine_id)
        for engine_id, modes in GUIDES.items():
            for mode, variants in modes.items():
                self.assertIn(DEFAULT_VARIANT[(engine_id, mode)], variants)
                for name, text in variants.items():
                    self.assertGreater(len(text), 200, (engine_id, mode, name))

    def test_guides_keep_the_users_words_and_ask_for_one_paragraph_in_english(self):
        for mode, variants in GUIDES['ltx25-distilled'].items():
            for name, text in variants.items():
                with self.subTest(mode=mode, variant=name):
                    self.assertRegex(text.lower(), r'english')
                    self.assertRegex(text.lower(), r'paragraph')

    def test_image_modes_ground_the_first_frame_and_audio_mode_forbids_inventing_sound(self):
        ltx = GUIDES['ltx25-distilled']
        self.assertIn('REFERENCE IMAGE', ltx['i2v']['hybrid'])
        self.assertIn('TWO pictures', ltx['first_last']['hybrid'])
        self.assertIn('do NOT describe sound', ltx['a2v']['hybrid'])
        self.assertNotIn('REFERENCE IMAGE', ltx['t2v']['hybrid'])

    def test_unknown_engine_mode_and_variant_are_rejected_and_files_need_the_diagnostic_folder(self):
        self.assertFalse(supported('wan22-ti2v-5b', 'nonsense'))
        self.assertFalse(supported('nobody', 't2v'))
        for engine, mode, variant in (('nobody', 't2v', None), ('ltx25-distilled', 's2v', None), ('ltx25-distilled', 't2v', 'invented'),
                                       ('ltx25-distilled', 't2v', 'file:anything.txt')):
            with self.subTest(engine=engine, mode=mode, variant=variant), self.assertRaises(ValueError):
                guide(engine, mode, variant)
        with self.assertRaises(ValueError):                                   # no path tricks even when the folder is set
            with patch.dict(os.environ, {'VISUALFORGE_PROMPT_GUIDE_DIR': str(Path(__file__).parent)}):
                guide('ltx25-distilled', 't2v', 'file:../test_prompt_enhancer.py')

    def test_file_variant_reads_the_diagnostic_folder(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'mine.txt').write_text('Rewrite it my way.', encoding='utf-8')
            with patch.dict(os.environ, {'VISUALFORGE_PROMPT_GUIDE_DIR': folder}):
                self.assertEqual(guide('ltx25-distilled', 't2v', 'file:mine.txt'), ('file:mine.txt', 'Rewrite it my way.'))


class CleanTests(unittest.TestCase):
    def test_wrappers_labels_and_line_breaks_are_removed(self):
        self.assertEqual(clean('```text\nA fox\nruns.\n```'), 'A fox runs.')
        self.assertEqual(clean('Here is the enhanced prompt:\nA fox runs.'), 'A fox runs.')
        self.assertEqual(clean('"A fox runs."'), 'A fox runs.')
        self.assertEqual(clean('  A   fox \n\n runs. '), 'A fox runs.')

    def test_inner_quotes_of_dialogue_survive(self):
        text = 'A man says "hello" and "goodbye".'
        self.assertEqual(clean(text), text)
        self.assertEqual(clean('"A man says hello." "Then he leaves."'), '"A man says hello." "Then he leaves."')   # two quotes: not one wrapped text


class EnhanceTests(unittest.TestCase):
    def make(self, answers, **kwargs):
        text = FakeText(answers, **kwargs)
        gpu = FakeGpu()
        return PromptEnhancer(text, gpu), text, gpu

    def test_sends_the_hidden_guide_and_the_request_and_returns_a_clean_paragraph(self):
        enhancer, text, gpu = self.make(['Here is the prompt:\n' + PARAGRAPH])
        result = enhancer.enhance('a fox in the snow', 'ltx25-distilled', 't2v')
        default = DEFAULT_VARIANT[('ltx25-distilled', 't2v')]
        self.assertEqual((result['prompt'], result['variant'], result['model']), (PARAGRAPH, default, 'gemma-test'))
        messages = text.calls[0]['messages']
        self.assertEqual(messages[0]['content'], GUIDES['ltx25-distilled']['t2v'][default])
        self.assertIn('a fox in the snow', messages[1]['content'])
        self.assertNotIn('images', messages[1])
        self.assertEqual(gpu.claimed, ['text'])
        self.assertEqual(text.owners[0], ('begin', prompt_enhancer.OWNER))
        self.assertEqual(text.owners[-1], ('end', prompt_enhancer.OWNER))

    def test_pictures_are_attached_as_base64_png_for_image_modes(self):
        enhancer, text, _ = self.make([PARAGRAPH])
        enhancer.enhance('the fox turns', 'ltx25-distilled', 'i2v', [picture(), 'not a picture'])
        attached = text.calls[0]['messages'][1]['images']
        self.assertEqual(len(attached), 1)
        self.assertTrue(base64.b64decode(attached[0]).startswith(b'\x89PNG'))

    def test_a_too_short_answer_is_retried_once_then_reported(self):
        enhancer, text, _ = self.make(['ok', PARAGRAPH])
        self.assertEqual(enhancer.enhance('fox', 'ltx25-distilled', 't2v')['prompt'], PARAGRAPH)
        self.assertEqual(len(text.calls), 2)
        failing, _, _ = self.make(['ok', 'still short'])
        with self.assertRaises(ValueError):
            failing.enhance('fox', 'ltx25-distilled', 't2v')

    def test_bad_requests_never_reach_the_model(self):
        enhancer, text, gpu = self.make([PARAGRAPH])
        for prompt in ('', '   ', None, 'x' * 5001, 12):
            with self.subTest(prompt=prompt), self.assertRaises(ValueError):
                enhancer.enhance(prompt, 'ltx25-distilled', 't2v')
        with self.assertRaises(ValueError):
            enhancer.enhance('fox', 'ltx25-distilled', 'not-a-mode')
        self.assertEqual((text.calls, gpu.claimed), ([], []))

    def test_unavailable_text_model_and_busy_gpu_are_reported_to_the_person(self):
        unavailable, text, _ = self.make([PARAGRAPH], available=False)
        with self.assertRaises(ValueError):
            unavailable.enhance('fox', 'ltx25-distilled', 't2v')
        self.assertEqual(text.calls, [])
        busy = PromptEnhancer(FakeText([PARAGRAPH]), FakeGpu(error=Busy('video is running')))
        with self.assertRaises(Busy):
            busy.enhance('fox', 'ltx25-distilled', 't2v')


if __name__ == '__main__':
    unittest.main()
