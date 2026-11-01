"""What the person attaches to a film: pictures of any format, videos of up to ten minutes and two gigabytes, songs of up to an hour and voice samples.

Everything lives in a draft folder (`<cache>/Director/drafts/<id>`): the converted files, `items.json` with one descriptor per attachment and `captions.json` with
what Gemma saw in each picture or video. A picture becomes a PNG through the studio's converter; a video is re-encoded by the bundled FFmpeg to an H.264 MP4 of at
most 1280 px (a size and a container every later step reads), measured, and shown to Gemma as a few frames and numbers. A song becomes an AAC file and is shown to
Gemma as its length and the quiet and loud stretches; a voice sample becomes a short 24 kHz mono WAV, the form the speech engines clone a voice from.

Every attachment belongs to a group (`plan_rules.GROUPS`: heroes, places, objects, reference videos, songs, narrators, characters), which tells the planner what it is for.
"""
from ui_strings import source as ui_text
import base64
import io
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import image_convert
from director import plan_rules as rules

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
DRAFT_ID = re.compile(r'[0-9a-f]{32}')
THUMB_SIDE = 160
FRAMES = 6
LABEL_LIMIT = 60
# Containers of audio and video that are read as they are. A playlist, a concat script or a network description is also something ffprobe can open, and it names other
# files or addresses to read; such a "video" is refused instead of being followed.
CONTAINERS = frozenset('mov mp4 m4a 3gp 3g2 mj2 matroska webm avi flv asf mpegts mpeg ogg ogv mp3 wav flac aac aiff amr caf ac3 eac3 dts opus wv ape mxf rm nut mpc w64 au voc tta'.split())
FILE_ONLY = ['-protocol_whitelist', 'file']
LOCK = threading.RLock()            # guards the index of every draft while it is read and written (never a conversion), see `Draft._begin`
PENDING = {}                        # draft folder -> {group: attachments being converted now}


def new_draft_id():
    return uuid.uuid4().hex


