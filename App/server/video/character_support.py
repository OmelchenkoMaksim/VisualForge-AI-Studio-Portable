"""Wan Animate 2 and SCAIL-2: model locations, readiness, the segment plan and the reference-video probe.

Both models take a reference video (the motion), one picture of the person and a text, and render the person doing what the video
shows. The video model makes 81 frames at a time, so a longer clip is a chain of segments that continue each other.
"""
from ui_strings import source as ui_text
import json
import math
from pathlib import Path
import subprocess

ANIMATE_ID = 'wan-animate2'
SCAIL_ID = 'scail2'
CHARACTER_IDS = (ANIMATE_ID, SCAIL_ID)
SEGMENT = 81                 # frames of one native segment (21 latent frames)
ANIMATE_OVERLAP = 1          # a continuation of Wan Animate 2 repeats the last frame of the previous segment
SCAIL_OVERLAP = 5            # SCAIL-2 was trained to continue from the last 5 frames (81-frame chunks advance by 76)
MIN_FRAMES = 17
MAX_DRIVING_SECONDS = 60
MAX_DRIVING_BYTES = 64 * 1024 * 1024

# Sizes of the pinned files; a file with another size is a broken download.
SHARED = {'clip_vision/clip_vision_h.safetensors': 1264219396,
          'vae/Wan2_1_VAE_bf16.safetensors': 253806278,
          'loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors': 738005744}
ANIMATE = {'standard': ('diffusion_models/wan_animate_2_distill_int8_convrot.safetensors', 16653175528),
           'high': ('diffusion_models/wan_animate_2_int8_convrot.safetensors', 16653175528)}
SCAIL = {'diffusion_models/wan2.1_14B_SCAIL_2_int8_convrot.safetensors': 16654506256,
         'loras/wan2.1_SCAIL_2_DPO_lora_bf16.safetensors': 1226936552}
SCAIL_RELIGHT = ('loras/wan2.1_SCAIL_2_relight_lora_bf16.safetensors', 1226936552)
SAM3 = ('checkpoints/sam3.1_multiplex_fp16.safetensors', 1745546848)
TEXT_ENCODER = ('text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors', 6735906897)


def models_root(h3_models):
    return Path(h3_models).parent


def folders(h3_models):
    return folders_in(models_root(h3_models))


def folders_in(root):
    """Folder of every file group under Models/Video; the text encoder is the one Wan TI2V-5B already downloaded."""
    root = Path(root)
    return dict(animate=root / 'WanAnimate2', scail=root / 'Scail2', shared=root / 'WanAnimateShared', sam3=root / 'Sam3',
                text=root / 'Wan22TI2V5B')


def _wrong(path, size):
    return not path.is_file() or path.stat().st_size != size


def engine_missing(runtime, h3_models, engine, quality=None, relight=False, fit=False):
    """Files and programs the engine still needs for a request of this quality (empty list: ready)."""
    runtime = Path(runtime)
    where = folders(h3_models)
    required = [runtime / p for p in ('python/python.exe', 'ffmpeg/bin/ffmpeg.exe', 'ffmpeg/bin/ffprobe.exe',
                                      'vendor/ComfyUI/comfy_extras/nodes_wan.py')]
    sized = [(where['shared'] / name, size) for name, size in SHARED.items()] + [(where['text'] / TEXT_ENCODER[0], TEXT_ENCODER[1])]
    if engine == ANIMATE_ID:
        name, size = ANIMATE[quality if quality in ANIMATE else 'standard']
        sized.append((where['animate'] / name, size))
        if fit:                                              # the scale fit finds the heads with SAM3
            sized.append((where['sam3'] / SAM3[0], SAM3[1]))
    else:
        required.append(runtime / 'vendor/ComfyUI/comfy_extras/nodes_scail.py')
        sized += [(where['scail'] / name, size) for name, size in SCAIL.items()]
        sized.append((where['sam3'] / SAM3[0], SAM3[1]))
        if relight:
            sized.append((where['scail'] / SCAIL_RELIGHT[0], SCAIL_RELIGHT[1]))
    missing = [str(p) for p in required if not p.is_file()]
    return missing + [str(path) for path, size in sized if _wrong(path, size)]


