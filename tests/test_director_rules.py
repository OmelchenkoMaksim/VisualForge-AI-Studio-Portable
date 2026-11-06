import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))

from director import plan_rules as rules  # noqa: E402


class SceneRangeTests(unittest.TestCase):
    def test_below_a_minute_a_film_has_exactly_two_scenes(self):
        for seconds in (5, 12, 30, 59.9):
            self.assertEqual(rules.scene_range(seconds), (2, 2))

    def test_from_a_minute_there_are_up_to_five_scenes_for_every_minute(self):
        self.assertEqual(rules.scene_range(60), (2, 5))
        self.assertEqual(rules.scene_range(90), (2, 7))
        self.assertEqual(rules.scene_range(120), (2, 10))
        self.assertEqual(rules.scene_range(300), (5, 25))                                                 # a scene lasts a minute at most: five minutes cannot be two scenes
        self.assertEqual(rules.scene_range(121), (3, 10))
        self.assertEqual(rules.scene_range(600), (10, 50))                                                # ten minutes: a scene a minute long at most, one in 12 s at the other end
        self.assertEqual(rules.scene_range(rules.MAX_SECONDS), (15, 75))
        for seconds in range(60, rules.MAX_SECONDS + 1):
            fewest, most = rules.scene_range(seconds)
            self.assertLessEqual(seconds / fewest, rules.MAX_SCENE_SECONDS)                              # the fewest scenes can always add up to the film
            self.assertGreaterEqual(seconds / most, rules.MIN_SCENE_SECONDS)                             # and so can the most

    def test_a_request_is_checked_as_a_whole(self):
        rules.check_request(5, 2)
        rules.check_request(60, 5)
        rules.check_request(300, 25)
        rules.check_request(600, 10)
        rules.check_request(rules.MAX_SECONDS, 75)
        for seconds, scenes in ((4, 2), (rules.MAX_SECONDS + 1, 40), (30, 3), (30, 1), (60, 6), (300, 26), (300, 4), (121, 2), (600, 9), (600, 51), (60, True), (60, 2.5), ('60', 2), (True, 2)):
            with self.subTest(seconds=seconds, scenes=scenes), self.assertRaises(ValueError):
                rules.check_request(seconds, scenes)

class GroupTests(unittest.TestCase):
    def test_every_group_has_a_kind_a_limit_and_a_name_for_each_slot(self):
        ids = [row['id'] for row in rules.GROUPS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sorted({row['media'] for row in rules.GROUPS}), ['audio', 'photo', 'video', 'voice'])
        self.assertEqual((rules.LIMIT_OF['narrator'], rules.LIMIT_OF['character']), (3, 10))             # what the owner asked for: three narrators, ten characters
        self.assertEqual(rules.MAX_SONG_SECONDS, 3600)
        for row in rules.GROUPS:
            with self.subTest(group=row['id']):
                self.assertGreater(row['limit'], 0)
                names = [rules.slot_name(row['id'], position) for position in range(1, row['limit'] + 1)]
                self.assertEqual(len(names), len(set(names)))
        for media, group in rules.DEFAULT_GROUP.items():
            self.assertEqual(rules.MEDIA_OF[group], media)

    def test_slots_are_named_the_way_the_planner_is_told(self):
        self.assertEqual([rules.slot_name('narrator', n) for n in (1, 2, 3)], ['Narrator', 'Narrator 2', 'Narrator 3'])
        self.assertEqual([rules.slot_name('character', n) for n in (1, 2, 3, 10)], ['Main character', 'Second main character', 'Character 3', 'Character 10'])
        self.assertEqual(rules.slot_name('place', 4), 'Place 4')
        self.assertEqual(rules.group_of('song')['media'], 'audio')
        with self.assertRaises(ValueError):
            rules.group_of('nobody')


def item(group, seconds=None):
    return dict(group=group, seconds=seconds)


