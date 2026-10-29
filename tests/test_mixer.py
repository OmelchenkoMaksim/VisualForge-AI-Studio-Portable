"""Audio mixer: request/plan/filter unit checks plus short real FFmpeg mixes; no GPU, no model."""
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from video import video_mixer as mixer
from video.video_delivery import DeliveryCancelled, ffmpeg_paths, probe

RUNTIME = Path(os.environ.get('VISUALFORGE_VIDEO_RUNTIME') or str(Path(__file__).resolve().parents[1] / 'Runtime' / 'Video'))
FFMPEG, FFPROBE = ffmpeg_paths(RUNTIME)
HAVE_FFMPEG = FFMPEG.is_file() and FFPROBE.is_file()
VIDEO = dict(duration=6.0, has_audio=True)


def track(**values):
    return dict(path='x.wav', label='Music', start=0.0, gain_db=-12.0, fade_in=0.0, fade_out=0.0, loop=False) | values


def request(*tracks, native='keep', ducking=False, native_gain=0.0):
    return dict(id='job', tracks=list(tracks), ducking=ducking, native=dict(mode=native, gain_db=native_gain))


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.file = Path(tempfile.mkdtemp(prefix='vf-mixer-req-')) / 'm.wav'
        self.file.write_bytes(b'RIFF')
        self.addCleanup(shutil.rmtree, self.file.parent, True)

    def good(self, **values):
        return dict(id='job', tracks=[dict(path=str(self.file))], **values)

    def test_defaults_and_normalisation(self):
        clean = mixer.validate_request(self.good())
        self.assertEqual(clean['tracks'][0]['gain_db'], -12.0)
        self.assertEqual((clean['native']['mode'], clean['native']['gain_db'], clean['ducking']), ('keep', 0.0, False))
        self.assertEqual(clean['tracks'][0]['label'], 'm')

    def test_rejections(self):
        base = dict(path=str(self.file))
        bad = [dict(id='j'), dict(id='j', tracks=[]), dict(id='j', tracks=[base] * 4), dict(id='j', tracks=[base], extra=1),
               dict(id='j', tracks=[base | dict(start=-1)]), dict(id='j', tracks=[base | dict(gain_db=float('nan'))]),
               dict(id='j', tracks=[base | dict(gain_db=True)]), dict(id='j', tracks=[base | dict(gain_db=99)]),
               dict(id='j', tracks=[base | dict(loop='yes')]), dict(id='j', tracks=[base | dict(path='relative.wav')]),
               dict(id='j', tracks=[base | dict(path=str(self.file.with_name('none.wav')))]), dict(id='j', tracks=[base | dict(path=str(self.file.with_suffix('.exe')))]),
               dict(id='j', tracks=[base | dict(extra=1)]), dict(id='j', tracks=[base], native=dict(mode='boost')), dict(id='j', tracks=[base], ducking='yes')]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                mixer.validate_request(data)


class PlanTests(unittest.TestCase):
    def plan(self, *tracks, durations, video=VIDEO, **options):
        return mixer.plan_mix(video, request(*tracks, **options), [dict(duration=d) for d in durations])

    def test_track_endings(self):
        plan = self.plan(track(), track(start=2.0), track(loop=True, start=1.0), durations=[10.0, 2.0, 0.5])
        self.assertEqual([s['ends'] for s in plan['spans']], ['video_end', 'file_end', 'loop'])
        self.assertEqual([s['span'] for s in plan['spans']], [6.0, 2.0, 5.0])
        self.assertTrue(any('длиннее ролика' in n for n in plan['notes']))
        self.assertTrue(any('закончится на 4.0 с' in n for n in plan['notes']))
        self.assertIsNone(plan['blocked'])

    def test_blocked_cases(self):
        self.assertIn('после конца', self.plan(track(start=6.0), durations=[3.0])['blocked'])
        self.assertIn('нарастание и затухание', self.plan(track(fade_in=2.0, fade_out=2.0), durations=[3.0])['blocked'])

    def test_native_and_ducking_notes(self):
        self.assertTrue(self.plan(track(), durations=[9.0], ducking=True)['ducking'])
        muted = self.plan(track(), durations=[9.0], ducking=True, native='mute')
        self.assertFalse(muted['ducking'])
        self.assertTrue(any('Приглушение не применяется' in n for n in muted['notes']))
        silent = self.plan(track(), durations=[9.0], video=dict(duration=6.0, has_audio=False), ducking=True)
        self.assertFalse(silent['native_in'] or silent['ducking'])
        self.assertTrue(any('нет собственного звука' in n for n in silent['notes']))
        self.assertEqual([lane['kind'] for lane in self.plan(track(), durations=[9.0])['lanes']], ['video', 'native', 'music'])


