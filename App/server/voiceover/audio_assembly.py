"""Joins synthesized fragments into one file: pauses, loudness and chapters. Uses the studio's own ffmpeg.

This module prepares files and builds the ffmpeg command; the manager runs it, so that Stop closes the process through
the same owned job as the synthesis worker.
"""
from pathlib import Path
import wave

from .text_prep import PAUSE

# Speech level commonly used for audiobooks (about -18 LUFS). Single-pass loudnorm only aims at its true-peak target: on a one-hour book the
# real peaks reached -0.3 dBFS, which clips in lossy encoders. A limiter follows it at -1.9 dBFS (0.8): the AAC encoder then adds about 0.5 dB,
# so the peak of the finished file stays near -1.4 dBFS (limit 0.84 left -1.0 dBFS in a measurement).
LOUDNESS = 'loudnorm=I=-18:TP=-1.5:LRA=11,alimiter=limit=0.8:level=0'


def wav_info(path):
    """(sample rate, channels, seconds) of a PCM WAV written by the worker."""
    with wave.open(str(path), 'rb') as stream:
        return stream.getframerate(), stream.getnchannels(), stream.getnframes() / stream.getframerate()


def write_silence(path, rate, channels, seconds):
    frames = int(round(seconds * rate))
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b'\x00\x00' * channels * frames)


def _escape_list(path):
    return str(path).replace('\\', '/').replace("'", "'\\''")


def _escape_meta(text):
    return ''.join('\\' + char if char in '=;#\\\n' else char for char in text)


def timeline(items, chapters, durations):
    """Chapter start/end times (seconds) and the total length, from fragment lengths and the pauses between them."""
    clock, starts, ends = 0.0, {}, {}
    for index, item in enumerate(items):
        starts.setdefault(item['chapter'], clock)
        clock += durations[index]
        ends[item['chapter']] = clock
        if index < len(items) - 1:
            clock += PAUSE[item['pause']]
    rows = [{'title': chapters[number]['title'], 'start': round(starts[number], 3), 'end': round(ends[number], 3)}
            for number in sorted(starts)]
    return rows, clock


def prepare(directory, cache, items, chapters, title, fmt, ffmpeg):
    """Writes the concat list and metadata; returns (command, output path, chapter rows, total seconds)."""
    directory = Path(directory)
    first = cache / (items[0]['key'] + '.wav')
    rate, channels, _ = wav_info(first)
    durations = []
    for item in items:
        item_rate, item_channels, seconds = wav_info(cache / (item['key'] + '.wav'))
        if (item_rate, item_channels) != (rate, channels):
            raise ValueError('Fragments have different audio formats: ' + item['key'])
        durations.append(seconds)
    gaps = {}
    for name, seconds in PAUSE.items():
        gaps[name] = directory / ('silence-%s.wav' % name)
        write_silence(gaps[name], rate, channels, seconds)
    lines = []
    for index, item in enumerate(items):
        lines.append("file '%s'" % _escape_list(cache / (item['key'] + '.wav')))
        if index < len(items) - 1:
            lines.append("file '%s'" % _escape_list(gaps[item['pause']]))
    (directory / 'concat.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    rows, total = timeline(items, chapters, durations)
    output = directory / ('voiceover.' + fmt)
    command = [str(ffmpeg), '-nostdin', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(directory / 'concat.txt')]
    if fmt == 'm4b':
        meta = [';FFMETADATA1', 'title=' + _escape_meta(title), 'genre=Audiobook']
        for row in rows:
            meta += ['[CHAPTER]', 'TIMEBASE=1/1000', 'START=%d' % round(row['start'] * 1000), 'END=%d' % round(row['end'] * 1000), 'title=' + _escape_meta(row['title'])]
        (directory / 'chapters.ffmeta').write_text('\n'.join(meta) + '\n', encoding='utf-8')
        command += ['-i', str(directory / 'chapters.ffmeta'), '-map', '0:a', '-map_metadata', '1', '-map_chapters', '1']
    else:
        command += ['-map', '0:a', '-metadata', 'title=' + title]
    command += ['-af', LOUDNESS, '-ar', str(rate), '-ac', str(channels)]  # loudnorm resamples internally; the rate is restored here
    if fmt == 'wav':
        command += ['-c:a', 'pcm_s16le']
    elif fmt == 'mp3':
        command += ['-c:a', 'libmp3lame', '-b:a', '128k' if rate > 24000 else '96k']  # a 48 kHz engine gets the bit rate its bandwidth needs
    else:
        command += ['-c:a', 'aac', '-b:a', '96k' if rate > 24000 else '64k', '-movflags', '+faststart', '-f', 'ipod']
    command.append(str(output))
    return command, output, rows, total
