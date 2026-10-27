"""Mixer inside the Video service: preview, background mix, history entry, shared slot, cancel. CPU only, no model."""
import argparse
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
from video.delivery import ffmpeg_paths, probe
from video.service import Jobs

RUNTIME = Path(os.environ.get('VISUALFORGE_VIDEO_RUNTIME') or str(Path(__file__).resolve().parents[1] / 'Runtime' / 'Video'))
FFMPEG, FFPROBE = ffmpeg_paths(RUNTIME)


def run(*arguments):
    subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', *arguments], check=True)


@unittest.skipUnless(FFMPEG.is_file() and FFPROBE.is_file(), 'bundled FFmpeg not found')
class MixerServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='vf-mixsvc-'))
        cls.saved = cls.root / 'Saved'
        cls.saved.mkdir()
        cls.clip, cls.long_clip, cls.music = cls.saved / 'clip.mp4', cls.saved / 'long.mp4', cls.root / 'music.wav'
        run('-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=24:duration=4', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', '-c:v', 'libx264', '-preset', 'ultrafast',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(cls.clip))
        run('-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=24:duration=120', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-an', str(cls.long_clip))
        run('-f', 'lavfi', '-i', 'sine=frequency=880:duration=8', '-ac', '2', str(cls.music))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        self.jobs = Jobs(argparse.Namespace(user=str(self.root / ('User-' + uuid.uuid4().hex[:6])), root=str(RUNTIME), models=str(self.root / 'm'), state=str(self.root / 's')))
        self.output = self.root / ('Out-' + uuid.uuid4().hex[:6])
        self.jobs.save_settings({'save_dir': str(self.output)})
        self.ids = {}
        for name, clip in (('clip', self.clip), ('long', self.long_clip)):
            self.ids[name] = str(uuid.uuid4())
            self.jobs.rows[self.ids[name]] = dict(id=self.ids[name], state='done', engine='wan22-ti2v-5b', mode='t2v', quality='standard', seed='1', original_prompt='A test clip',
                                                  output_path=str(clip), filename=clip.name, submitted_at=time.time(), metadata=dict(width=320, height=240, has_audio=name == 'clip', duration=4.0))

    def body(self, name='clip', **values):
        return dict(id=self.ids[name], tracks=[dict(path=str(self.music), label='Calm piano', gain_db=-9, fade_out=1)], **values)

    def wait(self, ident, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.jobs.export_status(ident)
            if row['state'] != 'running':
                return row
            time.sleep(0.1)
        self.fail('mix did not finish')

    def test_preview_is_the_real_plan_and_writes_nothing(self):
        plan = self.jobs.mix_preview(self.body(ducking=True))
        self.assertEqual((plan['duration'], plan['native_in'], plan['ducking']), (4.0, True, True))
        self.assertEqual([lane['kind'] for lane in plan['lanes']], ['video', 'native', 'music'])
        self.assertEqual(plan['spans'][0]['ends'], 'video_end')
        self.assertFalse(self.output.exists())
        late = self.jobs.mix_preview(dict(self.body(), tracks=[dict(path=str(self.music), start=9)]))
        self.assertIn('после конца', late['blocked'])

    def test_mix_runs_in_background_and_becomes_a_history_entry(self):
        started = self.jobs.start_mix(self.body(ducking=True))
        self.assertEqual((started['kind'], started['state']), ('mix', 'running'))
        row = self.wait(started['id'])
        self.assertEqual(row['state'], 'done', row.get('error'))
        self.assertNotIn('cancel_token', row)
        saved = Path(row['saved_path'])
        self.assertEqual(saved.parent, self.output)
        self.assertTrue(probe(FFPROBE, saved)['has_audio'])
        entry = self.jobs.rows[row['result_id']]
        self.assertEqual((entry['state'], entry['derived_from'], entry['derived_kind']), ('done', self.ids['clip'], 'mix'))
        self.assertEqual(entry['output_path'], str(saved))
        self.assertIn('Calm piano', entry['mix_summary'])
        self.assertTrue(entry['metadata']['has_audio'])
        self.assertEqual(self.jobs.rows[self.ids['clip']]['output_path'], str(self.clip))
        # The new entry can be exported (and mixed again) like any generated clip.
        again = self.jobs.start_export(dict(id=row['result_id'], preset='vertical-720x1280'))
        self.assertEqual(self.wait(again['id'])['state'], 'done')

    def test_request_errors_start_nothing(self):
        for data in (dict(id='nope', tracks=[dict(path=str(self.music))]), dict(id=self.ids['clip'], tracks=[]),
                     dict(self.body(), extra=1), dict(id=self.ids['clip'], tracks=[dict(path=str(self.music), start=-1)]),
                     dict(id=self.ids['clip'], tracks=[dict(path=str(self.root / 'missing.wav'))]), dict(self.body(), tracks=[dict(path=str(self.music), fade_in=3, fade_out=3)])):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.jobs.start_mix(data)
        self.assertEqual(self.jobs.exports, {})
        self.assertFalse(self.output.exists())

    def test_shares_the_slot_with_export_and_can_be_cancelled(self):
        first = self.jobs.start_export(dict(id=self.ids['long'], preset='landscape-1920x1080'))
        with self.assertRaisesRegex(ValueError, 'уже выполняется'):
            self.jobs.start_mix(self.body())
        self.jobs.cancel_export(dict(id=first['id'], cancel_token=first['cancel_token']))
        self.assertEqual(self.wait(first['id'])['state'], 'cancelled')
        self.assertEqual(self.wait(self.jobs.start_mix(self.body())['id'])['state'], 'done')
        self.assertEqual(len(list(self.output.glob('*.mp4'))), 1)

    def test_mix_cancel_removes_partial_files(self):
        started = self.jobs.start_mix(dict(id=self.ids['long'], tracks=[dict(path=str(self.music), loop=True)]))
        self.jobs.cancel_export(dict(id=started['id'], cancel_token=started['cancel_token']))
        row = self.wait(started['id'])
        if row['state'] == 'done':
            self.skipTest('mix finished before it could be cancelled')
        self.assertEqual(row['state'], 'cancelled')
        self.assertEqual([p.name for p in self.output.iterdir()], [])
        self.assertNotIn(started['id'], [r.get('derived_from') for r in self.jobs.rows.values()])


if __name__ == '__main__':
    unittest.main()
