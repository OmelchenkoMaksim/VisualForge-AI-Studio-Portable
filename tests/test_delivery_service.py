"""Delivery export inside the Video service: background job, preview, cancel, limits. CPU only, no model."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from video.video_delivery import PRESETS, ffmpeg_paths, public_presets
from video.video_service import Jobs

RUNTIME = Path(os.environ.get('VISUALFORGE_VIDEO_RUNTIME') or str(Path(__file__).resolve().parents[1] / 'Runtime' / 'Video'))
FFMPEG, FFPROBE = ffmpeg_paths(RUNTIME)


def make_clip(path, size='832x480', seconds=2, audio=True):
    command = [str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=24:duration={seconds}']
    if audio:
        command += ['-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}']
    command += ['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p'] + (['-c:a', 'aac', '-shortest'] if audio else ['-an']) + [str(path)]
    subprocess.run(command, check=True)


@unittest.skipUnless(FFMPEG.is_file() and FFPROBE.is_file(), 'bundled FFmpeg not found')
class ServiceExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='vf-service-'))
        cls.saved = cls.root / 'Saved'
        cls.saved.mkdir()
        cls.clips = {}
        for name, options in dict(sound={}, silent=dict(audio=False), long=dict(size='1920x1080', seconds=60)).items():
            cls.clips[name] = cls.saved / (name + '.mp4')
            make_clip(cls.clips[name], **options)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        user = self.root / ('User-' + uuid.uuid4().hex[:6])
        self.jobs = Jobs(argparse.Namespace(user=str(user), root=str(RUNTIME), models=str(self.root / 'models'), state=str(self.root / 'state')))
        self.output = self.root / ('Out-' + uuid.uuid4().hex[:6])
        self.jobs.save_settings({'save_dir': str(self.output)})
        self.ids = {}
        for name, clip in self.clips.items():
            ident = self.ids[name] = str(uuid.uuid4())
            self.jobs.rows[ident] = dict(id=ident, state='done', output_path=str(clip), submitted_at=time.time())
        self.jobs.rows['unfinished'] = dict(id='unfinished', state='running', output_path=str(self.clips['sound']), submitted_at=time.time())

    def wait(self, ident, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.jobs.export_status(ident)
            if row['state'] != 'running':
                return row
            time.sleep(0.1)
        self.fail('export did not finish')

    def test_presets_are_listed_for_the_interface(self):
        info = public_presets()
        self.assertEqual([p['id'] for p in info['presets']], list(PRESETS))
        self.assertEqual(info['audio_modes'], ['keep', 'require', 'fill'])

    def test_preview_uses_the_real_plan_and_writes_nothing(self):
        plan = self.jobs.export_preview(dict(id=[self.ids['sound']], preset=['reels-1080x1920'], audio=['keep']))
        self.assertEqual((plan['picture']['width'], plan['picture']['height'], plan['picture']['y']), (1080, 622, 648))
        self.assertEqual((plan['source']['width'], plan['source']['height']), (832, 480))
        self.assertTrue(plan['picture']['scaled_up'])
        self.assertEqual(plan['audio_origin'], 'source')
        blocked = self.jobs.export_preview(dict(id=[self.ids['silent']], preset=['reels-1080x1920'], audio=['require']))
        self.assertIn('нет звука', blocked['blocked'])
        self.assertFalse(self.output.exists())

    def test_export_runs_in_background_and_leaves_the_source_alone(self):
        before = self.clips['sound'].read_bytes()
        started = self.jobs.start_export(dict(id=self.ids['sound'], preset='vertical-720x1280'))
        self.assertEqual(started['state'], 'running')
        self.assertIn('cancel_token', started)
        row = self.wait(started['id'])
        self.assertEqual(row['state'], 'done', row.get('error'))
        self.assertNotIn('cancel_token', row)
        self.assertEqual(Path(row['saved_path']).parent, self.output)
        self.assertTrue(Path(row['saved_path']).is_file())
        self.assertEqual(row['receipt']['delivered']['width'], 720)
        self.assertEqual(row['receipt']['generation'], row['generation'])
        self.assertEqual(row['generation']['id'], started['generation']['id'])
        metadata = json.loads(Path(row['saved_path']).with_suffix('.delivery.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['generation'], row['generation'])
        self.assertEqual(self.clips['sound'].read_bytes(), before)
        self.assertIsNone(self.jobs.export_status('')['export'])

    def test_rejects_bad_requests_before_starting(self):
        for data in (dict(id=self.ids['sound'], preset='nope'), dict(id=self.ids['sound'], preset='reels-1080x1920', audio='loud'),
                     dict(id=self.ids['sound'], preset='reels-1080x1920', crf=1), dict(id='missing', preset='reels-1080x1920'),
                     dict(id='unfinished', preset='reels-1080x1920'), dict(id=['x'], preset='reels-1080x1920'),
                     dict(id=self.ids['silent'], preset='reels-1080x1920', audio='require')):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.jobs.start_export(data)
        self.assertEqual(self.jobs.exports, {})
        self.assertFalse(self.output.exists())

    def test_one_export_at_a_time_and_cancel_cleans_up(self):
        first = self.jobs.start_export(dict(id=self.ids['long'], preset='reels-1080x1920'))
        with self.assertRaisesRegex(ValueError, 'уже выполняется'):
            self.jobs.start_export(dict(id=self.ids['sound'], preset='reels-1080x1920'))
        with self.assertRaises(ValueError):
            self.jobs.cancel_export(dict(id=first['id'], cancel_token='wrong'))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and self.jobs.export_status(first['id'])['progress'] <= 0:
            time.sleep(0.1)
        if self.jobs.export_status(first['id'])['state'] != 'running':
            self.skipTest('export finished before it could be cancelled')
        self.assertEqual(self.jobs.export_status('')['export']['id'], first['id'])
        self.assertEqual(self.jobs.cancel_export(dict(id=first['id'], cancel_token=first['cancel_token'])), {'cancelled': True})
        row = self.wait(first['id'])
        self.assertEqual(row['state'], 'cancelled')
        self.assertEqual([p.name for p in self.output.iterdir()], [])
        self.assertEqual(self.jobs.cancel_export(dict(id=first['id'], cancel_token=first['cancel_token'])), {'cancelled': False})
        self.assertEqual(self.wait(self.jobs.start_export(dict(id=self.ids['silent'], preset='feed-1080x1350', audio='fill'))['id'])['state'], 'done')

    def test_shutdown_stops_a_running_export(self):
        first = self.jobs.start_export(dict(id=self.ids['long'], preset='landscape-1920x1080'))
        time.sleep(0.5)
        self.jobs.shutdown()
        self.assertIn(self.wait(first['id'])['state'], ('cancelled', 'done'))


if __name__ == '__main__':
    unittest.main()
