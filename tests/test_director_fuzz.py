"""Whatever Gemma answers and whatever a page sends, the Director either refuses with a message or keeps to its rules: random input against the invariants (no model, no GPU, no FFmpeg)."""
import math
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))

from director import media, plan, plan_rules as rules, soundtrack  # noqa: E402

CAPS = dict(keyframes=True, music=[dict(id='ace-step-15-turbo', modes=['instrumental', 'song'], max_duration=180, about='x'),
                                   dict(id='stable-audio-3-medium', modes=['audio'], max_duration=120, about='y')], speech=dict(engine='voxcpm2', languages=['Russian', 'English']))
WORDS = ['дорога', 'road', 'Поехали!', 'a b', '', ' ', 'x' * 400, 'Это длинное предложение, которое тянется. Вторая фраза? Третья!', '\n\t', '😀 smile', 'Chapter 1']


def noise(rand, depth=0):
    """Any JSON-like value, including the ones nobody should send."""
    kinds = ['int', 'float', 'text', 'none', 'bool', 'list', 'dict', 'big', 'nan', 'neg']
    kind = rand.choice(kinds if depth < 2 else kinds[:5] + ['big', 'nan', 'neg'])
    return {'int': lambda: rand.randint(-5, 30), 'float': lambda: rand.uniform(-10, 400), 'text': lambda: rand.choice(WORDS), 'none': lambda: None, 'bool': lambda: rand.random() < 0.5,
            'list': lambda: [noise(rand, depth + 1) for _ in range(rand.randint(0, 4))], 'dict': lambda: {rand.choice(['a', 'seconds', 'text']): noise(rand, depth + 1) for _ in range(rand.randint(0, 3))},
            'big': lambda: rand.choice([10 ** 12, -10 ** 12, 2 ** 70]), 'nan': lambda: float('nan'), 'neg': lambda: -rand.random()}[kind]()


def maybe(rand, good, bad_rate=0.25):
    return noise(rand) if rand.random() < bad_rate else good


def random_scene(rand, pictures):
    return maybe(rand, dict(seconds=maybe(rand, rand.uniform(0.1, 90)), prompt=maybe(rand, 'A woman drives a red car along a desert road at dawn, wind and a distant guitar.'),
                            references=maybe(rand, [maybe(rand, rand.choice(pictures + [99])) for _ in range(rand.randint(0, 9))]), keyframe=maybe(rand, rand.choice([None, 'A woman by a car'])),
                            transition=maybe(rand, rand.choice(list(rules.TRANSITIONS) + ['bogus']))), 0.1)


def random_plan(rand, request, pictures, songs, voices):
    scenes = [random_scene(rand, pictures) for _ in range(request['scenes'] + (rand.choice([0, 0, 0, 1, -1])))]
    return dict(title=maybe(rand, 'T'), summary=maybe(rand, 'S'), orientation=maybe(rand, rand.choice(['landscape', 'portrait', 'square'])), quality=maybe(rand, rand.choice(list(rules.QUALITIES))),
                style=maybe(rand, dict(phrase=maybe(rand, 'film'), warmth=maybe(rand, rand.uniform(-3, 3)), contrast=maybe(rand, 1), saturation=maybe(rand, 1))),
                music=maybe(rand, dict(engine=maybe(rand, rand.choice(['ace-step-15-turbo', 'stable-audio-3-medium', 'nope'])), mode=maybe(rand, rand.choice(['song', 'instrumental', 'audio'])),
                                       caption=maybe(rand, 'slow guitar'), lyrics=maybe(rand, 'la la'), gain_db=maybe(rand, rand.uniform(-80, 20)), start=maybe(rand, rand.uniform(-5, 400)))),
                songs=maybe(rand, [maybe(rand, dict(song=maybe(rand, rand.choice(list(songs) + [77])), offset=maybe(rand, rand.uniform(-5, 5000)), start=maybe(rand, rand.uniform(-5, 400)),
                                                    seconds=maybe(rand, rand.uniform(-5, 4000)), gain_db=maybe(rand, rand.uniform(-60, 30)), fade_in=maybe(rand, rand.uniform(-5, 50)),
                                                    fade_out=maybe(rand, rand.uniform(-5, 50)))) for _ in range(rand.randint(0, 12))]),
                speech=maybe(rand, [maybe(rand, dict(voice=maybe(rand, rand.choice(list(voices) + [88])), scene=maybe(rand, rand.randint(-1, request['scenes'] + 2)), text=maybe(rand, rand.choice(WORDS)),
                                                     language=maybe(rand, rand.choice(['Russian', 'English', 'Klingon'])), delay=maybe(rand, rand.uniform(-5, 100)), gain_db=maybe(rand, rand.uniform(-40, 40))))
                                    for _ in range(rand.randint(0, 14))]),
                scenes=scenes)


