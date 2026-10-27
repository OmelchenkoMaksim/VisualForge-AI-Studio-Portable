"""Adapter dispatch stays separate from validation, process ownership and storage."""
import json
from .registry import ENGINES
from pathlib import Path


def freevideo_command(root, models, folder, row):
    model_paths = folder / 'model-paths.yaml'
    model_paths.write_text('visualforge_video:\n  text_encoders: ' + json.dumps(str(models / 'text_encoders')) + '\n', encoding='utf-8')
    command = [str(root / 'python/python.exe'), '-u', '-m', 'freevideo_engine', 'generate',
        '--prompt-file', str(folder / 'prompt.txt'), '--cache', str(models / 'int8-convrot/cache'),
        '--base', str(models / 'h3-base'), '--checkpoint', str(models / 'stage-dmd-step-250'),
        '--encoder-root', str(root / 'vendor/ComfyUI'), '--model-paths', str(model_paths),
        '--encoder', 'qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',
        '--width', str(row['width']), '--height', str(row['height']), '--frames', str(row['frames']),
        '--seed', row['seed'], '--attention', 'sage2', '--base-steps', '16' if row['quality'] == 'high' else '8',
        '--refine-steps', '3', '--resource-retries', '0', '--out', str(folder / 'result.mp4')]
    if row['quality'] == 'preview':
        command.append('--preview')
    if row['mode'] != 't2v':
        command += ['--media', str(folder / 'media.json')]
    return command


def wan_command(root, models, folder, row):
    from .wan_support import model_root
    spec = folder / 'wan-request.json'
    spec.write_text(json.dumps(row,ensure_ascii=False),encoding='utf-8')
    return [str(root / 'python/python.exe'), '-u', str(Path(__file__).with_name('wan_worker.py')),
            '--root',str(root),'--models',str(model_root(models)), '--folder',str(folder),'--spec',str(spec)]

def s2v_command(root, models, folder, row):
    from .wan_support import model_root, S2V_ID
    spec=folder/'s2v-request.json'
    spec.write_text(json.dumps(row,ensure_ascii=False),encoding='utf-8')
    return [str(root/'python/python.exe'),'-u',str(Path(__file__).with_name('s2v_worker.py')),
            '--root',str(root),'--models',str(model_root(models,S2V_ID)),
            '--encoder-root',str(model_root(models)),'--voice-model',str(models.parent/'Qwen3TTS'),
            '--folder',str(folder),'--spec',str(spec)]

def ltx_command(root,models,folder,row):
    from .ltx_support import model_root
    spec=folder/'ltx-request.json'
    spec.write_text(json.dumps(row,ensure_ascii=False),encoding='utf-8')
    worker='ltx_long.py' if row['frames'] in (360,720,1440) else 'ltx_worker.py'
    return [str(root/'python/python.exe'),'-X','faulthandler','-u',str(Path(__file__).with_name(worker)),
        '--root',str(root),'--models',str(model_root(models)),'--folder',str(folder),'--spec',str(spec)]


ADAPTERS = {'freevideo_cli': freevideo_command, 'wan_comfy': wan_command,'wan_s2v':s2v_command,'ltx_native':ltx_command}


def build_command(root, models, folder, row):
    if row['engine'] == 'freevideo-h3-int8' and (row['frames'] == 1440 or row['mode'] == 'characters'):
        spec = folder / 'pipeline.json'
        spec.write_text(json.dumps(row, ensure_ascii=False), encoding='utf-8')
        command = [str(root / 'python/python.exe'), '-u', str(Path(__file__).with_name('pipeline.py')),
                   '--root', str(root), '--models', str(models), '--folder', str(folder), '--spec', str(spec)]
        # Internal maintenance only; the public request validator does not accept this field.
        if row.get('recover_export_only'):
            command.append('--assemble-only')
        return command
    return ADAPTERS[ENGINES[row['engine']]['adapter']](root, models, folder, row)
