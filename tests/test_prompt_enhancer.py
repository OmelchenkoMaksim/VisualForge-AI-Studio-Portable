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
from video.prompt_enhancer import DEFAULT_VARIANT, GUIDES, PromptEnhancer, character_note, clean, guide, split_parts, supported
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


TWO_TEXTS = ('CHARACTER AND BACKGROUND: Character appearance description: A young man with short light hair, light stubble and a silver chain. '
             'Background description: A dry golden field under an evening sky, front view, static camera.\nMOTION: He sits still with crossed legs, then raises his right arm and points.')


class CharacterTests(unittest.TestCase):
    def test_guides_exist_for_every_character_mode_and_tell_the_picture_order(self):
        self.assertEqual(set(GUIDES['wan-animate2']), {'animate'})
        self.assertEqual(set(GUIDES['scail2']), {'replace', 'animate'})
        for engine, modes in (('wan-animate2', ['animate']), ('scail2', ['replace', 'animate'])):
            for mode in modes:
                for name, text in GUIDES[engine][mode].items():
                    with self.subTest(engine=engine, mode=mode, variant=name):
                        self.assertIn('PHOTO', text)
                        self.assertIn('never name or identify', text)
        self.assertIn('MOTION:', GUIDES['wan-animate2']['animate']['structured'])
        self.assertIn('FINISHED video', GUIDES['scail2']['replace']['detailed'])
        self.assertNotIn('MOTION:', GUIDES['scail2']['replace']['detailed'])

    def test_two_text_answers_are_split_and_a_missing_part_is_refused(self):
        main, motion = split_parts(TWO_TEXTS)
        self.assertTrue(main.startswith('Character appearance description:') and 'static camera' in main)
        self.assertEqual(motion, 'He sits still with crossed legs, then raises his right arm and points.')
        self.assertIsNone(split_parts('Character appearance description: A man. Background description: A field.'))
        self.assertIsNone(split_parts('CHARACTER AND BACKGROUND: short\nMOTION: x'))
        fenced = split_parts('```\n' + TWO_TEXTS + '\n```')
        self.assertEqual(fenced[1], motion)

    def test_enhance_returns_both_texts_and_sends_the_picture_note(self):
        text = FakeText([TWO_TEXTS])
        result = PromptEnhancer(text, FakeGpu()).enhance('he sits in a field', 'wan-animate2', 'animate', [picture(), picture(), picture(), picture()], note='Picture 1: the PHOTO.')
        self.assertEqual((result['variant'], result['motion_prompt'].startswith('He sits still')), ('structured', True))
        self.assertTrue(result['prompt'].startswith('Character appearance description'))
        message = text.calls[0]['messages'][1]
        self.assertIn('Picture 1: the PHOTO.', message['content'])
        self.assertEqual(len(message['images']), 4)
        self.assertGreaterEqual(text.calls[0]['options']['num_ctx'], 10240)           # a photo and frames need more room than one frame
        retry = FakeText(['no labels at all, only a long enough sentence without the marker', TWO_TEXTS])
        self.assertIn('motion_prompt', PromptEnhancer(retry, FakeGpu()).enhance('he sits', 'wan-animate2', 'animate', [picture()]))
        self.assertEqual(len(retry.calls), 2)
        with self.assertRaises(ValueError):
            PromptEnhancer(FakeText(['nothing useful', 'still nothing']), FakeGpu()).enhance('he sits', 'wan-animate2', 'animate', [picture()])

    def test_scail_returns_one_paragraph(self):
        paragraph = 'A young man with short light hair sits on a folding chair in a golden field while a blue truck jumps behind him, medium shot, static camera.'
        result = PromptEnhancer(FakeText([paragraph]), FakeGpu()).enhance('replace him', 'scail2', 'replace', [picture()])
        self.assertEqual((result['prompt'], result['variant'], 'motion_prompt' in result), (paragraph, DEFAULT_VARIANT[('scail2', 'replace')], False))

    def test_the_note_names_every_picture(self):
        note = character_note({'reference': 'human', 'driving': 'human, truck', 'subjects': [{'reference': 'car', 'driving': 'truck'}]}, 1, 3)
        self.assertIn('Picture 1: the PHOTO', note)
        self.assertIn('Picture 2: an extra object, "car", which takes the place of "truck"', note)
        self.assertIn('Pictures 3-5: FRAMES', note)
        self.assertIn('Picture 2: FRAMES', character_note(None, 0, 1))
        self.assertEqual(character_note('junk', 0, 0).count('Picture'), 1)


class WanTests(unittest.TestCase):
    def test_guides_exist_and_the_picture_modes_say_what_the_picture_is(self):
        self.assertEqual(set(GUIDES['wan22-ti2v-5b']), {'t2v', 'i2v'})
        self.assertEqual(set(GUIDES['wan22-s2v-14b']), {'s2v'})
        for name, text in GUIDES['wan22-ti2v-5b']['i2v'].items():
            self.assertIn('START PICTURE', text, name)
            self.assertIn('Do NOT describe what the picture shows', text, name)
        for name, text in GUIDES['wan22-s2v-14b']['s2v'].items():
            self.assertIn('do NOT write what is said', text, name)
            self.assertIn('PHOTO', text, name)
        for name, text in GUIDES['wan22-ti2v-5b']['t2v'].items():
            self.assertNotIn('{', text, name)                  # the word range of the director text is filled in
            self.assertNotIn('START PICTURE', text, name)

    def test_the_enhancer_sends_the_photo_of_a_talking_character(self):
        text = FakeText(['A man in a grey shirt speaks to the camera in a bright kitchen, medium shot, steady camera.'])
        result = PromptEnhancer(text, FakeGpu()).enhance('a man talks', 'wan22-s2v-14b', 's2v', [picture()])
        self.assertEqual(result['variant'], DEFAULT_VARIANT[('wan22-s2v-14b', 's2v')])
        self.assertEqual(len(text.calls[0]['messages'][1]['images']), 1)


if __name__ == '__main__':
    unittest.main()