class PlanFuzzTests(unittest.TestCase):
    def test_a_plan_is_refused_or_keeps_to_every_rule(self):
        rand = random.Random(20261101)
        accepted = refused = 0
        for round_number in range(1500):
            seconds = rand.choice([5, 10, 30, 59, 60, 90, 300])
            scenes = rand.randint(*rules.scene_range(seconds))
            request = dict(prompt='Фильм', seconds=seconds, scenes=scenes, orientation=rand.choice([None, 'portrait']), quality=rand.choice([None, 'draft']), language=rand.choice(['Russian', 'English']),
                           speech_language=rand.choice(['Russian', 'English']))
            pictures = rand.sample(range(1, 30), rand.randint(0, 6))
            songs = {number: rand.uniform(3, 4000) for number in rand.sample(range(30, 40), rand.randint(0, 3))}
            voices = {number: rand.choice(['narrator', 'character']) for number in rand.sample(range(40, 60), rand.randint(0, 4))}
            materials = plan.Materials([dict(number=n, kind='photo', group='hero') for n in pictures] + [dict(number=n, kind='audio', group='song', seconds=s) for n, s in songs.items()]
                                        + [dict(number=n, kind='voice', group=g, seconds=9.0) for n, g in voices.items()])
            caps = rand.choice([CAPS, dict(CAPS, speech=None), dict(keyframes=False, music=[], speech=None)])
            raw = random_plan(rand, request, pictures, songs, voices) if rand.random() < 0.9 else noise(rand)
            try:
                result = plan.normalize(raw if isinstance(raw, dict) else {}, request, caps, materials)
            except ValueError:
                refused += 1                                                                   # the only way to refuse: a message for Gemma
                continue
            accepted += 1
            with self.subTest(round=round_number):
                self.check(result, request, caps, pictures, songs, voices, raw)
        self.assertGreater(accepted, 200)                                                    # the generator must reach the rules, not only the refusals
        self.assertGreater(refused, 20)

    def check(self, result, request, caps, pictures, songs, voices, raw=None):
        scenes = result['scenes']
        self.assertEqual(len(scenes), request['scenes'])
        self.assertAlmostEqual(sum(row['seconds'] for row in scenes), request['seconds'], delta=0.3, msg=repr((request['seconds'], [row.get('seconds') if isinstance(row, dict) else row for row in (raw or {}).get('scenes', [])])))
        for row in scenes:
            self.assertTrue(rules.MIN_SCENE_SECONDS <= row['seconds'] <= rules.MAX_SCENE_SECONDS)
            self.assertTrue(all(n in pictures for n in row['references']) and len(row['references']) <= 6)
            self.assertIn(row['transition'], rules.TRANSITIONS)
            self.assertTrue(row['keyframe'] is None or caps['keyframes'])
        self.assertEqual(scenes[-1]['transition'], 'cut')
        self.assertTrue(result['orientation'] in rules.ORIENTATIONS and result['quality'] in rules.QUALITIES)
        style = result['style']
        self.assertTrue(-1 <= style['warmth'] <= 1 and 0.8 <= style['contrast'] <= 1.3 and 0.0 <= style['saturation'] <= 1.4)
        if result['music']:
            engines = {engine['id']: engine for engine in caps['music']}
            music = result['music']
            self.assertIn(music['engine'], engines)
            self.assertIn(music['mode'], engines[music['engine']]['modes'])
            self.assertTrue(-30 <= music['gain_db'] <= -6 and 0 <= music['start'] <= request['seconds'] and 10 <= music['seconds'] <= engines[music['engine']]['max_duration'])
        cues = result['songs']
        self.assertLessEqual(len(cues), rules.MAX_SONG_CUES)
        self.assertEqual([cue['start'] for cue in cues], sorted(cue['start'] for cue in cues))
        for cue in cues:
            self.assertIn(cue['song'], songs)
            self.assertTrue(cue['seconds'] >= 2 and cue['offset'] >= 0 and cue['start'] >= 0)
            self.assertLessEqual(cue['offset'] + cue['seconds'], songs[cue['song']] + 0.011)                        # never past the end of the song
            self.assertLessEqual(cue['start'] + cue['seconds'], request['seconds'] + 0.011)                         # nor past the end of the film
            self.assertTrue(-24 <= cue['gain_db'] <= 0 and cue['fade_in'] + cue['fade_out'] <= cue['seconds'] + 0.011)
        lines = result['speech']
        if not voices or not caps['speech']:
            self.assertEqual(lines, [])
        per_scene = {}
        for line in lines:
            self.assertIn(line['voice'], voices)
            self.assertTrue(1 <= line['scene'] <= request['scenes'])
            self.assertIn(line['language'], caps['speech']['languages'])
            self.assertTrue(0 <= line['delay'] <= max(0.0, scenes[line['scene'] - 1]['seconds'] - 1) + 0.011 and -12 <= line['gain_db'] <= 6)
            self.assertTrue(line['text'] and len(line['text']) <= rules.MAX_LINE_CHARS and '\n' not in line['text'])
            per_scene[line['scene']] = per_scene.get(line['scene'], 0) + 1
        for scene_number, count in per_scene.items():
            self.assertLessEqual(count, rules.MAX_LINES_PER_SCENE)
            self.assertLessEqual(sum(len(line['text']) for line in lines if line['scene'] == scene_number), int(scenes[scene_number - 1]['seconds'] * rules.SPEECH_CHARS_PER_SECOND))


