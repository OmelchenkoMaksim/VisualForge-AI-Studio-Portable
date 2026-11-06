"""Per-section guide coverage, language rules, pure drafting and active-manager readiness (CPU only)."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))

from engines.image_registry import ENGINES as IMAGE_ENGINES  # noqa: E402
from engines.image_prompt_enhancer import PromptEnhancer as ImageEnhancer  # noqa: E402
from music.music_registry import ENGINES as MUSIC_ENGINES  # noqa: E402
from music.music_prompt_enhancer import PromptEnhancer as MusicEnhancer  # noqa: E402
from prompt_template import LANGUAGE_RULE  # noqa: E402
from video.gemma_task import GemmaTask  # noqa: E402
from voiceover.voiceover_prompt_enhancer import PromptEnhancer as VoiceoverEnhancer  # noqa: E402
from voiceover.voiceover_registry import ENGINES as VOICE_ENGINES  # noqa: E402

MANAGERS = (ImageEnhancer, MusicEnhancer, VoiceoverEnhancer)


class TemplateTests(unittest.TestCase):
    def test_drafts_cover_the_current_engine_mode_contracts(self):
        expected = (
            (ImageEnhancer, {key: set(engine.modes) for key, engine in IMAGE_ENGINES.items()}),
            (MusicEnhancer, {key: set(engine['modes']) for key, engine in MUSIC_ENGINES.items()}),
            (VoiceoverEnhancer, {key: {'speech'} for key in VOICE_ENGINES}),
        )
        for cls, registry in expected:
            with self.subTest(section=cls.SECTION):
                self.assertEqual({engine: set(modes) for engine, modes in cls.MODE_RULES.items()}, registry)
                self.assertEqual(set(cls.MODEL_RULES), set(registry))

    def test_every_pair_has_its_own_rules_and_the_language_policy_once(self):
        for cls in MANAGERS:
            manager = cls(None, None)
            instructions = set()
            for engine, modes in cls.MODE_RULES.items():
                for mode in modes:
                    guide = manager.guide(engine, mode)
                    system = guide['system']
                    with self.subTest(section=cls.SECTION, engine=engine, mode=mode):
                        self.assertEqual(guide['state'], 'active' if manager.supported(engine, mode) else 'template')
                        self.assertIn(engine, system)
                        self.assertIn('mode ' + mode, system)
                        self.assertIn(cls.MODEL_RULES[engine], system)
                        self.assertIn(cls.MODE_RULES[engine][mode], system)
                        # Speech classifies delivery only; the generic image/music rewrite contract caused real Gemma
                        # to invent narration fields, so it deliberately uses its own protected-content instruction.
                        self.assertEqual(system.count(LANGUAGE_RULE), 0 if cls is VoiceoverEnhancer else 1)
                        self.assertIn('English', system)
                        self.assertIn('Russian', system)
                        self.assertIn('without translating', system)
                        self.assertIn('letter for letter', system)
                        self.assertNotIn(system, instructions)
                        instructions.add(system)

    def test_unknown_engine_mode_and_cross_section_pairs_have_no_fallback(self):
        for cls in MANAGERS:
            manager = cls(None, None)
            engine = next(iter(cls.MODE_RULES))
            for pair in (('unknown', 'prompt'), (engine, 'unknown'), ([], 'prompt'), (engine, {})):
                with self.subTest(section=cls.SECTION, pair=pair), self.assertRaises(ValueError):
                    manager.guide(*pair)

    def test_draft_keeps_russian_text_lyrics_transcripts_and_controls_unchanged(self):
        prompt = '  Надпись «КОФЕ У ПЕТРА». Голос говорит: «Доброе утро!»  '
        context = dict(lettering='КОФЕ У ПЕТРА', lyrics='[Куплет]\nТихо падает снег.\n',
                       spoken_text='Глава 1\n«Доброе утро!»', sample_transcript='Это мой голос.',
                       pronunciation_dictionary=[['слово', 'с л о в о']], bpm=90, language='Russian')
        original = copy.deepcopy(context)
        for cls in MANAGERS:
            engine = next(iter(cls.MODE_RULES))
            mode = next(iter(cls.MODE_RULES[engine]))
            result = cls(None, None).draft(prompt, engine, mode, context)
            self.assertEqual(result['original_prompt'], prompt)
            self.assertEqual(json.loads(result['messages'][1]['content']), dict(request=prompt, context=original))
            self.assertEqual(context, original)
            self.assertIn('КОФЕ У ПЕТРА', result['messages'][1]['content'])

    def test_active_audio_managers_report_missing_text_without_claiming_gpu(self):
        owners, operations = set(), set()
        for cls in (MusicEnhancer, VoiceoverEnhancer):
            text, gpu = None, Mock()
            manager = cls(text, gpu)
            self.assertIsInstance(manager, GemmaTask)
            self.assertFalse(manager.status()['available'])
            self.assertEqual(manager.OPERATION, manager.PLANNED_OPERATION)
            self.assertNotIn(manager.OWNER, owners)
            self.assertNotIn(manager.PLANNED_OPERATION, operations)
            owners.add(manager.OWNER)
            operations.add(manager.PLANNED_OPERATION)
            for engine, modes in cls.MODE_RULES.items():
                for mode in modes:
                    self.assertTrue(manager.supported(engine, mode))
                    manager.draft('Тестовый запрос', engine, mode)
            with self.assertRaises(ValueError), manager.session():
                self.fail('An inactive template entered a model session')
            self.assertEqual(gpu.mock_calls, [])

    def test_current_image_models_are_active_but_unknown_pairs_have_no_fallback(self):
        text, gpu = Mock(), Mock()
        manager = ImageEnhancer(text, gpu)
        self.assertEqual(set(manager.ACTIVE_GUIDES), set(IMAGE_ENGINES))
        for engine, mode in (('unknown-image', 'prompt'), ('qwen-edit-2511', 'inpaint')):
            self.assertFalse(manager.supported(engine, mode))
            with self.assertRaises(ValueError):
                manager.guide(engine, mode)
            with self.assertRaises(ValueError):
                manager.enhance('Тестовый запрос', engine, mode)
        self.assertEqual(text.mock_calls, [])
        self.assertEqual(gpu.mock_calls, [])

    def test_existing_upsamplers_planner_and_speech_controls_are_distinguished(self):
        self.assertIn('neutral canvas', ImageEnhancer.MODEL_RULES['qwen-edit-2511'])
        self.assertIn('checkerboard', ImageEnhancer.MODEL_RULES['qwen-image-21-uc-fp8'])
        self.assertIn('box table stays separate', ImageEnhancer.MODE_RULES['flux3-klein-int8']['layout'])
        self.assertIn('separate stage', MusicEnhancer.MODEL_RULES['ace-step-15-turbo'])
        self.assertEqual(MusicEnhancer.OUTPUT_FIELD, 'caption')
        self.assertIn('lyrics', MusicEnhancer.VERBATIM_FIELDS)
        self.assertEqual(VoiceoverEnhancer.OUTPUT_FIELD, 'direction')
        self.assertIn('spoken_text', VoiceoverEnhancer.VERBATIM_FIELDS)
        self.assertIn('not a model with arbitrary free-form style controls', VoiceoverEnhancer.MODEL_RULES['qwen3-tts-12hz-0.6b-base'])


if __name__ == '__main__':
    unittest.main()
