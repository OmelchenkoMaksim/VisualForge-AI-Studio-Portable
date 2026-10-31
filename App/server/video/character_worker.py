"""One owned offline job of Wan Animate 2 or SCAIL-2 on native ComfyUI.

The person of one picture does what a reference video shows. Both models render 81 pictures per segment, so a longer clip is a chain:
every segment starts from the end of the previous one (Wan Animate 2 from its last picture, SCAIL-2 from its last five) and the
reference video is read on, picture for picture, at 24 fps. The clip is as long as the reference video (up to the chosen limit) and
keeps its sound. SCAIL-2 also needs masks: SAM3 tracks the people (or any word: "truck", "car") in the video and in the picture, and
each tracked object keeps one colour on both sides, so the model knows which one is replaced by which.
"""
import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from video.character_support import ANIMATE, ANIMATE_ID, ANIMATE_OVERLAP, SAM3, SCAIL_OVERLAP, SCAIL_RELIGHT, SEGMENT, folders_in, segment_plan
from video.comfy_memory import release_models
from video.wan_worker import Exporter

SHIFT = 5.0
LORA_STRENGTH = {'speed': 0.8, 'dpo': 1.0, 'relight': 1.0}
TRACK_WHOLE_CLIP = 401      # frames; a longer clip is tracked segment by segment (the SAM3 colours may then change between segments)


def event(kind, **values):
    print(json.dumps(dict(event=kind, **values), ensure_ascii=False), flush=True)


def confined(folder, value):
    path = Path(value).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ValueError('Входной файл вне папки задачи')
    return path


# ---- pictures and the reference video ------------------------------------------------------------------------------------
def fit_cover(image, width, height):
    """A PIL image scaled to cover width x height and cut from the centre: nothing is stretched."""
    from PIL import Image
    image = image.convert('RGB')
    scale = max(width / image.width, height / image.height)
    resized = image.resize((max(width, round(image.width * scale)), max(height, round(image.height * scale))), Image.LANCZOS)
    left, top = (resized.width - width) // 2, (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def fit_inside(image, width, height):
    """A PIL image scaled to fit inside width x height (aspect kept); the rest of the box is left to the caller."""
    from PIL import Image
    image = image.convert('RGB')
    scale = min(width / image.width, height / image.height)
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)


