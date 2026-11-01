"""The film plan: Gemma's JSON is checked, never trusted; the brain looks once, plans, and retries a wrong answer (no model, no GPU)."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image  # noqa: E402

from director import brain, media, plan  # noqa: E402
from test_scene_planner import FakeGpu, FakeText  # noqa: E402
from video.video_delivery import ffmpeg_paths  # noqa: E402

RUNTIME = Path(os.environ.get('VISUALFORGE_VIDEO_RUNTIME') or ROOT / 'Runtime/Video')
FFMPEG, FFPROBE = ffmpeg_paths(RUNTIME)

CAPS = dict(keyframes=True, music=[dict(id='ace-step-15-turbo', modes=['instrumental', 'song'], max_duration=180, about='Songs.'),
                                   dict(id='stable-audio-3-medium', modes=['audio'], max_duration=120, about='Sounds.')])
REQUEST = dict(prompt='A road trip in the desert, western music.', seconds=30, scenes=2)
PROMPT = 'Photorealistic western film, warm light. Wide shot of a red car on a desert road, a woman in a hat drives; tires hiss on sand; wind and a distant guitar.'


def scene(**values):
    return dict(dict(seconds=15, prompt=PROMPT, references=[1], keyframe=None, transition='fade'), **values)


def materials(*pictures, songs=None, voices=None):
    """A `Materials` of pictures with these numbers, songs `{number: seconds}` and voices `{number: group}`."""
    rows = [dict(number=n, kind='photo', group='hero') for n in pictures]
    rows += [dict(number=n, kind='audio', group='song', seconds=s) for n, s in (songs or {}).items()]
    rows += [dict(number=n, kind='voice', group=g, seconds=10.0) for n, g in (voices or {}).items()]
    return plan.Materials(rows)


class FitTests(unittest.TestCase):
    def test_scene_lengths_always_add_up_to_the_film(self):
        for values, total in (([10, 20], 30), ([1, 1, 1], 60), ([100, 100], 90), ([7.3, 2.2, 9.9, 30], 60), ([3, 3], 5), ([60, 60, 60, 60, 60], 300), ([2, 2, 2, 2, 2, 2, 2, 2, 2, 2], 60)):
            with self.subTest(values=values, total=total):
                result = plan.fit_seconds(values, total)
                self.assertAlmostEqual(sum(result), total, places=1)
                self.assertTrue(all(2.0 <= value <= 60.0 for value in result))


class NormalizeTests(unittest.TestCase):
    def raw(self, **values):
        return dict(dict(title='Road', summary='Two scenes.', orientation='portrait', quality='max', style=dict(phrase='35mm film', warmth=5, contrast=0.1, saturation='x'),
                         music=dict(engine='ace-step-15-turbo', mode='song', caption='western, slow, deep female voice', lyrics='la la', language='en', gain_db=-3),
                         scenes=[scene(seconds=10, transition='wipeleft'), scene(seconds=40, references=[1, 9, 'x', True], keyframe='A woman by a red car', transition='bogus')]), **values)

    def test_a_good_plan_is_repaired_into_the_limits(self):
        result = plan.normalize(self.raw(), REQUEST, CAPS, materials(1, 2))
        self.assertEqual([row['seconds'] for row in result['scenes']], [6.0, 24.0])             # 10:40 scaled to 30 s
        self.assertEqual(result['scenes'][0]['transition'], 'wipeleft')
        self.assertEqual(result['scenes'][1]['transition'], 'cut')                                # nothing follows the last scene
        self.assertEqual(result['scenes'][1]['references'], [1])                                  # 9, 'x' and True are not pictures
        self.assertEqual(result['scenes'][1]['keyframe'], 'A woman by a red car')
        self.assertEqual((result['orientation'], result['quality']), ('portrait', 'max'))
        self.assertEqual(result['style'], dict(phrase='35mm film', warmth=1.0, contrast=0.8, saturation=1.0))
        self.assertEqual((result['music']['gain_db'], result['music']['seconds'], result['music']['loop']), (-6.0, 30, False))

    def test_a_scene_with_several_pictures_gets_a_keyframe_when_gemma_wrote_none(self):
        rows = [dict(number=1, kind='photo', group='hero'), dict(number=2, kind='photo', group='hero'), dict(number=3, kind='photo', group='place'), dict(number=4, kind='photo', group='prop')]
        shown = plan.Materials(rows)
        raw = self.raw(scenes=[scene(seconds=15, prompt='Wide shot of a harbour at dawn, fog on the water. Boats creak.', references=[1, 2, 3, 4]),
                               scene(seconds=15, references=[3, 4], keyframe='Own words about picture 1 and picture 2'), scene(seconds=15, references=[3]), scene(seconds=15, references=[])])
        result = plan.normalize(raw, dict(REQUEST, scenes=4, seconds=60), CAPS, shown)['scenes']
        self.assertEqual(result[0]['keyframe'], 'Show the person from picture 1, the person from picture 2 and the object from picture 4 in the place from picture 3. Wide shot of a harbour at dawn, fog on the water.')
        self.assertEqual(result[1]['keyframe'], 'Own words about picture 1 and picture 2')            # what Gemma wrote is never replaced
        self.assertEqual((result[2]['keyframe'], result[3]['keyframe']), (None, None))                  # one picture already is the first frame, none needs none
        without = plan.normalize(raw, dict(REQUEST, scenes=4, seconds=60), dict(CAPS, keyframes=False), shown)['scenes']
        self.assertEqual([row['keyframe'] for row in without], [None] * 4)                              # Klein cannot compose without Klein
        places = plan.default_keyframe('x' * 900, [3, 3], shown)
        self.assertTrue(places.startswith('Show the scene in the place from picture 3 and the place from picture 3.') and len(places) <= 600)

    def test_the_plan_keeps_the_numbers_of_the_draft_and_the_picture_model_gets_the_places(self):
        shown = plan.Materials([dict(number=n, kind='photo', group='hero') for n in (3, 4, 5, 7)])
        text = 'The woman from Picture 4 stands next to the man from picture 5 by the water of image 3, not picture 9 or pictures 7.'
        raw = self.raw(scenes=[scene(seconds=15, references=[4, 5, 3], keyframe=text), scene(seconds=15, references=[7], keyframe='The car from picture 7 at dawn')])
        result = plan.normalize(raw, REQUEST, CAPS, shown)
        self.assertEqual([row['keyframe'] for row in result['scenes']], [text, 'The car from picture 7 at dawn'])             # what the person reads next to "Pictures: #4, #5, #3"
        self.assertEqual(plan.normalize(result, REQUEST, CAPS, shown), result)                                                   # a plan goes through the checks again when the film starts: nothing changes
        self.assertEqual(plan.renumber_pictures(text, [4, 5, 3]), 'The woman from Picture 1 stands next to the man from picture 2 by the water of image 3, not picture 9 or pictures 7.')
        self.assertEqual(plan.renumber_pictures('The car from picture 7 at dawn', [7]), 'The car from picture 1 at dawn')
        sample = 'the place from picture 3, the person from picture 4 and the person from picture 5'
        once = plan.renumber_pictures(sample, [3, 4, 5])
        self.assertEqual(once, 'the place from picture 1, the person from picture 2 and the person from picture 3')
        self.assertNotEqual(plan.renumber_pictures(once, [3, 4, 5]), once)                                                       # which is why it is done once, at the moment the picture model is asked

    def test_a_default_keyframe_names_the_pictures_by_their_numbers_in_the_draft(self):
        rows = [dict(number=3, kind='photo', group='place'), dict(number=4, kind='photo', group='hero'), dict(number=5, kind='photo', group='hero'), dict(number=7, kind='photo', group='prop')]
        shown = plan.Materials(rows)
        self.assertEqual(plan.default_keyframe('Wide shot of a harbour. Boats creak.', [3, 4, 5, 7], shown),
                         'Show the person from picture 4, the person from picture 5 and the object from picture 7 in the place from picture 3. Wide shot of a harbour.')
        raw = self.raw(scenes=[scene(seconds=15, references=[3, 4, 5], keyframe=None), scene(seconds=15, references=[7], keyframe=None)])
        once = plan.normalize(raw, REQUEST, CAPS, shown)
        self.assertIn('picture 4', once['scenes'][0]['keyframe'])
        self.assertEqual(plan.normalize(once, REQUEST, CAPS, shown), once)

    def test_a_black_and_white_grade_survives_and_only_nonsense_is_repaired(self):
        for asked, kept in ((0, 0.0), (0.0, 0.0), (-3, 0.0), (0.5, 0.5), (9, 1.4), ('x', 1.0), (None, 1.0), (True, 1.0)):
            with self.subTest(asked=asked):
                result = plan.normalize(self.raw(style=dict(phrase='black and white newsreel', saturation=asked)), REQUEST, CAPS, materials(1, 2))
                self.assertEqual(result['style']['saturation'], kept)

    def test_the_page_choice_wins_and_a_missing_ability_removes_what_needs_it(self):
        request = dict(REQUEST, orientation='landscape', quality='draft')
        result = plan.normalize(self.raw(), request, dict(keyframes=False, music=[]), materials(1))
        self.assertEqual((result['orientation'], result['quality'], result['music']), ('landscape', 'draft', None))
        self.assertIsNone(result['scenes'][1]['keyframe'])

    def test_music_is_repaired_or_dropped(self):
        engine = dict(CAPS)
        silent = plan.normalize(self.raw(music=None), REQUEST, engine, materials(1))
        self.assertIsNone(silent['music'])
        unknown = plan.normalize(self.raw(music=dict(engine='nope', mode='instrumental', caption='slow guitar')), REQUEST, engine, materials(1))
        self.assertEqual((unknown['music']['engine'], unknown['music']['mode']), ('ace-step-15-turbo', 'instrumental'))
        no_words = plan.normalize(self.raw(music=dict(engine='ace-step-15-turbo', mode='song', caption='slow guitar', lyrics='')), REQUEST, engine, materials(1))
        self.assertEqual(no_words['music']['mode'], 'instrumental')
        long_film = plan.normalize(self.raw(scenes=[scene(seconds=100), scene(seconds=100), scene(seconds=100)]), dict(REQUEST, seconds=300, scenes=3), engine, materials(1))
        self.assertEqual((long_film['music']['seconds'], long_film['music']['loop']), (180, True))
        self.assertIsNone(plan.normalize(self.raw(music=dict(engine='ace-step-15-turbo', mode='song', caption='x')), REQUEST, engine, materials(1))['music'])
        # Gemma once described the track in fields of its own (found by the first real film of 2026-11-01): the words are put together and the track is kept
        own = plan.normalize(self.raw(music=dict(genre='orchestral', mood='anxious, intense', instruments='staccato strings, brass', tempo='fast', voice=None, gain_db=-12, start=0)),
                             REQUEST, engine, materials(1))['music']
        self.assertEqual((own['caption'], own['engine'], own['mode'], own['gain_db']), ('orchestral, anxious, intense, staccato strings, brass, fast', 'ace-step-15-turbo', 'instrumental', -12.0))
        for odd in (dict(engine=['x'], mode={'a': 1}, caption='slow guitar', language=['en']), dict(engine=None, mode=5, caption='slow guitar')):
            self.assertEqual(plan.normalize(self.raw(music=odd), REQUEST, engine, materials(1))['music']['engine'], 'ace-step-15-turbo')       # a value of any type is no reason to fail

    def test_what_cannot_be_repaired_is_named(self):
        for raw, fragment in ((dict(scenes=[scene()]), '2'), (dict(scenes='x'), '0'), (dict(scenes=[scene(), 'x']), '2'), (dict(scenes=[scene(), scene(prompt='short')]), '2')):
            with self.subTest(raw=raw), self.assertRaises(ValueError) as caught:
                plan.normalize(self.raw(**raw), REQUEST, CAPS, materials(1))
            self.assertIn(fragment, str(caught.exception))

    def test_an_answer_is_read_from_a_fence_or_a_sentence(self):
        self.assertEqual(plan.read_object('Here it is:\n```json\n{"a": 1}\n```'), dict(a=1))
        for bad in ('no json', '{broken', '[1, 2]', ''):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                plan.read_object(bad)
        self.assertEqual(brain.read_array('```\n[{"number": 1}, 5]\n```'), [dict(number=1)])


def png(color, size=(320, 200)):
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, 'PNG')
    return buffer.getvalue()


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.draft = media.open_draft(self.temp.name, media.new_draft_id(), 'ffmpeg', 'ffprobe')

    def brain(self, answers, log=None):
        text = FakeText(answers)
        return brain.DirectorBrain(text, FakeGpu(), log=log), text

    def test_what_gemma_answered_is_kept_for_the_person_who_asks_why(self):
        log = Path(self.temp.name) / 'logs/Director/plans.log'
        wrong, good = json.dumps(dict(scenes=[scene()])), json.dumps(dict(scenes=[scene(), scene()], music=dict(engine='ace-step-15-turbo', caption='slow guitar')))
        director, _ = self.brain([wrong, good], log)
        director.make_plan(self.draft, dict(REQUEST), CAPS, 'English')
        rows = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]['kept'])                                                        # the first answer was refused and the log says why
        self.assertIn('2', rows[0]['problem'])
        self.assertEqual((rows[1]['problem'], rows[1]['kept']['music']), (None, True))
        self.assertEqual(json.loads(rows[1]['answer'])['music']['caption'], 'slow guitar')        # what Gemma wrote, not only what was kept
        self.assertEqual((rows[1]['seconds'], rows[1]['scenes'], rows[1]['prompt']), (30, 2, REQUEST['prompt']))

    def test_the_log_stays_small_and_never_fails_a_plan(self):
        log = Path(self.temp.name) / 'plans.log'
        good = json.dumps(dict(scenes=[scene(), scene()]))
        director, _ = self.brain([good] * 30, log)
        director.LOG_LIMIT = 6000
        for _ in range(30):
            director.make_plan(self.draft, dict(REQUEST), CAPS, 'English')
        self.assertLess(log.stat().st_size, 6000 + 2500)
        lines = log.read_text(encoding='utf-8').splitlines()
        self.assertTrue(lines and all(json.loads(line)['kept'] for line in lines))                # it was cut between lines, never in the middle of one
        blocked = Path(self.temp.name) / 'a file'
        blocked.write_text('x', encoding='utf-8')
        director, _ = self.brain([good], blocked / 'plans.log')                                   # a log that cannot be written
        self.assertEqual(len(director.make_plan(self.draft, dict(REQUEST), CAPS, 'English')['scenes']), 2)

    def test_pictures_are_described_once_in_batches_and_then_the_plan_is_written(self):
        for index in range(5):
            self.draft.add_photo(png((40 * index, 90, 160)), 'p%d.png' % index)
        look_first = json.dumps([dict(number=n, what='picture %d' % n, role='character') for n in (1, 2, 3, 4)])
        look_second = json.dumps([dict(number=5, what='a red car', role='vehicle')])
        good = json.dumps(dict(title='Road', summary='Two scenes.', scenes=[scene(), scene(references=[5])]))
        director, text = self.brain([look_first, look_second, good])
        result = director.make_plan(self.draft, dict(REQUEST), CAPS, 'Russian')
        self.assertEqual(len(text.calls), 3)
        self.assertEqual(len(text.calls[0]['messages'][1]['images']), 4)
        self.assertEqual(len(text.calls[1]['messages'][1]['images']), 1)
        self.assertEqual(self.draft.captions()['5'], dict(what='a red car', role='vehicle'))
        listing = text.calls[2]['messages'][1]['content']
        self.assertIn('Pictures of the HEROES', listing)
        self.assertIn('5. a red car (photo 320x200)', listing)
        self.assertIn('exactly 2 scenes', listing)
        self.assertIn('Russian', text.calls[2]['messages'][0]['content'])
        self.assertIn('FLUX.3 Klein', text.calls[2]['messages'][0]['content'])
        self.assertFalse(text.calls[2]['think'])
        system = text.calls[2]['messages'][0]['content']
        self.assertIn('"caption"', system)                                                           # the music object is spelled out for Gemma, field by field
        self.assertIn('"music": {"engine": "...", "mode": "instrumental", "caption": "..."', system)
        self.assertEqual([row['references'] for row in result['scenes']], [[1], [5]])
        self.assertEqual(text.owners, [('begin', 'video-director'), ('end', 'video-director')])
        # a second plan for the same materials looks at nothing new
        director, text = self.brain([good])
        director.make_plan(self.draft, dict(REQUEST), CAPS, 'English')
        self.assertEqual(len(text.calls), 1)
        silent, text = self.brain([good])
        silent.make_plan(self.draft, dict(REQUEST), dict(keyframes=False, music=[]), 'English')
        self.assertIn('"music": null,', text.calls[0]['messages'][0]['content'])                    # no music engine: the shape says so

    def test_a_wrong_answer_is_sent_back_once_and_a_second_failure_is_reported(self):
        wrong = json.dumps(dict(scenes=[scene()]))
        good = json.dumps(dict(scenes=[scene(), scene()]))
        director, text = self.brain([wrong, good])
        self.assertEqual(len(director.make_plan(self.draft, dict(REQUEST), CAPS, 'English')['scenes']), 2)
        self.assertIn('Exactly 2', text.calls[1]['messages'][-1]['content'].replace('Нужно ровно', 'Exactly'))
        director, _ = self.brain([wrong, wrong])
        with self.assertRaises(ValueError):
            director.make_plan(self.draft, dict(REQUEST), CAPS, 'English')

    def test_a_picture_gemma_cannot_describe_still_gets_a_plain_caption(self):
        self.draft.add_photo(png((10, 20, 30)), 'x.png')
        director, _ = self.brain(['I cannot help with that.', json.dumps(dict(scenes=[scene(), scene()]))])
        director.make_plan(self.draft, dict(REQUEST), CAPS, 'English')
        self.assertEqual(self.draft.captions()['1'], dict(what='a photo', role='other'))

    def test_a_request_that_breaks_the_rules_never_reaches_gemma(self):
        director, text = self.brain([])
        with self.assertRaises(ValueError):
            director.make_plan(self.draft, dict(REQUEST, scenes=3), CAPS, 'English')
        self.assertEqual(text.calls, [])


SPEECH_CAPS = dict(CAPS, speech=dict(engine='voxcpm2', languages=['Russian', 'English']))
LONG_TEXT = 'Это история о дороге. Она началась на рассвете и закончилась на закате. Никто не помнил, куда они ехали, но все помнили, зачем.'


class SongAndSpeechPlanTests(unittest.TestCase):
    """What the plan may say about the person's songs and voices: only what exists, inside the film, and short enough to be spoken."""

    def raw(self, **values):
        return dict(dict(title='Road', scenes=[scene(seconds=15), scene(seconds=15)]), **values)

    def test_a_song_excerpt_never_runs_past_the_song_or_the_film(self):
        songs = {1: 160.0, 2: 20.0}
        found = plan.normalize(self.raw(songs=[dict(song=1, offset=150, start=0, seconds=50, gain_db=5, fade_in=9, fade_out=99), dict(song=7, offset=0, start=0, seconds=5), 'x',
                                                dict(song=True, offset=0, start=0, seconds=5)]), REQUEST, CAPS, materials(1, songs=songs))['songs']
        self.assertEqual(len(found), 1)                                              # song 7 does not exist, True is not a number, 'x' is not a row
        cue = found[0]
        self.assertEqual((cue['offset'], cue['seconds'], cue['gain_db']), (150.0, 10.0, 0.0))                      # 160 - 150 s of the song are left, the gain stops at 0 dB
        self.assertLessEqual(cue['fade_in'] + cue['fade_out'], cue['seconds'])
        later = plan.normalize(self.raw(songs=[dict(song=2, offset=0, start=25, seconds=20)]), REQUEST, CAPS, materials(songs=songs))['songs'][0]
        self.assertEqual((later['start'], later['seconds']), (25.0, 5.0))                                            # the film ends at 30 s
        end = plan.normalize(self.raw(songs=[dict(song=2, offset=19, start=0, seconds=20)]), REQUEST, CAPS, materials(songs=songs))['songs'][0]
        self.assertEqual((end['offset'], end['seconds']), (18.0, 2.0))                                              # the offset moves back so that two seconds of the song are left

    def test_excerpts_follow_each_other_with_a_crossfade_and_come_in_time_order(self):
        cues = plan.normalize(self.raw(songs=[dict(song=2, offset=0, start=12, seconds=18), dict(song=1, offset=0, start=0, seconds=20)]), REQUEST, CAPS,
                              materials(songs={1: 60.0, 2: 60.0}))['songs']
        self.assertEqual([cue['song'] for cue in cues], [1, 2])
        self.assertEqual(cues[0]['seconds'], 14.0)                                                                   # up to the start of the next one and the 2 s crossfade
        many = [dict(song=1, offset=0, start=index * 3, seconds=3) for index in range(12)]
        self.assertEqual(len(plan.normalize(self.raw(songs=many), REQUEST, CAPS, materials(songs={1: 60.0}))['songs']), 8)

    def test_without_songs_the_plan_has_none(self):
        self.assertEqual(plan.normalize(self.raw(songs=[dict(song=1, offset=0, start=0, seconds=5)]), REQUEST, CAPS, materials(1))['songs'], [])

    def test_a_line_is_cut_to_what_its_scene_can_say(self):
        voices = {3: 'narrator', 4: 'character'}
        lines = [dict(voice=3, scene=1, text=LONG_TEXT, language='Russian'), dict(voice=4, scene=1, text='Поехали!', language='Klingon'), dict(voice=9, scene=1, text='Нет такого голоса.'),
                 dict(voice=3, scene=7, text='Нет такой сцены.'), dict(voice=3, scene=2, text='Конец дороги.', delay=99, gain_db=50), 'x']
        found = plan.normalize(self.raw(speech=lines), REQUEST, SPEECH_CAPS, materials(1, voices=voices))
        speech = found['speech']
        self.assertEqual([(row['voice'], row['scene']) for row in speech], [(3, 1), (4, 1), (3, 2)])
        self.assertLessEqual(len(speech[0]['text']) + len(speech[1]['text']), 15 * 12)                               # 12 characters a second of a 15 s scene
        self.assertEqual(speech[1]['language'], 'Russian')                                                           # an unknown language is the film's own
        self.assertEqual((speech[2]['delay'], speech[2]['gain_db']), (14.0, 6.0))
        self.assertEqual(found['notes'], [])

    def test_a_line_that_is_too_long_ends_with_a_whole_sentence(self):
        speech = plan.normalize(self.raw(speech=[dict(voice=3, scene=1, text=LONG_TEXT * 3)]), REQUEST, SPEECH_CAPS, materials(1, voices={3: 'narrator'}))['speech']
        self.assertEqual(len(speech), 1)
        self.assertLessEqual(len(speech[0]['text']), 15 * 12)
        self.assertTrue(speech[0]['text'].endswith(('.', '!', '?')))

    def test_a_scene_takes_three_lines_and_no_more(self):
        lines = [dict(voice=3, scene=1, text='Слово номер %d, и ещё слово.' % index) for index in range(6)]
        speech = plan.normalize(self.raw(speech=lines), REQUEST, SPEECH_CAPS, materials(1, voices={3: 'narrator'}))['speech']
        self.assertEqual(len(speech), 3)

    def test_voices_without_an_engine_or_without_lines_are_explained(self):
        voices = materials(1, voices={3: 'narrator'})
        no_engine = plan.normalize(self.raw(speech=[dict(voice=3, scene=1, text='Привет, мир.')]), REQUEST, CAPS, voices)
        self.assertEqual(no_engine['speech'], [])
        self.assertEqual(len(no_engine['notes']), 1)
        unused = plan.normalize(self.raw(), REQUEST, SPEECH_CAPS, voices)
        self.assertEqual((unused['speech'], len(unused['notes'])), ([], 1))
        self.assertEqual(plan.normalize(self.raw(speech=[dict(voice=3, scene=1, text='Привет, мир.')]), REQUEST, SPEECH_CAPS, materials(1))['notes'], [])

    def test_the_quotes_around_a_whole_line_are_not_spoken(self):
        for written, spoken in (('"Эта история началась в порту."', 'Эта история началась в порту.'), ('«Пора в путь.»', 'Пора в путь.'), ('“Поехали, друзья.”', 'Поехали, друзья.'),
                                ('"Привет," сказал он, "пока."', '"Привет," сказал он, "пока."'), ('Он сказал «пора» и ушёл.', 'Он сказал «пора» и ушёл.'), ('"Раз два"', 'Раз два')):
            with self.subTest(written=written):
                line = plan.normalize(self.raw(speech=[dict(voice=3, scene=1, text=written)]), REQUEST, SPEECH_CAPS, materials(1, voices={3: 'narrator'}))['speech']
                self.assertEqual(line[0]['text'], spoken)

    def test_a_long_line_is_cut_at_a_sentence_then_at_a_word(self):
        self.assertEqual(plan.fit_line('Первая фраза. Вторая фраза, подлиннее. Третья.', 40), 'Первая фраза. Вторая фраза, подлиннее.')
        self.assertEqual(plan.fit_line('Одно очень длинное предложение без точек внутри него самого', 30), 'Одно очень длинное предложение')
        self.assertEqual(plan.fit_line('Коротко.', 40), 'Коротко.')
        self.assertEqual(plan.fit_line('Что-то длинное и очень красивое.', 5), '')                                    # no room left in the scene
        self.assertEqual(plan.fit_line('   ', 100), '')

    def test_the_words_follow_the_language_of_the_idea_not_of_the_page(self):
        self.assertEqual([plan.idea_language(text) for text in ('Фильм про дорогу', 'A road film', 'Фильм про road trip across the desert', 'Road trip про дорогу и ещё про закат', '', '1234 !!!')],
                         ['Russian', 'English', 'English', 'Russian', 'English', 'English'])
        voices = materials(1, voices={3: 'narrator'})
        english_page = dict(REQUEST, language='English', speech_language='Russian')                                    # a Russian idea on an English page
        lines = plan.normalize(self.raw(speech=[dict(voice=3, scene=1, text='Это начало пути.')]), english_page, SPEECH_CAPS, voices)['speech']
        self.assertEqual(lines[0]['language'], 'Russian')
        lines = plan.normalize(self.raw(speech=[dict(voice=3, scene=1, text='This is the start.', language='English')]), english_page, SPEECH_CAPS, voices)['speech']
        self.assertEqual(lines[0]['language'], 'English')                                                              # a line that names its own language keeps it
        self.assertEqual(plan.normalize(self.raw(speech=[dict(voice=3, scene=1, text='Hello there.')]), REQUEST, SPEECH_CAPS, voices)['speech'][0]['language'], 'Russian')

    def test_a_generated_track_may_start_later(self):
        raw = self.raw(music=dict(engine='ace-step-15-turbo', mode='instrumental', caption='slow guitar', start=10))
        music = plan.normalize(raw, REQUEST, CAPS, materials(1))['music']
        self.assertEqual((music['start'], music['seconds']), (10.0, 20))


