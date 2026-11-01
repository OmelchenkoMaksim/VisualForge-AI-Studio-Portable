"""Attachments of a film: pictures of any format and videos with limits, measured with the bundled FFmpeg (no model, no GPU)."""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))

from director import media, plan_rules  # noqa: E402
from video.video_delivery import ffmpeg_paths  # noqa: E402

RUNTIME = Path(os.environ.get('VISUALFORGE_VIDEO_RUNTIME') or ROOT / 'Runtime/Video')
FFMPEG, FFPROBE = ffmpeg_paths(RUNTIME)


def make_clip(path, seconds=4, size='640x360', cut=True):
    """Two patterns after each other (one hard cut) with a sine tone, or one pattern without the cut."""
    first = 'testsrc2=size=%s:rate=25:duration=%s' % (size, seconds / 2 if cut else seconds)
    command = [str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', first]
    if cut:
        command += ['-f', 'lavfi', '-i', 'smptebars=size=%s:rate=25:duration=%s' % (size, seconds / 2)]
    command += ['-f', 'lavfi', '-i', 'sine=duration=%s' % seconds]
    if cut:
        command += ['-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-map', '2:a']
    command += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(path)]
    subprocess.run(command, check=True)


class BodyTests(unittest.TestCase):
    def test_a_body_is_written_in_steps_and_a_broken_one_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'body.bin'
            self.assertEqual(media.save_body(io.BytesIO(b'x' * 2_500_000), 2_500_000, target, 10_000_000), 2_500_000)
            self.assertEqual(target.stat().st_size, 2_500_000)
            for stream, length, limit in ((io.BytesIO(b'x' * 10), 20, 100), (io.BytesIO(b'x' * 200), 200, 100), (io.BytesIO(b''), 0, 100)):
                target.unlink(missing_ok=True)
                with self.assertRaises(ValueError):
                    media.save_body(stream, length, target, limit)
                self.assertFalse(target.exists())