class AdviceTests(unittest.TestCase):
    def test_with_nothing_attached_any_short_film_does_well(self):
        advice = rules.advise([])
        self.assertEqual((advice['low'], advice['high'], advice['ideal'], advice['scenes']), (10, 60, 30, 2))
        self.assertEqual([reason['code'] for reason in advice['reasons']], ['baseline'])

    def test_every_place_needs_a_scene_and_a_scene_needs_twelve_seconds(self):
        self.assertEqual(rules.advise([item("place")] * 2)["low"], 10)                                 # two scenes are what any film has
        advice = rules.advise([item('place')] * 6)
        self.assertEqual((advice['low'], advice['ideal'], advice['scenes']), (72, 72, 6))                # one scene for every place, and the length allows exactly that many
        self.assertIn(dict(code='places', count=6, seconds=72), advice['reasons'])
        self.assertNotIn('baseline', [reason['code'] for reason in advice['reasons']])                   # the general advice is for nothing else asking for more
        self.assertEqual(rules.advise([item('place')] * 20)['scenes'], 20)
        for places in range(0, 21):
            with self.subTest(places=places):
                low = rules.advise([item('place')] * places)['low']
                self.assertGreaterEqual(rules.scene_range(low)[1], max(2, places))

    def test_every_voice_needs_a_line_to_say(self):
        advice = rules.advise([item('narrator'), item('character')])
        self.assertEqual(advice['low'], 20)
        self.assertIn(dict(code='voices', count=2, seconds=20), advice['reasons'])
        self.assertEqual(rules.advise([item('narrator')] * 3 + [item('character')] * 10)['low'], 130)

    def test_a_song_is_best_played_whole_and_a_long_one_is_cut(self):
        advice = rules.advise([item('song', 192.0)])
        self.assertEqual((advice['low'], advice['high'], advice['ideal']), (60, 192, 192))
        self.assertIn(dict(code='songs', count=1, seconds=192), advice['reasons'])
        two = rules.advise([item('song', 100.0), item('song', 120.0)])
        self.assertEqual(two['ideal'], 220)
        hour = rules.advise([item('song', 3600.0)])
        self.assertEqual((hour['high'], hour['ideal']), (300, 300))
        self.assertIn(dict(code='song_excerpt', seconds=300), hour['reasons'])

    def test_the_advice_never_leaves_the_length_a_film_may_have(self):
        for places, voices, songs in ((0, 0, []), (25, 13, [3600.0] * 6), (3, 1, [5.0]), (20, 0, [10.0])):
            with self.subTest(places=places, voices=voices, songs=songs):
                advice = rules.advise([item('place')] * places + [item('character')] * voices + [item('song', length) for length in songs])
                self.assertTrue(rules.MIN_SECONDS <= advice['low'] <= advice['ideal'] <= advice['high'] <= rules.MAX_SECONDS, advice)
                self.assertTrue(all(reason['code'] in rules.ADVICE_CODES for reason in advice['reasons']))

    def test_speech_adds_to_the_time_estimate(self):
        plain = rules.estimate_minutes(60, 'standard', 0, False)
        self.assertGreater(rules.estimate_minutes(60, 'standard', 0, False, 6), plain)
        self.assertEqual(rules.estimate_minutes(60, 'standard', 0, False, 0), plain)


class ProfileTests(unittest.TestCase):
    ENGINES = [dict(id='ace-step-15-turbo', max_duration=180), dict(id='minimax-music3-int8', max_duration=60), dict(id='stable-audio-3-medium', max_duration=120)]

    def test_only_a_real_true_asks_for_ultra(self):
        self.assertIs(rules.profile(True), rules.PROFILES['ultra'])
        for value in (False, None, 1, 'true', 'yes', [True], dict(), 0):
            with self.subTest(value=value):
                self.assertIs(rules.profile(value), rules.PROFILES['normal'])

    def test_ultra_is_the_heavy_profile_and_the_estimate_knows_it(self):
        ultra, normal = rules.profile(True), rules.profile(False)
        self.assertEqual((ultra['quality'], normal['quality']), ('max', 'standard'))
        self.assertEqual((ultra['keyframes'], normal['keyframes']), ('all', 'several'))
        # Measured on 2026-11-06 (calibration of the video model): a window of the biggest picture takes 56 s, one of the standard picture 52 s, so the picture alone costs a few percent more;
        # what makes Ultra slow is the keyframe for every scene and the heavier engines, which the estimate adds. (The owner expected two to three times for the picture; the measurement says no.)
        ratio = rules.estimate_minutes(60, ultra['quality'], 0, False) / rules.estimate_minutes(60, normal['quality'], 0, False)
        self.assertTrue(1.0 < ratio < 1.5, ratio)
        with_keyframes = rules.estimate_minutes(60, ultra['quality'], 4, False) / rules.estimate_minutes(60, normal['quality'], 0, False)
        self.assertGreater(with_keyframes, ratio)
        self.assertTrue(all(name in rules.QUALITIES for name in (ultra['quality'], normal['quality'])))

    def test_music_engines_are_ordered_by_the_profile_and_by_what_covers_the_film(self):
        def order(seconds, ultra, engines=None):
            return [engine['id'] for engine in rules.music_order(engines or self.ENGINES, seconds, ultra)]
        self.assertEqual(order(30, True), ['minimax-music3-int8', 'ace-step-15-turbo', 'stable-audio-3-medium'])         # the strongest first
        self.assertEqual(order(30, False), ['ace-step-15-turbo', 'stable-audio-3-medium', 'minimax-music3-int8'])        # the fastest first
        self.assertEqual(order(90, True), ['ace-step-15-turbo', 'stable-audio-3-medium', 'minimax-music3-int8'])         # MiniMax stops at a minute and would loop
        self.assertEqual(order(200, False), ['ace-step-15-turbo', 'stable-audio-3-medium', 'minimax-music3-int8'])       # none covers it: the profile's order again
        self.assertEqual(order(600, True), ['ace-step-15-turbo', 'stable-audio-3-medium', 'minimax-music3-int8'])        # none covers ten minutes: the longest track first (fewest tracks and seams), whatever the profile
        unknown = self.ENGINES + [dict(id='newcomer', max_duration=300)]
        self.assertEqual(order(250, True, unknown)[0], 'newcomer')                                                      # an engine the profile does not know still counts when it alone covers the film
        self.assertEqual(order(30, True, unknown)[-1], 'newcomer')
        self.assertEqual(rules.music_order([], 30, True), [])


