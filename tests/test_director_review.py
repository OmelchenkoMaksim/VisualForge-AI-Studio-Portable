import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_scene_planner import FakeGpu, FakeText  # noqa: E402
from video import director_review as review  # noqa: E402
from video.video_prompt_enhancer import guide  # noqa: E402

RUNTIME = Path(__file__).resolve().parents[1] / 'Runtime/Video'
FIXED = 'A medium shot of a large red fox with thick orange fur walking left to right through deep snow in a pine forest at dawn, the camera tracks it.'
ANSWER = dict(verdict='fixable', score=5, summary='The fox is tiny.', problems=['The fox is a speck in frames 1-6'], fixes=['closer shot'], fixed_prompt=FIXED)


class ParseTests(unittest.TestCase):
    def test_a_valid_answer_is_cleaned(self):
        result = review.parse('```json\n' + json.dumps(ANSWER) + '\n```')
        self.assertEqual((result['verdict'], result['score'], result['fixed_prompt']), ('fixable', 5, FIXED))
        self.assertEqual(result['problems'], ['The fox is a speck in frames 1-6'])

    def test_a_wrong_or_unusable_answer_is_refused(self):
        for bad in ('no json at all', '{"verdict": "great", "fixed_prompt": "%s"}' % FIXED, json.dumps(dict(ANSWER, fixed_prompt='short')),
                    json.dumps(dict(ANSWER, fixed_prompt='x' * 2000)), json.dumps(dict(ANSWER, fixed_prompt=5)), '[1, 2]'):
            with self.subTest(answer=bad[:40]):
                self.assertIsNone(review.parse(bad))

    def test_the_score_and_the_lists_are_bounded(self):
        result = review.parse(json.dumps(dict(ANSWER, score=42, problems=['p'] * 20, fixes='not a list')))
        self.assertIsNone(result['score'])
        self.assertEqual((len(result['problems']), result['fixes']), (8, []))


class FactTests(unittest.TestCase):
    def test_the_facts_are_short_readable_lines(self):
        facts = dict(seconds=4.0, fps=24.0, width=768, height=512, frames=97, motion=0.4, cuts=0, burnt_share=0.02, crushed_share=0.0, brightness_first_last=[100, 98],
                     sound=dict(mean_db=-30.0, loudest_db=-12.0, silent_seconds=1))
        text = review.describe(facts, dict(expected='Hello there', heard='Hello', share=0.5))
        self.assertIn('almost static', text)
        self.assertIn('speech asked for: "Hello there"; speech heard by Whisper: "Hello"; share heard: 50%', text)
        self.assertIn('1 seconds near-silent', text)
        self.assertIn('sound: none', review.describe({k: v for k, v in facts.items() if k != 'sound'}))

    @unittest.skipUnless((RUNTIME / 'ffmpeg/bin/ffmpeg.exe').is_file(), 'Optional Video runtime')
    def test_real_measurements_and_frames_of_a_small_clip(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest('numpy is only in the runtimes')
        ffmpeg = RUNTIME / 'ffmpeg/bin/ffmpeg.exe'
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp) / 'clip.mp4'
            subprocess.run([str(ffmpeg), '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=128x96:rate=24', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000',
                            '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(clip)], check=True, timeout=60)
            facts = review.measure(ffmpeg, clip)
            self.assertEqual((facts['width'], facts['height'], facts['frames']), (128, 96, 48))
            self.assertAlmostEqual(facts['seconds'], 2.0, delta=0.2)
            self.assertGreater(facts['motion'], 0)
            self.assertIn('sound', facts)
            frames = review.extract_frames(ffmpeg, clip, facts['seconds'], count=4)
            self.assertEqual(len(frames), 4)
            self.assertTrue(all(frame.startswith(bytes([0x89]) + b'PNG') for frame in frames))


class ReviewTests(unittest.TestCase):
    def make(self, answers):
        text = FakeText(answers)
        return review.DirectorReview(text, FakeGpu()), text

    def test_the_frames_the_facts_and_the_model_rules_reach_gemma_and_the_answer_comes_back(self):
        reviewer, text = self.make([json.dumps(ANSWER)])
        facts = dict(seconds=3.4, fps=24.0, width=832, height=480, frames=81, motion=8.9, cuts=0, burnt_share=0.0, crushed_share=0.0, brightness_first_last=[170, 160])
        result = reviewer.review([b'frame'] * 3, facts, 'A red fox walks.', 'wan22-ti2v-5b', 't2v', guide('wan22-ti2v-5b', 't2v')[1], 'Russian', idea='лиса идёт')
        self.assertEqual((result['verdict'], result['fixed_prompt'], result['model']), ('fixable', FIXED, 'gemma-test'))
        messages = text.calls[0]['messages']
        self.assertIn('film director', messages[0]['content'])
        self.assertIn('Wan 2.2 TI2V-5B is silent', messages[0]['content'])          # the model's levers
        self.assertIn('Write it in English', messages[0]['content'])
        self.assertEqual(len(messages[1]['images']), 3)
        self.assertIn('motion: very strong', messages[1]['content'])
        self.assertIn('лиса идёт', messages[1]['content'])                         # the person's own idea beside the prompt that was sent
        self.assertIn('in Russian', messages[1]['content'])
        self.assertEqual((text.owners[0], text.owners[-1]), (('begin', review.OWNER), ('end', review.OWNER)))

    def test_a_malformed_first_answer_is_asked_again_once(self):
        reviewer, text = self.make(['I think the clip is fine.', json.dumps(ANSWER)])
        result = reviewer.review([b'frame'], dict(seconds=1, fps=24, width=64, height=64, frames=24), 'A fox.', 'ltx25-distilled', 't2v', 'rules')
        self.assertEqual(result['verdict'], 'fixable')
        self.assertEqual(len(text.calls), 2)
        failing = FakeText(['nope', 'still nope'])
        with self.assertRaises(ValueError):
            review.DirectorReview(failing, FakeGpu()).review([b'frame'], dict(seconds=1, fps=24, width=64, height=64, frames=24), 'A fox.', 'ltx25-distilled', 't2v', 'rules')

    def test_no_frames_and_an_unavailable_gemma_are_refused(self):
        reviewer, _ = self.make([])
        with self.assertRaises(ValueError):
            reviewer.review([], dict(), 'A fox.', 'ltx25-distilled', 't2v', 'rules')
        off = review.DirectorReview(FakeText([], available=False), FakeGpu())
        with self.assertRaises(ValueError):
            off.review([b'frame'], dict(seconds=1, fps=24, width=64, height=64, frames=24), 'A fox.', 'ltx25-distilled', 't2v', 'rules')


if __name__ == '__main__':
    unittest.main()
