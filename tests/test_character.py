"""Wan Animate 2 and SCAIL-2: request validation, the segment plan, readiness, input preparation and the picture helpers; no GPU."""
from pathlib import Path
import base64
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from video import character_input, character_support as support
from video.character_support import ANIMATE_ID, SCAIL_ID, segment_plan, usable_frames
from video.video_adapters import build_command
from video.video_registry import ENGINES, validate
from video.video_service import Jobs

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'Runtime/Video'
VIDEO = 'data:video/mp4;base64,' + base64.b64encode(b'not really a video').decode()


def png(color=(200, 80, 20), size=(64, 48)):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()


def request(engine, **extra):
    return dict(engine=engine, mode='animate' if engine == ANIMATE_ID else 'replace', prompt='A man sits on a chair', source_image=png(),
                driving_video=VIDEO, width=512, height=896, frames=161, fps=24, quality='standard') | extra


class PlanTests(unittest.TestCase):
    def test_segments_cover_the_clip_exactly_and_stay_on_the_time_grid(self):
        self.assertEqual(segment_plan(289, ANIMATE_ID), [81, 81, 81, 49])
        self.assertEqual(segment_plan(289, SCAIL_ID), [81, 81, 81, 61])
        self.assertEqual(segment_plan(81, SCAIL_ID), [81])
        self.assertEqual(segment_plan(17, ANIMATE_ID), [17])
        for engine, overlap in ((ANIMATE_ID, 1), (SCAIL_ID, 5)):
            for frames in range(17, 760, 7):
                lengths = segment_plan(frames, engine)
                produced = sum(length - (overlap if index else 0) for index, length in enumerate(lengths))
                self.assertTrue(all(length % 4 == 1 and 5 <= length <= 81 for length in lengths), (engine, frames, lengths))
                self.assertGreaterEqual(produced, frames)
                # no segment is wasted: without the last one the clip would be too short
                self.assertLess(produced - (lengths[-1] - (overlap if len(lengths) > 1 else 0)), frames, (engine, frames, lengths))

    def test_a_reference_video_under_a_second_is_refused_and_a_long_one_is_cut_to_the_limit(self):
        self.assertEqual(usable_frames(289, 161), 161)
        self.assertEqual(usable_frames(100, 161), 100)
        with self.assertRaises(ValueError):
            usable_frames(16, 161)


