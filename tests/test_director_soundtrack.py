"""The soundtrack of a film: lines placed in time, excerpts laid under the picture's own sound, and the ducking measured on real audio (real FFmpeg, no model, no GPU)."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))

from director import soundtrack  # noqa: E402
from video.video_delivery import DeliveryCancelled, DeliveryError, ffmpeg_paths  # noqa: E402

RUNTIME = Path(os.environ.get('VISUALFORGE_VIDEO_RUNTIME') or ROOT / 'Runtime/Video')
FFMPEG, FFPROBE = ffmpeg_paths(RUNTIME)


class MusicPartsTests(unittest.TestCase):
    """A film longer than the music engine's longest track gets several tracks, crossfaded (no loop of one track with a cut at the seam)."""

    def test_the_number_of_tracks_covers_the_film_with_a_crossfade_at_each_seam(self):
        track = dict(seconds=180, start=0.0, loop=True)
        self.assertEqual(soundtrack.music_parts(dict(track, loop=False), 120), 1)
        self.assertEqual(soundtrack.music_parts(track, 181), 2)
        self.assertEqual(soundtrack.music_parts(track, 600), 4)                     # 4 x 180 - 3 x 4 = 708 s
        self.assertEqual(soundtrack.music_parts(dict(track, seconds=60), 600), 11)
        self.assertEqual(soundtrack.music_parts(dict(track, start=300.0), 600), 2)  # a track that begins later in the film covers only the rest
        for film in range(181, 901, 7):
            for seconds in (60, 120, 180):
                with self.subTest(film=film, seconds=seconds):
                    count = soundtrack.music_parts(dict(track, seconds=seconds), film)
                    self.assertGreaterEqual(count * seconds - (count - 1) * soundtrack.MUSIC_CROSSFADE, film)
                    self.assertLess((count - 1) * seconds - (count - 2) * soundtrack.MUSIC_CROSSFADE, film if count > 1 else 1e9)

    @unittest.skipUnless(FFMPEG.is_file(), 'bundled FFmpeg not found')
    def test_tracks_are_joined_with_a_crossfade_into_one_file_of_the_asked_length(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            paths = []
            for index, frequency in enumerate((300, 600, 900)):
                path = work / ('t%d.wav' % index)
                subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'sine=frequency=%d:duration=10:sample_rate=44100' % frequency,
                                '-ac', '2', str(path)], check=True)
                paths.append(path)
            target = work / 'long.wav'
            soundtrack.crossfaded(FFMPEG, paths, 22.0, target)                       # 3 x 10 - 2 x 4 = 22 s
            info = subprocess.run([str(FFPROBE), '-v', 'error', '-show_entries', 'stream=sample_rate,channels:format=duration', '-of', 'default=nw=1', str(target)], capture_output=True, text=True, check=True).stdout
            self.assertIn('sample_rate=48000', info)
            self.assertIn('channels=2', info)
            self.assertAlmostEqual(float(re.search(r'duration=([0-9.]+)', info).group(1)), 22.0, delta=0.05)
            # the seam at 6 s is a crossfade: both tones are heard there, and the sound never drops out
            for second in (5.0, 6.0, 7.0):
                level = subprocess.run([str(FFMPEG), '-hide_banner', '-nostdin', '-ss', str(second), '-t', '0.5', '-i', str(target), '-af', 'volumedetect', '-f', 'null', '-'],
                                       capture_output=True, text=True).stderr
                self.assertGreater(float(re.search(r'mean_volume: (-?[0-9.]+) dB', level).group(1)), -30.0)            # a plain sine of this size is -21 dB; a dip to silence would be below -60