class SceneJobTests(unittest.TestCase):
    def test_a_short_scene_is_one_ltx_shot_and_a_long_one_a_continuous_take(self):
        job = rules.scene_job(4.0, 'draft', 'landscape', False, 7)
        self.assertEqual((job['engine'], job['mode'], job['quality'], job['frames'], job['width'], job['height']), ('ltx25-distilled', 't2v', 'preview', 97, 768, 512))
        self.assertEqual(rules.scene_job(2.0, 'draft', 'landscape', True, 7)['frames'], 49)
        self.assertEqual(rules.scene_job(2.0, 'draft', 'landscape', True, 7)['mode'], 'i2v')
        self.assertEqual(rules.scene_job(5.0, 'standard', 'portrait', False, 7)['frames'], 121)
        self.assertEqual((rules.scene_job(5.0, 'standard', 'portrait', False, 7)['width'], rules.scene_job(5.0, 'standard', 'portrait', False, 7)['height']), (576, 1024))
        # A long scene is asked for at its own length (rounded up to what the windows can make), not rounded up to 15, 30 or 60 seconds.
        for seconds, frames in ((5.2, 144), (6.0, 144), (6.1, 152), (9.0, 216), (10.0, 248), (15, 360), (15.1, 368), (30, 720), (45, 1080), (60, 1440)):
            self.assertEqual(rules.scene_job(seconds, 'max', 'landscape', False, 1)['frames'], frames, seconds)
        self.assertEqual(rules.scene_job(10, 'max', 'landscape', False, 1)['quality'], 'standard')
        self.assertEqual(rules.scene_job(60.6, 'draft', 'landscape', False, 1)['frames'], 1440)           # the longest scene (60 s) with the overlap of its transition: the longest take there is
        with self.assertRaises(ValueError):
            rules.scene_job(61, 'draft', 'landscape', False, 1)

    def test_a_scene_costs_the_windows_it_needs_and_never_less_than_its_length(self):
        from video.ltx_continuation import windows
        for tenth in range(20, 601):                        # every length from 2.0 to 60.0 seconds
            seconds = tenth / 10
            frames = rules.scene_job(seconds, 'draft', 'landscape', False, 1)['frames']
            with self.subTest(seconds=seconds):
                self.assertGreaterEqual(frames, seconds * rules.FPS - 1e-6)
                # The widest gap of the chain lengths is 216 to 248 (240 is a total of the panel's scenes mode): at most 1.3 s too long, never the 9 s that a round-up to 15 s cost.
                self.assertLess(frames - seconds * rules.FPS, 32 if frames > 121 else 48)
                if frames > 121:
                    self.assertEqual(windows(frames)[-1]['start'] + windows(frames)[-1]['frames'], frames + 1)
        # A 6.6 s scene was four windows (a 15 s chain) and is two now; a 16.6 s scene was eight (30 s) and is four.
        self.assertEqual(len(windows(rules.scene_job(6.6, 'draft', 'landscape', False, 1)['frames'])), 2)
        self.assertEqual(len(windows(rules.scene_job(16.6, 'draft', 'landscape', False, 1)['frames'])), 4)

    def test_every_job_is_a_request_the_video_registry_accepts(self):
        from video.video_registry import validate
        for quality in rules.QUALITIES:
            for orientation in rules.ORIENTATIONS:
                for seconds in (2.0, 4.6, 5.0, 12, 30, 60):
                    with self.subTest(quality=quality, orientation=orientation, seconds=seconds):
                        job = rules.scene_job(seconds, quality, orientation, False, 5)
                        validate(dict(job, prompt='A fox runs through the snow.'))


if __name__ == '__main__':
    unittest.main()