class UltraProfileTests(unittest.TestCase):
    """The Ultra toggle: the best quality, a keyframe for every scene and the strongest music engine; a request without the flag is the normal profile, which is what the rest of the tests use."""
    MUSIC = [dict(id='ace-step-15-turbo', modes=['instrumental', 'song'], max_duration=180, about='x'), dict(id='minimax-music3-int8', modes=['instrumental', 'song'], max_duration=60, about='y'),
             dict(id='stable-audio-3-medium', modes=['audio'], max_duration=120, about='z')]

    def raw(self, **values):
        return dict(dict(title='Road', summary='Two scenes.', style=dict(phrase='35mm film'), scenes=[scene(seconds=15, references=[]), scene(seconds=15, references=[1])]), **values)

    def made(self, ultra, raw=None, caps=None, **request):
        return plan.normalize(raw or self.raw(), dict(REQUEST, ultra=ultra, **request), caps or dict(CAPS, music=self.MUSIC), materials(1))

    def test_ultra_gives_every_scene_a_keyframe_and_normal_only_the_scenes_that_need_one(self):
        found = self.made(True)
        self.assertTrue(found['ultra'])
        self.assertEqual(found['scenes'][0]['keyframe'], PROMPT)                                     # no picture: drawn from the scene's first sentences, which are the whole prompt here
        self.assertEqual(found['scenes'][1]['keyframe'], 'Show the person from picture 1. Photorealistic western film, warm light.')
        normal = self.made(False)
        self.assertFalse(normal['ultra'])
        self.assertEqual([row['keyframe'] for row in normal['scenes']], [None, None])
        own = self.raw(scenes=[scene(seconds=15, references=[1], keyframe='Own words about picture 1'), scene(seconds=15, references=[])])
        self.assertEqual(self.made(True, own)['scenes'][0]['keyframe'], 'Own words about picture 1')                  # what Gemma wrote is never replaced
        self.assertEqual([row['keyframe'] for row in self.made(True, caps=dict(CAPS, keyframes=False))['scenes']], [None, None])      # no picture model, no keyframes

    def test_the_quality_nobody_chose_follows_the_profile(self):
        self.assertEqual((self.made(True)['quality'], self.made(False)['quality']), ('max', 'standard'))
        self.assertEqual(self.made(True, self.raw(quality='draft'))['quality'], 'draft')                    # a draft that Gemma was asked for stays one
        self.assertEqual(self.made(True, quality='standard')['quality'], 'standard')                        # the person's own choice on the page wins
        self.assertEqual(self.made(False, quality='max')['quality'], 'max')
        self.assertEqual(self.made(True, self.raw(quality='nonsense'))['quality'], 'max')

    def test_only_a_real_true_is_ultra(self):
        for flag in ('yes', 1, 'true', None, [True]):
            with self.subTest(flag=flag):
                self.assertFalse(self.made(flag)['ultra'])

    def test_the_music_engine_gemma_did_not_name_is_the_first_choice_of_the_profile(self):
        raw = self.raw(music=dict(engine='nope', mode='instrumental', caption='slow guitar'))
        self.assertEqual(self.made(True, raw)['music']['engine'], 'minimax-music3-int8')
        self.assertEqual(self.made(False, raw)['music']['engine'], 'ace-step-15-turbo')
        long_film = plan.normalize(raw, dict(REQUEST, ultra=True, seconds=90, scenes=2), dict(CAPS, music=self.MUSIC), materials(1))
        self.assertEqual(long_film['music']['engine'], 'ace-step-15-turbo')                                 # a track of 60 s would have to loop under 90 s of film
        named = self.raw(music=dict(engine='stable-audio-3-medium', mode='audio', caption='rain'))
        self.assertEqual(self.made(True, named)['music']['engine'], 'stable-audio-3-medium')                # a valid choice of Gemma is kept in both modes

    def test_a_keyframe_without_pictures_is_made_of_the_first_two_sentences(self):
        shown = plan.Materials([])
        self.assertEqual(plan.default_keyframe('One. Two! Three four.', [], shown), 'One. Two.')
        self.assertEqual(plan.default_keyframe('A single sentence without an end', [], shown), 'A single sentence without an end.')
        self.assertTrue(len(plan.default_keyframe('x' * 2000, [], shown)) <= 600)