class PlacementTests(unittest.TestCase):
    def test_a_line_starts_with_its_scene_and_never_over_the_line_before(self):
        placed = soundtrack.place_lines([(0.3, 3.0), (2.0, 4.0), (8.0, 1.0)], 12.0)
        self.assertEqual(placed, [(0.3, 1.0), (3.55, 1.0), (8.0, 1.0)])                         # the second waits for the first and the gap of a quarter second

    def test_a_line_that_runs_over_the_end_is_sped_up_a_little_or_dropped(self):
        self.assertEqual(soundtrack.place_lines([(8.0, 2.0)], 10.0), [(8.0, 1.25)])               # 10 - 0.4 - 8 = 1.6 s of room for 2 s: 1.25 times faster
        self.assertEqual(soundtrack.place_lines([(7.0, 2.0)], 10.0), [(7.0, 1.0)])               # it fits as it is
        self.assertEqual(soundtrack.place_lines([(8.0, 4.0)], 10.0), [None])                         # 2.5 times faster is not speech
        self.assertEqual(soundtrack.place_lines([(8.0, 2.0)], 9.9), [None])                          # 1.33 times is more than the limit of 1.3
        self.assertEqual(soundtrack.place_lines([(9.8, 1.0)], 10.0), [None])                         # no room at all

    def test_a_dropped_line_leaves_room_for_the_next(self):
        placed = soundtrack.place_lines([(0.0, 2.0), (1.0, 30.0), (5.0, 2.0)], 12.0)
        self.assertIsNone(placed[1])
        self.assertEqual(placed[2], (5.0, 1.0))

    def test_scenes_start_where_the_lengths_before_them_end(self):
        self.assertEqual(soundtrack.scene_starts([dict(seconds=4.5), dict(seconds=5.5), dict(seconds=10)]), [0.0, 4.5, 10.0])


class GainTests(unittest.TestCase):
    def test_every_song_is_brought_to_one_level_before_its_own_gain(self):
        self.assertEqual(soundtrack.normalizing_gain(-16.0), 0.0)
        self.assertEqual(soundtrack.normalizing_gain(-30.0), 14.0)
        self.assertEqual(soundtrack.normalizing_gain(-8.0), -8.0)
        self.assertEqual(soundtrack.normalizing_gain(-80.0), 18.0)                                    # a nearly silent file is not boosted without end
        self.assertEqual(soundtrack.normalizing_gain(10.0), -24.0)
        self.assertEqual(soundtrack.normalizing_gain(None), 0.0)

    def test_the_scenes_own_sound_is_brought_down_to_a_bed_under_songs_and_voices(self):
        self.assertEqual(soundtrack.native_gain(-10.0), -16.0)                                       # a loud steady ambience goes down to -26 dB
        self.assertEqual(soundtrack.native_gain(-26.0), 0.0)
        self.assertEqual(soundtrack.native_gain(-30.0), 4.0)                                         # a quiet one is raised a little
        self.assertEqual(soundtrack.native_gain(-60.0), 6.0)
        self.assertEqual(soundtrack.native_gain(0.0), -18.0)                                         # but never silenced
        self.assertEqual(soundtrack.native_gain(None), 0.0)

    def test_cues_become_beds_with_the_gain_of_the_file_added(self):
        cues = [dict(offset=12.0, start=3.0, seconds=8.0, gain_db=-10.0, fade_in=1.0, fade_out=2.0), dict(offset=0.0, start=0.0, seconds=20.0, gain_db=-16.0, fade_in=1.5, fade_out=3.0, loop=True)]
        beds = soundtrack.beds_for(cues, {0: ('song.m4a', -26.0), 1: ('music.wav', None)})
        self.assertEqual([bed['gain_db'] for bed in beds], [0.0, -16.0])
        self.assertEqual([bed['loop'] for bed in beds], [False, True])
        track = soundtrack.generated_cue(dict(gain_db=-18.0, start=30.0, loop=True), 40.0)
        self.assertEqual((track['start'], track['seconds'], track['loop'], track['offset']), (30.0, 10.0, True, 0.0))
        self.assertEqual(soundtrack.generated_cue(dict(gain_db=-18.0), 40.0)['seconds'], 40.0)


