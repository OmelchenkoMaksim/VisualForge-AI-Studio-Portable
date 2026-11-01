"""The sound of a film: voices, songs and generated music laid under the picture's own sound, written into the assembled film.

Three buses are mixed. The picture's own sound (the scenes' noise and ambience that LTX draws with the picture) is the floor; the music (excerpts of the person's songs
and the generated track) goes under it; the speech (narrators and characters) goes over everything. Two rules make a mix that can be understood: the music gives way
to the speech and to the picture's own sound, and that sound gives way to the speech. Each is a compressor whose detector listens to the louder bus
(`sidechaincompress`): the music falls by about ten decibels while someone speaks and returns in about half a second.

Songs and tracks are first brought to one average level (`REFERENCE_DB`), so that the `gain_db` of a cue means the same for a quiet ballad and a loud march.
The finished mix gets one bounded loudness gain before limiting; the sidechain detectors never see that gain, so their balance stays the same.
"""
import math
from pathlib import Path

from ui_strings import source as ui_text
from video.video_delivery import DeliveryError, probe as probe_film, run_tool
from video.video_mixer import loudness

SAMPLE_RATE = 48000
REFERENCE_DB = -16.0              # the average level (RMS) songs and tracks are brought to before their own gain_db is applied
LINE_GAP = 0.25                   # silence kept between two lines of speech, in seconds
FILM_TAIL = 0.4                   # the last word ends this long before the film does
MAX_TEMPO = 1.3                   # a line that runs over is sped up at most this much (more is not speech any more); beyond that it is dropped
# Leave headroom for AAC reconstruction: a limiter near full scale still produced decoded peaks above 0 dBFS in a real film.
LIMIT = 0.8
TARGET_LUFS = -18.0
MIN_MAKEUP_DB, MAX_MAKEUP_DB = -6.0, 14.0
DURATION_TOLERANCE = 0.15
# The `threshold` of FFmpeg's sidechaincompress is not a level in dBFS: a key of -14 dB RMS needs about 0.002 to push the music down 19 dB, and 0.04 (the value the Video mixer
# uses) hardly moves it. The three profiles were measured with sine keys of known level (reduction of a 440 Hz tone at -12 dBFS):
#   speech over music    threshold 0.002, ratio 8:  key -14 dB RMS -> 19 dB, -20 -> 14 dB, -26 -> 8 dB, -32 -> 3 dB, -40 -> none; speech at -18 LUFS lowers the music by about 14 dB
#   speech over the picture's sound     0.004, ratio 3:  about 6 dB under the same speech
#   the picture's sound over music      0.008, ratio 3:  about 2 dB under a quiet ambience, up to 6 dB under a loud one (the song stays the soundtrack)
SPEECH_DUCKING = 'sidechaincompress=threshold=0.002:ratio=8:attack=15:release=600:makeup=1'
NATIVE_DUCKING = 'sidechaincompress=threshold=0.004:ratio=3:attack=20:release=500:makeup=1'
SOUND_DUCKING = 'sidechaincompress=threshold=0.008:ratio=3:attack=30:release=600:makeup=1'


NATIVE_DB = -26.0                 # the average level (RMS) the scenes' own sound is brought to once something is laid over it


def native_gain(mean_db):
    """The gain in dB for the picture's own sound under songs and voices. The scenes' sound comes out of the assembly levelled to a loud peak (LEVELING in
    assemble.py), which leaves a steady ambience near -10 dB RMS: left there it would drown a song at -22 dB RMS and a voice at -20. Quieter sound is raised a little at most."""
    if mean_db is None:
        return 0.0
    return round(max(-18.0, min(6.0, NATIVE_DB - mean_db)), 2)


def normalizing_gain(mean_db):
    """The gain in dB that brings a song of average level `mean_db` to the reference level; none for a song that could not be measured, and never more than a loud boost."""
    if mean_db is None:
        return 0.0
    return round(max(-24.0, min(18.0, REFERENCE_DB - mean_db)), 2)


