"""Scenario planning for long Wan clips and the chain validation; no GPU, the text model is a stub."""
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from gpu_coordinator import Busy
from video.scene_planner import ScenePlanner, _extract
from video.video_registry import validate, WAN_SEGMENTS, scene_frames
from video.wan_support import WAN_ID


class FakeText:
    """Answers each chat call from a queue; records what the planner sent."""
    def __init__(self, answers, available=True):
        self.answers, self.calls, self.owners = list(answers), [], []
        self.ready = available

    def availability(self):
        return dict(available=self.ready, reason='' if self.ready else 'not installed')

    def default_model(self):
        return 'gemma-test'

    def gemma_model(self):
        return self.default_model()

    def begin(self, owner):
        self.owners.append(('begin', owner))
        return threading.Event()

    def end(self, owner):
        self.owners.append(('end', owner))

    def start(self):
        pass

    def stream_chat(self, model, messages, think, cancel, options=None, generation_id=None):
        self.calls.append(dict(messages=[dict(m) for m in messages], options=options, think=think))
        yield dict(message=dict(content=self.answers.pop(0)))


class FakeGpu:
    def __init__(self, error=None):
        self.error, self.claimed = error, []

    def claim(self, module, *args):
        if self.error:
            raise self.error
        self.claimed.append(module)


class ExtractTests(unittest.TestCase):
    def test_json_fenced_and_numbered_answers(self):
        self.assertEqual(_extract('["a b", "c"]', 2), ['a b', 'c'])
        self.assertEqual(_extract('```json\n["one",\n"two"]\n```', 2), ['one', 'two'])
        self.assertEqual(_extract('1. First beat\n2. Second beat\n3) Third beat', 3), ['First beat', 'Second beat', 'Third beat'])

    def test_wrong_count_and_garbage_are_rejected(self):
        for answer, count in (('["a"]', 2), ('no list at all', 2), ('[1, 2]', 2), ('["' + 'x' * 1501 + '", "y"]', 2)):
            with self.subTest(answer=answer[:20]), self.assertRaises(ValueError):
                _extract(answer, count)


class PlannerTests(unittest.TestCase):
    def test_plan_claims_the_text_slot_and_releases_it(self):
        text, gpu = FakeText(['["Fox in snow, walks.", "Fox in snow, stops."]']), FakeGpu()
        result = ScenePlanner(text, gpu).plan('A fox walks through snow', 2)
        self.assertEqual(result['prompts'], ['Fox in snow, walks.', 'Fox in snow, stops.'])
        self.assertEqual(gpu.claimed, ['text'])
        self.assertEqual(text.owners, [('begin', 'video-plan'), ('end', 'video-plan')])
        system = text.calls[0]['messages'][0]['content']
        self.assertIn('exactly 2 prompts', system)
        self.assertIs(text.calls[0]['think'], False)

    def test_one_correction_round_then_error(self):
        text = FakeText(['["only one"]', '["a", "b", "c"]'])
        with self.assertRaises(ValueError):
            ScenePlanner(text, FakeGpu()).plan('idea', 2)
        self.assertEqual(len(text.calls), 2)
        self.assertIn('Wrong format', text.calls[1]['messages'][-1]['content'])
        self.assertEqual(text.owners[-1], ('end', 'video-plan'))
        recovered = FakeText(['["only one"]', '["a", "b"]'])
        self.assertEqual(ScenePlanner(recovered, FakeGpu()).plan('idea', 2)['prompts'], ['a', 'b'])

    def test_refusals_do_not_start_the_model(self):
        with self.assertRaises(Busy):
            ScenePlanner(FakeText([]), FakeGpu(Busy('Video works'))).plan('idea', 2)
        with self.assertRaises(ValueError):
            ScenePlanner(FakeText([], available=False), FakeGpu()).plan('idea', 2)
        for idea, count in (('', 2), ('idea', 1), ('idea', 13), ('idea', True), ('idea', '2')):
            with self.subTest(idea=idea, count=count), self.assertRaises(ValueError):
                ScenePlanner(FakeText([]), FakeGpu()).plan(idea, count)


class ChainValidationTests(unittest.TestCase):
    def request(self, frames, prompts, **extra):
        return dict(engine=WAN_ID, prompt='A fox runs.', frames=frames, quality='preview', segment_prompts=prompts) | extra

    def test_segment_counts_match_the_frame_budget(self):
        for frames, count in WAN_SEGMENTS.items():
            self.assertGreaterEqual(121 + 120 * (count - 1), frames)
            self.assertLess(121 + 120 * (count - 2), frames)
            row = validate(self.request(frames, [' beat %d ' % i for i in range(count)]))
            self.assertEqual(row['segment_prompts'][0], 'beat 0')
            self.assertEqual(row['frames'], frames)

    def test_rejected_chain_requests(self):
        with self.assertRaises(ValueError):
            validate(self.request(360, ['a', 'b']))          # wrong count
        with self.assertRaises(ValueError):
            validate(self.request(360, ['a', 'b', ' ']))     # empty prompt
        with self.assertRaises(ValueError):
            validate(dict(engine=WAN_ID, prompt='x', frames=360))   # no scenario at all
        with self.assertRaises(ValueError):
            validate(self.request(121, ['a']))               # a single clip has no scenario
        with self.assertRaises(ValueError):
            validate(dict(engine='ltx25-distilled', prompt='x', frames=97, segment_prompts=['a']))
        with self.assertRaises(ValueError):
            validate(self.request(240, ['a', 'b'], mode='i2v'))   # i2v still needs its source frame


