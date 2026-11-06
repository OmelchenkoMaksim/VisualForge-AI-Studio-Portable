"""Joining scene clips with the real FFmpeg: exact length, transitions, grade, sound for clips with and without audio."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))

from director import assemble  # noqa: E402
from video.video_delivery import ffmpeg_paths  # noqa: E402

RUNTIME = Path(os.environ.get('VISUALFORGE_VIDEO_RUNTIME') or ROOT / 'Runtime/Video')
FFMPEG, FFPROBE = ffmpeg_paths(RUNTIME)
STYLE = dict(phrase='', warmth=0.4, contrast=1.1, saturation=0.9)


def clip(path, seconds, audio=True, size='640x360', pattern='testsrc2'):
    command = [str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', '%s=size=%s:rate=24:duration=%s' % (pattern, size, seconds)]
    if audio:
        command += ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=%s' % seconds, '-c:a', 'aac']
    command += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)]
    subprocess.run(command, check=True)


def probe(path):
    result = subprocess.run([str(FFPROBE), '-v', 'error', '-count_frames', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    audio = next((s for s in info['streams'] if s['codec_type'] == 'audio'), None)
    return video, audio, float(info['format']['duration'])


def colour(path):
    """The mean colour saturation (SATAVG of FFmpeg's signalstats) over the frames of a film: about 0 for a black-and-white one."""
    result = subprocess.run([str(FFMPEG), '-hide_banner', '-nostdin', '-i', str(path), '-an', '-vf', 'signalstats,metadata=print:key=lavfi.signalstats.SATAVG', '-f', 'null', '-'],
                            capture_output=True, text=True, check=True)
    values = [float(value) for value in re.findall(r'SATAVG=([0-9.]+)', result.stderr)]
    return sum(values) / len(values)


class LengthTests(unittest.TestCase):
    def test_a_clip_is_asked_for_its_scene_plus_the_overlap_of_its_transition(self):
        scenes = [dict(seconds=4.0, transition='fade'), dict(seconds=5.0, transition='cut'), dict(seconds=3.0, transition='wipeleft'), dict(seconds=2.0, transition='cut')]
        self.assertEqual(assemble.overlaps(scenes), [0.6, 0.0, 0.6, 0.0])      # a cut and the end of the film add nothing
        self.assertEqual(assemble.clip_lengths(scenes), [4.6, 5.0, 3.6, 2.0])
        self.assertEqual(assemble.grade_filters(dict(warmth=0, contrast=1.0, saturation=1.0)), [])
        self.assertEqual(len(assemble.grade_filters(STYLE)), 2)


@unittest.skipUnless(FFMPEG.is_file() and FFPROBE.is_file(), 'bundled FFmpeg not found')
class AssembleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp(prefix='vf-assemble-'))
        cls.clips = [cls.work / 'a.mp4', cls.work / 'b.mp4', cls.work / 'c.mp4']
        clip(cls.clips[0], 6)
        clip(cls.clips[1], 5, size='480x270', pattern='smptebars')
        clip(cls.clips[2], 7, audio=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def film(self, transitions, size=(320, 180), seconds=12.0, progress=None):
        scenes = [dict(seconds=4.0, transition=transitions[0]), dict(seconds=4.0, transition=transitions[1]), dict(seconds=4.0, transition='cut')]
        scenes[0]['seconds'] = scenes[1]['seconds'] = scenes[2]['seconds'] = seconds / 3
        output = self.work / ('film-%s-%dx%d.mp4' % ('-'.join(transitions), size[0], size[1]))
        result = assemble.assemble(FFMPEG, FFPROBE, self.clips, dict(scenes=scenes, seconds=seconds, style=STYLE), size, output, progress=progress)
        return output, result

    def test_the_film_has_exactly_the_planned_frames_and_sound_with_every_kind_of_hand_over(self):
        for transitions in (('fade', 'cut'), ('cut', 'cut'), ('wipeleft', 'circleopen')):
            with self.subTest(transitions=transitions):
                output, result = self.film(transitions)
                video, audio, duration = probe(output)
                self.assertEqual((video['width'], video['height'], int(video['nb_read_frames'])), (320, 180, 288))
                self.assertEqual(result['frames'], 288)
                self.assertIsNotNone(audio)                                  # the third clip has no sound: silence fills its part
                self.assertAlmostEqual(float(audio['duration']), 12.0, delta=0.06)
                self.assertAlmostEqual(duration, 12.0, delta=0.06)

    def test_a_hard_cut_followed_by_a_transition_assembles(self):
        """FFmpeg's `concat` writes its picture in the timebase 1/1000000 and `xfade` refuses two inputs of different timebases: a cut followed by any other hand over failed the whole assembly.
        It happened after a film of ten minutes in forty scenes (29 cuts, the rest dissolves and fades) had been shot for 107 minutes; the tests had only cuts after the last transition."""
        for transitions in (('cut', 'fade'), ('cut', 'wipeleft'), ('fade', 'cut')):
            with self.subTest(transitions=transitions):
                scenes = [dict(seconds=4.0, transition=name) for name in transitions] + [dict(seconds=4.0, transition='cut')]
                output = self.work / ('film-%s.mp4' % '-'.join(transitions))
                result = assemble.assemble(FFMPEG, FFPROBE, self.clips, dict(scenes=scenes, seconds=12.0, style=STYLE), (320, 180), output)
                video, audio, duration = probe(output)
                self.assertEqual((int(video['nb_read_frames']), result['frames']), (288, 288))
                self.assertAlmostEqual(float(audio['duration']), 12.0, delta=0.06)

    def test_a_long_film_of_mixed_hand_overs_assembles_with_exact_length(self):
        clips = [self.clips[index % 3] for index in range(12)]
        names = ['cut', 'fade', 'cut', 'cut', 'dissolve', 'cut', 'wipeleft', 'fade', 'cut', 'fadeblack', 'cut']
        scenes = [dict(seconds=2.0, transition=name) for name in names] + [dict(seconds=2.0, transition='cut')]
        output = self.work / 'film-mixed.mp4'
        assemble.assemble(FFMPEG, FFPROBE, clips, dict(scenes=scenes, seconds=24.0, style=STYLE), (320, 180), output)
        video, audio, duration = probe(output)
        self.assertEqual(int(video['nb_read_frames']), 576)
        self.assertAlmostEqual(float(audio['duration']), 24.0, delta=0.06)
        self.assertAlmostEqual(duration, 24.0, delta=0.06)

    def test_a_portrait_film_crops_instead_of_stretching_and_reports_progress(self):
        seen = []
        output, _ = self.film(('fade', 'fade'), size=(180, 320), progress=seen.append)
        video, _, _ = probe(output)
        self.assertEqual((video['width'], video['height']), (180, 320))
        self.assertTrue(seen and max(seen) <= 1.0)

    def test_a_saturation_of_zero_takes_all_the_colour_out(self):
        average = {}
        for saturation in (1.0, 0.5, 0.0):
            output = self.work / ('grade-%s.mp4' % saturation)
            scenes = [dict(seconds=4.0, transition='cut') for _ in range(3)]
            assemble.assemble(FFMPEG, FFPROBE, self.clips, dict(scenes=scenes, seconds=12.0, style=dict(phrase='', warmth=0.0, contrast=1.0, saturation=saturation)), (320, 180), output)
            average[saturation] = colour(output)
        self.assertGreater(average[1.0], 20)
        self.assertLess(average[0.5], average[1.0] * 0.7)
        self.assertLess(average[0.0], 2)

    def test_a_long_graph_travels_in_a_file_and_gives_the_same_film(self):
        """Windows refuses a command line over 32,767 characters and a film of sixty scenes needs a longer graph: such a graph is read from a file (`-/filter_complex`)."""
        plain, _ = self.film(('fade', 'cut'))
        with mock.patch('video.video_delivery.GRAPH_INLINE_LIMIT', 100):
            output = self.work / 'film-from-file.mp4'
            scenes = [dict(seconds=4.0, transition='fade'), dict(seconds=4.0, transition='cut'), dict(seconds=4.0, transition='cut')]
            result = assemble.assemble(FFMPEG, FFPROBE, self.clips, dict(scenes=scenes, seconds=12.0, style=STYLE), (320, 180), output)
        self.assertEqual(result['frames'], 288)
        self.assertIn('-/filter_complex', result['command'])
        self.assertNotIn('-filter_complex', result['command'])
        self.assertTrue((self.work / 'assemble-graph.txt').is_file())
        video, audio, duration = probe(output)
        self.assertEqual((video['width'], video['height'], int(video['nb_read_frames'])), (320, 180, 288))
        self.assertAlmostEqual(float(audio['duration']), 12.0, delta=0.06)
        self.assertAlmostEqual(duration, probe(plain)[2], delta=0.06)

    def test_a_film_of_seventy_scenes_has_a_graph_that_could_not_be_a_command_line_and_is_a_file(self):
        scenes = [dict(seconds=8.0, transition='fade') for _ in range(70)]
        text, frames = assemble.graph(dict(scenes=scenes, seconds=560, style=STYLE), [9.0] * 70, [True] * 70, (1280, 704))
        self.assertGreater(len(text) + 70 * 120, 32767)                                              # on the command line with seventy clip paths (120 characters each) this is over the 32,767 characters of Windows
        self.assertEqual(frames, 560 * 24)                                                           # every clip is its scene plus the 0.6 s its transition eats: the film keeps its length
        from video.video_delivery import GRAPH_INLINE_LIMIT, filter_complex
        self.assertEqual(filter_complex('short', self.work / 'unused.txt'), ['-filter_complex', 'short'])
        self.assertEqual(filter_complex('x' * (GRAPH_INLINE_LIMIT + 1), self.work / 'long.txt'), ['-/filter_complex', str(self.work / 'long.txt')])
        self.assertEqual((self.work / 'long.txt').read_text(encoding='utf-8'), 'x' * (GRAPH_INLINE_LIMIT + 1))

    def test_a_wrong_number_of_clips_is_refused(self):
        with self.assertRaises(ValueError):
            assemble.assemble(FFMPEG, FFPROBE, self.clips[:2], dict(scenes=[dict(seconds=4, transition='cut')] * 3, seconds=12, style=STYLE), (320, 180), self.work / 'x.mp4')


if __name__ == '__main__':
    unittest.main()