@unittest.skipUnless(FFMPEG.is_file() and FFPROBE.is_file(), 'bundled FFmpeg not found')
class ModePromptTests(unittest.TestCase):
    """Gemma is told which mode the person chose, and the engines are listed in the order that mode prefers."""

    def test_the_mode_is_told_to_gemma_and_orders_the_music_engines(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = media.open_draft(folder, media.new_draft_id(), FFMPEG, FFPROBE)
            caps = dict(CAPS, music=UltraProfileTests.MUSIC)
            answer = json.dumps(dict(scenes=[scene(), scene()]))
            for ultra, present, absent, first in ((True, 'QUALITY MODE "ultra"', 'QUALITY MODE "normal"', 'minimax-music3-int8'),
                                                  (False, 'QUALITY MODE "normal"', 'QUALITY MODE "ultra"', 'ace-step-15-turbo'), (None, 'QUALITY MODE "normal"', 'QUALITY MODE "ultra"', 'ace-step-15-turbo')):
                with self.subTest(ultra=ultra):
                    text = FakeText([answer])
                    brain.DirectorBrain(text, FakeGpu()).make_plan(draft, dict(REQUEST, ultra=ultra), caps, 'Russian')
                    system = text.calls[0]['messages'][0]['content']
                    self.assertIn(present, system)
                    self.assertNotIn(absent, system)
                    self.assertEqual('Give every scene a "keyframe".' in system, ultra is True)
                    self.assertLess(system.index('Music "%s"' % first), min(system.index('Music "%s"' % row['id']) for row in UltraProfileTests.MUSIC if row['id'] != first))


class ListingTests(unittest.TestCase):
    def items(self):
        return [dict(number=1, kind='photo', group='hero', label='Alex', width=640, height=480), dict(number=2, kind='photo', group='place', label='', width=1000, height=500),
                dict(number=3, kind='photo', group='prop', label='', width=300, height=300),
                dict(number=4, kind='video', group='video', label='', seconds=12.0, width=640, height=360, facts={}),
                dict(number=5, kind='audio', group='song', label='Main song', seconds=192.0, facts=dict(mean_db=-14.2, stretches=[[0.0, 24.0, 'quiet'], [24.0, 192.0, 'loud']])),
                dict(number=6, kind='voice', group='narrator', label='', seconds=12.0, facts={}), dict(number=7, kind='voice', group='narrator', label='', seconds=9.0, facts={}),
                dict(number=8, kind='voice', group='character', label='Alex', seconds=8.0, facts={})]

    def test_every_group_is_named_and_the_person_s_words_are_kept(self):
        captions = {'1': dict(what='a tall man'), '2': dict(what='a harbour'), '3': dict(what='a red car'), '4': dict(what='a chase', style='fast cuts')}
        text = brain.DirectorBrain(None, None).listing(self.items(), captions)
        for expected in ('Pictures of the HEROES', '1 [Alex]. a tall man (photo 640x480)', 'Pictures of PLACES', '2. a harbour (photo 1000x500)', 'Pictures of OBJECTS', '3. a red car',
                         'Reference VIDEOS', '4. a chase Style: fast cuts', 'SONGS of the person', '5 [Main song]. song, 3:12 long, average level -14 dB, by time: quiet 0:00-0:24, loud 0:24-3:12',
                         'VOICES', '6. Narrator (voice sample, 12 s)', '7. Narrator 2 (voice sample, 9 s)', '8 [Alex]. Main character (voice sample, 8 s)'):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)
        self.assertIn('alone', brain.DirectorBrain(None, None).listing([], {}))

    def test_slots_are_named_by_their_place_in_the_group(self):
        names = brain.slot_names(self.items() + [dict(number=9, kind='voice', group='character'), dict(number=10, kind='voice', group='character')])
        self.assertEqual([names[n] for n in (6, 7, 8, 9, 10)], ['Narrator', 'Narrator 2', 'Main character', 'Second main character', 'Character 3'])
        self.assertEqual(names[1], 'Hero 1')