def place_lines(wanted, film_seconds):
    """Where each line of speech starts: [(earliest start, seconds)] in film order -> [(start, tempo)], None for a line that does not fit.

    A line starts when its scene does (plus its delay) or when the one before it has finished, whichever is later. A line that would run past the end of the film is
    sped up to fit when that takes at most `MAX_TEMPO`, otherwise it is dropped: a sentence cut in the middle is worse than a missing one."""
    placed, clock = [], 0.0
    for earliest, seconds in wanted:
        start = max(earliest, clock)
        room = film_seconds - FILM_TAIL - start
        tempo = 1.0 if seconds <= room else (seconds / room if room > 0.5 else None)
        if tempo is None or tempo > MAX_TEMPO:
            placed.append(None)
            continue
        placed.append((round(start, 3), round(tempo, 4)))
        clock = start + seconds / tempo + LINE_GAP
    return placed


def _bus(labels, name, total):
    """The filter that sums the streams `labels` into the bus `name` and pads it to the length of the film, or None for no streams."""
    if not labels:
        return None
    if len(labels) == 1:
        return '%sapad=whole_dur=%.3f[%s]' % (labels[0], total, name)
    return '%samix=inputs=%d:normalize=0:duration=longest,apad=whole_dur=%.3f[%s]' % (''.join(labels), len(labels), total, name)


def graph(total, beds, voices, native_db=0.0, makeup_db=0.0, measure=False):
    """-> (filter_complex text, input arguments after the film). Input 0 is the film, then one input per bed and one per voice line.

    `beds`: dict(path, offset, start, seconds, gain_db, fade_in, fade_out) - an excerpt of `seconds` taken `offset` into the file and placed `start` seconds into the film.
    `voices`: dict(path, start, tempo, gain_db) - one line of speech.
    `measure` omits only the final gain and limiter, keeping the same ducking and exact audible span for the measuring pass."""
    parts = ['[0:a]aresample=%d,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=%.2fdB,apad=whole_dur=%.3f[native]' % (SAMPLE_RATE, native_db, total)]
    inputs, bed_labels, voice_labels = [], [], []
    for bed in beds:
        number = len(bed_labels) + 1
        chain = ['aresample=%d' % SAMPLE_RATE, 'aformat=sample_fmts=fltp:channel_layouts=stereo', 'asetpts=PTS-STARTPTS']
        if bed['fade_in']:
            chain.append('afade=t=in:st=0:d=%.3f' % bed['fade_in'])
        if bed['fade_out']:
            chain.append('afade=t=out:st=%.3f:d=%.3f' % (bed['seconds'] - bed['fade_out'], bed['fade_out']))
        chain.append('volume=%.2fdB' % bed['gain_db'])
        if bed['start'] > 0:
            chain.append('adelay=%d:all=1' % round(bed['start'] * 1000))
        parts.append('[%d:a]%s[b%d]' % (number, ','.join(chain), number))
        # An excerpt is read from its offset; a track shorter than its place in the film is repeated (`loop`) and cut where the cue ends.
        inputs += (['-stream_loop', '-1'] if bed.get('loop') else ['-ss', '%.3f' % bed['offset']]) + ['-t', '%.3f' % bed['seconds'], '-i', str(Path(bed['path']))]
        bed_labels.append('[b%d]' % number)
    for voice in voices:
        number = len(bed_labels) + len(voice_labels) + 1
        chain = ['aresample=%d' % SAMPLE_RATE, 'aformat=sample_fmts=fltp:channel_layouts=stereo', 'asetpts=PTS-STARTPTS']
        if abs(voice['tempo'] - 1.0) > 0.001:
            chain.append('atempo=%.4f' % voice['tempo'])
        chain.append('volume=%.2fdB' % voice['gain_db'])
        if voice['start'] > 0:
            chain.append('adelay=%d:all=1' % round(voice['start'] * 1000))
        parts.append('[%d:a]%s[v%d]' % (number, ','.join(chain), number))
        inputs += ['-i', str(Path(voice['path']))]
        voice_labels.append('[v%d]' % number)
    parts += [bus for bus in (_bus(bed_labels, 'music', total), _bus(voice_labels, 'speech', total)) if bus]
    music, speech = bool(bed_labels), bool(voice_labels)
    if music and speech:
        parts += ['[native]asplit=2[nk][nm]', '[speech]asplit=3[sk1][sk2][sm]',
                  '[nm][sk1]%s[natural]' % NATIVE_DUCKING, '[music][sk2]%s[low]' % SPEECH_DUCKING, '[low][nk]%s[music_low]' % SOUND_DUCKING,
                  '[natural][music_low][sm]amix=inputs=3:normalize=0:duration=longest[mixed]']
    elif speech:
        parts += ['[speech]asplit=2[sk][sm]', '[native][sk]%s[natural]' % NATIVE_DUCKING, '[natural][sm]amix=inputs=2:normalize=0:duration=longest[mixed]']
    elif music:
        parts += ['[native]asplit=2[nk][nm]', '[music][nk]%s[music_low]' % SOUND_DUCKING, '[nm][music_low]amix=inputs=2:normalize=0:duration=longest[mixed]']
    else:
        parts.append('[native]anull[mixed]')
    finish = '' if measure else 'volume=%.3fdB,alimiter=limit=%.2f:level=0,' % (makeup_db, LIMIT)
    parts.append('[mixed]%sapad=whole_dur=%.3f,atrim=end_sample=%d,asetpts=PTS-STARTPTS[aout]' % (finish, total, int(round(total * SAMPLE_RATE))))
    return ';'.join(parts), inputs