class GraphTests(unittest.TestCase):
    bed = dict(path='song.m4a', offset=12.0, start=2.0, seconds=8.0, gain_db=-6.0, fade_in=1.0, fade_out=2.0)
    voice = dict(path='line-0.wav', start=4.0, tempo=1.25, gain_db=1.5)

    def count(self, text, word):
        return len(re.findall(word, text))

    def test_music_alone_gives_way_to_the_pictures_own_sound(self):
        text, inputs = soundtrack.graph(10.0, [self.bed], [])
        self.assertEqual(self.count(text, 'sidechaincompress'), 1)
        self.assertIn('afade=t=out:st=6.000:d=2.000', text)                                          # the fade-out begins 2 s before the end of the 8 s excerpt
        self.assertIn('adelay=2000:all=1', text)
        self.assertEqual(inputs, ['-ss', '12.000', '-t', '8.000', '-i', 'song.m4a'])

    def test_speech_alone_lowers_the_pictures_own_sound(self):
        text, inputs = soundtrack.graph(10.0, [], [self.voice])
        self.assertEqual(self.count(text, 'sidechaincompress'), 1)
        self.assertIn('atempo=1.2500', text)
        self.assertIn('adelay=4000:all=1', text)
        self.assertIn('volume=1.50dB', text)
        self.assertEqual(inputs, ['-i', 'line-0.wav'])

    def test_music_and_speech_together_are_ducked_by_both_in_turn(self):
        text, inputs = soundtrack.graph(10.0, [self.bed, dict(self.bed, loop=True, offset=0.0)], [self.voice, dict(self.voice, tempo=1.0)])
        self.assertEqual(self.count(text, 'sidechaincompress'), 3)
        self.assertEqual(self.count(text, 'amix=inputs=2'), 2)                                       # one for the beds and one for the lines
        self.assertEqual(self.count(text, 'amix=inputs=3'), 1)                                       # the last: the picture's sound, the music and the speech
        self.assertEqual(self.count(text, 'atempo'), 1)                                              # the line at normal speed has no tempo filter
        self.assertIn('-stream_loop', inputs)
        self.assertEqual(inputs.count('-i'), 4)
        for number in range(1, 5):                                                                   # every input is used exactly once
            self.assertEqual(self.count(text, r'\[%d:a\]' % number), 1)
        self.assertTrue(text.endswith('atrim=end_sample=480000,asetpts=PTS-STARTPTS[aout]'))

    def test_nothing_to_add_leaves_the_pictures_own_sound(self):
        text, inputs = soundtrack.graph(10.0, [], [])
        self.assertIn('[native]anull[mixed]', text)
        self.assertEqual(inputs, [])


def tone(path, frequency, seconds, level=0.25, stereo=False):
    subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'sine=frequency=%d:duration=%s:sample_rate=48000' % (frequency, seconds),
                    '-af', 'volume=%s' % level, '-ac', '2' if stereo else '1', str(path)], check=True)


def band(path, frequency, start, length):
    """The mean level in dB of what lies near `frequency` in a stretch of a file: a tone is measured alone even when other tones sound with it."""
    done = subprocess.run([str(FFMPEG), '-hide_banner', '-nostdin', '-ss', str(start), '-t', str(length), '-i', str(path), '-vn', '-af',
                           'bandpass=f=%d:width_type=h:w=60,volumedetect' % frequency, '-f', 'null', '-'], capture_output=True, text=True)
    found = re.search(r'mean_volume: (-?[\d.]+|-inf) dB', done.stderr)
    return -99.0 if found is None or found.group(1) == '-inf' else float(found.group(1))


def measured_audio(path):
    """Independent R128 summary from the decoded file, with float output so overshoots cannot be clipped by the measuring sink."""
    done = subprocess.run([str(FFMPEG), '-hide_banner', '-nostdin', '-i', str(path), '-vn', '-af', 'ebur128=peak=true',
                           '-c:a', 'pcm_f64le', '-f', 'null', '-'], capture_output=True, text=True, check=True)
    lufs = re.findall(r'I:\s+(-?[\d.]+) LUFS', done.stderr)
    peaks = re.findall(r'Peak:\s+(-?[\d.]+|-inf) dBFS', done.stderr)
    return float(lufs[-1]), float(peaks[-1])