@unittest.skipUnless(FFMPEG.is_file() and FFPROBE.is_file(), 'bundled FFmpeg not found')
class MaterialsPromptTests(unittest.TestCase):
    """The planning call tells Gemma about songs and voices only when there are some (a prompt without them must not invite invented numbers)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.draft = media.open_draft(self.temp.name, media.new_draft_id(), FFMPEG, FFPROBE)

    def sound(self, name, seconds, source='sine=frequency=440'):
        path = Path(self.temp.name) / name
        subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', '%s:duration=%s' % (source, seconds), str(path)], check=True)
        return path

    def brain(self, answers):
        text = FakeText(answers)
        return brain.DirectorBrain(text, FakeGpu()), text

    def test_songs_and_voices_enter_the_prompt_and_the_plan(self):
        self.draft.add_song(self.sound('song.wav', 30), 'song.wav')
        self.draft.add_voice(self.sound('voice.wav', 6, 'anoisesrc=color=pink:amplitude=0.3'), 'voice.wav', 'narrator')
        answer = json.dumps(dict(scenes=[scene(), scene()], songs=[dict(song=1, offset=0, start=0, seconds=20)], speech=[dict(voice=2, scene=1, text='Это начало пути.')]))
        director, text = self.brain([answer])
        result = director.make_plan(self.draft, dict(REQUEST), SPEECH_CAPS, 'Russian')
        system, listing = text.calls[0]['messages'][0]['content'], text.calls[0]['messages'][1]['content']
        for expected in ('attached SONGS', '"songs": []', 'attached VOICES', '"speech": []', 'Speech: the attached voices'):
            with self.subTest(expected=expected):
                self.assertIn(expected, system)
        self.assertIn('The idea is written in English', listing)                                                          # the words of the voices follow the idea
        self.assertIn('1. song, 0:30 long', listing)
        self.assertIn('2. Narrator (voice sample', listing)
        self.assertEqual((len(result['songs']), len(result['speech'])), (1, 1))

    def test_a_film_with_neither_gets_a_prompt_with_neither(self):
        director, text = self.brain([json.dumps(dict(scenes=[scene(), scene()]))])
        director.make_plan(self.draft, dict(REQUEST), SPEECH_CAPS, 'Russian')
        system = text.calls[0]['messages'][0]['content']
        for absent in ('SONGS', '"songs"', 'VOICES', '"speech"', 'Speech:'):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, system)

    def test_voices_without_a_speech_engine_are_not_offered_to_gemma(self):
        self.draft.add_voice(self.sound('voice.wav', 6, 'anoisesrc=color=pink:amplitude=0.3'), 'voice.wav', 'narrator')
        director, text = self.brain([json.dumps(dict(scenes=[scene(), scene()]))])
        result = director.make_plan(self.draft, dict(REQUEST), CAPS, 'Russian')
        self.assertNotIn('"speech"', text.calls[0]['messages'][0]['content'])
        self.assertEqual((result['speech'], len(result['notes'])), ([], 1))


if __name__ == '__main__':
    unittest.main()
