"""One offline MiniMax Music3 job in an owned native-Comfy process."""
import json
import logging
import os
from pathlib import Path
import sys
import subprocess
import time
import traceback
import re


def save_minimax_audio(waveform, rate, output, ffmpeg):
    """Resample before quantization, retain dynamics and leave measured true-peak headroom."""
    import numpy as np
    from ui_strings import source as ui_text
    output, ffmpeg = Path(output), Path(ffmpeg)
    floating = output.with_name('Float MiniMax Music3.wav')
    duration = len(waveform) / rate
    last_sample = max(0, round(duration * 32000) - 1) / 32000
    common = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n']
    options = dict(check=True, capture_output=True, timeout=60,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    # The old PCM clamp preceded resampling: both decoder overs and resampling peaks could clip permanently.
    # Float transport keeps those peaks intact until a constant gain is chosen; 5 ms ramps prevent nonzero edge cuts.
    subprocess.run(common + ['-f', 'f32le', '-ar', str(rate), '-ac', '2', '-i', 'pipe:0',
                             '-af', f'aresample=32000,afade=t=in:d=0.005,afade=t=out:st={max(0, last_sample-.005):.8f}:d=0.005',
                             '-c:a', 'pcm_f32le', str(floating)], input=np.asarray(waveform, dtype='<f4').tobytes(), **options)
    analysis = subprocess.run([str(ffmpeg), '-nostdin', '-hide_banner', '-i', str(floating), '-af',
                               'loudnorm=I=-18:TP=-1:LRA=11:print_format=json', '-f', 'null', '-'], **options)
    match = re.search(rb'\{\s*"input_i".*?\}', analysis.stderr, re.S)
    if not match:
        raise RuntimeError(ui_text('music.minimax_peak_measurement_error'))
    true_peak = float(json.loads(match.group())['input_tp'])
    if not np.isfinite(true_peak):
        raise RuntimeError(ui_text('music.minimax_peak_invalid_error'))
    gain = min(0., -1.0 - true_peak)
    # loudnorm above is measurement only. A single constant attenuation preserves musical dynamics and stereo balance.
    subprocess.run(common + ['-i', str(floating), '-af', f'volume={gain:.3f}dB', '-ar', '32000', '-ac', '2',
                             '-c:a', 'pcm_s16le', str(output)], **options)
    floating.unlink(missing_ok=True)
    return dict(version='minimax-wav-v2', gain_db=round(gain, 3), input_true_peak_db=true_peak, edge_ramp_ms=5)


def main():
    spec_path = Path(sys.argv[1]).resolve()
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    directory = spec_path.parent
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from music.music_storage import write_json
    started = time.monotonic()
    last_phase = [None]

    def phase(name, fraction=None):
        try:
            write_json(directory / 'progress.json', {'phase': name, 'progress': fraction})
        except OSError:
            pass  # progress is only shown on the page; a file the bridge still holds open must not end the generation
        if last_phase[0] != name:
            print(name, flush=True)
            last_phase[0] = name

    try:
        root, models = Path(spec['runtime']), Path(spec['models'])
        request = spec['request']
        sys.path.insert(0, str(root / 'vendor/ComfyUI'))
        sys.argv = ['visualforge-minimax-music', '--disable-dynamic-vram', '--disable-pinned-memory',
                    '--disable-smart-memory', '--use-pytorch-cross-attention', '--reserve-vram', '1.5']
        logging.basicConfig(level=logging.WARNING)
        import comfy.options
        comfy.options.enable_args_parsing()
        import torch
        import numpy as np
        import folder_paths
        import nodes
        import comfy.utils
        import comfy.model_management as memory
        from comfy_extras.nodes_minimax_music import MiniMaxMusic3TextEncode, EmptyMiniMaxMusic3LatentAudio
        from comfy_extras.nodes_audio import VAEDecodeAudioTiled
        if not torch.cuda.is_available():
            raise RuntimeError('MiniMax Music3 требует NVIDIA CUDA GPU')
        torch.set_num_threads(min(8, os.cpu_count() or 4))
        comfy.utils.PROGRESS_BAR_ENABLED = False
        for category in ('diffusion_models', 'text_encoders', 'vae'):
            folder_paths.add_model_folder_path(category, str(models / category), is_default=True)
        config = dict(engine=request['engine'], seed=request['seed'], max_duration=request['duration'],
                      steps=30, cfg=1.7, ar_top_k=50, sampler='euler', scheduler='simple',
                      precision='INT8 ConvRot text encoder and DiT', vae='tiled 1536 / overlap 64',
                      offload='native ComfyUI; reserve 1.5 GB', native_comfy_version='0.34.0')
        write_json(directory / 'inference-config.json', config)
        caption = request['caption']
        if request['bpm'] is not None:
            caption += f"\nTempo: {request['bpm']} BPM."
        if request['mode'] == 'instrumental':
            # Keep a single mode statement beside the structural token; negative vocal keyword piles are not a guarantee.
            caption += '\nFully instrumental.'
            lyrics = '[Instrumental]'
        else:
            lyrics = request['lyrics']
            language = {'ru':'Russian','en':'English','zh':'Chinese','ja':'Japanese','ko':'Korean',
                        'es':'Spanish','fr':'French','de':'German','it':'Italian','pt':'Portuguese'}.get(request['language'])
            if language:
                caption += '\nVocal language: ' + language + '.'
        with torch.inference_mode():
            phase('Загрузка MiniMax Music3')
            clip = nodes.CLIPLoader().load_clip('minimax_music3_text_encoder_pruned_int8_convrot.safetensors', 'minimax', device='default')[0]
            phase('Создание музыкальной структуры и вокала')
            last_update = [0.0]
            def ar_progress(value, total, preview=None, **kwargs):
                now = time.monotonic()
                if now - last_update[0] >= 2 or value == total:
                    phase('Создание музыкальной структуры и вокала', round(value / max(1, total), 3))
                    last_update[0] = now
            comfy.utils.set_progress_bar_global_hook(ar_progress)
            positive, seconds = MiniMaxMusic3TextEncode.execute(clip, caption, lyrics, request['seed'], request['duration'], 1.7, 50)
            del clip
            memory.unload_all_models()
            memory.soft_empty_cache()
            negative = nodes.ConditioningZeroOut().zero_out(positive)[0]
            latent = EmptyMiniMaxMusic3LatentAudio.execute(seconds, 1)[0]
            phase('Синтез звучания')
            model = nodes.UNETLoader().load_unet('minimax_music3_dit_int8_convrot.safetensors', 'default')[0]
            def sample_progress(value, total, preview=None, **kwargs):
                phase('Синтез звучания', round(value / max(1, total), 3))
            comfy.utils.set_progress_bar_global_hook(sample_progress)
            sampled = nodes.KSampler().sample(model, request['seed'], 30, 1.7, 'euler', 'simple', positive, negative, latent)[0]
            del model, positive, negative, latent
            memory.unload_all_models()
            memory.soft_empty_cache()
            phase('Сохранение стерео WAV')
            vae = nodes.VAELoader().load_vae('minimax_music3_dav.safetensors')[0]
            audio = VAEDecodeAudioTiled.execute(vae, sampled, 1536, 64)[0]
            waveform = audio['waveform'][0].float().cpu().transpose(0, 1).numpy()
            rate = int(audio['sample_rate'])
            if rate not in (32000,44100) or waveform.ndim != 2 or waveform.shape[1] != 2:
                raise RuntimeError(f'MiniMax Music3: неожиданный формат аудио {rate} Hz, shape={waveform.shape}')
            if waveform.size == 0 or not np.isfinite(waveform).all() or float(np.max(np.abs(waveform))) < 1e-5:
                raise RuntimeError('MiniMax Music3 вернул пустое или беззвучное аудио')
            duration = waveform.shape[0] / rate
            if not .5 <= duration <= request['duration'] + 1:
                raise RuntimeError('MiniMax Music3 вернул неверную длительность аудио')
            output = directory / 'audio' / 'MiniMax Music3.wav'
            output.parent.mkdir(exist_ok=True)
            delivery = save_minimax_audio(waveform, rate, output, root/'ffmpeg/bin/ffmpeg.exe')
            metadata = dict(sample_rate=32000, native_sample_rate=rate, channels=2, duration=round(duration, 3),
                            requested_max_duration=request['duration'], generation_sec=round(time.monotonic()-started, 2),
                            peak_allocated_vram_mb=round(torch.cuda.max_memory_allocated()/1024**2),
                            torch=torch.__version__, python=sys.version.split()[0], inference=config, delivery=delivery)
            write_json(directory / 'result.json', {'path': str(output), 'metadata': metadata})
            phase('Аудио готово')
    except Exception as exc:
        traceback.print_exc(limit=12)
        write_json(directory / 'error.json', {'error': str(exc)[:2000]})
        raise SystemExit(1)


if __name__ == '__main__':
    main()
