"""A film assembled by the Director becomes an ordinary entry of the Video history: copied under the usual name, playable, exportable, deletable."""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))

from video.video_delivery import ffmpeg_paths  # noqa: E402
from video.video_service import Jobs  # noqa: E402

RUNTIME = Path(os.environ.get('VISUALFORGE_VIDEO_RUNTIME') or ROOT / 'Runtime/Video')
FFMPEG, FFPROBE = ffmpeg_paths(RUNTIME)
SCENES = ['Photorealistic western film. A red car crosses a desert road at dawn.', 'Photorealistic western film. A woman steps out and closes the door.']


@unittest.skipUnless(FFMPEG.is_file() and FFPROBE.is_file(), 'bundled FFmpeg not found')
class RegisterFilmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        work = Path(self.temp.name)
        self.user = work / 'UserData'
        self.jobs = Jobs(argparse.Namespace(user=str(self.user), root=str(RUNTIME), models=str(work / 'models'), state=str(work / 'state')))
        self.out = work / 'Results'
        self.jobs.save_settings({'save_dir': str(self.out)})
        run = self.user / 'Director/runs/abc'
        run.mkdir(parents=True)
        self.film = run / 'film.mp4'
        subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24:duration=2', '-f', 'lavfi', '-i', 'sine=duration=2',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(self.film)], check=True)

    def register(self, **changes):
        return self.jobs.register_film(dict(dict(path=str(self.film), prompt='Дорога в пустыне', scenes=SCENES, quality='draft', title='Road', run='abc', name_prompt=SCENES[0], log='steps'), **changes))

    def test_the_film_gets_a_history_entry_with_a_file_in_the_save_folder(self):
        row = self.register()
        self.assertEqual((row['state'], row['engine'], row['mode'], row['frames'], row['width'], row['height']), ('done', 'director', 'director', 48, 320, 180))
        self.assertTrue(row['metadata']['has_audio'])
        self.assertTrue(row['saved_path'].endswith(' - Director.mp4'))
        self.assertTrue(Path(row['saved_path']).is_file())
        self.assertEqual(Path(row['saved_path']).parent, self.out)
        self.assertEqual(row['segment_prompts'], SCENES)                    # a film of scenes: the clip review refuses it
        self.assertEqual(self.jobs.export_source(row['id']), Path(row['saved_path']))        # the export and the mixer find it by its id
        self.assertTrue(Jobs(self.jobs.args).rows[row['id']]['state'] == 'done')             # and a restart finds it in the history file

    def test_the_entry_is_deleted_like_any_clip_and_takes_its_folder_with_it(self):
        row = self.register()
        folder = Path(self.jobs.rows[row['id']]['folder'])
        self.assertTrue((folder / 'backend.log').is_file())
        self.jobs.delete(dict(id=row['id']))
        self.assertFalse(Path(row['saved_path']).exists())
        self.assertFalse(folder.exists())
        self.assertNotIn(row['id'], self.jobs.rows)

    def test_only_a_file_in_the_directors_folder_with_a_description_is_accepted(self):
        outside = Path(self.temp.name) / 'outside.mp4'
        shutil.copyfile(self.film, outside)
        for changes in (dict(path=str(outside)), dict(path='relative.mp4'), dict(path=str(self.film.with_suffix('.mkv'))), dict(prompt=''), dict(scenes=['one'])):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.register(**changes)
        self.assertEqual(self.jobs.rows, {})
        self.assertEqual(list((self.user / 'Cache/Video/Jobs').glob('*')) if (self.user / 'Cache/Video/Jobs').exists() else [], [])


if __name__ == '__main__':
    unittest.main()