def compose_reference(person, extras, width, height):
    """One picture holding every subject: the person fills the upper part, the others share the lower part on a white ground.

    SCAIL-2 takes one primary reference with all identities on it; SAM3 then finds each of them by its word.
    """
    from PIL import Image
    if not extras:
        return fit_cover(person, width, height)
    canvas = Image.new('RGB', (width, height), (255, 255, 255))
    top = int(height * 0.58)
    canvas.paste(fit_cover(person, width, top), (0, 0))
    cell = width // len(extras)
    for index, picture in enumerate(extras):
        fitted = fit_inside(picture, cell - 16, height - top - 16)
        canvas.paste(fitted, (index * cell + (cell - fitted.width) // 2, top + 8 + (height - top - 16 - fitted.height) // 2))
    return canvas


def first_mask(track_data, unpack):
    """Mask (a boolean tensor, height x width) of the first tracked object in the picture where it first appears, or None."""
    packed = track_data.get('packed_masks')
    if packed is None or packed.shape[1] == 0:
        return None
    masks = unpack(packed)[:, 0]
    seen = masks.flatten(1).any(dim=1).nonzero()
    return masks[int(seen[0])] if len(seen) else None


def first_box(track_data, unpack):
    """Box (left, top, right, bottom as fractions of the picture) of the first tracked object where it first appears, or None."""
    mask = first_mask(track_data, unpack)
    if mask is None:
        return None
    rows, columns = mask.any(dim=1).nonzero().flatten(), mask.any(dim=0).nonzero().flatten()
    height, width = mask.shape
    return (columns.min().item() / width, rows.min().item() / height, (columns.max().item() + 1) / width, (rows.max().item() + 1) / height)


def aligned_picture(picture, ref_box, drv_box):
    """The picture scaled and moved so that the head in it is as large as, and where, the head of the person in the video is.

    SCAIL-2 draws the new person at the scale the picture shows: a close-up selfie put into a wide shot gives a giant head
    (measured 2026-10-31 on a 12 s clip: the same photo gave a head three times too large, while a picture made at the scale of the
    video gave an almost exact copy of the scene). Only the head sizes are compared, because a photo rarely shows the whole body.
    """
    from PIL import Image
    ref_width, drv_width = ref_box[2] - ref_box[0], drv_box[2] - drv_box[0]
    if ref_width <= 0 or drv_width <= 0:
        return picture
    scale = drv_width / ref_width
    if 0.85 <= scale <= 1.18:
        return picture                                   # already about the same size
    scale = min(max(scale, 0.12), 1.6)
    width, height = picture.size
    scaled = picture.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS)
    left = round((drv_box[0] + drv_box[2]) / 2 * width - (ref_box[0] + ref_box[2]) / 2 * scaled.width)
    top = round((drv_box[1] + drv_box[3]) / 2 * height - (ref_box[1] + ref_box[3]) / 2 * scaled.height)
    canvas = Image.new('RGB', picture.size, (255, 255, 255))
    canvas.paste(scaled, (left, top))
    return canvas


def cutout_picture(picture, mask, ref_box, drv_box, ground=(236, 236, 236)):
    """The person of the photo, cut out and scaled to the head size of the video, alone on a plain ground.

    Wan Animate 2 keeps the whole reference picture as the starting scene: a smaller photo pasted into a blurred or white frame stayed visible as a
    rectangle (a room inside the field) for the first seconds (measured 2026-10-31). Without the room only the person is carried over and the
    text decides the place. `mask` is a boolean array the size of the picture.
    """
    import numpy as np
    from PIL import Image, ImageFilter
    ref_width, drv_width = ref_box[2] - ref_box[0], drv_box[2] - drv_box[0]
    if ref_width <= 0 or drv_width <= 0:
        return picture
    scale = min(max(drv_width / ref_width, 0.12), 1.6)
    width, height = picture.size
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    alpha = Image.fromarray((np.asarray(mask, dtype=np.uint8) * 255)).filter(ImageFilter.GaussianBlur(1.5)).resize(size, Image.LANCZOS)
    left = round((drv_box[0] + drv_box[2]) / 2 * width - (ref_box[0] + ref_box[2]) / 2 * size[0])
    top = round((drv_box[1] + drv_box[3]) / 2 * height - (ref_box[1] + ref_box[3]) / 2 * size[1])
    canvas = Image.new('RGB', picture.size, ground)
    canvas.paste(picture.resize(size, Image.LANCZOS), (left, top), alpha)
    return canvas


def decode_driving(ffmpeg, source, folder, width, height, fps, frames):
    """The reference video as a (frames, height, width, 3) uint8 array on disk: 24 fps, covering the size, cut from the centre."""
    import numpy as np
    target = folder / 'driving.rgb'
    scale = 'fps=%d,scale=%d:%d:force_original_aspect_ratio=increase:flags=area,crop=%d:%d' % (fps, width, height, width, height)
    with target.open('wb') as stream:
        subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', str(source), '-vf', scale, '-frames:v', str(frames),
                        '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=stream, check=True, timeout=1800, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    size = width * height * 3
    count = target.stat().st_size // size
    if count < 1:
        raise RuntimeError('Не удалось прочитать кадры видео-референса')
    clip = np.memmap(target, dtype=np.uint8, mode='r', shape=(count, height, width, 3))
    return clip, count


def segment_frames(clip, start, length):
    """`length` pictures of the clip from `start` as float RGB 0..1; past the end the last picture is held."""
    import numpy as np
    import torch
    count = len(clip)
    part = np.array(clip[min(start, count - 1):min(start + length, count)])
    if len(part) < length:
        part = np.concatenate([part, np.repeat(part[-1:], length - len(part), axis=0)])
    return torch.from_numpy(part).float() / 255


def _lab(rgb):
    import numpy as np
    linear = np.where(rgb > 0.04045, ((rgb + 0.055) / 1.055) ** 2.4, rgb / 12.92)
    xyz = linear @ np.array([[0.4124564, 0.2126729, 0.0193339], [0.3575761, 0.7151522, 0.1191920], [0.1804375, 0.0721750, 0.9503041]])
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def _rgb(lab):
    import numpy as np
    fy = (lab[..., 0] + 16) / 116
    f = np.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], axis=-1)
    xyz = np.where(f ** 3 > 0.008856, f ** 3, (f - 16 / 116) / 7.787) * np.array([0.95047, 1.0, 1.08883])
    linear = xyz @ np.array([[3.2404542, -0.9692660, 0.0556434], [-1.5371385, 1.8760108, -0.2040259], [-0.4985314, 0.0415560, 1.0572252]])
    linear = np.clip(linear, 0, 1)
    return np.where(linear > 0.0031308, 1.055 * linear ** (1 / 2.4) - 0.055, 12.92 * linear)