@unittest.skipUnless(FFMPEG.is_file() and FFPROBE.is_file(), 'bundled FFmpeg not found')
class MixdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp(prefix='vf-director-mix-'))
        cls.film = cls.work / 'film.mp4'
        tone(cls.work / 'native.wav', 150, 10)
        subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24:duration=10', '-i', str(cls.work / 'native.wav'),
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(cls.film)], check=True)
        tone(cls.work / 'music.wav', 440, 12, 0.25, True)
        tone(cls.work / 'line.wav', 1000, 2, 0.5)
        cls.bed = dict(path=cls.work / 'music.wav', offset=1.0, start=0.0, seconds=10.0, gain_db=0.0, fade_in=0.0, fade_out=0.0)
        cls.line = dict(path=cls.work / 'line.wav', start=4.0, tempo=1.0, gain_db=0.0)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def mix(self, name, beds, voices, **options):
        output = self.work / name
        return output, soundtrack.mixdown(FFMPEG, FFPROBE, self.film, output, beds, voices, **options)

    def test_a_long_mix_graph_is_read_from_a_file_and_sounds_the_same(self):
        """A hundred lines of speech make a graph that cannot be a Windows command line: it is read from a file, the two measuring passes and the mix alike."""
        plain, plain_result = self.mix('inline.mp4', [self.bed], [self.line])
        with mock.patch('video.video_delivery.GRAPH_INLINE_LIMIT', 100):
            filed, filed_result = self.mix('filed.mp4', [self.bed], [self.line])
        self.assertTrue((self.work / 'mix-graph.txt').is_file() and (self.work / 'measure-graph.txt').is_file())
        self.assertAlmostEqual(filed_result['loudness']['makeup_gain_db'], plain_result['loudness']['makeup_gain_db'], delta=0.05)
        self.assertAlmostEqual(band(filed, 1000, 4.2, 1.5), band(plain, 1000, 4.2, 1.5), delta=0.3)
        self.assertAlmostEqual(band(filed, 440, 1.0, 2.0), band(plain, 440, 1.0, 2.0), delta=0.3)

    def test_the_music_gives_way_to_a_voice_and_comes_back(self):
        output, result = self.mix('both.mp4', [self.bed], [self.line])
        before, during, after = band(output, 440, 1.0, 2.0), band(output, 440, 4.6, 1.2), band(output, 440, 7.5, 2.0)
        self.assertLess(during, before - 8, (before, during))                                       # about 12 dB lower while the line is spoken
        self.assertGreater(after, before - 3, (before, after))                                      # and back at its level when the line is over
        self.assertGreater(band(output, 1000, 4.6, 1.2), -35)                                       # the voice itself is heard
        self.assertLess(band(output, 1000, 1.0, 2.0) - result['loudness']['makeup_gain_db'], -60)   # only where it was put, allowing for the common gain
        self.assertEqual(result['seconds'], 10.0)

    def test_the_pictures_own_sound_gives_way_to_a_voice_too(self):
        output, _ = self.mix('speech.mp4', [], [self.line])
        before, during = band(output, 150, 1.0, 2.0), band(output, 150, 4.6, 1.2)
        self.assertLess(during, before - 3, (before, during))
        self.assertGreater(band(output, 1000, 4.6, 1.2), -35)

    def test_the_music_alone_is_lowered_by_the_pictures_own_sound(self):
        output, result = self.mix('music.mp4', [self.bed], [])
        plain = band(self.work / 'music.wav', 440, 1.0, 2.0)
        # Remove only the common mastering gain when comparing with the unmixed source; the ducking itself must still reduce the music.
        self.assertLess(band(output, 440, 1.0, 2.0) - result['loudness']['makeup_gain_db'], plain - 3)
        self.assertGreater(band(output, 150, 1.0, 2.0), -40)

    def test_a_quiet_mix_reaches_the_target_with_a_bounded_linear_gain(self):
        output, result = self.mix('quiet-master.mp4', [], [], native_db=3.0)
        lufs, peak = measured_audio(output)
        report = result['loudness']
        self.assertLess(report['before_lufs'], -30)
        self.assertGreater(report['makeup_gain_db'], 0)
        self.assertLessEqual(report['makeup_gain_db'], soundtrack.MAX_MAKEUP_DB)
        self.assertAlmostEqual(lufs, soundtrack.TARGET_LUFS, delta=1.5)
        self.assertAlmostEqual(report['after_lufs'], lufs, delta=0.15)
        self.assertAlmostEqual(lufs - report['before_lufs'], report['makeup_gain_db'], delta=0.3)
        self.assertLess(peak, 0)

    def test_a_mix_below_the_boost_budget_does_not_get_unlimited_gain(self):
        output, result = self.mix('very-quiet-master.mp4', [], [], native_db=-6.0)
        report = result['loudness']
        self.assertEqual(report['makeup_gain_db'], soundtrack.MAX_MAKEUP_DB)
        lufs, _ = measured_audio(output)
        self.assertLess(lufs, soundtrack.TARGET_LUFS - 1.5)
        self.assertAlmostEqual(lufs - report['before_lufs'], soundtrack.MAX_MAKEUP_DB, delta=0.3)

    def test_a_loud_mix_is_attenuated_and_limited_before_aac(self):
        output, result = self.mix('loud-master.mp4', [], [], native_db=45.0)
        report = result['loudness']
        self.assertEqual(report['makeup_gain_db'], soundtrack.MIN_MAKEUP_DB)
        self.assertGreater(report['before_true_peak_db'], 0)
        _, peak = measured_audio(output)
        self.assertLessEqual(peak, 0)
        self.assertLessEqual(report['after_true_peak_db'], 0)
        self.assertLessEqual(result['max_db'], report['limiter_db'] + 0.8)  # decoded AAC may overshoot the PCM ceiling, but has headroom

    def test_mastering_preserves_the_music_to_voice_balance(self):
        reference = self.work / 'unmastered.wav'
        text, inputs = soundtrack.graph(10.0, [self.bed], [self.line], measure=True)
        subprocess.run([str(FFMPEG), '-v', 'error', '-nostdin', '-y', '-i', str(self.film), *inputs, '-filter_complex', text,
                        '-map', '[aout]', '-c:a', 'pcm_f32le', str(reference)], check=True)
        output, result = self.mix('balanced-master.mp4', [self.bed], [self.line])
        self.assertGreater(result['loudness']['makeup_gain_db'], 0)
        before = band(reference, 440, 4.6, 1.2) - band(reference, 1000, 4.6, 1.2)
        after = band(output, 440, 4.6, 1.2) - band(output, 1000, 4.6, 1.2)
        self.assertAlmostEqual(after, before, delta=0.5)

    def test_silence_gets_no_makeup_gain_and_is_not_published(self):
        film = self.work / 'zero-film.mp4'
        subprocess.run([str(FFMPEG), '-v', 'error', '-nostdin', '-y', '-i', str(self.film), '-af', 'volume=0',
                        '-c:v', 'copy', '-c:a', 'aac', str(film)], check=True)
        before = soundtrack.measure_loudness(FFMPEG, film, 10.0)
        self.assertIsNone(before['lufs'])
        self.assertIsNone(before['true_peak_db'])
        self.assertEqual(soundtrack.makeup_gain(before['lufs']), 0.0)
        output = self.work / 'zero-master.mp4'
        scans = []
        measure = soundtrack.measure_loudness

        def scan(*args, **kwargs):
            found = measure(*args, **kwargs)
            scans.append(found)
            return found

        with mock.patch.object(soundtrack, 'measure_loudness', side_effect=scan), self.assertRaisesRegex(DeliveryError, 'silent'):
            soundtrack.mixdown(FFMPEG, FFPROBE, film, output, [], [])
        self.assertEqual(len(scans), 2)
        self.assertIsNone(scans[-1]['lufs'])
        self.assertIsNone(scans[-1]['true_peak_db'])  # the encoded result also stayed exactly silent before rejection
        self.assertFalse(output.exists())

    def test_a_failed_or_cancelled_final_measurement_removes_the_unchecked_film(self):
        measure = soundtrack.measure_loudness
        for error in (DeliveryError('measurement failed'), DeliveryCancelled('stopped')):
            with self.subTest(error=type(error).__name__):
                output = self.work / 'unchecked.mp4'

                def scan(ffmpeg, film, *args, target=output, failure=error, **kwargs):
                    if Path(film) == target:
                        self.assertTrue(target.is_file())
                        raise failure
                    return measure(ffmpeg, film, *args, **kwargs)

                with mock.patch.object(soundtrack, 'measure_loudness', side_effect=scan), self.assertRaises(type(error)):
                    soundtrack.mixdown(FFMPEG, FFPROBE, self.film, output, [], [])
                self.assertFalse(output.exists())

    def test_the_picture_is_copied_and_the_sound_is_aac_stereo_of_the_length_of_the_film(self):
        output, _ = self.mix('check.mp4', [self.bed], [self.line])
        probe = subprocess.run([str(FFPROBE), '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', str(output)], capture_output=True, text=True, check=True).stdout
        source = subprocess.run([str(FFPROBE), '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', str(self.film)], capture_output=True, text=True, check=True).stdout
        import json
        found, original = json.loads(probe)['streams'], json.loads(source)['streams']
        video, first = next(s for s in found if s['codec_type'] == 'video'), next(s for s in original if s['codec_type'] == 'video')
        audio = next(s for s in found if s['codec_type'] == 'audio')
        self.assertEqual((video['nb_read_frames'], video['codec_name'], video['width']), (first['nb_read_frames'], first['codec_name'], first['width']))
        self.assertEqual((audio['codec_name'], audio['sample_rate'], audio['channels']), ('aac', '48000', 2))
        self.assertAlmostEqual(float(audio['duration']), 10.0, delta=0.1)

    def test_a_track_shorter_than_its_place_is_repeated(self):
        short = self.work / 'short.wav'
        tone(short, 440, 3, 0.25, True)
        bed = dict(self.bed, path=short, offset=0.0, loop=True)
        output, _ = self.mix('loop.mp4', [bed], [])
        self.assertGreater(band(output, 440, 7.0, 2.0), band(output, 440, 1.0, 2.0) - 3)
        silent, result = self.mix('once.mp4', [dict(bed, loop=False)], [])
        self.assertLess(band(silent, 440, 7.0, 2.0) - result['loudness']['makeup_gain_db'], -60)    # without the repeat the track ends at three seconds

    def test_a_cut_excerpt_starts_where_it_is_told_and_fades(self):
        steps = self.work / 'steps.wav'
        subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-filter_complex',
                        'sine=f=300:d=5:r=48000[a];sine=f=800:d=5:r=48000[b];[a][b]concat=n=2:v=0:a=1', '-ac', '2', str(steps)], check=True)
        bed = dict(path=steps, offset=4.0, start=2.0, seconds=6.0, gain_db=0.0, fade_in=1.0, fade_out=1.0)      # the last second of the 300 Hz half, then the 800 Hz half
        output, result = self.mix('excerpt.mp4', [bed], [])
        gain = result['loudness']['makeup_gain_db']
        self.assertLess(band(output, 800, 2.0, 0.9) - gain, -55)                                  # the 300 Hz second plays from 2 s to 3 s of the film
        self.assertGreater(band(output, 800, 3.3, 3.5), -30)                                         # then the 800 Hz half, from the offset
        self.assertLess(band(output, 800, 0.0, 1.6) - gain, -55)                                  # nothing before the start
        self.assertLess(band(output, 800, 8.3, 1.5) - gain, -55)                                  # nothing after the end of the excerpt (8 s)
        self.assertLess(band(output, 800, 7.6, 0.3), band(output, 800, 4.0, 0.5) - 8)                # and the last second is a fade-out

    def test_a_mix_nobody_can_hear_is_refused_and_leaves_no_file(self):
        output = self.work / 'silent.mp4'
        silent_film = self.work / 'silent-film.mp4'
        subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=24:duration=3',
                        '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-t', '3', str(silent_film)], check=True)
        with self.assertRaises(DeliveryError) as caught:
            soundtrack.mixdown(FFMPEG, FFPROBE, silent_film, output, [dict(self.bed, gain_db=-90.0, seconds=2.0)], [])
        self.assertIn('silent', str(caught.exception))
        self.assertFalse(output.exists())

    def test_a_stop_before_the_start_writes_nothing(self):
        cancel = threading.Event()
        cancel.set()
        output = self.work / 'stopped.mp4'
        with self.assertRaises(DeliveryCancelled):
            soundtrack.mixdown(FFMPEG, FFPROBE, self.film, output, [self.bed], [self.line], cancel=cancel)
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