def measure_loudness(ffmpeg, film, total, text=None, inputs=(), cancel=None):
    """Integrated LUFS and decoded sample/true peaks, before mastering when `text` is a mix graph, otherwise from the finished AAC.

    R128 metadata goes to stdout so the existing cancellable runner owns this pass too. Float output avoids the 16-bit clamp of volumedetect,
    which hid decoded AAC peaks above full scale. A gated-out signal has no measurable LUFS and must not be amplified as if it were a quiet programme.
    """
    meter = 'ebur128=metadata=1:peak=sample+true:framelog=quiet,ametadata=mode=print:file=-'
    command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', str(film), *inputs]
    if text is not None:
        command += ['-filter_complex', text + ';[aout]' + meter + '[metered]', '-map', '[metered]']
    else:
        command += ['-map', '0:a:0', '-vn', '-af', meter]
    command += ['-t', '%.3f' % total, '-c:a', 'pcm_f64le', '-f', 'null', '-']
    output = run_tool(command, int(600 + total * 6), cancel).decode('utf-8', errors='replace')
    values = {}
    for line in output.splitlines():
        key, _, value = line.partition('=')
        if key in ('lavfi.r128.I', 'lavfi.r128.sample_peak', 'lavfi.r128.true_peak'):
            number = float(value)
            if not math.isfinite(number):
                raise DeliveryError(ui_text('director.sound_loudness_measurement_failed'))
            values[key] = number if key.endswith('.I') else max(number, values.get(key, 0.0))
    if len(values) != 3:
        raise DeliveryError(ui_text('director.sound_loudness_measurement_failed'))
    integrated = values['lavfi.r128.I']
    return dict(lufs=round(integrated, 3) if integrated > -70.0 else None,
                sample_peak_db=round(20 * math.log10(values['lavfi.r128.sample_peak']), 3) if values['lavfi.r128.sample_peak'] > 0 else None,
                true_peak_db=round(20 * math.log10(values['lavfi.r128.true_peak']), 3) if values['lavfi.r128.true_peak'] > 0 else None)


def makeup_gain(lufs):
    """One constant gain for the whole mix; silence or an unmeasurable signal gets no boost."""
    return round(max(MIN_MAKEUP_DB, min(MAX_MAKEUP_DB, TARGET_LUFS - lufs)), 3) if lufs is not None else 0.0