def segment_plan(frames, engine):
    """Lengths of the segments that together give at least `frames` pictures.

    Each length is 4n+1 (the model's time grid). A continuation re-renders `overlap` frames of the previous segment as its anchor and
    those are dropped from the result, so it adds length - overlap pictures; the last segment is the shortest that completes the clip.
    """
    overlap = ANIMATE_OVERLAP if engine == ANIMATE_ID else SCAIL_OVERLAP
    lengths, produced = [], 0
    while produced < frames:
        need = frames - produced + (0 if not lengths else overlap)
        length = min(SEGMENT, 4 * math.ceil((need - 1) / 4) + 1)
        lengths.append(length)
        produced += length if len(lengths) == 1 else length - overlap
    return lengths


def usable_frames(available, limit):
    """Pictures taken from a reference video that has `available` of them at the model's frame rate."""
    frames = min(limit, available)
    if frames < MIN_FRAMES:
        raise ValueError(ui_text('video.character_reference_video_too_short'))
    return frames


def probe_driving(ffprobe, path, fps):
    """-> dict(duration, frames, width, height, has_audio) of a reference video; frames counted at the model's `fps`."""
    result = subprocess.run([str(ffprobe), '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
                            capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        info = json.loads(result.stdout)
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        duration = float(info['format']['duration'])
    except (ValueError, KeyError, StopIteration, TypeError):
        raise ValueError(ui_text('video.character_reference_video_unreadable')) from None
    if result.returncode or not 0 < duration <= MAX_DRIVING_SECONDS or int(video.get('width', 0)) < 64 or int(video.get('height', 0)) < 64:
        raise ValueError(ui_text('video.character_reference_video_unreadable'))
    return dict(duration=duration, frames=int(duration * fps + 1e-6), width=int(video['width']), height=int(video['height']),
                has_audio=any(s['codec_type'] == 'audio' for s in info['streams']))


CHARACTER_FIELDS = ('driving_video', 'subjects', 'driving_object', 'reference_object', 'relight', 'keep_audio', 'motion_prompt', 'fit_reference')
OBJECT_WORD = r'[A-Za-z][A-Za-z0-9 ,:.-]{0,39}'


def _word(value, default='human'):
    import re
    if value in (None, ''):
        return default
    if not isinstance(value, str) or not re.fullmatch(OBJECT_WORD, value.strip()):
        raise ValueError(ui_text('video.character_invalid_object_word'))
    return value.strip()


def validate_fields(data, engine, mode):
    """The request fields only the character engines have -> what the job spec keeps (pictures and video are stored separately)."""
    video = data.get('driving_video')
    if not isinstance(video, str) or not video.startswith('data:video/') or ';base64,' not in video or len(video) > MAX_DRIVING_BYTES * 4 // 3 + 4096:
        raise ValueError(ui_text('video.character_reference_video_required'))
    if engine == ANIMATE_ID and any(key in data for key in ('subjects', 'driving_object', 'reference_object', 'relight')):
        raise ValueError(ui_text('video.character_fields_scail_only'))
    subjects = data.get('subjects', [])
    if not isinstance(subjects, list) or len(subjects) > 2 or any(not isinstance(item, dict) or set(item) - {'image', 'reference_object', 'driving_object'}
                                                                     or not isinstance(item.get('image'), str) for item in subjects):
        raise ValueError(ui_text('video.character_invalid_subjects'))
    relight = data.get('relight', False)
    fit = data.get('fit_reference', True)
    keep_audio = data.get('keep_audio', True)
    if not isinstance(relight, bool) or not isinstance(keep_audio, bool) or not isinstance(fit, bool) or relight and mode != 'replace':
        raise ValueError(ui_text('video.character_invalid_options'))
    result = dict(keep_audio=keep_audio, fit_reference=fit)
    motion = data.get('motion_prompt', '')
    if not isinstance(motion, str) or len(motion) > 1500 or motion.strip() and engine != ANIMATE_ID:
        raise ValueError(ui_text('video.character_invalid_options'))
    if motion.strip():
        result['motion_prompt'] = motion.strip()
    if engine == SCAIL_ID:
        result.update(reference_object=_word(data.get('reference_object')), driving_object=_word(data.get('driving_object')), relight=relight,
                      subjects=[dict(reference_object=_word(item.get('reference_object'), 'car'), driving_object=_word(item.get('driving_object'), 'car'))
                                for item in subjects])
    return result
