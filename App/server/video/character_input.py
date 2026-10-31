"""Store and bound the inputs of a character-animation job before an owned GPU worker starts."""
from ui_strings import source as ui_text
import base64
import hashlib

from .character_support import MAX_DRIVING_BYTES, probe_driving, usable_frames


def decode_video(value):
    """The bytes of a `data:video/...;base64,` reference video, or ValueError."""
    if not isinstance(value, str) or ';base64,' not in value:
        raise ValueError(ui_text('video.character_reference_video_required'))
    try:
        raw = base64.b64decode(value.partition(';base64,')[2], validate=True)
    except ValueError:
        raise ValueError(ui_text('video.character_reference_video_unreadable')) from None
    if not raw or len(raw) > MAX_DRIVING_BYTES:
        raise ValueError(ui_text('video.character_reference_video_too_large'))
    return raw


def check(jobs, data):
    """Cheap checks done before the GPU is claimed: the video decodes and every extra picture is a PNG."""
    decode_video(data['driving_video'])
    for item in data.get('subjects', []):
        jobs.image(item['image'], None)


def prepare(jobs, data, folder, spec, media):
    """Write the reference video and the extra pictures into the job folder; the clip is as long as the video (up to the chosen limit)."""
    raw = decode_video(data['driving_video'])
    target = folder / 'driving.input'
    target.write_bytes(raw)
    info = probe_driving(jobs.root / 'ffmpeg/bin/ffprobe.exe', target, spec['fps'])
    spec.update(requested_frames=spec['frames'], frames=usable_frames(info['frames'], spec['frames']), driving_sha256=hashlib.sha256(raw).hexdigest(),
                driving_seconds=round(info['duration'], 2), driving_has_audio=info['has_audio'])
    media['driving'] = str(target)
    media['subjects'] = []
    for index, item in enumerate(data.get('subjects', []), 2):
        picture = folder / ('subject-%d.png' % index)
        spec.setdefault('subject_sha256', []).append(jobs.image(item['image'], picture))
        media['subjects'].append(str(picture))