class SceneModeTests(unittest.TestCase):
    def request(self, frames, scenes, **extra):
        return dict(engine=WAN_ID, prompt='A fox runs.', frames=frames, scenes=scenes, quality='preview',
                    segment_prompts=['shot %d' % i for i in range(scenes)]) | extra

    def test_scene_length_is_the_shortest_4n_plus_1_that_reaches_the_minimum(self):
        self.assertEqual(scene_frames(720, 7), 105)    # 102.9 -> 4n+1
        self.assertEqual(scene_frames(720, 6), 121)    # exactly five seconds
        self.assertEqual(scene_frames(720, 30), 33)    # never shorter than the shortest tested clip
        for total in (240, 360, 720, 1440, 2880):
            for scenes in range(2, 31):
                try:
                    length = scene_frames(total, scenes)
                except ValueError:
                    self.assertGreater(-(-total // scenes), 121)
                    continue
                self.assertEqual(length % 4, 1)
                self.assertGreaterEqual(length * scenes, total)
                self.assertLessEqual(33, length)
                self.assertLessEqual(length, 121)

    def test_valid_scene_request_reports_the_real_total(self):
        row = validate(self.request(720, 7))
        self.assertEqual((row['scenes'], row['scene_frames'], row['frames'], row['requested_frames'], row['join']), (7, 105, 735, 720, 'cut'))
        self.assertEqual(len(row['segment_prompts']), 7)
        self.assertEqual(validate(self.request(2880, 24))['frames'], 24 * 121)

    def test_rejected_scene_requests(self):
        for bad in (self.request(720, 5),                    # 144 frames per scene
                    self.request(720, 31), self.request(720, 1), self.request(720, True),
                    self.request(121, 3), self.request(720, 7, mode='t2v') | dict(segment_prompts=['a'] * 6),
                    self.request(720, 7, engine='wan22-s2v-14b')):          # scenes belong to Wan 2.2 TI2V and LTX 2.5
            with self.subTest(scenes=bad.get('scenes'), frames=bad['frames']), self.assertRaises(ValueError):
                validate(bad)
        with self.assertRaises(ValueError):                  # two minutes is not offered as one chain
            validate(dict(engine=WAN_ID, prompt='x', frames=2880, segment_prompts=['a'] * 24))

    def test_planner_uses_the_shots_prompt_for_cuts(self):
        text = FakeText(['["a", "b"]'])
        ScenePlanner(text, FakeGpu()).plan('idea', 2, join='cut')
        system = text.calls[0]['messages'][0]['content']
        self.assertIn('hard cuts', system)
        self.assertIn('fully self-contained', system)
        chain = FakeText(['["a", "b"]'])
        ScenePlanner(chain, FakeGpu()).plan('idea', 2)
        self.assertNotIn('hard cuts', chain.calls[0]['messages'][0]['content'])
        for count, join in ((31, 'cut'), (13, 'continue'), (2, 'other')):
            with self.subTest(count=count, join=join), self.assertRaises(ValueError):
                ScenePlanner(FakeText([]), FakeGpu()).plan('idea', count, join=join)

    def test_talking_character_prompts_leave_lips_to_the_voice_and_pass_the_speech(self):
        text = FakeText(['["a", "b", "c"]'])
        ScenePlanner(text, FakeGpu()).plan('A guitarist tells about his guitar', 3, join='cut', talking=True, speech='Здравствуйте! Я играю двадцать лет.')
        system, user = (m['content'] for m in text.calls[0]['messages'])
        self.assertIn('speech-to-video', system)
        self.assertIn('Never describe the words, the lips or the mouth', system)
        self.assertIn('hard cuts', system)
        self.assertIn('Я играю двадцать лет.', user)
        self.assertIn('3 parts', user)
        silent = FakeText(['["a", "b"]'])
        ScenePlanner(silent, FakeGpu()).plan('idea', 2, join='cut', talking=True)
        self.assertNotIn('What the person says', silent.calls[0]['messages'][1]['content'])
        # a talking clip may have up to 16 segments, 30 only without speech
        ScenePlanner(FakeText(['["%s"]' % '", "'.join('x' * 3 for _ in range(16))]), FakeGpu()).plan('idea', 16, join='cut', talking=True)
        with self.assertRaises(ValueError):
            ScenePlanner(FakeText([]), FakeGpu()).plan('idea', 17, join='cut', talking=True)


    def test_ltx_prompts_name_sound_and_scenes_are_self_contained(self):
        text = FakeText(['["a", "b", "c"]'])
        ScenePlanner(text, FakeGpu()).plan('A fox walks in snow', 3, join='cut', sound=True)
        system = text.calls[0]['messages'][0]['content']
        self.assertIn('LTX 2.5', system)
        self.assertIn('SOUND', system)
        self.assertIn('hard cuts', system)
        self.assertIn('fully self-contained', system)
        chain = FakeText(['["a", "b"]'])
        ScenePlanner(chain, FakeGpu()).plan('idea', 2, sound=True)
        self.assertIn('ONE continuous', chain.calls[0]['messages'][0]['content'])
        # a 60 s continuation has 15 windows
        ScenePlanner(FakeText(['["%s"]' % '", "'.join('x' * 3 for _ in range(15))]), FakeGpu()).plan('idea', 15, sound=True)
        with self.assertRaises(ValueError):
            ScenePlanner(FakeText([]), FakeGpu()).plan('idea', 16, sound=True)


if __name__ == '__main__':
    unittest.main()