def match_colors(frames, anchor):
    """Frames (n, h, w, 3) in 0..1 moved to the mean and spread of `anchor` (h, w, 3) in Lab, picture by picture (Reinhard).

    A continuation segment drifts a little in tone from the one before it; matching its pictures to the last picture it continues from
    keeps the seam invisible (the same step the official SCAIL-2 workflow takes).
    """
    import numpy as np
    reference = _lab(np.asarray(anchor, dtype=np.float64))
    mean, spread = reference.reshape(-1, 3).mean(axis=0), reference.reshape(-1, 3).std(axis=0)
    out = np.empty(frames.shape, dtype=np.float32)
    for index, frame in enumerate(frames):
        lab = _lab(np.asarray(frame, dtype=np.float64))
        flat = lab.reshape(-1, 3)
        own_mean, own_spread = flat.mean(axis=0), np.maximum(flat.std(axis=0), 1e-6)
        out[index] = np.clip(_rgb((lab - own_mean) / own_spread * spread + mean), 0, 1)
    return out


def found(track_data, where, words):
    """SAM3 tracked at least one object, otherwise the masks would be empty and the model would silently replace nothing."""
    packed = track_data.get('packed_masks')
    if packed is None or packed.shape[1] == 0:
        raise ValueError('SAM3 не нашёл %s ни одного объекта по словам «%s». Попробуйте другое слово (human, man, woman, person, car).' % (where, words))


def accept_cls_keyword():
    """Let transformers 5.15 build the CLIP tokenizer that SAM3 needs on top of the tokenizers 0.23.0rc0 the Video runtime pins.

    transformers calls RobertaProcessing(sep=..., cls=...); the tokenizers release candidate renamed the second argument `cls_token`, so
    loading the SAM3 text encoder stops with "RobertaProcessing.__new__() got an unexpected keyword argument 'cls'" (seen 2026-10-31).
    """
    import inspect
    from tokenizers import processors
    original = processors.RobertaProcessing
    if getattr(original, 'accepts_cls_keyword', False) or 'cls_token' not in inspect.signature(original).parameters:
        return

    def compatible(*args, cls=None, **kwargs):
        if cls is not None:
            kwargs['cls_token'] = cls
        return original(*args, **kwargs)
    compatible.accepts_cls_keyword = True
    processors.RobertaProcessing = compatible


