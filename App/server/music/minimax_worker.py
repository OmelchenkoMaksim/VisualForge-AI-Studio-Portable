"""One offline MiniMax Music3 job in an owned native-Comfy process."""
import json
import logging
import os
from pathlib import Path
import sys
import subprocess
import time
import traceback


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
        import wave
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
            caption += '\nInstrumental music only, no vocals, no singing, no spoken words.'
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
            pcm = (np.clip(waveform, -1, 1) * 32767).round().astype('<i2')
            native_output = output if rate == 32000 else output.with_name('Native MiniMax Music3.wav')
            with wave.open(str(native_output), 'wb') as stream:
                stream.setnchannels(2); stream.setsampwidth(2); stream.setframerate(rate)
                stream.writeframes(pcm.tobytes())
            if rate != 32000:
                # The native Comfy decoder uses 44.1 kHz; resample without changing pitch or duration.
                subprocess.run([str(root/'ffmpeg/bin/ffmpeg.exe'),'-hide_banner','-loglevel','error','-nostdin','-n',
                                '-i',str(native_output),'-ar','32000','-ac','2','-c:a','pcm_s16le',str(output)],
                               check=True,capture_output=True,timeout=60,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            metadata = dict(sample_rate=32000, native_sample_rate=rate, channels=2, duration=round(duration, 3),
                            requested_max_duration=request['duration'], generation_sec=round(time.monotonic()-started, 2),
                            peak_allocated_vram_mb=round(torch.cuda.max_memory_allocated()/1024**2),
                            torch=torch.__version__, python=sys.version.split()[0], inference=config)
            write_json(directory / 'result.json', {'path': str(output), 'metadata': metadata})
            phase('Аудио готово')
    except Exception as exc:
        traceback.print_exc(limit=12)
        write_json(directory / 'error.json', {'error': str(exc)[:2000]})
        raise SystemExit(1)


if __name__ == '__main__':
    main()
