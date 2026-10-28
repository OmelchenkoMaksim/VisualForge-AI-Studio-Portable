"""Synthesizes the fragments of one voiceover job with VoxCPM2 (48 kHz), in one owned process, one fragment at a time.

Same contract as worker.py (the Qwen3-TTS worker): the process exits when the job ends so the video card is free again, a fragment whose
cache file exists is skipped (that is what makes a stopped job resumable), and every file is written atomically. VoxCPM2 has no batch
interface; its own speed is about 0.3 s of work per second of audio.
"""
import json
import os
from pathlib import Path
import sys
import time
import traceback

# Run as a script by the manager, so the server folder is put on sys.path by hand before the shared helpers are imported.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voiceover.worker import event, plausible  # noqa: E402


def main():
    spec_path = Path(sys.argv[1])
    directory = spec_path.parent
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    for package in reversed(spec['packages']):
        sys.path.insert(0, package)
    try:
        from generation import operation
        from voiceover.storage import write_json
        cache = Path(spec['cache'])
        cache.mkdir(parents=True, exist_ok=True)
        for leftover in cache.glob('*.tmp'):
            leftover.unlink(missing_ok=True)  # a write interrupted by Stop or a crash
        total = len(spec['fragments'])
        todo = [item for item in spec['fragments'] if not (cache / (item['key'] + '.wav')).is_file()]
        done = total - len(todo)
        flagged = []

        def progress(phase):
            try:
                write_json(directory / 'progress.json', {'phase': phase, 'done': done, 'total': total})
            except OSError:
                pass  # progress is only shown on the page; a file the bridge holds open must not end the job
            event('progress', phase=phase, done=done, total=total)

        progress('loading')
        if todo:
            import numpy as np
            import soundfile as sf
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError('Озвучка требует доступную NVIDIA CUDA GPU')
            from voxcpm import VoxCPM

            def load(optimize):
                # No denoiser (it would need the ModelScope package and a download). torch.compile (2.3 times faster here, about 110 s
                # for the first compilation, cached afterwards) only where triton exists: the package switches it off by itself
                # and says so when triton is missing.
                return VoxCPM(voxcpm_model_path=spec['model'], zipenhancer_model_path=None, enable_denoiser=False, optimize=optimize, device='cuda')

            optimize = bool(spec.get('optimize', False))
            model = load(optimize)
            rate = model.tts_model.sample_rate
            references = {'reference_wav_path': spec['voice']}
            if spec.get('transcript'):
                # «Ultimate cloning»: the model continues the sample, which needs the exact words spoken in it.
                references.update(prompt_wav_path=spec['voice'], prompt_text=spec['transcript'])
            started = time.monotonic()
            with operation('SpeechSynthesis'):
                for item in todo:
                    best = None
                    for attempt in range(spec.get('attempts', 3)):
                        # The seed comes from the fragment's identity: a re-run of the same text is reproducible,
                        # a retry shifts it to get a different take.
                        torch.manual_seed(int(item['key'][:8], 16) + attempt * 7919 + int(spec.get('seed_shift', 0)) * 104729)
                        try:
                            wav = model.generate(text=item['text'], cfg_value=float(spec.get('cfg', 2.0)), inference_timesteps=int(spec.get('steps', 10)),
                                                 max_len=int(len(item['text']) * 1.3) + 40, **references)
                        except Exception as error:
                            if not optimize:
                                raise
                            # The compilation is lazy, so a missing compiler shows up only at the first call. Start again without it.
                            event('compile_failed', error=str(error)[:300])
                            optimize, model = False, None
                            torch.cuda.empty_cache()
                            model = load(False)
                            wav = model.generate(text=item['text'], cfg_value=float(spec.get('cfg', 2.0)), inference_timesteps=int(spec.get('steps', 10)),
                                                 max_len=int(len(item['text']) * 1.3) + 40, **references)
                        wav = np.asarray(wav, dtype=np.float32)
                        samples = wav[:, None] if wav.ndim == 1 else wav
                        best = samples
                        if plausible(samples, rate, item['text']):
                            break
                    else:
                        flagged.append(item['key'])  # kept anyway; the page lists it for a manual decision
                    target = cache / (item['key'] + '.wav')
                    temporary = target.with_name(target.name + '.tmp')
                    sf.write(str(temporary), best, rate, subtype='PCM_16', format='WAV')
                    os.replace(temporary, target)
                    done += 1
                    progress('synthesis')
            event('synthesis_done', seconds=round(time.monotonic() - started, 1), peak_vram_mb=round(torch.cuda.max_memory_allocated() / 1024 ** 2),
                  peak_reserved_mb=round(torch.cuda.max_memory_reserved() / 1024 ** 2), sample_rate=rate)
        write_json(directory / 'result.json', {'fragments': total, 'reused': total - len(todo), 'flagged': flagged})
        progress('done')
    except Exception as exc:
        traceback.print_exc(limit=12)
        try:
            from voiceover.storage import write_json
            write_json(directory / 'error.json', {'error': str(exc)[:2000]})
        except Exception:
            pass
        raise SystemExit(1)


if __name__ == '__main__':
    main()
