"""Native ComfyUI Wan 2.2 TI2V-5B in one owned, offline Video process."""
import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

def event(name, **values):
    print(json.dumps(dict(event=name, **values), ensure_ascii=False), flush=True)

def export_video(images, output, ffmpeg, width, height, fps):
    """Write RGB frames directly to the bundled FFmpeg, with no image sequence cache."""
    import torch
    process = subprocess.Popen([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-n',
        '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', f'{width}x{height}',
        '-framerate', str(fps), '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-preset', 'medium',
        '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(output)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        for image in images:
            frame = (image[..., :3].clamp(0, 1) * 255).round().to(dtype=torch.uint8).cpu().numpy()
            process.stdin.write(frame.tobytes())
        process.stdin.close()
        error = process.stderr.read(8192).decode('utf-8', errors='replace')
        if process.wait(timeout=120) != 0:
            raise RuntimeError('FFmpeg: ' + error)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)

def run(root, models, folder, request):
    """Execute the official text/latent/sampler/VAE chain with native RAM offload."""
    sys.path.insert(0, str(root / 'vendor/ComfyUI'))
    # These flags apply only to this worker, never to the running Image or H3 process.
    sys.argv = ['visualforge-wan', '--disable-dynamic-vram', '--disable-pinned-memory',
                '--disable-smart-memory', '--use-pytorch-cross-attention', '--reserve-vram', '1.5']
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
    steps = {'preview': 12, 'standard': 20, 'high': 30}[request['quality']]
    profile = dict(steps=steps, cfg=5.0, shift=8.0, sampler='uni_pc', scheduler='simple',
                   width=request['width'], height=request['height'], frames=request['frames'],
                   fps=request['fps'], seed=request['seed'], mode=request['mode'],
                   native_comfy_version='0.34.0', offload='native estimate-based, reserve 1.5 GB',
                   vae_decode='tiled, 512px, temporal 16, overlap 4')
    (folder / 'wan-inference-config.json').write_text(json.dumps(profile, indent=2), encoding='utf-8')
    event('sampling_plan', **profile)
    started = time.monotonic()
    with torch.inference_mode():
        event('encoder_start')
        clip = nodes.CLIPLoader().load_clip('umt5_xxl_fp8_e4m3fn_scaled.safetensors', 'wan', device='default')[0]
        positive = nodes.CLIPTextEncode().encode(clip, request['prompt'])[0]
        negative = nodes.CLIPTextEncode().encode(clip,
            'blurry, low quality, static image, watermark, subtitles, deformed faces, extra limbs, flicker')[0]
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
        latent = Wan22ImageToVideoLatent.execute(vae, request['width'], request['height'],
                                               request['frames'], 1, start_image=start_image)[0]
        memory.unload_all_models()
        memory.soft_empty_cache()
        event('model_load_phase', phase='diffusion')
        original = nodes.UNETLoader().load_unet('wan2.2_ti2v_5B_fp16.safetensors', 'default')[0]
        model = ModelSamplingSD3().patch(original, 8.0)[0]
        last_step = [-1]
        def progress(value, total, preview=None, **kwargs):
            if value != last_step[0]:
                last_step[0] = value
                event('step', completed_steps=int(value), total=int(total))
        comfy.utils.set_progress_bar_global_hook(progress)
        event('sampling_start')
        sampled = nodes.KSampler().sample(model, int(request['seed']), steps, 5.0, 'uni_pc', 'simple',
                                          positive, negative, latent, denoise=1.0)[0]
        comfy.utils.set_progress_bar_global_hook(None)
        del model, original, latent, positive, negative
        memory.unload_all_models()
        memory.soft_empty_cache()
        event('decode_phase')
        images = nodes.VAEDecodeTiled().decode(vae, sampled, tile_size=512, overlap=64,
                                             temporal_size=16, temporal_overlap=4)[0]
        if images.ndim != 4 or len(images) != request['frames']:
            raise RuntimeError('Wan вернул неверное число кадров')
        if not torch.isfinite(images).all():
            raise RuntimeError('Wan вернул повреждённые кадры')
        if float(images.max() - images.min()) < .001:
            raise RuntimeError('Wan вернул однотонные кадры')
        images = images.cpu()
        memory.unload_all_models()
        memory.soft_empty_cache()
        export_video(images, folder / 'result.mp4', root / 'ffmpeg/bin/ffmpeg.exe',
                     request['width'], request['height'], request['fps'])
        event('wan_metrics', inference_sec=round(time.monotonic() - started, 2),
              peak_allocated_vram_mb=round(torch.cuda.max_memory_allocated() / 1024**2),
              torch=torch.__version__, python=sys.version.split()[0])

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
