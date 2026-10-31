import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))

from video import ltx_scenes  # noqa: E402
from video.speech_check import heard_share, reseed, spoken_lines  # noqa: E402

RUSSIAN = 'A woman looks into the lens and says in Russian, "Сегодня я покажу, как починить старые часы." The room ticks.'
ENGLISH = 'He grins and says, "Hello everyone, welcome back." A sign reads "OPEN NOW" behind him.'


class LineTests(unittest.TestCase):
    def test_only_quoted_words_that_somebody_says_are_speech(self):
        self.assertEqual(spoken_lines(RUSSIAN), [('Сегодня я покажу, как починить старые часы.', 'ru')])
        self.assertEqual(spoken_lines(ENGLISH), [('Hello everyone, welcome back.', 'en')])      # the sign is not speech
        self.assertEqual(spoken_lines('He concludes in Russian, "...и не забудьте посолить." He nods.')[0][0], '...и не забудьте посолить.')
        self.assertEqual(spoken_lines('He continues the sentence: "and then we wait for an hour."')[0][1], 'en')
        self.assertEqual(spoken_lines('She says "Hi".'), [])                                  # one word is not worth a check
        self.assertEqual(spoken_lines('The camera pushes in on a "STOP" sign.'), [])
        self.assertEqual(spoken_lines('Он говорит «Привет, друзья!» и машет рукой.'), [])      # no English verb: the planner and the rewrite write English
        self.assertEqual(spoken_lines('He says in German, "Guten Morgen, meine Freunde."')[0][1], 'de')

    def test_the_share_heard_ignores_case_punctuation_yo_and_extra_words(self):
        line = 'Сегодня я покажу, как починить старые часы.'
        self.assertEqual(heard_share(line, 'сегодня я покажу как починить старые часы'), 1.0)
        self.assertGreaterEqual(heard_share(line, 'Ну вот. Сегодня я покажу, как починить старые часы. Спасибо.'), 0.99)
        self.assertGreaterEqual(heard_share('Всё ещё идёт', 'все еще идет'), 0.99)
        self.assertLess(heard_share(line, 'and it is so encased and what with the real stuff'), 0.5)
        self.assertEqual(heard_share(line, ''), 0.0)
        self.assertEqual(heard_share('', 'anything'), 1.0)

    def test_a_retry_changes_the_seed_but_keeps_its_type(self):
        self.assertEqual(reseed('11', 1), '7930')
        self.assertEqual(reseed(11, 2), 11+2*7919)
        self.assertNotEqual(reseed('11', 1), reseed('11', 2))


class FakeListener:
    heard = {}
    started = 0

    def __init__(self, *args):
        pass

    def available(self):
        return True

    def start(self):
        FakeListener.started += 1

    def hear(self, sound, language, seconds):
        return FakeListener.heard[Path(sound).parent.name]

    def stop(self):
        pass