# ---- the job -----------------------------------------------------------------------------------------------------------------
def run(args):
    root, models, folder = (Path(value).resolve() for value in (args.root, args.models, args.folder))
    request = json.loads(Path(args.spec).read_text(encoding='utf-8'))
    engine, mode, quality = request['engine'], request['mode'], request['quality']
    width, height, frames, fps = request['width'], request['height'], request['frames'], request['fps']
    animate = engine == ANIMATE_ID
    replace = mode == 'replace'
    started = time.monotonic()
    where = folders_in(models)
    sys.path.insert(0, str(root / 'vendor/ComfyUI'))
    import importlib.util
    sage = os.environ.get('VISUALFORGE_WAN_ATTENTION') != 'pytorch' and importlib.util.find_spec('sageattention') is not None
    sys.argv = ['visualforge-character', '--disable-dynamic-vram', '--disable-pinned-memory', '--disable-smart-memory',
                '--use-sage-attention' if sage else '--use-pytorch-cross-attention', '--reserve-vram', '2.0', '--disable-all-custom-nodes']
    logging.basicConfig(level=logging.WARNING)
    import comfy.options
    comfy.options.enable_args_parsing()
    import numpy as np
    import torch
    from PIL import Image
    import folder_paths
    import nodes
    import comfy.model_management as memory
    import comfy.utils
    from comfy_extras.nodes_custom_sampler import BasicScheduler, KSamplerSelect, SamplerCustom
    from comfy_extras.nodes_model_advanced import ModelSamplingSD3
    from comfy_extras.nodes_wan import TrimVideoLatent, WanAnimate2Cache, WanAnimate2ToVideo
    if not animate or request.get('fit_reference', True):    # Wan Animate 2 uses SAM3 only to fit the photo to the video's scale
        from comfy_extras.nodes_sam3 import SAM3_VideoTrack
        from comfy.ldm.sam3.tracker import unpack_masks
    if not animate:
        from comfy_extras.nodes_scail import SCAIL2ColoredMask, WanSCAILToVideo
    if not torch.cuda.is_available():
        raise RuntimeError('Для Wan Animate 2 и SCAIL-2 требуется NVIDIA CUDA GPU')
    torch.set_num_threads(min(8, os.cpu_count() or 4))
    own = where['animate'] if animate else where['scail']
    for name, folders in (('diffusion_models', [own / 'diffusion_models']), ('loras', [where['shared'] / 'loras', where['scail'] / 'loras']),
                          ('clip_vision', [where['shared'] / 'clip_vision']), ('vae', [where['shared'] / 'vae']),
                          ('text_encoders', [where['text'] / 'text_encoders']), ('checkpoints', [where['sam3'] / 'checkpoints'])):
        for path in folders:
            folder_paths.add_model_folder_path(name, str(path), is_default=True)
    comfy.utils.PROGRESS_BAR_ENABLED = False
    release = lambda: release_models(memory, torch)

    plan = segment_plan(frames, engine)
    overlap = ANIMATE_OVERLAP if animate else SCAIL_OVERLAP
    if animate:
        weights, steps, cfg, sampler_name = ANIMATE[quality][0].split('/')[-1], (10 if quality == 'standard' else 6), 1.0, 'lcm'
        loras = [('lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors', 1.0)] if quality == 'high' else []
    else:
        weights = 'wan2.1_14B_SCAIL_2_int8_convrot.safetensors'
        steps, cfg, sampler_name = (6, 1.0, 'euler') if quality == 'standard' else (40, 5.0, 'euler')
        loras = [('wan2.1_SCAIL_2_DPO_lora_bf16.safetensors', LORA_STRENGTH['dpo'])]
        if replace and request.get('relight'):
            loras.append((SCAIL_RELIGHT[0].split('/')[-1], LORA_STRENGTH['relight']))
        if quality == 'standard':
            loras.append(('lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors', LORA_STRENGTH['speed']))
    profile = dict(engine=engine, mode=mode, quality=quality, width=width, height=height, frames=frames, fps=fps, segments=len(plan), segment_lengths=plan,
                   steps=steps, cfg=cfg, sampler=sampler_name, shift=SHIFT, weights=weights, loras=loras, seed=request['seed'], attention='sage' if sage else 'pytorch',
                   offload='native ComfyUI RAM offload, reserve 2 GiB, chunked unload', driving_seconds=request.get('driving_seconds'), native_comfy_version='0.34.0')
    (folder / 'character-inference-config.json').write_text(json.dumps(profile, indent=2), encoding='utf-8')
    event('sampling_plan', **profile)

    media = json.loads((folder / 'media.json').read_text(encoding='utf-8'))
    ffmpeg = root / 'ffmpeg/bin/ffmpeg.exe'
    with torch.inference_mode():
        clip, available = decode_driving(ffmpeg, confined(folder, media['driving']), folder, width, height, fps, frames)
        with Image.open(confined(folder, media['first'])) as picture:
            person = picture.convert('RGB')
        extras = []
        for path in media.get('subjects', []):
            with Image.open(confined(folder, path)) as picture:
                extras.append(picture.convert('RGB'))
        reference_picture = compose_reference(person, extras, width, height) if not animate else fit_cover(person, width, height)
        sam_model = sam_clip = None
        fit = request.get('fit_reference', True) and not extras and (replace or animate)
        if not animate or fit:
            if not animate:
                event('tracking_start', words=[request['driving_object'], request['reference_object']])
            accept_cls_keyword()
            sam_model, sam_clip = nodes.CheckpointLoaderSimple().load_checkpoint(SAM3[0].split('/')[-1])[:2]
            if fit:
                heads = nodes.CLIPTextEncode().encode(sam_clip, 'head')[0]
                head = lambda picture: first_box(SAM3_VideoTrack.execute(picture, sam_model, conditioning=heads, detection_threshold=0.5, max_objects=1, detect_interval=1)[0], unpack_masks)
                ref_box = head(torch.from_numpy(np.asarray(reference_picture).copy()).float().unsqueeze(0) / 255)
                drv_box = head(segment_frames(clip, 0, 1))
                if ref_box and drv_box and animate:
                    person = SAM3_VideoTrack.execute(torch.from_numpy(np.asarray(reference_picture).copy()).float().unsqueeze(0) / 255, sam_model,
                                                     conditioning=nodes.CLIPTextEncode().encode(sam_clip, 'human')[0], detection_threshold=0.5, max_objects=1, detect_interval=1)[0]
                    mask = first_mask(person, unpack_masks)
                    if mask is not None and 0.85 <= (drv_box[2] - drv_box[0]) / (ref_box[2] - ref_box[0]) <= 1.18:
                        mask = None                       # the photo already has the video's scale: its own room stays
                    if mask is not None:
                        reference_picture = cutout_picture(reference_picture, mask.cpu().numpy(), ref_box, drv_box)
                elif ref_box and drv_box:
                    reference_picture = aligned_picture(reference_picture, ref_box, drv_box)
                (folder / 'reference-fit.json').write_text(json.dumps(dict(reference_head=ref_box, video_head=drv_box)), encoding='utf-8')
            if animate:
                del sam_model, sam_clip
                release()
        reference_picture.save(folder / 'reference-used.png')
        reference = torch.from_numpy(np.asarray(reference_picture).copy()).float().unsqueeze(0) / 255

        event('encoder_start')
        text_clip = nodes.CLIPLoader().load_clip('umt5_xxl_fp8_e4m3fn_scaled.safetensors', 'wan', device='default')[0]
        encode = lambda text: nodes.CLIPTextEncode().encode(text_clip, text)[0]
        positive, negative = encode(request['prompt']), encode(request.get('negative_prompt') or '')
        pose_text = encode(request['motion_prompt']) if animate and request.get('motion_prompt') else None
        del text_clip
        release()
        vision = nodes.CLIPVisionLoader().load_clip('clip_vision_h.safetensors')[0]
        look = lambda image: nodes.CLIPVisionEncode().encode(vision, image, 'none')[0]
        vision_reference, vision_pose = look(reference), look(segment_frames(clip, 0, 1))
        del vision
        release()
        vae = nodes.VAELoader().load_vae('Wan2_1_VAE_bf16.safetensors')[0]

        driving_mask = reference_mask = None
        if not animate:
            words = lambda text: nodes.CLIPTextEncode().encode(sam_clip, text)[0]
            reference_words = ', '.join([request['reference_object']] + [item['reference_object'] for item in request.get('subjects', [])])
            driving_words = ', '.join([request['driving_object']] + [item['driving_object'] for item in request.get('subjects', [])])
            reference_track = SAM3_VideoTrack.execute(reference, sam_model, conditioning=words(reference_words), detection_threshold=0.5, max_objects=4, detect_interval=1)[0]
            found(reference_track, 'на фото', reference_words)
            order = 'area' if extras else 'left_to_right'
            driving_conditioning = words(driving_words)

            def track(start, length):
                return SAM3_VideoTrack.execute(segment_frames(clip, start, length), sam_model, conditioning=driving_conditioning,
                                               detection_threshold=0.5, max_objects=4, detect_interval=1)[0]
            holder = {}

            def colored(track_data):
                found(track_data, 'в видео', driving_words)
                masks, holder['reference'] = SCAIL2ColoredMask.execute(track_data, '', order, replace, ref_track_data=reference_track)
                return masks
            whole = colored(track(0, frames)) if frames <= TRACK_WHOLE_CLIP else None

            def driving_mask(start, length):
                masks = whole if whole is not None else colored(track(start, length))
                base = start if whole is not None else 0
                part = masks[min(base, len(masks) - 1):base + length]
                return part if len(part) == length else torch.cat([part, part[-1:].expand(length - len(part), *part.shape[1:])])
            if whole is None:
                driving_mask(0, SEGMENT)
            reference_mask = holder['reference']
            Image.fromarray((reference_mask[0].float().cpu().numpy() * 255).astype('uint8')).save(folder / 'reference-mask.png')
            if whole is not None:
                # Longer clips track every segment when it comes (driving_mask above), so SAM3 and the reference track must stay alive until the
                # last segment; deleting them here made the second segment fail with an unbound variable (found by the first 30 s run, 721 frames).
                del sam_model, sam_clip, reference_track
                release()

        event('model_load_phase', phase='diffusion')
        original = nodes.UNETLoader().load_unet(weights, 'default')[0]
        for name, strength in loras:
            original = nodes.LoraLoaderModelOnly().load_lora_model_only(original, name, strength)[0]
        sampler = KSamplerSelect.execute(sampler_name)[0]
        state = dict(segment=0)
        output = Exporter(folder / 'silent-video.mp4', ffmpeg, width, height, fps)
        written, tail, started_at = 0, None, 0
        try:
            def progress(value, total, preview=None, **kwargs):
                event('step', completed_steps=int(value), total=int(total), segment=state['segment'], segments=len(plan), elapsed=round(time.monotonic() - started, 1))
            comfy.utils.set_progress_bar_global_hook(progress)
            event('sampling_start', elapsed=round(time.monotonic() - started, 1))
            for index, length in enumerate(plan):
                state['segment'] = index
                begun = time.monotonic()
                pose = segment_frames(clip, started_at, length)
                release()
                if animate:
                    model = WanAnimate2Cache.execute(original, 'cpu', 'int8')[0]
                    pos, neg, latent, trim_latent, trim_image, _ = WanAnimate2ToVideo.execute(
                        positive, negative, vae, width, height, length, 1, 0, reference_image=reference, pose_video=pose,
                        clip_vision_output=vision_reference, positive_pose=pose_text, clip_vision_output_pose=vision_pose,
                        continue_motion=tail if index else None)
                else:
                    model = original
                    pos, neg, latent, _ = WanSCAILToVideo.execute(
                        positive, negative, vae, width, height, length, 1, 1.0, 0.0, 1.0, 0, SCAIL_OVERLAP, replacement_mode=replace,
                        reference_image=reference, clip_vision_output=vision_reference, pose_video=pose,
                        pose_video_mask=driving_mask(started_at, length), reference_image_mask=reference_mask, previous_frames=tail if index else None)
                    trim_latent, trim_image = 0, (SCAIL_OVERLAP if index else 0)
                patched = ModelSamplingSD3().patch(model, SHIFT)[0]
                sigmas = BasicScheduler.execute(model, 'simple', steps, 1.0)[0]
                sampled = SamplerCustom.execute(patched, True, int(request['seed']) % 2 ** 64, cfg, pos, neg, sampler, sigmas, latent)[0]
                if trim_latent:
                    sampled = TrimVideoLatent.execute(sampled, trim_latent)[0]
                del pos, neg, latent, patched, model
                release()
                images = nodes.VAEDecodeTiled().decode(vae, sampled, tile_size=512, overlap=64, temporal_size=64, temporal_overlap=8)[0]
                if images.ndim != 4 or not torch.isfinite(images).all() or float(images.max() - images.min()) < .001:
                    raise RuntimeError('Модель вернула повреждённые или однотонные кадры')
                images = images[trim_image:].cpu()
                if not animate and index:
                    images = torch.from_numpy(match_colors(images.numpy(), tail[-1].numpy()))
                images = images[:max(0, frames - written)]
                output.write(images)
                written += len(images)
                tail = images[-overlap:] if tail is None or len(images) >= overlap else torch.cat([tail, images])[-overlap:]
                started_at = written - overlap
                event('segment_timing', index=index, segments=len(plan), seconds=round(time.monotonic() - begun, 1), frames=written)
                del sampled, images, pose
            output.close()
        except BaseException:
            output.abort()
            raise
        finally:
            comfy.utils.set_progress_bar_global_hook(None)
        if written != frames:
            raise RuntimeError('Модель вернула %d кадров вместо %d' % (written, frames))
        del original, vae
        clip._mmap.close()                                   # the decoded reference video is large and only this job needs it
        (folder / 'driving.rgb').unlink(missing_ok=True)
        release()
        event('decode_phase', elapsed=round(time.monotonic() - started, 1))
        command = [str(ffmpeg), '-nostdin', '-v', 'error', '-n', '-i', str(folder / 'silent-video.mp4')]
        if request.get('keep_audio', True) and request.get('driving_has_audio'):
            command += ['-i', str(confined(folder, media['driving'])), '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k', '-ar', '48000', '-ac', '2',
                        '-af', 'apad', '-t', str(frames / fps)]
        else:
            command += ['-map', '0:v:0', '-c:v', 'copy']
        subprocess.run(command + ['-movflags', '+faststart', str(folder / 'result.mp4')], check=True, timeout=600)
        event('character_metrics', inference_sec=round(time.monotonic() - started, 2), segments=len(plan), frames=frames,
              peak_allocated_vram_mb=round(torch.cuda.max_memory_allocated() / 1024 ** 2), torch=torch.__version__, python=sys.version.split()[0])


def main():
    parser = argparse.ArgumentParser()
    for name in ('root', 'models', 'folder', 'spec'):
        parser.add_argument('--' + name, required=True)
    try:
        run(parser.parse_args())
    except Exception:
        traceback.print_exc(limit=12)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
