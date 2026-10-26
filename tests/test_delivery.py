"""Delivery presets: geometry/command unit checks plus short real FFmpeg exports; no GPU, no model."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from video import delivery
from video.delivery import PRESETS, DeliveryError, build_command, build_filter, export_clip, ffmpeg_paths, fit_geometry, probe

# Read-only use of the bundled FFmpeg of an installed root (<repo>/Runtime/Video); override with VISUALFORGE_VIDEO_RUNTIME.
RUNTIME = Path(os.environ.get('VISUALFORGE_VIDEO_RUNTIME') or str(Path(__file__).resolve().parents[1] / 'Runtime' / 'Video'))
FFMPEG, FFPROBE = ffmpeg_paths(RUNTIME)
HAVE_FFMPEG = FFMPEG.is_file() and FFPROBE.is_file()


class GeometryTests(unittest.TestCase):
    def test_known_fits(self):
        self.assertEqual(fit_geometry(832, 480, 1080, 1920), dict(width=1080, height=622, x=0, y=648, scale=1080 / 832))
        self.assertEqual(fit_geometry(512, 288, 1920, 1080)['width'], 1920)
        self.assertEqual(fit_geometry(512, 288, 1920, 1080)['height'], 1080)
        exact = fit_geometry(1080, 1920, 1080, 1920)
        self.assertEqual((exact['width'], exact['height'], exact['x'], exact['y'], exact['scale']), (1080, 1920, 0, 0, 1))
        self.assertEqual(fit_geometry(1000, 1000, 1080, 1350)['height'], 1080)

    def test_always_even_inside_target_and_undistorted(self):
        for preset in PRESETS.values():
            for width, height in ((832, 480), (480, 832), (512, 512), (3, 3), (4000, 100), (100, 4000), (1920, 1080)):
                with self.subTest(preset=preset['aspect'], source=(width, height)):
                    g = fit_geometry(width, height, preset['width'], preset['height'])
                    self.assertTrue(g['width'] % 2 == g['height'] % 2 == g['x'] % 2 == g['y'] % 2 == 0)
                    self.assertTrue(2 <= g['width'] <= preset['width'] and 2 <= g['height'] <= preset['height'])
                    self.assertTrue(g['x'] + g['width'] <= preset['width'] and g['y'] + g['height'] <= preset['height'])
                    self.assertLess(abs(g['width'] / g['height'] - width / height), 3 / min(g['width'], g['height']) * max(1, width / height))

    def test_filter_pads_only_when_needed(self):
        wide = PRESETS['reels-1080x1920']
        padded = build_filter(fit_geometry(832, 480, 1080, 1920), wide)
        self.assertIn('scale=1080:622:flags=lanczos', padded)
        self.assertIn('pad=1080:1920:0:648:color=black', padded)
        self.assertTrue(padded.endswith('setsar=1,fps=30,format=yuv420p'))
        self.assertNotIn('pad=', build_filter(fit_geometry(1080, 1920, 1080, 1920), wide))


class CommandTests(unittest.TestCase):
    info = dict(has_audio=True)
    geometry = fit_geometry(832, 480, 1080, 1920)
    preset = PRESETS['reels-1080x1920']

    def command(self, audio, has_audio):
        return build_command('ffmpeg', 'in.mp4', 'out.mp4', dict(has_audio=has_audio), self.geometry, self.preset, audio)

    def test_keep_maps_source_audio_and_never_overwrites(self):
        command = self.command('keep', True)
        self.assertIn('-n', command)
        self.assertNotIn('-y', command)
        self.assertEqual(command[command.index('-map') + 1], '0:v:0')
        self.assertIn('0:a:0', command)
        self.assertIn('-shortest', command)
        self.assertEqual(command[command.index('-c:a') + 1], 'aac')
        self.assertEqual(command[command.index('-ar') + 1], '48000')
        self.assertEqual(command[-1], 'out.mp4')

    def test_no_audio_and_silence_fill(self):
        self.assertIn('-an', self.command('keep', False))
        self.assertNotIn('-shortest', self.command('keep', False))
        filled = self.command('fill', False)
        self.assertIn('1:a:0', filled)
        self.assertTrue(any(part.startswith('anullsrc=') for part in filled))
        self.assertNotIn('1:a:0', self.command('fill', True))

    def test_validation_happens_before_any_file_is_touched(self):
        with tempfile.TemporaryDirectory() as temporary:
            for bad in (dict(preset_id='nope'), dict(audio='loud'), dict(crf=5), dict(crf=18.0), dict(x264_preset='placebo')):
                arguments = dict(source=__file__, directory=temporary, preset_id='reels-1080x1920', ffmpeg='x', ffprobe='y') | bad
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    export_clip(**arguments)
            with self.assertRaises(ValueError):
                export_clip(Path(temporary) / 'missing.mp4', temporary, 'reels-1080x1920', 'x', 'y')
            self.assertEqual(os.listdir(temporary), [])

    def test_unique_reservation_never_replaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'Clip.mp4').write_bytes(b'user file')
            first, receipt = delivery._reserve(directory, 'Clip')
            second, _ = delivery._reserve(directory, 'Clip')
            self.assertEqual((first.name, second.name, receipt.name), ('Clip 1.mp4', 'Clip 2.mp4', 'Clip 1.delivery.json'))
            self.assertEqual((directory / 'Clip.mp4').read_bytes(), b'user file')


@unittest.skipUnless(HAVE_FFMPEG, 'bundled FFmpeg not found')
class RealExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='vf-delivery-'))
        base = [str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n']
        video = ['-f', 'lavfi', '-i', 'testsrc2=size=832x480:rate=24:duration=2']
        encode = ['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p']
        cls.with_audio, cls.silent = cls.root / 'Wan Test.mp4', cls.root / 'Silent Test.mp4'
        subprocess.run(base + video + ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=2'] + encode + ['-c:a', 'aac', '-shortest', str(cls.with_audio)], check=True)
        subprocess.run(base + video + encode + ['-an', str(cls.silent)], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def export(self, source, preset, **options):
        out = self.root / 'out'
        return export_clip(source, out, preset, FFMPEG, FFPROBE, x264_preset='ultrafast', **options), out

    def test_all_presets_exact_geometry_and_audio(self):
        digest = hashlib.sha256(self.with_audio.read_bytes()).hexdigest()
        for preset_id, preset in PRESETS.items():
            with self.subTest(preset=preset_id):
                receipt, out = self.export(self.with_audio, preset_id)
                result = probe(FFPROBE, out / receipt['output_file'])
                self.assertEqual((result['width'], result['height']), (preset['width'], preset['height']))
                self.assertEqual((result['display_width'], result['display_height']), (preset['width'], preset['height']))
                self.assertEqual((result['fps'], result['video_codec'], result['pix_fmt']), (30, 'h264', 'yuv420p'))
                self.assertEqual((result['audio']['codec'], result['audio']['sample_rate'], result['audio']['channels']), ('aac', 48000, 2))
                self.assertAlmostEqual(result['duration'], 2.0, delta=0.15)
                self.assertEqual(receipt['source']['width'], 832)
                self.assertEqual(receipt['delivered']['width'], preset['width'])
                self.assertEqual(receipt['enhancement'], 'none')
                self.assertEqual(receipt['frame_rate_conversion'], 'frames duplicated or dropped; no new motion is created')
                self.assertEqual(json.loads((out / receipt['output_file'].replace('.mp4', '.delivery.json')).read_text(encoding='utf-8')), receipt)
        self.assertEqual(hashlib.sha256(self.with_audio.read_bytes()).hexdigest(), digest)

    def test_scale_up_is_recorded_and_bars_are_black(self):
        receipt, out = self.export(self.with_audio, 'reels-1080x1920')
        self.assertTrue(receipt['picture']['scaled_up'])
        self.assertEqual((receipt['picture']['width'], receipt['picture']['height'], receipt['picture']['y']), (1080, 622, 648))
        pixel = subprocess.run([str(FFMPEG), '-v', 'error', '-i', str(out / receipt['output_file']), '-vf', 'crop=2:2:0:0,format=rgb24',
                                '-frames:v', '1', '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
        self.assertTrue(all(value < 8 for value in pixel), pixel)

    def test_audio_modes_on_a_silent_source(self):
        receipt, out = self.export(self.silent, 'vertical-720x1280')
        self.assertIsNone(receipt['delivered']['audio'])
        self.assertEqual(receipt['delivered']['audio_origin'], 'none')
        filled, _ = self.export(self.silent, 'vertical-720x1280', audio='fill')
        self.assertEqual(filled['delivered']['audio_origin'], 'silence')
        self.assertTrue(probe(FFPROBE, out / filled['output_file'])['has_audio'])
        before = sorted(os.listdir(out))
        with self.assertRaisesRegex(ValueError, 'нет звука'):
            self.export(self.silent, 'vertical-720x1280', audio='require')
        self.assertEqual(sorted(os.listdir(out)), before)

    def test_repeated_export_gets_new_names_and_failure_leaves_nothing(self):
        first, out = self.export(self.with_audio, 'feed-1080x1350')
        second, _ = self.export(self.with_audio, 'feed-1080x1350')
        self.assertNotEqual(first['output_file'], second['output_file'])
        broken = self.root / 'broken.mp4'
        broken.write_bytes(self.with_audio.read_bytes()[:2000])
        before = sorted(os.listdir(out))
        with self.assertRaises((ValueError, DeliveryError)):
            self.export(broken, 'feed-1080x1350')
        self.assertEqual(sorted(os.listdir(out)), before)


if __name__ == '__main__':
    unittest.main()