class FilterTests(unittest.TestCase):
    def graph(self, *tracks, **options):
        req = request(*tracks, **options)
        plan = mixer.plan_mix(VIDEO, req, [dict(duration=10.0)] * len(tracks))
        return req, plan, mixer.build_filter(req, plan)

    def test_single_track_with_native(self):
        req, plan, graph = self.graph(track(start=1.5, fade_in=0.3, fade_out=1.2, gain_db=-9))
        self.assertIn('afade=t=in:st=0:d=0.300', graph)
        self.assertIn('afade=t=out:st=3.300:d=1.200', graph)
        self.assertIn('volume=-9.00dB', graph)
        self.assertIn('adelay=1500:all=1', graph)
        self.assertIn('[native][music]amix=inputs=2:normalize=0', graph)
        self.assertTrue(graph.endswith('[mixed]alimiter=limit=0.97:level=0,apad[aout]'))
        self.assertNotIn('sidechaincompress', graph)

    def test_two_tracks_ducking_and_no_native(self):
        graph = self.graph(track(), track(start=3), ducking=True)[2]
        self.assertIn('[t1][t2]amix=inputs=2:normalize=0:duration=longest[music]', graph)
        self.assertIn('[music][native1]sidechaincompress=', graph)
        plain = self.graph(track(), native='mute', ducking=True)[2]
        self.assertIn('[music]anull[mixed]', plain)
        self.assertNotIn('[0:a:0]', plain)

    def test_command_loops_inputs_and_never_overwrites(self):
        req, plan, _ = self.graph(track(path='a.wav', loop=True), track(path='b.wav'))
        command = mixer.build_command('ffmpeg', 'clip.mp4', 'out.mp4', req, plan)
        self.assertEqual(command[command.index('-stream_loop') + 1:command.index('-stream_loop') + 4], ['-1', '-i', 'a.wav'])
        self.assertEqual(command.count('-stream_loop'), 1)
        self.assertIn('-n', command)
        self.assertEqual(command[command.index('-c:v') + 1], 'copy')
        self.assertIn('-shortest', command)
        self.assertEqual(command[-1], 'out.mp4')


def run(*arguments):
    return subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', *arguments], check=True, capture_output=True)


def band(path, hz, start, length):
    """Mean volume (dB) of one narrow frequency band in a window of the file's audio."""
    done = subprocess.run([str(FFMPEG), '-hide_banner', '-nostdin', '-ss', str(start), '-t', str(length), '-i', str(path), '-vn',
                           '-af', 'bandpass=f=%d:width_type=h:w=60,volumedetect' % hz, '-f', 'null', '-'], capture_output=True)
    match = re.search(r'mean_volume: (-?[\d.]+|-inf) dB', done.stderr.decode())
    return -200.0 if match is None or match.group(1) == '-inf' else float(match.group(1))


def stream_md5(path):
    done = subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', str(path), '-map', '0:v:0', '-c', 'copy', '-f', 'md5', '-'],
                          check=True, capture_output=True)
    return done.stdout.decode().strip()


