"""Native ComfyUI Wan 2.2 TI2V-5B in one owned, offline Video process.

A long clip has one prompt per segment, generated one after another in this same process, and all frames go through one
FFmpeg encode. Two joins exist: "continue" (a chain of 121-frame segments, each starting from the exact last frame of the
previous one) and "cut" (independent scenes, each its own shot, joined with hard cuts).
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

DEFAULT_NEGATIVE = 'blurry, low quality, static image, watermark, subtitles, deformed faces, extra limbs, flicker'
SEGMENT_FRAMES = 121

def event(name, **values):
    print(json.dumps(dict(event=name, **values), ensure_ascii=False), flush=True)

class Exporter:
    """One FFmpeg process fed frame by frame, with no image sequence cache.

    A chain of segments is written through a single encoder: joining separate MP4 files would either re-encode every
    segment again or need stream copying of clips whose boundaries repeat a frame.
    """
    def __init__(self, output, ffmpeg, width, height, fps):
        self.process = subprocess.Popen([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-n',
            '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', f'{width}x{height}',
            '-framerate', str(fps), '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-preset', 'medium',
            '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(output)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.written = 0

    def write(self, images):
        import torch
        for image in images:
            frame = (image[..., :3].clamp(0, 1) * 255).round().to(dtype=torch.uint8).cpu().numpy()
            self.process.stdin.write(frame.tobytes())
            self.written += 1

    def close(self):
        self.process.stdin.close()
        error = self.process.stderr.read(8192).decode('utf-8', errors='replace')
        if self.process.wait(timeout=300) != 0:
            raise RuntimeError('FFmpeg: ' + error)

    def abort(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)

def export_video(images, output, ffmpeg, width, height, fps):
    """Write one finished clip of RGB frames to the bundled FFmpeg. The S2V and LTX workers import this name."""
    exporter = Exporter(output, ffmpeg, width, height, fps)
    try:
        exporter.write(images)
        exporter.close()
    except BaseException:
        exporter.abort()
        raise

def run(root, models, folder, request):
    """Execute the official text/latent/sampler/VAE chain with native RAM offload."""
    sys.path.insert(0, str(root / 'vendor/ComfyUI'))
    # These flags apply only to this worker, never to the running Image or H3 process.
    # SageAttention (installed with the Video runtime) cut the sampling steps by about 19% on an RTX 5070 Ti with visually
    # equivalent output (same seed, same layout); PyTorch attention stays the fallback and can be forced for diagnosis.
    import importlib.util
    sage = os.environ.get('VISUALFORGE_WAN_ATTENTION') != 'pytorch' and importlib.util.find_spec('sageattention') is not None
    attention = '--use-sage-attention' if sage else '--use-pytorch-cross-attention'
    sys.argv = ['visualforge-wan', '--disable-dynamic-vram', '--disable-pinned-memory',
                '--disable-smart-memory', attention, '--reserve-vram', '1.5']
    logging.basicConfig(level=logging.WARNING)
    import comfy.options
    comfy.options.enable_args_parsing()
    import torch
    import numpy as np
    from PIL import Image
    import folder_paths
    import nodes
    import comfy.model_management as memory
    import comfy.utils
    from comfy_extras.nodes_wan import Wan22ImageToVideoLatent
    from comfy_extras.nodes_model_advanced import ModelSamplingSD3
    if not torch.cuda.is_available():
        raise RuntimeError('Wan требует NVIDIA CUDA GPU')
    torch.set_num_threads(min(8, os.cpu_count() or 4))
    for name in ('diffusion_models', 'text_encoders', 'vae'):
        folder_paths.add_model_folder_path(name, str(models / name), is_default=True)
    comfy.utils.PROGRESS_BAR_ENABLED = False
    # fp8 matrix multiplication needs an Ada/Blackwell GPU (compute capability 8.9+). Measured on an RTX 5070 Ti with the
    # same seed: sampling steps 37% faster, peak VRAM 11.7 -> 7.3 GB, composition and detail visually the same as fp16.
    # Older cards, or VISUALFORGE_WAN_WEIGHTS=default, keep the original fp16 weights.
    fast_weights = os.environ.get('VISUALFORGE_WAN_WEIGHTS') != 'default' and torch.cuda.get_device_capability() >= (8, 9)
    weight_dtype = 'fp8_e4m3fn_fast' if fast_weights else 'default'
    steps = {'preview': 12, 'standard': 20, 'high': 30}[request['quality']]
    prompts = request.get('segment_prompts') or [request['prompt']]
    segments = len(prompts)
    total = request['frames']
    cut = request.get('join') == 'cut'
    segment_frames = request['scene_frames'] if cut else SEGMENT_FRAMES if segments > 1 else total
    profile = dict(steps=steps, cfg=5.0, shift=8.0, sampler='uni_pc', scheduler='simple',
                   width=request['width'], height=request['height'], frames=total,
                   fps=request['fps'], seed=request['seed'], mode=request['mode'],
                   segments=segments, segment_frames=segment_frames, join='cut' if cut else 'continue',
                   attention='sage' if sage else 'pytorch', weights=weight_dtype,
                   native_comfy_version='0.34.0', offload='native estimate-based, reserve 1.5 GB',
                   vae_decode='tiled, 512px, temporal 16, overlap 4')
    (folder / 'wan-inference-config.json').write_text(json.dumps(profile, indent=2), encoding='utf-8')
    event('sampling_plan', **profile)
    started = time.monotonic()
    exporter = Exporter(folder / 'result.mp4', root / 'ffmpeg/bin/ffmpeg.exe',
                        request['width'], request['height'], request['fps'])
    try:
        with torch.inference_mode():
            event('encoder_start')
            clip = nodes.CLIPLoader().load_clip('umt5_xxl_fp8_e4m3fn_scaled.safetensors', 'wan', device='default')[0]
            # Every segment prompt is encoded now, so the 6 GB text encoder is loaded once for the whole chain.
            positives = [nodes.CLIPTextEncode().encode(clip, text)[0] for text in prompts]
            # CFG is 5, so the negative branch is active; the user's text replaces the default list entirely.
            negative = nodes.CLIPTextEncode().encode(clip, request.get('negative_prompt') or DEFAULT_NEGATIVE)[0]
            del clip
            memory.unload_all_models()
            memory.soft_empty_cache()
            event('model_load_phase', phase='vae')
            vae = nodes.VAELoader().load_vae('wan2.2_vae.safetensors')[0]
            start_image = None
            if request['mode'] == 'i2v':
                media = json.loads((folder / 'media.json').read_text(encoding='utf-8'))
                image_path = Path(media['first']).resolve()
                if not image_path.is_relative_to(folder.resolve()):
                    raise ValueError('Исходный кадр вне папки задачи Wan')
                with Image.open(image_path) as image:
                    start_image = torch.from_numpy(np.asarray(image.convert('RGB')).copy()).float().unsqueeze(0) / 255
            model = None
            for index in range(segments):
                if cut:
                    event('scene_start', scene=index + 1, scenes=segments)
                    # Every scene is a new shot: only the first one may start from the user's picture.
                    if index:
                        start_image = None
                elif segments > 1:
                    event('continuation_start', part=index + 1, parts=segments)
                marks = dict(begin=time.monotonic())
                latent = Wan22ImageToVideoLatent.execute(vae, request['width'], request['height'],
                                                       segment_frames, 1, start_image=start_image)[0]
                memory.unload_all_models()
                memory.soft_empty_cache()
                if model is None:
                    event('model_load_phase', phase='diffusion')
                    # Built once: reading and converting the 10 GB checkpoint cost about 4 s per scene. The patcher only holds
                    # weights in RAM between segments; sampling moves them to the GPU and unload_all_models() moves them back.
                    original = nodes.UNETLoader().load_unet('wan2.2_ti2v_5B_fp16.safetensors', weight_dtype)[0]
                    model = ModelSamplingSD3().patch(original, 8.0)[0]
                last_step = [-1]
                def progress(value, total_steps, preview=None, **kwargs):
                    if value != last_step[0]:
                        last_step[0] = value
                        marks.setdefault('first_step', time.monotonic())
                        marks['last_step'] = time.monotonic()
                        event('step', completed_steps=int(value), total=int(total_steps))
                comfy.utils.set_progress_bar_global_hook(progress)
                event('sampling_start')
                marks['sampling'] = time.monotonic()
                # Chained segments get a new seed each (the same noise under a new prompt repeats the composition). Independent
                # scenes share one: in a 4-scene comparison this kept the subject's fur and clothes slightly more alike
                # (a small sample), while the differing prompts still give different shots.
                seed = int(request['seed']) % 2**64 if cut else (int(request['seed']) + index) % 2**64
                sampled = nodes.KSampler().sample(model, seed, steps, 5.0, 'uni_pc', 'simple',
                                                  positives[index], negative, latent, denoise=1.0)[0]
                comfy.utils.set_progress_bar_global_hook(None)
                del latent
                memory.unload_all_models()
                memory.soft_empty_cache()
                event('decode_phase')
                marks['decode'] = time.monotonic()
                # Larger tiles (1024/128, 32/8 frames) were slower in a 121-frame test, so the original 512 tiles stay.
                images = nodes.VAEDecodeTiled().decode(vae, sampled, tile_size=512, overlap=64,
                                                     temporal_size=16, temporal_overlap=4)[0]
                if images.ndim != 4 or len(images) != segment_frames:
                    raise RuntimeError('Wan вернул неверное число кадров')
                if not torch.isfinite(images).all():
                    raise RuntimeError('Wan вернул повреждённые кадры')
                if float(images.max() - images.min()) < .001:
                    raise RuntimeError('Wan вернул однотонные кадры')
                images = images.cpu()
                memory.unload_all_models()
                memory.soft_empty_cache()
                # The next segment starts from this exact decoded frame (float, before 8-bit rounding).
                if not cut:
                    start_image = images[-1:].clone()
                # Frame 0 of a continued segment is the conditioning frame, which the previous segment already ended with.
                fresh = images if index == 0 or cut else images[1:]
                exporter.write(fresh[:max(0, total - exporter.written)])
                del images, fresh, sampled
                marks['end'] = time.monotonic()
                # Where a segment's time goes: weights + first step, the remaining steps, VAE decode, and the write to FFmpeg.
                event('segment_timing', segment=index + 1, prepare_sec=round(marks['sampling'] - marks['begin'], 1),
                      load_and_first_step_sec=round(marks['first_step'] - marks['sampling'], 1),
                      other_steps_sec=round(marks['last_step'] - marks['first_step'], 1),
                      decode_and_write_sec=round(marks['end'] - marks['decode'], 1))
            if exporter.written != total:
                raise RuntimeError('Wan chain produced %d of %d frames' % (exporter.written, total))
            exporter.close()
            event('wan_metrics', inference_sec=round(time.monotonic() - started, 2),
                  peak_allocated_vram_mb=round(torch.cuda.max_memory_allocated() / 1024**2),
                  torch=torch.__version__, python=sys.version.split()[0])
    except BaseException:
        exporter.abort()
        raise

def main():
    parser = argparse.ArgumentParser()
    for name in ('root', 'models', 'folder', 'spec'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    root, models, folder = map(lambda value: Path(value).resolve(), (args.root, args.models, args.folder))
    request = json.loads(Path(args.spec).read_text(encoding='utf-8'))
    try:
        run(root, models, folder, request)
    except Exception:
        traceback.print_exc(limit=12)
        raise SystemExit(1)

if __name__ == '__main__':
    main()