def save_body(stream, length, target, limit):
    """Write `length` bytes of a request body to `target` in 1 MiB steps; a body over `limit` or cut short leaves no file behind."""
    if not 0 < length <= limit:
        raise ValueError(ui_text('director.media_body_size', limit // (1024 * 1024)))
    written = 0
    try:
        with Path(target).open('wb') as out:
            while written < length:
                chunk = stream.read(min(1024 * 1024, length - written))
                if not chunk:
                    raise ValueError(ui_text('director.media_upload_interrupted'))
                out.write(chunk)
                written += len(chunk)
    except BaseException:
        Path(target).unlink(missing_ok=True)
        raise
    return written


def _run(command, timeout):
    return subprocess.run(command, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)


def _inspect(ffprobe, path, unreadable):
    """ffprobe's streams and format of a file in a known container; ValueError(unreadable) for anything else."""
    try:
        result = _run([str(ffprobe), '-v', 'error', *FILE_ONLY, '-show_format', '-show_streams', '-of', 'json', str(path)], 60)
        info = json.loads(result.stdout)
        names = set(str(info['format']['format_name']).split(','))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        raise ValueError(unreadable) from None
    if result.returncode or not names & CONTAINERS:
        raise ValueError(unreadable)
    return info


def probe(ffprobe, path):
    """-> dict(seconds, width, height, fps, has_audio) of a video, or ValueError when it is not a video the Director can use."""
    unreadable = ui_text('director.media_video_unreadable')
    info = _inspect(ffprobe, path, unreadable)
    try:
        video = next(s for s in info['streams'] if s['codec_type'] == 'video' and s.get('width'))
        seconds = float(info['format']['duration'])
        numerator, _, denominator = str(video.get('avg_frame_rate') or video.get('r_frame_rate') or '0/1').partition('/')
        fps = float(numerator) / float(denominator or 1) if float(denominator or 1) else 0.0
    except (ValueError, KeyError, StopIteration, TypeError):
        raise ValueError(unreadable) from None
    if seconds <= 0:
        raise ValueError(unreadable)
    return dict(seconds=round(seconds, 2), width=int(video['width']), height=int(video['height']), fps=round(fps, 2),
                has_audio=any(s['codec_type'] == 'audio' for s in info['streams']))


def probe_audio(ffprobe, path):
    """-> dict(seconds, sample_rate, channels) of the first audio stream, or ValueError when the file has no sound the Director can read."""
    unreadable = ui_text('director.media_audio_unreadable')
    info = _inspect(ffprobe, path, unreadable)
    try:
        stream = next(s for s in info['streams'] if s['codec_type'] == 'audio')
        seconds = float(stream.get('duration') or info['format']['duration'])
        rate, channels = int(stream.get('sample_rate') or 0), int(stream.get('channels') or 0)
    except (ValueError, KeyError, StopIteration, TypeError):
        raise ValueError(unreadable) from None
    if seconds <= 0 or not math.isfinite(seconds) or rate <= 0:
        raise ValueError(unreadable)
    return dict(seconds=round(seconds, 2), sample_rate=rate, channels=channels)


def convert_video(ffmpeg, ffprobe, upload, target):
    """Check the limits, then re-encode `upload` to `target` (H.264 + AAC MP4, at most 1280 px wide, 30 fps). Returns the probe of the result."""
    source = probe(ffprobe, upload)
    if source['seconds'] > rules.MAX_VIDEO_SECONDS:
        raise ValueError(ui_text('director.media_video_too_long', rules.MAX_VIDEO_SECONDS // 60))
    command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', *FILE_ONLY, '-i', str(upload), '-map', '0:v:0', '-map', '0:a:0?',
               '-vf', "scale='min(1280,iw)':-2,fps=30", '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '24', '-pix_fmt', 'yuv420p',
               '-c:a', 'aac', '-b:a', '128k', '-ac', '2', '-movflags', '+faststart', str(target)]
    try:
        result = _run(command, int(300 + source['seconds'] * 3))
    except subprocess.TimeoutExpired:
        Path(target).unlink(missing_ok=True)
        raise ValueError(ui_text('director.media_video_conversion_failed')) from None
    if result.returncode or not Path(target).is_file():
        Path(target).unlink(missing_ok=True)
        raise ValueError(ui_text('director.media_video_conversion_failed'))
    return probe(ffprobe, target)


def convert_song(ffmpeg, ffprobe, upload, target):
    """Check the length, then re-encode `upload` to `target` (AAC 192 kbit/s stereo at 48 kHz in an M4A: a fifth of the size of the usual lossless file, and
    ffmpeg cuts an excerpt from it by time without reading what comes before). Returns the probe of the result."""
    source = probe_audio(ffprobe, upload)
    if source['seconds'] > rules.MAX_SONG_SECONDS + 1:
        raise ValueError(ui_text('director.media_song_too_long', rules.MAX_SONG_SECONDS // 60))
    command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', *FILE_ONLY, '-i', str(upload), '-vn', '-map', '0:a:0', '-ac', '2', '-ar', '48000',
               '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(target)]
    try:
        result = _run(command, int(300 + source['seconds'] * 0.5))
    except subprocess.TimeoutExpired:
        Path(target).unlink(missing_ok=True)
        raise ValueError(ui_text('director.media_audio_conversion_failed')) from None
    if result.returncode or not Path(target).is_file():
        Path(target).unlink(missing_ok=True)
        raise ValueError(ui_text('director.media_audio_conversion_failed'))
    return probe_audio(ffprobe, target)


def convert_voice(ffmpeg, ffprobe, upload, target):
    """The voice sample the speech engines clone from: mono 24 kHz WAV, leading silence removed, at most `VOICE_SAMPLE_SECONDS` long. Returns its probe."""
    source = probe_audio(ffprobe, upload)
    if source['seconds'] > rules.MAX_VOICE_SOURCE_SECONDS:
        raise ValueError(ui_text('director.media_voice_too_long', rules.MAX_VOICE_SOURCE_SECONDS // 60))
    command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', *FILE_ONLY, '-i', str(upload), '-vn', '-map', '0:a:0',
               '-af', 'silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB,aresample=24000,aformat=sample_fmts=s16:channel_layouts=mono',
               '-t', str(rules.VOICE_SAMPLE_SECONDS), '-c:a', 'pcm_s16le', str(target)]
    try:
        result = _run(command, 300)
    except subprocess.TimeoutExpired:
        Path(target).unlink(missing_ok=True)
        raise ValueError(ui_text('director.media_audio_conversion_failed')) from None
    if result.returncode or not Path(target).is_file():
        Path(target).unlink(missing_ok=True)
        raise ValueError(ui_text('director.media_audio_conversion_failed'))
    info = probe_audio(ffprobe, target)
    if info['seconds'] < rules.MIN_VOICE_SECONDS:
        Path(target).unlink(missing_ok=True)
        raise ValueError(ui_text('director.media_voice_too_short', rules.MIN_VOICE_SECONDS))
    return info


def frame_pngs(ffmpeg, video, seconds, count=FRAMES, width=768):
    """PNG bytes of `count` frames taken along the video (not at its first and last frame)."""
    frames = []
    for index in range(count):
        moment = max(0.0, seconds * (index + 0.5) / count)
        result = _run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-ss', '%.3f' % moment, '-i', str(video), '-frames:v', '1',
                       '-vf', 'scale=%d:-2' % width, '-f', 'image2pipe', '-vcodec', 'png', '-'], 120)
        if result.returncode == 0 and result.stdout:
            frames.append(result.stdout)
    return frames


def _jpeg_thumb(png_bytes):
    from PIL import Image
    with Image.open(io.BytesIO(png_bytes)) as image:
        image = image.convert('RGB')
        image.thumbnail((THUMB_SIDE, THUMB_SIDE))
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG', quality=80)
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


# What is asked of the picture model about the shape of a keyframe: the canvas a picture is put on when it has not the film's shape (only the shape matters, not the size) and the
# sentence that tells the model what that canvas is.
FRAME_CANVAS = dict(landscape=(1280, 704), portrait=(704, 1280))
PAD_NOTE = dict(landscape='The first picture sits on a wider canvas with blurred sides: fill the whole frame by continuing the scene naturally to the left and right, and keep the people exactly as they are.',
                portrait='The first picture sits on a taller canvas with blurred bands: fill the whole frame by continuing the scene naturally above and below, and keep the people exactly as they are.')


def _on_canvas(png, orientation):
    """The picture centred on a canvas of the film's shape, over a blurred copy of itself that fills the rest."""
    from PIL import Image, ImageFilter, ImageOps
    size = FRAME_CANVAS[orientation]
    with Image.open(io.BytesIO(png)) as source:
        photo = source.convert('RGB')
    canvas = ImageOps.fit(photo, size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    fitted = ImageOps.contain(photo, size, Image.LANCZOS)
    canvas.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    buffer = io.BytesIO()
    canvas.save(buffer, 'PNG')
    return buffer.getvalue()


def frame_pictures(pngs, orientation):
    """The pictures of one scene as the picture model should be given them: (order, pictures, note).

    Klein draws on the proportions of the FIRST picture it is given and ignores the canvas it is asked for as soon as there is a picture (measured 2026-11-01: with a portrait photo
    of a hero first, a keyframe for a landscape film came out 832x1248, and the video model then cut a middle band out of it; with the photo of the place first, a wide harbour came
    out at 1248x832). So the pictures that have the film's shape go first, the rest keep their order; when none has it, the first one is put on a canvas of the film's shape with a blurred
    copy of itself around it, and `note` asks the model to continue the scene into the blur (measured: it keeps the person's clothes and fills the sides with the scenery).
    `order` lists the indexes of `pngs` in the order given, so that the words of the instruction can be renumbered the same way."""
    if not pngs:
        return [], [], ''
    from PIL import Image
    fits = []
    for png in pngs:
        with Image.open(io.BytesIO(png)) as image:
            width, height = image.size
        fits.append(width != height and (width > height) == (orientation == 'landscape'))
    order = [i for i in range(len(pngs)) if fits[i]] + [i for i in range(len(pngs)) if not fits[i]]
    pictures = [pngs[i] for i in order]
    if fits[order[0]]:
        return order, pictures, ''
    pictures[0] = _on_canvas(pictures[0], orientation)
    return order, pictures, PAD_NOTE[orientation]


def palette_and_light(frames):
    """Dominant colours (hex), mean brightness and mean saturation (0-100) of a few frames: what the colour grade of a reference is made of."""
    from PIL import Image
    colours, brightness, saturation = [], [], []
    for data in frames:
        with Image.open(io.BytesIO(data)) as image:
            small = image.convert('RGB').resize((96, 54))
            hsv = small.convert('HSV').tobytes()          # hue, saturation, value bytes one after another
            saturation.append(sum(hsv[1::3]) / len(hsv[1::3]) / 2.55)
            brightness.append(sum(hsv[2::3]) / len(hsv[2::3]) / 2.55)
            quantized = small.quantize(colors=4)
            counts = sorted(quantized.getcolors(), reverse=True)
            table = quantized.getpalette()
            colours.extend('#%02x%02x%02x' % tuple(table[3 * index:3 * index + 3]) for _, index in counts[:2])
    unique = list(dict.fromkeys(colours))[:6]
    return dict(colours=unique, brightness=round(sum(brightness) / max(1, len(brightness))), saturation=round(sum(saturation) / max(1, len(saturation))))


def cut_rate(ffmpeg, video, seconds):
    """Hard cuts found in a video and the average length of a shot: the pace of a reference. Looks at 8 small frames a second."""
    try:
        result = _run([str(ffmpeg), '-hide_banner', '-nostdin', '-t', str(rules.MAX_VIDEO_SECONDS), '-i', str(video), '-an', '-vf',
                       "fps=8,scale=320:-2,select='gt(scene,0.30)',showinfo", '-f', 'null', '-'], int(120 + seconds))
    except subprocess.TimeoutExpired:
        return dict(cuts=None, shot_seconds=None)
    cuts = len(re.findall(rb'pts_time:', result.stderr))
    return dict(cuts=cuts, shot_seconds=round(seconds / (cuts + 1), 1))


# --- sound -----------------------------------------------------------------------------------------------------------------------------------
def levels(ffmpeg, path, seconds, step=1.0):
    """The RMS level in dB of every `step` seconds of an audio file (-90 for silence): the shape of a song or of a spoken sample. [] when it cannot be measured."""
    rate = 8000
    chain = ('aresample=%d,aformat=channel_layouts=mono,asetnsamples=n=%d:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-'
             % (rate, int(rate * step)))
    try:
        result = _run([str(ffmpeg), '-hide_banner', '-nostdin', '-v', 'error', *FILE_ONLY, '-i', str(path), '-vn', '-af', chain, '-f', 'null', '-'], int(120 + seconds / 8))
    except subprocess.TimeoutExpired:
        return []
    found = re.findall(rb'RMS_level=(-?[\d.]+|-inf)', result.stdout)
    return [-90.0 if raw == b'-inf' else max(-90.0, float(raw)) for raw in found] if result.returncode == 0 else []


def mean_level(values):
    """The average level in dB of a list of RMS levels, averaged as energy (so one loud second is not hidden by many quiet ones); None for an empty list."""
    if not values:
        return None
    return round(10 * math.log10(sum(10 ** (value / 10) for value in values) / len(values) + 1e-12), 1)


def stretches(values, step=1.0):
    """Quiet, medium and loud stretches of a song from its levels: [[start, end, name]], at most about sixteen of them, none shorter than a few seconds.

    A level counts as loud near or above the 70th percentile of the song itself and as quiet near or below the 30th, so every song has its own idea of loud (the
    margin keeps two plateaus of the same level, a hair apart, from being called by different names); a song whose levels hardly move (less than 3 dB between
    those two percentiles) is medium all the way."""
    if not values:
        return []
    window = max(5, round(len(values) * step / 120))
    smooth = [sum(values[max(0, i - window // 2):i + window // 2 + 1]) / len(values[max(0, i - window // 2):i + window // 2 + 1]) for i in range(len(values))]
    ordered = sorted(smooth)
    low, high = ordered[int(len(ordered) * 0.3)], ordered[min(len(ordered) - 1, int(len(ordered) * 0.7))]
    margin = min(1.0, (high - low) * 0.15)
    names = ['medium' if high - low < 3.0 else 'quiet' if value <= low + margin else 'loud' if value >= high - margin else 'medium' for value in smooth]
    runs = []
    for index, name in enumerate(names):
        if runs and runs[-1][2] == name:
            runs[-1][1] = (index + 1) * step
        else:
            runs.append([index * step, (index + 1) * step, name])
    shortest = max(8.0, len(values) * step / 40)
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for index, run in enumerate(runs):
            if run[1] - run[0] < shortest:          # a short stretch joins its neighbour, the longer of the two
                neighbours = [i for i in (index - 1, index + 1) if 0 <= i < len(runs)]
                best = max(neighbours, key=lambda i: runs[i][1] - runs[i][0])
                runs[best][0], runs[best][1] = min(runs[best][0], run[0]), max(runs[best][1], run[1])
                del runs[index]
                changed = True
                break
        merged = []
        for run in runs:
            if merged and merged[-1][2] == run[2]:
                merged[-1][1] = run[1]
            else:
                merged.append(run)
        runs = merged
    return [[round(start, 1), round(end, 1), name] for start, end, name in runs]


def waveform_thumb(values, width=160, height=48):
    """A small bar picture of the levels of a sound as a JPEG data URL (the person sees the shape of the song or the sample at a glance)."""
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (width, height), (24, 28, 36))
    if values:
        draw = ImageDraw.Draw(image)
        low = min(values)
        spread = max(6.0, max(values) - low)
        for x in range(width):
            part = values[x * len(values) // width:max(x * len(values) // width + 1, (x + 1) * len(values) // width)]
            level = max(part) if part else low
            bar = max(1, round((level - low) / spread * (height - 6)))
            draw.line([(x, height // 2 - bar // 2), (x, height // 2 + bar // 2)], fill=(96, 165, 250))
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=80)
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def clock(seconds):
    """m:ss, or h:mm:ss from an hour on."""
    hours, rest = divmod(int(round(seconds)), 3600)
    minutes, whole = divmod(rest, 60)
    return '%d:%02d:%02d' % (hours, minutes, whole) if hours else '%d:%02d' % (minutes, whole)


def describe(item):
    """The numbers of an attachment as one English line for Gemma."""
    facts = item.get('facts') or {}
    if item['kind'] == 'photo':
        return 'photo %dx%d' % (item['width'], item['height'])
    if item['kind'] == 'voice':
        return 'voice sample, %.0f s' % item['seconds']
    if item['kind'] == 'audio':
        parts = ['song, %s long' % clock(item['seconds'])]
        if facts.get('mean_db') is not None:
            parts.append('average level %.0f dB' % facts['mean_db'])
        if facts.get('stretches'):
            parts.append('by time: ' + ', '.join('%s %s-%s' % (name, clock(start), clock(end)) for start, end, name in facts['stretches'][:16]))
        return ', '.join(parts)
    parts = ['video %.0f s, %dx%d' % (item['seconds'], item['width'], item['height'])]
    if facts.get('shot_seconds'):
        parts.append('%d hard cuts, a shot lasts %.1f s on average' % (facts['cuts'], facts['shot_seconds']))
    if facts.get('colours'):
        parts.append('dominant colours %s, brightness %d%%, saturation %d%%' % (' '.join(facts['colours']), facts['brightness'], facts['saturation']))
    return ', '.join(parts)


class Draft:
    """The attachments of one film. Numbers are never reused: a removed attachment leaves a gap."""

    def __init__(self, folder, ffmpeg, ffprobe):
        self.folder = Path(folder)
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        self.folder.mkdir(parents=True, exist_ok=True)
        self.index = self.folder / 'items.json'
        self.captions_path = self.folder / 'captions.json'

    def _read(self, path, default):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return default

    def _write(self, path, value):
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)

    def items(self):
        """The descriptors in the order they were attached. A draft that predates the groups gets its group from the kind of the file."""
        return [row if row.get('group') in rules.MEDIA_OF else dict(row, group=rules.DEFAULT_GROUP[row['kind']]) for row in self._read(self.index, {}).get('items', [])]

    def captions(self):
        return self._read(self.captions_path, {})

    def save_captions(self, captions):
        self._write(self.captions_path, captions)

    def path(self, number):
        item = next((row for row in self.items() if row['number'] == number), None)
        if item is None:
            raise ValueError(ui_text('director.media_unknown_attachment'))
        return self.folder / item['file']

    # A page sends several files at once (songs while pictures are still converting), and a song takes about a minute: the bookkeeping below is short and locked, the
    # conversion is not. A number is taken and a place in the group counted when an attachment begins, and the finished item is added to whatever the index holds by then;
    # without that two uploads would share a number, pass the limit together, or one would overwrite the other's item.
    def _begin(self, group, media):
        """Check that `group` exists, holds this kind of file and has room (counting the files being converted), and take the number of the new attachment."""
        if group not in rules.MEDIA_OF or rules.MEDIA_OF[group] != media:
            raise ValueError(ui_text('director.media_wrong_group'))
        with LOCK:
            raw = self._read(self.index, {})
            limit = rules.group_of(group)['limit']
            making = PENDING.setdefault(str(self.folder), {})
            if sum(1 for row in self.items() if row['group'] == group) + making.get(group, 0) >= limit:
                raise ValueError(ui_text('director.media_group_full', limit))
            number = raw.get('next', 1)
            self._write(self.index, dict(items=raw.get('items', []), next=number + 1))
            making[group] = making.get(group, 0) + 1
            return number

    def _end(self, group, item=None):
        """The attachment is made (`item`) or it failed (None): stop counting it and, if it is made, add it to the index."""
        with LOCK:
            making = PENDING.get(str(self.folder), {})
            making[group] = max(0, making.get(group, 0) - 1)
            if not any(making.values()):
                PENDING.pop(str(self.folder), None)
            if item is not None:
                raw = self._read(self.index, {})
                self._write(self.index, dict(items=raw.get('items', []) + [item], next=max(raw.get('next', 1), item['number'] + 1)))

    def add_photo(self, raw, name, group='hero'):
        number = self._begin(group, 'photo')
        item, file = None, 'photo-%d.png' % number
        try:
            converted = image_convert.convert(raw, 'video')
            (self.folder / file).write_bytes(converted.data)
            item = dict(number=number, kind='photo', group=group, label='', name=str(name)[:120], file=file, width=converted.width, height=converted.height, bytes=len(converted.data),
                        thumb=_jpeg_thumb(converted.data))
            return item
        finally:
            if item is None:
                (self.folder / file).unlink(missing_ok=True)
            self._end(group, item)

    def add_video(self, upload, name, group='video'):
        """`upload` is the file the server wrote from the request body; it is deleted whatever the result."""
        item, file = None, None
        try:
            number = self._begin(group, 'video')
            file = 'video-%d.mp4' % number
            info = convert_video(self.ffmpeg, self.ffprobe, upload, self.folder / file)
            frames = frame_pngs(self.ffmpeg, self.folder / file, info['seconds'])
            facts = dict(cut_rate(self.ffmpeg, self.folder / file, info['seconds']), **(palette_and_light(frames) if frames else {}))
            item = dict(number=number, kind='video', group=group, label='', name=str(name)[:120], file=file, bytes=(self.folder / file).stat().st_size, facts=facts,
                        thumb=_jpeg_thumb(frames[len(frames) // 2]) if frames else '', **info)
            return item
        finally:
            self._finish(group, item, file, upload)

    def add_song(self, upload, name, group='song'):
        """A song of up to an hour; `upload` is deleted whatever the result."""
        item, file = None, None
        try:
            number = self._begin(group, 'audio')
            file = 'song-%d.m4a' % number
            info = convert_song(self.ffmpeg, self.ffprobe, upload, self.folder / file)
            curve = levels(self.ffmpeg, self.folder / file, info['seconds'])
            facts = dict(mean_db=mean_level(curve), stretches=stretches(curve))
            item = dict(number=number, kind='audio', group=group, label='', name=str(name)[:120], file=file, bytes=(self.folder / file).stat().st_size, facts=facts,
                        thumb=waveform_thumb(curve), seconds=info['seconds'])
            return item
        finally:
            self._finish(group, item, file, upload)

    def add_voice(self, upload, name, group='narrator'):
        """A voice sample; `upload` is deleted whatever the result. A sample with no speech in it is refused."""
        item, file = None, None
        try:
            number = self._begin(group, 'voice')
            file = 'voice-%d.wav' % number
            info = convert_voice(self.ffmpeg, self.ffprobe, upload, self.folder / file)
            curve = levels(self.ffmpeg, self.folder / file, info['seconds'], 0.1)
            mean = mean_level(curve)
            if mean is None or mean < -55:
                raise ValueError(ui_text('director.media_voice_silent'))
            item = dict(number=number, kind='voice', group=group, label='', name=str(name)[:120], file=file, bytes=(self.folder / file).stat().st_size, facts=dict(mean_db=mean),
                        thumb=waveform_thumb(curve), seconds=info['seconds'])
            return item
        finally:
            self._finish(group, item, file, upload)

    def _finish(self, group, item, file, upload):
        """The end of an attachment that came from an upload: its working file goes whatever happened, the converted file only when it failed. A refusal at the very start
        (`file` unknown) never counted the attachment, so there is nothing to give back."""
        Path(upload).unlink(missing_ok=True)
        if file is None:
            return
        if item is None:
            (self.folder / file).unlink(missing_ok=True)
        self._end(group, item)

    def set_label(self, number, label):
        """The short name the person gives an attachment ("Alexey", "Opening song"); the planner reads it."""
        with LOCK:
            raw = self._read(self.index, {})
            if not any(row['number'] == number for row in self.items()):
                raise ValueError(ui_text('director.media_unknown_attachment'))
            text = ' '.join(str(label or '').split())[:LABEL_LIMIT]
            self._write(self.index, dict(items=[dict(row, label=text) if row['number'] == number else row for row in raw.get('items', [])], next=raw.get('next', 1)))
            return text

    def remove(self, number):
        with LOCK:
            raw = self._read(self.index, {})
            rows = raw.get('items', [])
            keep = [row for row in rows if row['number'] != number]
            if len(keep) == len(rows):
                raise ValueError(ui_text('director.media_unknown_attachment'))
            for row in rows:
                if row['number'] == number:
                    (self.folder / row['file']).unlink(missing_ok=True)
            self._write(self.index, dict(items=keep, next=raw.get('next', 1)))
            captions = self.captions()
            if captions.pop(str(number), None) is not None:
                self.save_captions(captions)


def open_draft(root, draft_id, ffmpeg, ffprobe):
    """The draft of a page session; the id is checked, so a client can never name another folder."""
    if not isinstance(draft_id, str) or not DRAFT_ID.fullmatch(draft_id):
        raise ValueError(ui_text('director.media_unknown_draft'))
    return Draft(Path(root) / 'drafts' / draft_id, ffmpeg, ffprobe)


def prune_drafts(root, days=7):
    """Remove drafts nobody touched for `days` days (their pictures, songs and converted videos are only cache)."""
    folder = Path(root) / 'drafts'
    if not folder.is_dir():
        return 0
    limit, removed = time.time() - days * 86400, 0
    for child in folder.iterdir():
        if child.is_dir() and DRAFT_ID.fullmatch(child.name) and child.stat().st_mtime < limit:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