class RequestTests(unittest.TestCase):
    def test_valid_requests_keep_what_the_job_needs(self):
        animate = validate(request(ANIMATE_ID, motion_prompt='a man sits still'))
        self.assertEqual((animate['engine'], animate['mode'], animate['motion_prompt'], animate['keep_audio']), (ANIMATE_ID, 'animate', 'a man sits still', True))
        scail = validate(request(SCAIL_ID, relight=True, reference_object='human', driving_object='human, truck:1',
                                 subjects=[dict(image=png(), reference_object='car', driving_object='truck')]))
        self.assertEqual((scail['mode'], scail['relight'], scail['driving_object'], scail['subjects']), ('replace', True, 'human, truck:1', [dict(reference_object='car', driving_object='truck')]))
        self.assertNotIn('driving_video', scail)               # the video travels as a file, never inside the job spec
        self.assertEqual(validate(request(SCAIL_ID, mode='animate'))['mode'], 'animate')
        self.assertTrue(animate['fit_reference'])                # the photo is fitted to the video's scale unless the person turns it off
        self.assertFalse(validate(request(ANIMATE_ID, fit_reference=False))['fit_reference'])

    def test_bad_requests_are_refused(self):
        cases = [(ANIMATE_ID, dict(driving_video=None)), (ANIMATE_ID, dict(driving_video='data:audio/wav;base64,AAAA')), (ANIMATE_ID, dict(source_image=None)),
                 (ANIMATE_ID, dict(relight=True)), (ANIMATE_ID, dict(reference_object='human')), (ANIMATE_ID, dict(subjects=[])), (ANIMATE_ID, dict(mode='replace')),
                 (ANIMATE_ID, dict(fit_reference='yes')), (ANIMATE_ID, dict(frames=240)), (ANIMATE_ID, dict(width=832, height=480)), (ANIMATE_ID, dict(negative_prompt='ugly')),
                 (SCAIL_ID, dict(mode='animate', relight=True)), (SCAIL_ID, dict(reference_object='<script>')), (SCAIL_ID, dict(reference_object='x' * 41)),
                 (SCAIL_ID, dict(subjects=[dict(image=png())] * 3)), (SCAIL_ID, dict(subjects=[dict(image=1)])), (SCAIL_ID, dict(keep_audio='yes')),
                 (SCAIL_ID, dict(motion_prompt='x')), (SCAIL_ID, dict(quality='preview'))]
        for engine, extra in cases:
            with self.subTest(engine=engine, extra=list(extra)):
                data = request(engine, **{k: v for k, v in extra.items() if v is not None})
                for key, value in extra.items():
                    if value is None:
                        data.pop(key)
                with self.assertRaises(ValueError):
                    validate(data)

    def test_other_engines_do_not_take_character_fields(self):
        with self.assertRaises(ValueError):
            validate(dict(engine='ltx25-distilled', prompt='a fox', driving_video=VIDEO))
        with self.assertRaises(ValueError):
            validate(dict(engine='wan22-ti2v-5b', prompt='a fox', keep_audio=True))

    def test_registry_entries_are_consistent(self):
        for engine_id in (ANIMATE_ID, SCAIL_ID):
            engine = ENGINES[engine_id]
            self.assertEqual(set(engine['prompt_enhancer']), set(engine['modes']))
            self.assertRegex(engine['file_label'], r'^[A-Za-z0-9 .-]+$')
            self.assertTrue(engine['needs_driving_video'])
            for width, height in engine['resolutions']:
                self.assertEqual((width % 32, height % 32), (0, 0))
            self.assertTrue(set(engine['duration_labels']) == set(engine['frames']))
        self.assertEqual(validate(request(ANIMATE_ID, frames=81))['frames'], 81)

    def test_the_command_starts_the_character_worker_with_its_own_spec(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            spec = validate(request(SCAIL_ID))
            command = build_command(folder, folder / 'Models/Video/H3', folder, spec)
            self.assertIn('character_worker.py', ' '.join(command))
            self.assertEqual(json.loads((folder / 'character-request.json').read_text(encoding='utf-8'))['engine'], SCAIL_ID)
            self.assertEqual(Path(command[command.index('--models') + 1]), folder / 'Models/Video')


class ReadinessTests(unittest.TestCase):
    def test_missing_files_are_listed_per_engine_and_quality(self):
        with tempfile.TemporaryDirectory() as temp:
            models = Path(temp) / 'Models/Video/H3'
            animate = support.engine_missing(Path(temp) / 'Runtime', models, ANIMATE_ID)
            scail = support.engine_missing(Path(temp) / 'Runtime', models, SCAIL_ID, 'standard', relight=True)
            self.assertTrue(any('wan_animate_2_distill_int8_convrot' in item for item in animate))
            self.assertFalse(any('wan_animate_2_int8_convrot' in item for item in animate))
            self.assertFalse(any('sam3.1_multiplex' in item for item in animate))       # SAM3 only finds the heads when the scale fit is on
            self.assertTrue(any('sam3.1_multiplex' in item for item in support.engine_missing(Path(temp) / 'Runtime', models, ANIMATE_ID, fit=True)))
            self.assertTrue(any('wan_animate_2_int8_convrot' in item for item in support.engine_missing(Path(temp), models, ANIMATE_ID, 'high')))
            self.assertTrue(any('sam3.1_multiplex' in item for item in scail) and any('relight' in item for item in scail))
            self.assertFalse(any('relight' in item for item in support.engine_missing(Path(temp), models, SCAIL_ID, 'standard')))

    def test_a_file_with_the_wrong_size_counts_as_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            models = Path(temp) / 'Models/Video/H3'
            clip = Path(temp) / 'Models/Video/WanAnimateShared/clip_vision/clip_vision_h.safetensors'
            clip.parent.mkdir(parents=True)
            clip.write_bytes(b'short')
            self.assertTrue(any('clip_vision_h' in item for item in support.engine_missing(Path(temp), models, ANIMATE_ID)))


@unittest.skipUnless((RUNTIME / 'ffmpeg/bin/ffmpeg.exe').is_file(), 'Optional Video runtime')
class InputTests(unittest.TestCase):
    def clip(self, folder, seconds, sound=True):
        target = Path(folder) / 'clip.mp4'
        command = [str(RUNTIME / 'ffmpeg/bin/ffmpeg.exe'), '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=128x96:rate=30']
        if sound:
            command += ['-f', 'lavfi', '-i', 'sine=frequency=440']
        subprocess.run(command + ['-t', str(seconds), '-c:v', 'libx264', '-pix_fmt', 'yuv420p'] + (['-c:a', 'aac'] if sound else []) + [str(target)], check=True, timeout=60)
        return target

    def stub(self):
        return types.SimpleNamespace(root=RUNTIME, image=lambda value, path: Jobs.image(None, value, path))

    def test_the_clip_takes_the_length_of_the_video_up_to_the_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = self.clip(folder, 2.5)
            data = request(ANIMATE_ID, frames=161, driving_video='data:video/mp4;base64,' + base64.b64encode(source.read_bytes()).decode())
            spec, media = validate(data), {}
            character_input.prepare(self.stub(), data, folder, spec, media)
            self.assertEqual((spec['frames'], spec['requested_frames'], spec['driving_has_audio']), (60, 161, True))
            self.assertTrue((folder / 'driving.input').is_file() and media['driving'].endswith('driving.input'))
            data['frames'] = 81
            spec = validate(data)
            long_source = self.clip(folder, 6, sound=False)
            data['driving_video'] = 'data:video/mp4;base64,' + base64.b64encode(long_source.read_bytes()).decode()
            character_input.prepare(self.stub(), data, folder, spec, {})
            self.assertEqual((spec['frames'], spec['driving_has_audio']), (81, False))

    def test_unreadable_and_too_short_videos_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            data = request(ANIMATE_ID)
            with self.assertRaises(ValueError):
                character_input.prepare(self.stub(), data, folder, validate(data), {})
            data['driving_video'] = 'data:video/mp4;base64,' + base64.b64encode(self.clip(folder, 0.4).read_bytes()).decode()
            with self.assertRaises(ValueError):
                character_input.prepare(self.stub(), data, folder, validate(data), {})


@unittest.skipUnless((RUNTIME / 'ffmpeg/bin/ffmpeg.exe').is_file(), 'Optional Video runtime')
class ServiceTests(unittest.TestCase):
    def test_a_submitted_job_keeps_its_inputs_in_its_folder_and_not_in_its_record(self):
        import argparse
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clip = root / 'clip.mp4'
            subprocess.run([str(RUNTIME / 'ffmpeg/bin/ffmpeg.exe'), '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=128x96:rate=30', '-t', '2.5',
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip)], check=True, timeout=60)
            jobs = Jobs(argparse.Namespace(user=str(root / 'User'), root=str(RUNTIME), models=str(root / 'Models/Video/H3'), state=str(root / 'state')))
            data = request(SCAIL_ID, frames=161, driving_video='data:video/mp4;base64,' + base64.b64encode(clip.read_bytes()).decode(),
                           subjects=[dict(image=png((10, 20, 30)), reference_object='car', driving_object='truck')], keep_audio=False)
            with patch('video.video_service.character_missing', return_value=[]) as missing, patch.object(Jobs, 'run'):
                row = jobs.submit(data)
            self.assertEqual(missing.call_args.args[2:], (SCAIL_ID, 'standard', False, True))
            self.assertEqual((row['frames'], row['requested_frames'], row['mode'], row['keep_audio']), (60, 161, 'replace', False))
            folder = Path(jobs.rows[row['id']]['folder'])
            media = json.loads((folder / 'media.json').read_text(encoding='utf-8'))
            self.assertTrue(Path(media['driving']).is_file() and Path(media['first']).is_file() and len(media['subjects']) == 1 and Path(media['subjects'][0]).is_file())
            self.assertNotIn('base64', json.dumps(row))
            self.assertEqual(row['subjects'], [dict(reference_object='car', driving_object='truck')])
            self.assertEqual(len(row['subject_sha256']), 1)
            with self.assertRaises(ValueError):                          # one job at a time: the slot is taken
                jobs.submit(data)


