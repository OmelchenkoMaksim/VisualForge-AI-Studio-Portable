import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))

from video.video_registry import ENGINES, RATING_AXES, rating  # noqa: E402


class RatingTests(unittest.TestCase):
    def test_every_engine_has_a_rating_on_the_ten_point_scale(self):
        for engine_id, engine in ENGINES.items():
            with self.subTest(engine=engine_id):
                row = engine['rating']
                self.assertEqual(set(row), set(RATING_AXES) | {'overall'})
                for axis, value in row.items():
                    self.assertTrue(value is None and axis == 'sound' or value is not None and 0 <= value <= 10 and value * 2 == int(value * 2), (axis, value))
                self.assertIsNotNone(row['overall'])

    def test_overall_is_the_mean_of_the_known_axes_in_half_steps(self):
        self.assertEqual(rating(5, 5, 5, None, 5)['overall'], 5)
        self.assertEqual(rating(7, 7, 6.5, 6.5, 7)['overall'], 7)             # 6.8 -> 7
        self.assertEqual(rating(3, 4, 4, 4, 4)['overall'], 4)                 # 3.8 -> 4
        self.assertEqual(rating(6, 5, 6, 5, 4)['overall'], 5)                 # 5.2 -> 5
        self.assertEqual(rating(6.5, 7.5, 6.5, None, 5)['overall'], 6.5)

    def test_no_local_model_claims_to_be_close_to_a_real_camera(self):
        self.assertLess(max(engine['rating']['overall'] for engine in ENGINES.values()), 8)


if __name__ == '__main__':
    unittest.main()
