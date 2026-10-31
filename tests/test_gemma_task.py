import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_scene_planner import FakeGpu, FakeText  # noqa: E402
from video.gemma_task import GemmaTask, small_image  # noqa: E402


class Task(GemmaTask):
    OWNER = 'video-test'
    OPERATION = 'VideoPromptPlanning'


class SessionTests(unittest.TestCase):
    def test_the_model_is_claimed_started_and_always_given_back(self):
        text, gpu = FakeText(['one ', 'two']), FakeGpu()
        task = Task(text, gpu)
        with task.session() as cancel:
            self.assertFalse(cancel.is_set())
            self.assertEqual(text.owners, [('begin', 'video-test')])
        self.assertEqual(text.owners, [('begin', 'video-test'), ('end', 'video-test')])
        with self.assertRaises(RuntimeError), task.session():
            raise RuntimeError('the work failed')
        self.assertEqual(text.owners[-1], ('end', 'video-test'))             # released after a failure as well

    def test_an_unavailable_model_is_a_message_for_the_person_and_claims_nothing(self):
        text = FakeText([], available=False)
        with self.assertRaises(ValueError):
            with Task(text, FakeGpu()).session():
                self.fail('the block must not run')
        self.assertEqual(text.owners, [])
        self.assertFalse(Task(None, FakeGpu()).status()['available'])
        self.assertEqual(Task(FakeText([]), FakeGpu()).status(), dict(available=True, reason='', model='gemma-test'))

    def test_an_error_of_the_model_server_becomes_a_value_error(self):
        text = FakeText([])
        with self.assertRaises(ValueError) as caught:
            with Task(text, FakeGpu()).session():
                raise OSError('x' * 400)
        self.assertLessEqual(len(str(caught.exception)), 260)
        self.assertEqual(text.owners[-1], ('end', 'video-test'))

    def test_ask_joins_the_streamed_pieces_and_passes_the_options(self):
        text = FakeText(['The answer.'])
        task = Task(text, FakeGpu())
        with task.session() as cancel:
            self.assertEqual(task.ask([dict(role='user', content='hi')], cancel, temperature=0.1, num_ctx=2048), 'The answer.')
        self.assertEqual(text.calls[0]['options'], dict(temperature=0.1, num_ctx=2048))
        self.assertFalse(text.calls[0]['think'])


class SmallImageTests(unittest.TestCase):
    def test_a_picture_is_scaled_down_and_a_broken_one_is_skipped(self):
        import base64
        import io
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (2000, 1000), (200, 30, 30)).save(buffer, 'PNG')
        url = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
        with Image.open(io.BytesIO(base64.b64decode(small_image(url)))) as image:
            self.assertEqual(image.size, (1024, 512))
        self.assertIsNone(small_image('data:image/png;base64,AAAA'))
        self.assertIsNone(small_image('nonsense'))


if __name__ == '__main__':
    unittest.main()