class PictureTests(unittest.TestCase):
    def test_fit_cover_fills_the_box_without_stretching(self):
        from PIL import Image
        from video.character_worker import fit_cover, fit_inside, compose_reference
        picture = Image.new('RGB', (300, 400), (255, 0, 0))
        picture.paste((0, 0, 255), (0, 0, 300, 20))          # a marker row at the top
        fitted = fit_cover(picture, 512, 896)
        self.assertEqual(fitted.size, (512, 896))
        self.assertEqual(fit_inside(picture, 100, 100).size, (75, 100))
        wide = Image.new('RGB', (800, 400), (0, 255, 0))
        combined = compose_reference(picture, [wide], 512, 896)
        self.assertEqual(combined.size, (512, 896))
        self.assertEqual(combined.getpixel((256, 880)), (255, 255, 255))     # the corner of the lower part stays white
        self.assertEqual(compose_reference(picture, [], 512, 896).size, (512, 896))

    def test_colour_matching_moves_the_tone_to_the_anchor_without_leaving_the_range(self):
        import numpy as np
        from video.character_worker import match_colors
        rng = np.random.default_rng(3)
        anchor = np.clip(rng.normal(0.35, 0.08, (24, 32, 3)), 0, 1)
        frames = np.clip(rng.normal(0.6, 0.12, (3, 24, 32, 3)), 0, 1).astype('float32')
        matched = match_colors(frames, anchor)
        self.assertEqual(matched.shape, frames.shape)
        self.assertTrue(0 <= matched.min() and matched.max() <= 1)
        self.assertLess(abs(matched.mean() - anchor.mean()), abs(frames.mean() - anchor.mean()) / 3)
        same = match_colors(anchor[None].astype('float32'), anchor)
        self.assertLess(float(np.abs(same - anchor).max()), 0.02)

    def test_the_photo_is_scaled_and_moved_to_the_head_of_the_person_in_the_video(self):
        from PIL import Image
        from video.character_worker import aligned_picture
        photo = Image.new('RGB', (512, 896), (30, 60, 90))
        ref_head = (0.25, 0.22, 0.70, 0.60)        # a close-up: the head fills almost half of the picture
        video_head = (0.73, 0.57, 0.88, 0.71)      # a wide shot: a small head low on the right
        fitted = aligned_picture(photo, ref_head, video_head)
        self.assertEqual(fitted.size, (512, 896))
        self.assertEqual(fitted.getpixel((5, 5)), (255, 255, 255))             # the room around the smaller photo is blank
        scale = (video_head[2] - video_head[0]) / (ref_head[2] - ref_head[0])
        centre_x, centre_y = round((video_head[0] + video_head[2]) / 2 * 512), round((video_head[1] + video_head[3]) / 2 * 896)
        self.assertEqual(fitted.getpixel((centre_x, centre_y)), (30, 60, 90))   # the head of the photo sits where the video's head is
        self.assertAlmostEqual(scale, 0.333, places=2)
        # Wan Animate 2 keeps the whole picture as its scene, so only the person is carried over, alone on a plain ground
        from video.character_worker import cutout_picture
        import numpy
        mask = numpy.zeros((896, 512), dtype=bool)
        mask[150:896, 100:420] = True
        cut = cutout_picture(photo, mask, ref_head, video_head)
        self.assertEqual(cut.size, (512, 896))
        self.assertEqual(cut.getpixel((5, 5)), (236, 236, 236))
        self.assertEqual(cut.getpixel((centre_x, centre_y)), (30, 60, 90))
        # nearly the same size: the picture is returned untouched; an unusable box too
        self.assertIs(aligned_picture(photo, (0.4, 0.3, 0.6, 0.5), (0.41, 0.3, 0.62, 0.5)), photo)
        self.assertIs(aligned_picture(photo, (0.4, 0.3, 0.4, 0.5), video_head), photo)

    def test_the_first_box_of_a_track(self):
        try:
            import torch
        except ImportError:
            torch = None
        if torch is None:
            self.skipTest('torch is only in the Video runtime')
        from video.character_worker import first_box
        masks = torch.zeros(3, 2, 10, 20, dtype=torch.bool)
        masks[1, 0, 2:6, 4:12] = True                         # the first object appears in the second picture
        masks[2, 0, 0:2, 0:2] = True
        self.assertEqual(first_box({'packed_masks': masks}, lambda value: value), (4 / 20, 2 / 10, 12 / 20, 6 / 10))
        self.assertIsNone(first_box({'packed_masks': torch.zeros(3, 2, 10, 20, dtype=torch.bool)}, lambda value: value))
        self.assertIsNone(first_box({'packed_masks': None}, lambda value: value))
        self.assertIsNone(first_box({'packed_masks': torch.zeros(3, 0, 10, 20, dtype=torch.bool)}, lambda value: value))


if __name__ == '__main__':
    unittest.main()
