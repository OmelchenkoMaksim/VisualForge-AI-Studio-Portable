import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpu_coordinator import Busy, GpuUnavailable  # noqa: E402
from video import video_routes  # noqa: E402

CLIP = '12345678-1234-1234-1234-123456789abc'


class Handler:
    def __init__(self):
        self.answer = None

    def response_json(self, code, data):
        self.answer = (code, data)


class Planner:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def plan(self, *args, **options):
        self.calls.append((args, options))
        if self.error:
            raise self.error
        return self.result


class ReplyTests(unittest.TestCase):
    def test_errors_map_to_statuses_the_page_understands(self):
        for error, code in ((Busy('video is running'), 409), (GpuUnavailable('no card'), 503), (ValueError('x' * 900), 400)):
            handler = Handler()
            video_routes.plan(handler, Planner(error=error), dict(prompt='a fox', segments=2))
            self.assertEqual(handler.answer[0], code)
            self.assertLessEqual(len(handler.answer[1]['error']), 800)

    def test_plan_passes_the_fields_and_a_missing_module_is_503(self):
        handler, planner = Handler(), Planner(result=dict(prompts=['a', 'b']))
        video_routes.plan(handler, planner, dict(prompt='a fox', segments=2, source_image='data', join='cut', talking=True, speech_text='hi', sound=True))
        self.assertEqual(handler.answer, (200, dict(prompts=['a', 'b'])))
        self.assertEqual(planner.calls[0], (('a fox', 2, 'data', 'cut'), dict(talking=True, speech='hi', sound=True)))
        missing = Handler()
        video_routes.plan(missing, None, {})
        self.assertEqual(missing.answer[0], 503)


class EnhanceTests(unittest.TestCase):
    def test_pictures_are_collected_and_character_modes_get_a_note(self):
        seen = []
        enhancer = SimpleNamespace(enhance=lambda *args: seen.append(args) or dict(prompt='ok'))
        handler = Handler()
        video_routes.enhance(handler, enhancer, dict(prompt='x', engine='scail2', mode='replace', source_image='photo', driving_frames=['f1', 'f2', 5], subject_images=['car'],
                                                     objects=dict(reference='human', driving='human')))
        prompt, engine, mode, pictures, variant, note = seen[0]
        self.assertEqual((prompt, engine, mode, pictures, variant), ('x', 'scail2', 'replace', ['photo', 'car', 'f1', 'f2'], None))
        self.assertIn('Picture 1: the PHOTO', note)
        seen.clear()
        video_routes.enhance(handler, enhancer, dict(prompt='x', engine='ltx25-distilled', mode='first_last', source_image='a', end_image='b'))
        self.assertEqual((seen[0][3], seen[0][5]), (['a', 'b'], ''))


class ReviewTests(unittest.TestCase):
    def run_review(self, entries, data, files=()):
        with tempfile.TemporaryDirectory() as folder:
            for name in files:
                (Path(folder) / name).write_bytes(b'x')
            video = SimpleNamespace(call=lambda route: dict(entries=[dict(entry, saved_path=str(Path(folder) / entry['saved_path'])) if 'saved_path' in entry else entry for entry in entries]))
            handler = Handler()
            reviewer = SimpleNamespace(review=lambda *args: self.fail('Gemma must not be asked'))
            video_routes.review(handler, reviewer, video, SimpleNamespace(busy_modules=lambda: []), SimpleNamespace(root=Path(folder), cache=Path(folder)), data)
            return handler.answer

    def test_only_a_finished_clip_of_the_history_with_its_file_can_be_reviewed(self):
        done = dict(id=CLIP, state='done', saved_path='clip.mp4', engine='ltx25-distilled', mode='t2v', prompt='a fox')
        for entries, data, files in (([done], dict(id='../../x'), ['clip.mp4']), ([done], dict(id=CLIP), []), ([dict(done, state='error')], dict(id=CLIP), ['clip.mp4']),
                                     ([], dict(id=CLIP), []), ([done], dict(id=5), ['clip.mp4'])):
            with self.subTest(data=data, files=files):
                self.assertEqual(self.run_review(entries, data, files)[0], 400)

    def test_a_film_of_scenes_is_refused_before_any_slow_work(self):
        film = dict(id=CLIP, state='done', saved_path='film.mp4', segment_prompts=['a', 'b'])
        answer = self.run_review([film], dict(id=CLIP), ['film.mp4'])        # measure() would fail on a fake file: the refusal must come first
        self.assertEqual(answer[0], 400)
        self.assertIn('одним промптом', answer[1]['error'])

    def test_a_missing_module_is_503(self):
        handler = Handler()
        video_routes.review(handler, None, None, None, None, {})
        self.assertEqual(handler.answer[0], 503)


if __name__ == '__main__':
    unittest.main()