@unittest.skipUnless(HAVE_FFMPEG, 'bundled FFmpeg not found')
class RealMixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='vf-mixer-'))
        video = ['-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=24:duration=6', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p']
        # Own sound: a 440 Hz tone for the first 3 s, then silence. Music: 880 Hz tones (10 s and 1.5 s).
        cls.clip = cls.root / 'clip.mp4'
        run('-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=24:duration=6', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3,apad=whole_dur=6',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(cls.clip))
        cls.silent = cls.root / 'silent.mp4'
        run(*video, '-an', str(cls.silent))
        cls.long = cls.root / 'long.wav'
        cls.short = cls.root / 'short.wav'
        run('-f', 'lavfi', '-i', 'sine=frequency=880:duration=10', '-ac', '2', str(cls.long))
        run('-f', 'lavfi', '-i', 'sine=frequency=880:duration=1.5', '-ac', '2', str(cls.short))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def mix(self, source, *tracks, directory=None, **options):
        body = dict(id='job', tracks=[dict(path=str(self.long)) | t for t in tracks], **options)
        return mixer.mix_clip(source, directory or self.root / 'out', body, FFMPEG, FFPROBE)

    def test_video_copied_audio_rebuilt_and_receipt(self):
        before = hashlib.sha256(self.clip.read_bytes()).hexdigest()
        receipt = self.mix(self.clip, dict(gain_db=-6, fade_in=0.3, fade_out=1.2))
        out = self.root / 'out' / receipt['output_file']
        self.assertEqual(stream_md5(out), stream_md5(self.clip))
        result = probe(FFPROBE, out)
        self.assertEqual((result['audio']['codec'], result['audio']['sample_rate'], result['audio']['channels']), ('aac', 48000, 2))
        self.assertAlmostEqual(result['duration'], 6.0, delta=0.15)
        self.assertEqual((receipt['video'], receipt['tracks'][0]['ends'], receipt['native']['used']), ('stream copied, not re-encoded', 'video_end', True))
        self.assertLessEqual(receipt['delivered']['max_volume_db'], 0.0)
        self.assertTrue((out.with_suffix('').with_suffix('.mix.json')).is_file() or (self.root / 'out' / (out.stem + '.mix.json')).is_file())
        self.assertEqual(hashlib.sha256(self.clip.read_bytes()).hexdigest(), before)

    def test_music_and_own_sound_are_both_present_and_mute_removes_own_sound(self):
        kept = self.root / 'out' / self.mix(self.clip, dict())['output_file']
        self.assertGreater(band(kept, 440, 0.5, 2), -45)
        self.assertGreater(band(kept, 880, 0.5, 2), -45)
        muted = self.root / 'out' / self.mix(self.clip, dict(), native=dict(mode='mute'))['output_file']
        self.assertLess(band(muted, 440, 0.5, 2), band(kept, 440, 0.5, 2) - 25)
        self.assertGreater(band(muted, 880, 0.5, 2), -45)

    def test_ducking_lowers_music_only_while_own_sound_plays(self):
        plain = self.root / 'out' / self.mix(self.clip, dict(gain_db=-6))['output_file']
        ducked_receipt = self.mix(self.clip, dict(gain_db=-6), ducking=True)
        ducked = self.root / 'out' / ducked_receipt['output_file']
        self.assertTrue(ducked_receipt['ducking']['applied'])
        self.assertLess(band(ducked, 880, 0.8, 1.5), band(plain, 880, 0.8, 1.5) - 5)
        self.assertAlmostEqual(band(ducked, 880, 4.5, 1.0), band(plain, 880, 4.5, 1.0), delta=1.5)

    def test_start_loop_and_short_track(self):
        late = self.root / 'out' / self.mix(self.silent, dict(start=3.0))['output_file']
        self.assertLess(band(late, 880, 0.2, 2.0), -60)
        self.assertGreater(band(late, 880, 3.5, 2.0), -45)
        once = self.root / 'out' / self.mix(self.silent, dict(path=str(self.short)))['output_file']
        self.assertLess(band(once, 880, 3.5, 2.0), -60)
        looped = self.root / 'out' / self.mix(self.silent, dict(path=str(self.short), loop=True))['output_file']
        self.assertGreater(band(looped, 880, 3.5, 2.0), -45)

    def test_two_tracks_and_a_silent_source(self):
        receipt = self.mix(self.silent, dict(gain_db=-10), dict(path=str(self.short), start=1.0, gain_db=-10))
        self.assertEqual([t['ends'] for t in receipt['tracks']], ['video_end', 'file_end'])
        self.assertFalse(receipt['source']['had_audio'])
        self.assertTrue(probe(FFPROBE, self.root / 'out' / receipt['output_file'])['has_audio'])

    def test_limiter_keeps_loud_sums_below_full_scale(self):
        receipt = self.mix(self.clip, dict(gain_db=12), dict(gain_db=12), native=dict(mode='keep', gain_db=12))
        self.assertLessEqual(receipt['delivered']['max_volume_db'], 0.0)

    def test_blocked_cancel_and_failure_leave_nothing_behind(self):
        out = self.root / 'clean'
        for options in (dict(start=7.0), dict(fade_in=4.0, fade_out=4.0)):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.mix(self.clip, options, directory=out)
        stop = threading.Event()
        stop.set()
        with self.assertRaises(DeliveryCancelled):
            mixer.mix_clip(self.clip, out, dict(id='job', tracks=[dict(path=str(self.long))]), FFMPEG, FFPROBE, cancel=stop)
        self.assertEqual(list(out.iterdir()) if out.exists() else [], [])


if __name__ == '__main__':
    unittest.main()