class SpeechFuzzTests(unittest.TestCase):
    def test_a_line_is_cut_to_fit_and_never_cut_to_nothing_silently(self):
        rand = random.Random(5)
        for _ in range(3000):
            text = ' '.join(rand.choice(['слово', 'a', 'Конец.', 'wait?', 'yes!', 'длиннейшее_слово_без_пробелов' * rand.randint(1, 3), '...', '—']) for _ in range(rand.randint(0, 60)))
            limit = rand.randint(-5, 400)
            result = plan.fit_line(text, limit)
            self.assertLessEqual(len(result), max(0, limit) if limit >= 8 else 0)
            self.assertEqual(result, ' '.join(result.split()))
            if len(' '.join(text.split())) <= limit and limit >= 8:
                self.assertEqual(result, ' '.join(text.split()))                                      # what fits is never touched

    def test_placed_lines_never_overlap_and_never_pass_the_end(self):
        rand = random.Random(9)
        for _ in range(3000):
            film = rand.uniform(5, 300)
            lines = sorted((rand.uniform(0, film), rand.uniform(0.3, 25)) for _ in range(rand.randint(0, 12)))
            placed = soundtrack.place_lines(lines, film)
            self.assertEqual(len(placed), len(lines))
            clock = 0.0
            for (earliest, seconds), where in zip(lines, placed):
                if where is None:
                    continue
                start, tempo = where
                self.assertTrue(1.0 <= tempo <= soundtrack.MAX_TEMPO + 1e-3)
                self.assertGreaterEqual(start, earliest - 1e-3)
                self.assertGreaterEqual(start, clock - 1e-3)                                       # never over the line before
                self.assertLessEqual(start + seconds / tempo, film - soundtrack.FILM_TAIL + 1e-2)
                clock = start + seconds / tempo + soundtrack.LINE_GAP

    def test_scene_lengths_always_add_up_for_any_numbers(self):
        rand = random.Random(11)
        for _ in range(2000):
            scenes = rand.randint(2, 25)
            total = rand.uniform(max(5.0, scenes * 2.0), min(300.0, scenes * 60.0))
            values = [rand.choice([0.0, 0.001, rand.uniform(0, 500), 1e9]) for _ in range(scenes)]
            fitted = plan.fit_seconds([max(v, 0.001) for v in values], total)
            self.assertAlmostEqual(sum(fitted), total, delta=0.06)
            self.assertTrue(all(2.0 <= v <= 60.0 for v in fitted))


class MediaFuzzTests(unittest.TestCase):
    def test_the_stretches_of_any_song_cover_it_once_from_end_to_end(self):
        rand = random.Random(3)
        for _ in range(400):
            count = rand.choice([1, 2, 9, 40, 200, 3600])
            levels = [rand.choice([-90.0, rand.uniform(-60, -5)]) for _ in range(count)]
            found = media.stretches(levels)
            self.assertEqual(found[0][0], 0.0)
            self.assertEqual(found[-1][1], float(count))
            for before, after in zip(found, found[1:]):
                self.assertEqual(before[1], after[0])                                              # no gap and no overlap
                self.assertNotEqual(before[2], after[2])                                           # neighbours of one name are one stretch
            self.assertLessEqual(len(found), max(2, count))
            self.assertLessEqual(len(found), 45)

    def test_the_mean_level_is_between_the_quietest_and_the_loudest_second(self):
        rand = random.Random(4)
        for _ in range(500):
            levels = [rand.uniform(-90, 0) for _ in range(rand.randint(1, 50))]
            mean = media.mean_level(levels)
            self.assertTrue(min(levels) - 0.2 <= mean <= max(levels) + 0.2 and math.isfinite(mean))

    def test_a_clock_never_shows_sixty_seconds(self):
        for seconds in [x / 7 for x in range(0, 30000, 13)]:
            text = media.clock(seconds)
            self.assertNotIn(':60', text)
            self.assertRegex(text, r'^\d+:\d\d(:\d\d)?$')

    def test_a_label_never_breaks_the_index(self):
        rand = random.Random(8)
        alphabet = 'abc Дд\n\t"\\{}[]😀'
        for _ in range(300):
            text = ''.join(rand.choice(alphabet) for _ in range(rand.randint(0, 200)))
            cleaned = ' '.join(str(text or '').split())[:media.LABEL_LIMIT]
            self.assertLessEqual(len(cleaned), media.LABEL_LIMIT)
            self.assertNotIn('\n', cleaned)


if __name__ == '__main__':
    unittest.main()