@unittest.skipUnless(FFMPEG.is_file() and FFPROBE.is_file(), 'bundled FFmpeg not found')
class DraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp(prefix='vf-director-'))
        cls.clip = cls.work / 'clip.mkv'
        make_clip(cls.clip)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def draft(self):
        return media.open_draft(self.work / 'cache', media.new_draft_id(), FFMPEG, FFPROBE)

    def test_a_picture_of_any_format_becomes_a_png_with_a_thumbnail(self):
        from PIL import Image
        draft = self.draft()
        buffer = io.BytesIO()
        Image.new('RGB', (900, 600), (200, 80, 40)).save(buffer, 'WEBP')
        first = draft.add_photo(buffer.getvalue(), 'car.webp')
        buffer = io.BytesIO()
        Image.new('RGB', (300, 500), (20, 80, 200)).save(buffer, 'GIF')
        second = draft.add_photo(buffer.getvalue(), 'hero.gif')
        self.assertEqual((first['number'], second['number'], first['kind']), (1, 2, 'photo'))
        self.assertEqual((first['width'], first['height']), (900, 600))
        self.assertTrue(first['thumb'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(draft.path(1).read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
        draft.remove(1)
        self.assertEqual([row['number'] for row in draft.items()], [2])
        self.assertEqual(draft.add_photo(buffer.getvalue(), 'again.gif')['number'], 3)          # numbers are never reused
        with self.assertRaises(ValueError):
            draft.add_photo(b'not a picture', 'x.bin')
        with self.assertRaises(ValueError):
            draft.remove(99)

    def test_a_video_is_converted_measured_and_its_upload_removed(self):
        draft = self.draft()
        upload = self.work / 'upload.part'
        shutil.copyfile(self.clip, upload)
        item = draft.add_video(upload, 'style.mkv')
        self.assertFalse(upload.exists())
        self.assertEqual((item['kind'], item['width'], item['height']), ('video', 640, 360))
        self.assertAlmostEqual(item['seconds'], 4.0, delta=0.3)
        self.assertTrue(item['has_audio'])
        self.assertEqual(draft.path(item['number']).suffix, '.mp4')
        self.assertGreaterEqual(item['facts']['cuts'], 1)
        self.assertTrue(item['facts']['colours'] and 0 <= item['facts']['brightness'] <= 100)
        self.assertIn('hard cuts', media.describe(item))

    def test_a_video_over_ten_minutes_is_refused_before_any_conversion(self):
        long_clip = self.work / 'long.mp4'
        subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'color=c=black:size=64x64:rate=1:duration=%d' % (plan_rules.MAX_VIDEO_SECONDS + 5),
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(long_clip)], check=True)
        draft = self.draft()
        with self.assertRaises(ValueError) as caught:
            draft.add_video(long_clip, 'long.mp4')
        self.assertIn('10', str(caught.exception))
        self.assertFalse(long_clip.exists())
        self.assertEqual(draft.items(), [])
        broken = self.work / 'broken.mp4'
        broken.write_bytes(b'this is not a video' * 100)
        with self.assertRaises(ValueError):
            draft.add_video(broken, 'broken.mp4')

    def test_a_draft_is_named_by_a_checked_id_and_old_ones_are_pruned(self):
        root = self.work / 'prune'
        with self.assertRaises(ValueError):
            media.open_draft(root, '../../x', FFMPEG, FFPROBE)
        old, fresh = media.new_draft_id(), media.new_draft_id()
        for ident in (old, fresh):
            media.open_draft(root, ident, FFMPEG, FFPROBE)
        stamp = time.time() - 9 * 86400
        os.utime(root / 'drafts' / old, (stamp, stamp))
        self.assertEqual(media.prune_drafts(root, 7), 1)
        self.assertFalse((root / 'drafts' / old).exists())
        self.assertTrue((root / 'drafts' / fresh).exists())


def make_sound(path, graph, *extra):
    """A sound file from an FFmpeg filter graph (`graph` ends in an unnamed audio output, so that concat works)."""
    subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-filter_complex', graph, *extra, str(path)], check=True)


SONG_GRAPH = ('sine=f=220:d=10:r=44100,volume=0.05[a];sine=f=440:d=15:r=44100,volume=0.8[b];sine=f=330:d=15:r=44100,volume=0.05[c];[a][b][c]concat=n=3:v=0:a=1')


def speech_graph(seconds, silence=2):
    """Pink noise (it stands in for speech) after some silence, mono."""
    return 'anullsrc=r=44100:cl=mono,atrim=duration=%s[s];anoisesrc=d=%s:c=pink:r=44100:a=0.3[n];[s][n]concat=n=2:v=0:a=1' % (silence, seconds)


class StretchTests(unittest.TestCase):
    def test_a_song_is_split_into_quiet_medium_and_loud_stretches(self):
        levels = [-50.0] * 10 + [-26.0] * 15 + [-50.0] * 15
        found = media.stretches(levels)
        self.assertEqual([row[2] for row in found], ['quiet', 'loud', 'quiet'])
        for edge, expected in ((found[0][1], 10), (found[1][1], 25)):                              # the levels are smoothed over a few seconds, so an edge is only that exact
            self.assertAlmostEqual(edge, expected, delta=3)
        self.assertEqual((found[0][0], found[-1][1]), (0.0, 40.0))
        self.assertEqual(media.stretches([-20.0] * 30), [[0.0, 30.0, 'medium']])                    # a song that does not move has no loud part
        self.assertEqual(media.stretches([]), [])

    def test_a_short_dip_is_part_of_the_stretch_around_it(self):
        levels = [-20.0] * 30 + [-40.0] * 3 + [-20.0] * 30
        self.assertEqual([row[2] for row in media.stretches(levels)], ['medium'])

    def test_a_long_song_has_a_handful_of_stretches_not_hundreds(self):
        levels = [(-20.0 if (index // 20) % 2 else -40.0) for index in range(3600)]            # an hour that changes every twenty seconds
        self.assertLessEqual(len(media.stretches(levels)), 20)

    def test_levels_are_averaged_as_energy(self):
        self.assertAlmostEqual(media.mean_level([-10.0, -10.0]), -10.0, places=1)
        self.assertGreater(media.mean_level([-10.0, -70.0]), -14.0)                                   # one loud second is not hidden by a quiet one
        self.assertIsNone(media.mean_level([]))

    def test_a_clock_reads_minutes_and_hours(self):
        self.assertEqual([media.clock(value) for value in (0, 59.6, 192, 3599, 3600, 3725)], ['0:00', '1:00', '3:12', '59:59', '1:00:00', '1:02:05'])


@unittest.skipUnless(FFMPEG.is_file() and FFPROBE.is_file(), 'bundled FFmpeg not found')
class SoundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp(prefix='vf-director-sound-'))
        cls.song = cls.work / 'song.wav'
        make_sound(cls.song, SONG_GRAPH)
        cls.speech = cls.work / 'speech.wav'
        make_sound(cls.speech, speech_graph(40))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def draft(self):
        return media.open_draft(self.work / 'cache', media.new_draft_id(), FFMPEG, FFPROBE)

    def upload(self, source):
        target = self.work / ('upload-%s.part' % media.new_draft_id()[:8])
        shutil.copyfile(source, target)
        return target

    def test_a_song_is_converted_measured_and_described(self):
        draft = self.draft()
        upload = self.upload(self.song)
        item = draft.add_song(upload, 'my song.wav')
        self.assertFalse(upload.exists())
        self.assertEqual((item['kind'], item['group'], item['number']), ('audio', 'song', 1))
        self.assertAlmostEqual(item['seconds'], 40.0, delta=0.3)
        self.assertEqual(draft.path(1).suffix, '.m4a')
        self.assertTrue(item['thumb'].startswith('data:image/jpeg;base64,'))
        names = [row[2] for row in item['facts']['stretches']]
        self.assertIn('loud', names)
        self.assertIn('quiet', names)
        loud = next(row for row in item['facts']['stretches'] if row[2] == 'loud')
        self.assertAlmostEqual(loud[0], 10, delta=3)
        self.assertAlmostEqual(loud[1], 25, delta=3)
        text = media.describe(item)
        self.assertIn('song, 0:40 long', text)
        self.assertIn('by time: quiet', text)

    def test_a_song_of_an_hour_and_more_is_refused_before_it_is_converted(self):
        long_song = self.work / 'long.mp3'
        make_sound(long_song, 'anullsrc=r=8000:cl=mono,atrim=duration=%d' % (plan_rules.MAX_SONG_SECONDS + 120), '-c:a', 'libmp3lame', '-b:a', '8k')
        draft = self.draft()
        with self.assertRaises(ValueError) as caught:
            draft.add_song(long_song, 'long.mp3')
        self.assertIn('60', str(caught.exception))
        self.assertFalse(long_song.exists())
        self.assertEqual(draft.items(), [])

    def test_a_voice_sample_loses_its_silence_and_keeps_half_a_minute(self):
        draft = self.draft()
        item = draft.add_voice(self.upload(self.speech), 'voice.wav', 'narrator')
        self.assertEqual((item['kind'], item['group'], draft.path(1).suffix), ('voice', 'narrator', '.wav'))
        self.assertAlmostEqual(item['seconds'], plan_rules.VOICE_SAMPLE_SECONDS, delta=0.5)         # 40 s of speech after 2 s of silence: the first 30 s are kept
        probe = media.probe_audio(FFPROBE, draft.path(1))
        self.assertEqual((probe['sample_rate'], probe['channels']), (24000, 1))
        self.assertIn('voice sample', media.describe(item))

    def test_a_sample_without_speech_or_with_too_little_is_refused(self):
        draft = self.draft()
        quiet, short = self.work / 'quiet.wav', self.work / 'short.wav'
        make_sound(quiet, 'anullsrc=r=44100:cl=mono,atrim=duration=8')
        make_sound(short, speech_graph(1.5, 0.5))
        for path in (quiet, short):
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                draft.add_voice(path, path.name, 'narrator')
            self.assertFalse(path.exists())
        self.assertEqual((draft.items(), list(draft.folder.glob('voice-*'))), ([], []))              # nothing is kept of a refused sample

    def test_a_group_has_a_limit_and_takes_only_its_own_kind_of_file(self):
        draft = self.draft()
        for index in range(plan_rules.LIMIT_OF['narrator']):
            draft.add_voice(self.upload(self.speech), 'v%d.wav' % index, 'narrator')
        with self.assertRaises(ValueError) as caught:
            draft.add_voice(self.upload(self.speech), 'extra.wav', 'narrator')
        self.assertIn(str(plan_rules.LIMIT_OF['narrator']), str(caught.exception))
        draft.add_voice(self.upload(self.speech), 'hero.wav', 'character')                          # another group has its own room
        self.assertEqual([row['group'] for row in draft.items()], ['narrator'] * 3 + ['character'])
        for call in (lambda: draft.add_photo(b'x', 'a.png', 'song'), lambda: draft.add_song(self.upload(self.song), 'a.wav', 'hero'), lambda: draft.add_voice(self.upload(self.speech), 'a.wav', 'nobody')):
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(list(self.work.glob('upload-*.part')), [])                                  # the uploads of the refused calls are gone too

    def test_uploads_at_the_same_time_neither_share_a_number_nor_pass_the_limit(self):
        draft = self.draft()
        numbers, refused = [], []

        def add(index):
            try:
                numbers.append(draft.add_voice(self.upload(self.speech), 'v%d.wav' % index, 'narrator')['number'])
            except ValueError as error:
                refused.append(str(error))
        threads = [threading.Thread(target=add, args=(index,)) for index in range(7)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        self.assertEqual((len(numbers), len(set(numbers)), len(refused)), (3, 3, 4))                 # three places in the group, however the seven uploads interleave
        self.assertEqual(sorted(row['number'] for row in draft.items()), sorted(numbers))
        self.assertEqual(sorted(path.name for path in draft.folder.glob('voice-*')), sorted('voice-%d.wav' % number for number in numbers))     # the refused left no file

    def test_different_kinds_made_at_once_all_reach_the_index(self):
        from PIL import Image
        draft = self.draft()
        buffer = io.BytesIO()
        Image.new('RGB', (40, 30), (9, 8, 7)).save(buffer, 'PNG')
        jobs = [lambda: draft.add_photo(buffer.getvalue(), 'p.png', 'place'), lambda: draft.add_song(self.upload(self.song), 's.wav'), lambda: draft.add_voice(self.upload(self.speech), 'v.wav', 'character'),
                lambda: draft.add_photo(buffer.getvalue(), 'q.png', 'hero')]
        threads = [threading.Thread(target=job) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        items = draft.items()
        self.assertEqual(sorted(row['group'] for row in items), ['character', 'hero', 'place', 'song'])
        self.assertEqual(len({row['number'] for row in items}), 4)
        self.assertEqual(draft.add_photo(buffer.getvalue(), 'r.png', 'hero')['number'], 5)             # and the counter went on past all of them

    def test_a_refused_file_gives_its_place_back(self):
        draft = self.draft()
        quiet = self.work / 'quiet-again.wav'
        make_sound(quiet, 'anullsrc=r=44100:cl=mono,atrim=duration=8')
        for _ in range(plan_rules.LIMIT_OF['narrator'] + 1):
            with self.assertRaises(ValueError):
                draft.add_voice(shutil.copyfile(quiet, self.work / ('again-%s.part' % media.new_draft_id()[:6])), 'q.wav', 'narrator')
        for index in range(plan_rules.LIMIT_OF['narrator']):                                          # failed attempts did not use up the places
            draft.add_voice(self.upload(self.speech), 'v%d.wav' % index, 'narrator')
        self.assertEqual(len(draft.items()), plan_rules.LIMIT_OF['narrator'])
        self.assertEqual(media.PENDING.get(str(draft.folder)), None)

    def test_a_picture_group_refuses_the_eleventh_hero(self):
        from PIL import Image
        draft = self.draft()
        buffer = io.BytesIO()
        Image.new('RGB', (40, 30), (1, 2, 3)).save(buffer, 'PNG')
        for _ in range(plan_rules.LIMIT_OF['hero']):
            draft.add_photo(buffer.getvalue(), 'h.png', 'hero')
        with self.assertRaises(ValueError):
            draft.add_photo(buffer.getvalue(), 'h.png', 'hero')
        self.assertEqual(draft.add_photo(buffer.getvalue(), 'place.png', 'place')['group'], 'place')

    def test_an_attachment_can_be_named_and_the_name_is_cleaned(self):
        draft = self.draft()
        draft.add_voice(self.upload(self.speech), 'v.wav', 'narrator')
        self.assertEqual(draft.set_label(1, '  Алексей \n  капитан  '), 'Алексей капитан')
        self.assertEqual(len(draft.set_label(1, 'я' * 200)), media.LABEL_LIMIT)
        self.assertEqual(draft.items()[0]['label'], 'я' * media.LABEL_LIMIT)
        self.assertEqual(draft.set_label(1, None), '')
        with self.assertRaises(ValueError):
            draft.set_label(9, 'x')

    def test_a_file_that_only_points_elsewhere_is_not_followed(self):
        draft = self.draft()
        for name, body in (('list.m3u8', b'#EXTM3U\n#EXTINF:5,\nfile:///C:/Windows/win.ini\n'), ('list.ffconcat', b"ffconcat version 1.0\nfile 'C:/Windows/win.ini'\n"),
                           ('page.sdp', b'v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=x\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\nm=audio 5004 RTP/AVP 0\r\n')):
            path = self.work / name
            path.write_bytes(body)
            with self.subTest(name=name):
                for add in (lambda path=path, name=name: draft.add_song(self.upload(path), name), lambda path=path, name=name: draft.add_voice(self.upload(path), name, 'narrator'),
                            lambda path=path, name=name: draft.add_video(self.upload(path), name)):
                    with self.assertRaises(ValueError):
                        add()
        self.assertEqual(draft.items(), [])

    def test_a_draft_from_before_the_groups_gets_a_group_from_its_kind(self):
        draft = self.draft()
        draft.index.write_text('{"items": [{"number": 1, "kind": "photo"}, {"number": 2, "kind": "video"}], "next": 3}', encoding='utf-8')
        self.assertEqual([row['group'] for row in draft.items()], ['hero', 'video'])


def picture(size, left=(200, 30, 30), right=None):
    """A PNG of the given size: one colour, or two halves (left and right)."""
    from PIL import Image
    image = Image.new('RGB', size, left)
    if right:
        image.paste(Image.new('RGB', (size[0] - size[0] // 2, size[1]), right), (size[0] // 2, 0))
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


class FramingTests(unittest.TestCase):
    """The picture model draws on the shape of the first picture it is given: the pictures of a scene are ordered and, when needed, put on a canvas of the film's shape."""

    def shape(self, png):
        from PIL import Image
        return Image.open(io.BytesIO(png)).size

    def test_the_pictures_that_have_the_shape_of_the_film_go_first_and_the_rest_keep_their_order(self):
        tall, wide, other_tall, other_wide = picture((200, 300)), picture((300, 200)), picture((100, 150)), picture((150, 100))
        order, pictures, note = media.frame_pictures([tall, other_tall, wide, other_wide], 'landscape')
        self.assertEqual((order, note), ([2, 3, 0, 1], ''))
        self.assertEqual(pictures, [wide, other_wide, tall, other_tall])
        order, pictures, note = media.frame_pictures([wide, tall, other_wide], 'portrait')
        self.assertEqual((order, pictures, note), ([1, 0, 2], [tall, wide, other_wide], ''))
        self.assertEqual(media.frame_pictures([wide, other_wide], 'landscape')[:2], ([0, 1], [wide, other_wide]))         # nothing to change: nothing is changed
        self.assertEqual(media.frame_pictures([], 'landscape'), ([], [], ''))

    def test_when_no_picture_has_the_shape_the_first_one_is_put_on_a_canvas_of_it(self):
        tall = picture((200, 300), left=(250, 0, 0), right=(0, 0, 250))
        order, pictures, note = media.frame_pictures([tall], 'landscape')
        self.assertEqual((order, note), ([0], media.PAD_NOTE['landscape']))
        self.assertEqual(self.shape(pictures[0]), media.FRAME_CANVAS['landscape'])
        from PIL import Image
        canvas = Image.open(io.BytesIO(pictures[0])).convert('RGB')
        width, height = canvas.size
        left, right = canvas.getpixel((width // 2 - 10, height // 2)), canvas.getpixel((width // 2 + 10, height // 2))
        self.assertTrue(left[0] > 200 and left[2] < 60, left)                                                          # the photo itself sits in the middle, untouched
        self.assertTrue(right[2] > 200 and right[0] < 60, right)
        edge = canvas.getpixel((5, height // 2))
        self.assertTrue(edge[0] > 150 and edge[2] < 100, edge)                                                         # and its blurred copy fills what is left on the sides
        order, pictures, note = media.frame_pictures([picture((300, 200)), picture((200, 300))], 'portrait')
        self.assertEqual((order, note, self.shape(pictures[0])), ([1, 0], '', (200, 300)))                              # a portrait film wants the tall one first
        order, pictures, note = media.frame_pictures([picture((300, 200))], 'portrait')
        self.assertEqual((note, self.shape(pictures[0])), (media.PAD_NOTE['portrait'], media.FRAME_CANVAS['portrait']))

    def test_a_square_picture_has_the_shape_of_neither_film(self):
        square = picture((256, 256))
        for orientation in ('landscape', 'portrait'):
            with self.subTest(orientation=orientation):
                order, pictures, note = media.frame_pictures([square], orientation)
                self.assertEqual((order, note, self.shape(pictures[0])), ([0], media.PAD_NOTE[orientation], media.FRAME_CANVAS[orientation]))


if __name__ == '__main__':
    unittest.main()
