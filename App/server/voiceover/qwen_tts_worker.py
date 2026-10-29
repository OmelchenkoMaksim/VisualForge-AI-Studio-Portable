"""Synthesizes the fragments of one voiceover job with a Qwen3-TTS voice clone, in one owned process.

The process exits when the job ends, so the video card is free afterwards. A fragment whose cache file already exists is
skipped: that is what makes a stopped or crashed job resumable (the file name is the fragment's content hash).
"""
import json
import os
from pathlib import Path
import sys
import time
import traceback


def event(kind, **fields):
    print(json.dumps({'event': kind, **fields}, ensure_ascii=True), flush=True)


def make_batches(items, size):
    """Groups of at most `size` fragments. Batches are formed from fragments of similar length, because a batch lasts as long
    as its longest member; size 1 keeps the reading order."""
    if size <= 1:
        return [[item] for item in items]
    ordered = sorted(items, key=lambda item: len(item['text']), reverse=True)
    return [ordered[start:start + size] for start in range(0, len(ordered), size)]


# Measured on the RTX 5070 Ti with 0.6B in bfloat16 and fragments of up to 300 characters: the model and prompt take about
# 2.3 GB, every fragment of a batch adds about 0.55 GB at its peak (batch 8: 6.7 GB). The margin is left for the CUDA context
# and for whatever else (a game, a browser) uses the card.
BATCH_ITEM_MB = 600
BATCH_MARGIN_MB = 1024


def fit_batch(requested, free_mb):
    """The largest batch (up to `requested`) that fits into the free video memory; never below 1."""
    return max(1, min(int(requested), int((free_mb - BATCH_MARGIN_MB) // BATCH_ITEM_MB)))


def plausible(samples, rate, text):
    """A cheap sanity check, not speech recognition: finite, not silent, and a length that fits the text."""
    import numpy as np
    if samples.size == 0 or not np.isfinite(samples).all() or float(np.max(np.abs(samples))) < 1e-4:
        return False
    expected = len(text) / 14.0
    seconds = samples.shape[0] / rate
    return 0.3 * expected - 0.5 <= seconds <= 3.0 * expected + 1.5


def main():
    spec_path = Path(sys.argv[1])
    directory = spec_path.parent
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    packages = spec['packages'] if isinstance(spec['packages'], list) else [spec['packages']]
    for package in reversed(packages):
        sys.path.insert(0, package)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from generation_identity import operation
        from voiceover.voiceover_storage import write_json
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
            import soundfile as sf
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError('Озвучка требует доступную NVIDIA CUDA GPU')
            from qwen_tts import Qwen3TTSModel
            model = Qwen3TTSModel.from_pretrained(spec['model'], device_map='cuda:0', dtype=torch.bfloat16,
                                                  attn_implementation='sdpa', local_files_only=True)
            # The speaker embedding is the same for every fragment: computing it once saves a speaker-encoder pass per call.
            prompt = model.create_voice_clone_prompt(ref_audio=spec['voice'], x_vector_only_mode=True)

            def synthesize(group, attempt):
                # The seed comes from the group's first fragment, so a re-run of the same group is reproducible; a retry
                # shifts it to get a different take. With batch 1 this is exactly "the seed of the fragment".
                torch.manual_seed(int(group[0]['key'][:8], 16) + attempt * 7919 + int(spec.get('seed_shift', 0)) * 104729)
                wavs, rate = model.generate_voice_clone(
                    text=[item['text'] for item in group], language=spec['language'], voice_clone_prompt=prompt,
                    max_new_tokens=min(2048, int(max(len(item['text']) for item in group) * 3) + 64))
                return [wav[:, None] if wav.ndim == 1 else wav for wav in wavs], rate

            def synthesize_fitting(group, attempt):
                # The batch size was chosen from the free memory at the start; if the card is taken by something else in the
                # meantime, the batch is halved instead of failing the job.
                try:
                    return synthesize(group, attempt)
                except torch.cuda.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    if len(group) == 1:
                        raise
                    middle = len(group) // 2
                    first, rate = synthesize_fitting(group[:middle], attempt)
                    second, _ = synthesize_fitting(group[middle:], attempt)
                    return first + second, rate

            free_mb = torch.cuda.mem_get_info()[0] // (1024 * 1024)
            batch = fit_batch(spec.get('batch', 1), free_mb)
            event('batch', requested=int(spec.get('batch', 1)), used=batch, free_mb=int(free_mb))
            started = time.monotonic()
            with operation('SpeechSynthesis'):
                for group in make_batches(todo, batch):
                    results, pending = {}, group
                    for attempt in range(spec.get('attempts', 3)):
                        wavs, rate = synthesize_fitting(pending, attempt)
                        retry = []
                        for item, samples in zip(pending, wavs):
                            results[item['key']] = (samples, rate)  # the last take is kept even when it is still implausible
                            if not plausible(samples, rate, item['text']):
                                retry.append(item)
                        pending = retry
                        if not pending:
                            break
                    flagged.extend(item['key'] for item in pending)  # kept anyway; the page lists them for a manual decision
                    # PyTorch keeps freed blocks for reuse; with batches of different lengths that cache grew to the whole card (15.7 GB
                    # in use on a one-hour book, 7.8 GB really needed), starving a game or any other program. Give it back after every batch.
                    torch.cuda.empty_cache()
                    for item in group:
                        samples, rate = results[item['key']]
                        target = cache / (item['key'] + '.wav')
                        temporary = target.with_name(target.name + '.tmp')
                        sf.write(str(temporary), samples, rate, subtype='PCM_16', format='WAV')
                        os.replace(temporary, target)
                        done += 1
                        progress('synthesis')
            event('synthesis_done', seconds=round(time.monotonic() - started, 1), peak_vram_mb=round(torch.cuda.max_memory_allocated() / 1024 ** 2),
                  peak_reserved_mb=round(torch.cuda.max_memory_reserved() / 1024 ** 2))
        write_json(directory / 'result.json', {'fragments': total, 'reused': total - len(todo), 'flagged': flagged})
        progress('done')
    except Exception as exc:
        traceback.print_exc(limit=12)
        try:
            from voiceover.voiceover_storage import write_json
            write_json(directory / 'error.json', {'error': str(exc)[:2000]})
        except Exception:
            pass
        raise SystemExit(1)


if __name__ == '__main__':
    main()