def mixdown(ffmpeg, ffprobe, film, output, beds, voices, native_db=0.0, cancel=None, progress=None):
    """Write `film` with its sound rebuilt to `output` (the picture is copied, not encoded again) and check the result; returns levels and before/after loudness.

    `beds` and `voices` are as in `graph`."""
    source = probe_film(ffprobe, film)
    total = round(source['duration'], 3)
    text, inputs = graph(total, beds, voices, native_db, measure=True)
    before = measure_loudness(ffmpeg, film, total, text, inputs, cancel)
    gain = makeup_gain(before['lufs'])
    text, inputs = graph(total, beds, voices, native_db, makeup_db=gain)
    command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-progress', 'pipe:1', '-nostats', '-i', str(film), *inputs,
               '-filter_complex', text, '-map', '0:v:0', '-map', '[aout]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', str(SAMPLE_RATE), '-ac', '2',
               '-t', '%.3f' % total, '-movflags', '+faststart', str(output)]
    try:
        run_tool(command, int(600 + total * 6), cancel, (lambda done: progress(min(1.0, done / total))) if progress else None)
        result = probe_film(ffprobe, output)
        levels = loudness(ffmpeg, output)
        after = measure_loudness(ffmpeg, output, total, cancel=cancel)
        problems = []
        if not result['has_audio'] or result['audio']['codec'] != 'aac' or (result['audio']['sample_rate'], result['audio']['channels']) != (SAMPLE_RATE, 2):
            problems.append('the sound is not AAC stereo at 48 kHz')
        if abs(result['duration'] - source['duration']) > DURATION_TOLERANCE:
            problems.append('the film is %.2f s long instead of %.2f s' % (result['duration'], source['duration']))
        if levels['mean'] is None or levels['mean'] < -70:
            problems.append('the sound is silent')
        if after['true_peak_db'] is not None and after['true_peak_db'] > 0.0:
            problems.append(ui_text('director.sound_decoded_peak_above_zero'))
        if problems:
            raise DeliveryError('The mix failed its check: ' + '; '.join(problems))
        return dict(seconds=round(result['duration'], 3), mean_db=levels['mean'], max_db=after['sample_peak_db'],
                    loudness=dict(target_lufs=TARGET_LUFS, before_lufs=before['lufs'], after_lufs=after['lufs'], makeup_gain_db=gain,
                                  before_true_peak_db=before['true_peak_db'], after_true_peak_db=after['true_peak_db'], limiter_db=round(20 * math.log10(LIMIT), 3)))
    except BaseException:
        # A stop or failed post-encode measurement must not leave an unchecked film that looks ready to register.
        Path(output).unlink(missing_ok=True)
        raise


def beds_for(cues, files):
    """The `beds` of `graph` from the plan's cues: `files` maps a cue index to (path, average level of the file in dB)."""
    beds = []
    for index, cue in enumerate(cues):
        path, mean_db = files[index]
        beds.append(dict(path=path, offset=cue['offset'], start=cue['start'], seconds=cue['seconds'], gain_db=round(cue['gain_db'] + normalizing_gain(mean_db), 2),
                         fade_in=cue['fade_in'], fade_out=cue['fade_out'], loop=bool(cue.get('loop'))))
    return beds


def scene_starts(scenes):
    """The second of the film at which each scene begins: the lengths added up (a transition overlaps the clips, but the cut falls where the scenes' lengths say)."""
    starts, clock = [], 0.0
    for scene in scenes:
        starts.append(round(clock, 3))
        clock += scene['seconds']
    return starts


def generated_cue(music, film_seconds):
    """The generated track as a cue of the same shape as the songs' excerpts: it begins at the start the plan gave it and fades in and out."""
    start = min(music.get('start', 0.0), max(0.0, film_seconds - 2))
    return dict(offset=0.0, start=start, seconds=round(film_seconds - start, 3), gain_db=music['gain_db'], fade_in=1.5, fade_out=3.0, loop=bool(music.get('loop')))
