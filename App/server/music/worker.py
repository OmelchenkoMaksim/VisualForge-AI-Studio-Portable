"""One Music inference per owned process; exiting releases all GPU allocations."""
import json
import os
from pathlib import Path
import sys
import time
import traceback

def main():
    spec = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    directory = Path(sys.argv[1]).parent
    sys.path.insert(0, str(Path(spec['runtime']) / 'vendor/ACE-Step'))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from music.storage import write_json
    announced = [None]
    def phase(name, fraction=None):
        try:
            write_json(directory / 'progress.json', {'phase': name, 'progress': fraction})
        except OSError:
            pass  # progress is only shown on the page; a file the bridge still holds open must not end the generation
        if name != announced[0]:
            announced[0] = name
            print(name, flush=True)
    try:
        phase('Загрузка Music')
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, level='WARNING', backtrace=False, diagnose=False,
                   filter=lambda record: record['level'].no >= 40 or not str(record['name']).startswith('acestep.training'),
                   format='{time:HH:mm:ss} | {level} | {message}')
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError('Music требует доступную NVIDIA CUDA GPU')
        torch.set_num_threads(min(8, os.cpu_count() or 4))
        from acestep.handler import AceStepHandler
        from acestep.llm_inference import LLMHandler
        from acestep.inference import GenerationParams, GenerationConfig, generate_music
        class OfflineMusicHandler(AceStepHandler):
            def _ensure_models_present(self, *, checkpoint_path, config_path, prefer_source, vae_variant=None):
                # Upstream's main bundle check insists on the optional 1.7B LM.
                # This integration uses the separately downloaded 0.6B LM instead.
                required = [checkpoint_path / name for name in (
                    config_path + '/model.safetensors', 'vae/diffusion_pytorch_model.safetensors',
                    'Qwen3-Embedding-0.6B/model.safetensors')]
                if any(not path.is_file() for path in required):
                    return 'Файлы Music отсутствуют; восстановите установленный пакет моделей.', False
                return None
        dit = OfflineMusicHandler()
        phase('Загрузка музыкальной модели')
        status, ok = dit.initialize_service(
            project_root=str(Path(spec['runtime']) / 'vendor/ACE-Step'),
            config_path='acestep-v15-turbo', device='cuda',
            use_flash_attention=False, compile_model=False, offload_to_cpu=True,
            offload_dit_to_cpu=True, quantization=None, use_mlx_dit=False)
        if not ok:
            raise RuntimeError(status)
        lm = LLMHandler()
        request = spec['request']
        if request['planner']:
            phase('Загрузка музыкального планировщика')
            status, ok = lm.initialize(checkpoint_dir=spec['models'],
                lm_model_path='acestep-5Hz-lm-0.6B', backend='pt', device='cuda',
                offload_to_cpu=True, dtype=torch.bfloat16)
            if not ok:
                raise RuntimeError(status)
        instrumental = request['mode'] == 'instrumental'
        params = GenerationParams(caption=request['caption'],
            lyrics='[Instrumental]' if instrumental else request['lyrics'], instrumental=instrumental,
            duration=request['duration'], seed=request['seed'], bpm=request['bpm'],
            vocal_language=request['language'], inference_steps=8, thinking=request['planner'],
            use_cot_metas=request['planner'], use_cot_caption=False, use_cot_language=False,
            fade_in_duration=.05, fade_out_duration=.3)
        config = GenerationConfig(batch_size=1, use_random_seed=False, seeds=[request['seed']],
                                  audio_format='wav', allow_lm_batch=False)
        last_phase = ['']
        def progress(value=None, desc='', **kwargs):
            # Keep phase updates small; no invented percentages or per-step log spam.
            if desc and desc != last_phase[0]:
                last_phase[0] = desc
                phase('Генерация музыки', float(value) if isinstance(value, (int, float)) else None)
        phase('Генерация музыки')
        started = time.monotonic()
        result = generate_music(dit, lm, params, config, save_dir=str(directory / 'audio'), progress=progress)
        if not result.success or not result.audios:
            raise RuntimeError(result.error or result.status_message or 'Music не вернул аудио')
        audio = Path(result.audios[0]['path']).resolve()
        if not audio.is_relative_to(directory.resolve()) or not audio.is_file():
            raise RuntimeError('Music вернул неверный путь аудио')
        import soundfile as sf
        import numpy as np
        samples, rate = sf.read(audio, dtype='float32', always_2d=True)
        if not np.isfinite(samples).all() or samples.size == 0 or float(np.max(np.abs(samples))) < 1e-5:
            raise RuntimeError('Music вернул пустое, повреждённое или беззвучное аудио')
        metadata = {'sample_rate': rate, 'channels': samples.shape[1],
                    'duration': round(samples.shape[0] / rate, 3),
                    'generation_sec': round(time.monotonic() - started, 2),
                    'torch': torch.__version__, 'python': sys.version.split()[0],
                    'peak_allocated_vram_mb': round(torch.cuda.max_memory_allocated() / 1024**2)}
        if abs(metadata['duration'] - request['duration']) > 2:
            raise RuntimeError('Фактическая длительность Music не соответствует запросу')
        write_json(directory / 'result.json', {'path': str(audio), 'metadata': metadata})
        phase('Аудио готово')
    except Exception as exc:
        traceback.print_exc(limit=12)
        write_json(directory / 'error.json', {'error': str(exc)[:2000]})
        raise SystemExit(1)

if __name__ == '__main__':
    main()