class RepairTests(unittest.TestCase):
    def run_repair(self, prompts, heard, available=True):
        FakeListener.heard, FakeListener.started = heard, 0
        made = []
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            outputs = [folder/('scene-%02d' % (index+1)) for index in range(len(prompts))]

            def make(index, attempt=0):
                made.append((index, attempt))
                return folder/('scene-%02d-try%d' % (index+1, attempt))
            listener = FakeListener
            if not available:
                listener = type('Off', (FakeListener,), {'available': lambda self: False})
            with patch.object(ltx_scenes, 'Listener', listener), patch.object(ltx_scenes, 'event'):
                result = ltx_scenes.repair_speech(folder/'Runtime/Video', folder/'ffmpeg.exe', folder, dict(fps=24), prompts, outputs, [90]*len(prompts), make)
            return [path.name for path in result], made

    def test_a_scene_that_does_not_say_its_line_is_generated_again_until_it_does(self):
        prompts = ['The shot is silent.', RUSSIAN, 'Waves.']
        said = 'сегодня я покажу как починить старые часы'
        names, made = self.run_repair(prompts, {'scene-02': 'бла бла', 'scene-02-try1': 'не то', 'scene-02-try2': said})
        self.assertEqual(made, [(1, 1), (1, 2)])
        self.assertEqual(names, ['scene-01', 'scene-02-try2', 'scene-03'])

    def test_good_scenes_are_not_touched_and_the_best_try_wins_when_none_is_good(self):
        said = 'сегодня я покажу как починить старые часы'
        names, made = self.run_repair([RUSSIAN], {'scene-01': said})
        self.assertEqual((made, names), ([], ['scene-01']))
        names, made = self.run_repair([RUSSIAN], {'scene-01': 'сегодня я покажу как', 'scene-01-try1': 'ничего', 'scene-01-try2': 'ага'})
        self.assertEqual(made, [(0, 1), (0, 2)])
        self.assertEqual(names, ['scene-01'])                                      # the first take was the closest

    def test_a_line_too_long_for_its_scene_is_not_retried(self):
        long_line = ' '.join(['слово'] * 30)
        prompt = 'She looks into the lens and says in Russian, "%s"' % long_line
        names, made = self.run_repair([prompt], {'scene-01': 'слово слово'})
        self.assertEqual((made, names), ([], ['scene-01']))                       # 30 words in 3.75 s: no seed can help

    def test_without_speech_lines_or_a_speech_model_nothing_is_done(self):
        names, made = self.run_repair(['A fox.', 'An owl.'], {})
        self.assertEqual((made, names, FakeListener.started), ([], ['scene-01', 'scene-02'], 0))
        names, made = self.run_repair([RUSSIAN], {}, available=False)
        self.assertEqual((made, names), ([], ['scene-01']))

    def test_a_failing_listener_never_costs_the_clip(self):
        class Broken(FakeListener):
            def hear(self, sound, language, seconds):
                raise RuntimeError('the worker died')
        with tempfile.TemporaryDirectory() as temp, patch.object(ltx_scenes, 'Listener', Broken), patch.object(ltx_scenes, 'event'):
            folder = Path(temp)
            result = ltx_scenes.repair_speech(folder/'Runtime/Video', folder/'f.exe', folder, dict(fps=24), [RUSSIAN], [folder/'scene-01'], [90], lambda *a: None)
        self.assertEqual([path.name for path in result], ['scene-01'])

    def test_the_job_service_is_told_about_every_pass_and_the_final_scores(self):
        heard = {'scene-01': 'бла', 'scene-01-try1': 'ещё бла', 'scene-01-try2': 'сегодня я покажу как починить старые часы'}
        FakeListener.heard = heard
        events = []
        with tempfile.TemporaryDirectory() as temp, patch.object(ltx_scenes, 'Listener', FakeListener), patch.object(ltx_scenes, 'event', lambda name, **values: events.append((name, values))):
            folder = Path(temp)
            ltx_scenes.repair_speech(folder/'Runtime/Video', folder/'f.exe', folder, dict(fps=24), [RUSSIAN], [folder/'scene-01'], [90], lambda index, attempt: folder/('scene-01-try%d' % attempt))
        self.assertEqual([values['attempt'] for name, values in events if name == 'speech_check_start'], [0, 1, 2])
        name, values = events[-1]
        self.assertEqual((name, values['scores'], values['too_long']), ('speech_check_done', {'1': 1.0}, []))
        long_prompt = 'She looks into the lens and says in Russian, "%s"' % ' '.join(['слово'] * 30)
        FakeListener.heard, events[:] = {'scene-01': 'слово'}, []
        with tempfile.TemporaryDirectory() as temp, patch.object(ltx_scenes, 'Listener', FakeListener), patch.object(ltx_scenes, 'event', lambda name, **values: events.append((name, values))):
            folder = Path(temp)
            ltx_scenes.repair_speech(folder/'Runtime/Video', folder/'f.exe', folder, dict(fps=24), [long_prompt], [folder/'scene-01'], [90], lambda *a: None)
        self.assertEqual(events[-1][1]['too_long'], [1])


class ServiceEventTests(unittest.TestCase):
    """The Video service turns the worker's speech events into a phase and a per-scene result of the job."""

    def setUp(self):
        from video.video_service import Jobs
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.jobs = Jobs(argparse.Namespace(user=str(root/'User'), root=str(root/'Runtime'), models=str(root/'models'), state=str(root/'state')))
        self.row = dict(events=[])

    def test_phases_and_scores_follow_the_events(self):
        self.jobs.event(self.row, dict(event='speech_check_start', scenes=3, attempt=0))
        self.assertEqual(self.row['phase'], 'Проверка речи (Whisper)')
        self.jobs.event(self.row, dict(event='speech_check', scene=2, attempt=0, score=0.4))
        self.jobs.event(self.row, dict(event='scene_start', scene=2, scenes=3, attempt=1))
        self.assertEqual((self.row['scene'], self.row['phase']), (2, 'Сцена 2 / 3: речь не совпала, повтор 1'))
        self.jobs.event(self.row, dict(event='scene_start', scene=3, scenes=3, attempt=0))
        self.assertEqual(self.row['phase'], 'Сцена 3 / 3: подготовка')
        self.jobs.event(self.row, dict(event='speech_check_done', scores={'2': 0.97}, too_long=[1]))
        self.assertEqual((self.row['speech_scores'], self.row['speech_too_long']), ({'2': 0.97}, [1]))
        self.jobs.event(self.row, dict(event='speech_check_done', scores='broken', too_long='broken'))      # a damaged event never replaces good data
        self.assertEqual((self.row['speech_scores'], self.row['speech_too_long']), ({'2': 0.97}, [1]))


if __name__ == '__main__':
    unittest.main()
