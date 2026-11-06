"""Opt-in sequential real audio qualification on isolated C state, using the normal section route handlers.

Requires explicit owner approval before --yes-gpu. No downloads or installed settings/output-folder changes.
The browser/fake-backend UI checks are separate; these results never constitute listening by the agent.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))


def command(args):
    return subprocess.run([str(value) for value in args], capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=180, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def free_memory():
    result = command(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'])
    if result.returncode:
        raise RuntimeError('Cannot read free GPU memory')
    return int(result.stdout.strip().splitlines()[0])


def measure(path, ffmpeg):
    probe = command([ffmpeg.with_name('ffprobe.exe'), '-v', 'error', '-show_entries',
                     'format=duration:stream=sample_rate,channels,codec_name', '-of', 'json', path])
    if probe.returncode:
        raise RuntimeError(probe.stderr[-1000:])
    result = json.loads(probe.stdout)
    # Input measurements describe the finished encoded file; the analysis writes no processed audio.
    analysis = command([ffmpeg, '-nostdin', '-hide_banner', '-i', path, '-af',
                        'loudnorm=I=-18:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'])
    match = re.search(r'\{\s*"input_i".*?\}', analysis.stderr, re.S)
    result['loudness'] = json.loads(match.group()) if match else dict(error=analysis.stderr[-1000:])
    stats = command([ffmpeg, '-nostdin', '-hide_banner', '-i', path, '-af',
                     'astats=metadata=0:reset=0,silencedetect=noise=-50dB:d=0.3', '-f', 'null', '-'])
    result['integrity_lines'] = [line.strip() for line in stats.stderr.splitlines()
                                if re.search(r'NaNs|Infs|Peak level|Peak count|silence_start|silence_end', line)][-60:]
    return result


def generate(manager, route, gpu, lock, request):
    done = threading.Event()
    original = manager._run
    def run(row, directory):
        try:
            original(row, directory)
        finally:
            done.set()
    manager._run = run
    class Handler:
        answer = None
        def response_json(self, status, value):
            self.answer = status, value
    handler = Handler()
    try:
        with lock:
            route(handler, manager, gpu, 'generate', request, '')
        status, answer = handler.answer
        if status != 200:
            return dict(state='error', error=answer.get('error'), request=request)
        if not done.wait(3600):
            manager.cancel(dict(id=answer['id'], token=answer['token']))
            raise TimeoutError('Audio qualification job exceeded one hour')
        return manager.status(answer['id'])
    finally:
        manager._run = original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--yes-gpu', action='store_true')
    parser.add_argument('--scratch', default=str(Path(os.environ['TEMP']) / 'audio-qualification'))
    parser.add_argument('--comfy-url', default='http://127.0.0.1:8188')
    parser.add_argument('--retry-failed', action='store_true', help='Reuse this harness state; retain failures and skip completed labels')
    parser.add_argument('--focus', choices=('all', 'presets'), default='all')
    parser.add_argument('--reference', action='append', default=[], help='Approved original WAV; imported into isolated C voice state only')
    args = parser.parse_args()
    if not args.yes_gpu:
        parser.error('Owner approval is required; pass --yes-gpu only after receiving it (estimate 35-60 minutes).')
    scratch = Path(args.scratch).resolve()
    if scratch.drive.lower() != 'c:' or not str(scratch).isascii():
        parser.error('Scratch, logs and comparisons must use an ASCII C path')
    if args.retry_failed and not (scratch/'audio-summary.json').is_file():
        parser.error('--retry-failed requires a previous audio-summary.json')
    scratch.mkdir(parents=True, exist_ok=args.retry_failed)
    print(command(['nvidia-smi', '--query-compute-apps=pid,process_name,used_memory', '--format=csv']).stdout, flush=True)
    if free_memory() < 14500:
        raise RuntimeError('Less than 14.5 GB free before Gemma. Stop here; do not evict foreign GPU processes.')
    from portable_paths import from_server
    from text.text_manager import TextManager
    from music.music_manager import MusicManager
    from voiceover.voiceover_manager import VoiceoverManager
    from music.music_prompt_enhancer import PromptEnhancer as MusicPrompt
    from voiceover.voiceover_prompt_enhancer import PromptEnhancer as VoicePrompt
    from music.music_routes import handle as music_route
    from voiceover.voiceover_routes import handle as voice_route
    from music.comfy_backend import ComfyClient
    from gpu_coordinator import GpuCoordinator
    from generation_identity import configure
    from video.gemma_task import GemmaTask
    source_paths = from_server()
    # copy.copy probes special methods before populating entries; PortablePaths.__getattr__ then recurses.
    # Clone its existing attributes directly so only this harness redirects mutable state.
    paths = object.__new__(type(source_paths))
    paths.__dict__.update(vars(source_paths))
    paths.entries = dict(paths.entries)
    for name in ('settings', 'logs', 'cache', 'results', 'prompts', 'profiles', 'state'):
        paths.entries[name] = scratch / name
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    os.environ.update(VISUALFORGE_TEXT_PORT=str(port), VISUALFORGE_TEXT_STATE_ROOT=str(scratch/'Text'),
                      VISUALFORGE_MUSIC_STATE_ROOT=str(scratch/'Music'), VISUALFORGE_VOICEOVER_STATE_ROOT=str(scratch/'Voiceover'),
                      VISUALFORGE_VOICEOVER_VOICES=str(source_paths.settings.parent/'Voiceover/Voices'),
                      HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TEMP=str(scratch), TMP=str(scratch))
    text, music, voice = TextManager(paths), MusicManager(paths), VoiceoverManager(paths)
    # Use the owner's current model selection; only the isolated test manager's settings are written.
    selected = TextManager(source_paths).settings()
    text.settings(selected)
    client = ComfyClient(args.comfy_url)
    def image_busy():
        if args.focus == 'presets':
            return False  # No Comfy server is started by this subset; nvidia-smi admission still applies.
        queue = client.get('/queue', timeout=5)
        return bool(queue.get('queue_running') or queue.get('queue_pending'))
    gpu = GpuCoordinator(dict(music=music.busy, voiceover=voice.busy, text=text.busy, image=image_busy),
                         releasers=dict(text=text.release_gpu, **({} if args.focus == 'presets' else
                             dict(image=lambda: client.post('/free', dict(unload_models=True, free_memory=True), timeout=15)))),
                         free_vram=free_memory)
    lock = threading.RLock()
    music.comfy.attach(client)
    music.prompt_enhancer = MusicPrompt(text, gpu, lock)
    voice.prompt_enhancer = VoicePrompt(text, gpu, lock)
    music.settings(dict(save_dir=str(scratch/'Results/Music')))
    voice.settings(dict(save_dir=str(scratch/'Results/Voiceover')))
    configure(scratch/'events.sqlite')
    results = (json.loads((scratch/'audio-summary.json').read_text(encoding='utf-8')) if args.retry_failed else
               dict(started=time.time(), gemma=text.gemma_model(), listening='Not performed by this tool/agent', jobs=[]))
    results.setdefault('runs', []).append(dict(started=time.time(), retry_failed=args.retry_failed, gemma=text.gemma_model()))
    original_ask = GemmaTask.ask
    def audited_ask(self, messages, cancel, **options):
        answer = original_ask(self, messages, cancel, **options)
        # Only this explicit fixture harness records model replies, including rejected attempts, in its C scratch.
        with (scratch/'gemma-replies.jsonl').open('a', encoding='utf-8') as output:
            output.write(json.dumps(dict(operation=self.OPERATION, messages=messages, response=answer), ensure_ascii=False) + '\n')
        return answer
    GemmaTask.ask = audited_ask
    def completed(label):
        manager = voice if label.startswith(('qwen3-', 'voxcpm2/')) else music
        def current(entry):
            if entry['label'] != label or entry['row']['state'] != 'done':
                return False
            preparation = entry['row'].get('preparation')
            if label.startswith('minimax-music3-int8/') and entry['row'].get('metadata', {}).get('delivery', {}).get('version') != 'minimax-wav-v2':
                return False
            return label.endswith('/neutral') or (preparation or {}).get('guide_version') == manager.prompt_enhancer.VERSION
        return args.retry_failed and any(current(entry) for entry in results['jobs'])
    def record(label, row):
        print(label, row['state'], row.get('error', ''), flush=True)
        entry = dict(label=label, row=row)
        if row['state'] == 'done':
            entry['measurements'] = measure(Path(row['saved_path']), voice.ffmpeg)
        results['jobs'].append(entry)
        (scratch/'audio-summary.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        for engine in (() if args.focus == 'presets' else ('ace-step-15-turbo', 'minimax-music3-int8', 'stable-audio-3-medium')):
            for mode in ('instrumental', 'song') if engine != 'stable-audio-3-medium' else ('audio',):
                if completed(engine+'/'+mode):
                    continue
                caption = ('Грустная песня об отъезде из родного города, но припев с надеждой. Тёплый женский голос без оперного пения.'
                           if mode == 'song' else 'Тихая фоновая музыка для дождливого вечера, с пианино, но не депрессивная, без вокала.')
                request = dict(engine=engine, mode=mode, caption=caption, lyrics='[verse]\nЯ уезжаю, но вернусь.\n[chorus]\nВ окнах ждёт меня рассвет.' if mode=='song' else '',
                               duration=20 if mode=='song' else 10, seed=71, bpm='', language='ru', planner=True)
                record(engine+'/'+mode, generate(music, music_route, gpu, lock, request))
        book = voice.prepare(dict(title='Audio preparation qualification', text=
            'Глава 1\nАнна вернулась домой 5 мая 2025 года. «Ты помнишь наш город?» — спросила она.\n\n'
            'В окне горел тёплый свет. Доктор Ёлкин купил 23 книги, а Мария — две.\n\n'
            'Глава 2\n«Конечно, помню! Неужели ты сомневалась?» — ответил Алексей. Всё только начиналось.'))
        builtins = voice.voices()['builtin']
        if not builtins:
            raise RuntimeError('No ready built-in reference. Do not invent consent for a saved human voice.')
        for engine in (() if args.focus == 'presets' else ('qwen3-tts-12hz-0.6b-base', 'voxcpm2')):
            request = dict(engine=engine, book_id=book['book_id'], voice_id=builtins[0]['id'], language='Russian', format='mp3', seed=71,
                           verify=True, normalize=True, dictionary=True)
            enhancer = voice.prompt_enhancer
            if not completed(engine+'/neutral'):
                voice.prompt_enhancer = None
                try:
                    baseline = generate(voice, voice_route, gpu, lock, request)
                finally:
                    voice.prompt_enhancer = enhancer
                record(engine+'/neutral', baseline)
            if not completed(engine+'/prepared'):
                record(engine+'/prepared', generate(voice, voice_route, gpu, lock,
                       dict(request, delivery='Естественно, как интересную историю, без дикторского голоса и затянутых пауз.')))
        if args.focus == 'presets':
            # Keep the installed built-in folder read-only; imports and originals go only into this test's C directory.
            voice.voices_dir = scratch/'References/Saved'
            references = [('builtin', builtins[0]['id'])]
            provenance = []
            for path in map(Path, args.reference):
                raw = path.read_bytes()
                added = voice.add_voice(dict(name='Approved public donor ' + path.stem, consent=True,
                                            audio=base64.b64encode(raw).decode('ascii')))
                references.append((path.stem, added['id']))
                provenance.append(dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), voice_id=added['id']))
            results['references'] = provenance
            for name, ident in references:
                for variant, delivery in [('neutral', ''), ('story', 'Естественно, как интересную историю, без дикторского пафоса и затянутых пауз.'),
                                           ('calm', 'Спокойно и близко, обычным голосом, без шёпота и растягивания фраз.')]:
                    label = 'voxcpm2/' + name + '/' + variant
                    if completed(label):
                        continue
                    record(label, generate(voice, voice_route, gpu, lock,
                           dict(engine='voxcpm2', book_id=book['book_id'], voice_id=ident, language='Russian', format='mp3', seed=71,
                                verify=True, normalize=True, dictionary=True, delivery=delivery, style_control=True)))
            for seed in (71, 173):
                for variant in ('old-negative', 'short-positive'):
                    label = 'minimax-music3-int8/' + str(seed) + '/' + variant
                    if completed(label):
                        continue
                    # Hold the caption fixed except the mode statement. This bypasses Gemma only in the A/B harness.
                    caption = 'Quiet instrumental piano music for a rainy evening, gently hopeful mood.'
                    if variant == 'old-negative':
                        caption += ' No vocals, no singing, no spoken words.'
                    enhancer = music.prompt_enhancer
                    music.prompt_enhancer = None
                    try:
                        row = generate(music, music_route, gpu, lock,
                                       dict(engine='minimax-music3-int8', mode='instrumental', caption=caption, duration=10, seed=seed))
                    finally:
                        music.prompt_enhancer = enhancer
                    record(label, row)
        # Match decoded finished outputs at a common lower level for an actual listener. Keep original encoded files too.
        compare_dir = scratch/'Comparisons'
        compare_dir.mkdir(exist_ok=True)
        for entry in {entry['label']: entry for entry in results['jobs']}.values():
            if args.focus != 'presets' and '/neutral' not in entry['label'] and '/prepared' not in entry['label']:
                continue
            if entry['row']['state'] != 'done':
                continue
            loudness = float(entry['measurements']['loudness']['input_i'])
            target = compare_dir/(entry['label'].replace('/', ' - ')+'.wav')
            if target.is_file() and entry.get('level_matched_comparison') == str(target):
                continue
            converted = command([voice.ffmpeg, '-nostdin', '-v', 'error', '-n', '-i', entry['row']['saved_path'],
                                 '-af', 'volume=%.3fdB' % (-20-loudness), '-c:a', 'pcm_s24le', target])
            if converted.returncode:
                raise RuntimeError(converted.stderr[-1000:])
            entry['level_matched_comparison'] = str(target)
        results['finished'] = time.time()
        (scratch/'audio-summary.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print('AUDIO_QUALIFICATION_COMPLETE', scratch, flush=True)
    finally:
        GemmaTask.ask = original_ask
        music.close(); voice.close(); text.close()


if __name__ == '__main__':
    main()
