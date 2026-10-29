"""Local hardware inventory and conservative compatibility for shipped inference profiles.

No inference, network, identifiers, disk scan or persisted machine report. Memory is GiB.
Red means a known runtime/launch requirement is unmet, not an estimated speed verdict.
"""
from ui_strings import source as ui_text
import copy
import csv
import ctypes
import io
import json
import math
import os
import platform
from pathlib import Path
import subprocess
import threading
import time

GIB = 1024 ** 3
_lock = threading.Lock()
_cached = None
_at = 0


def _run(command, timeout=6):
    return subprocess.check_output(command, timeout=timeout, text=True, encoding='utf-8',
        errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def parse_nvidia(text):
    cards = []
    for row in csv.reader(io.StringIO(text)):
        try:
            index, name, memory = row
            size = float(memory.strip()) / 1024
            if not math.isfinite(size) or size <= 0 or int(index) < 0:
                continue
            cards.append(dict(index=int(index), name=name.strip(), vram_gb=round(size, 2), cuda=True))
        except (ValueError, TypeError):
            continue
    return sorted(cards, key=lambda card: card['index'])


def scan():
    result = dict(os=platform.system(), architecture=platform.machine(), cpu=platform.processor() or ui_text('hardware.hardware_scan_not_detected'),
        logical_cores=os.cpu_count(), ram_gb=None, gpus=[], gpu_detection='unknown')
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                result['cpu'] = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
        except OSError:
            pass
        try:
            memory = ctypes.c_ulonglong()
            if ctypes.windll.kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(memory)):
                result['ram_gb'] = round(memory.value / 1024 ** 2, 2)
        except (OSError, AttributeError):
            pass
    try:
        result['gpus'] = parse_nvidia(_run(['nvidia-smi', '--query-gpu=index,name,memory.total', '--format=csv,noheader,nounits']))
        if result['gpus']:
            result['gpu_detection'] = 'nvidia-smi'
    except (OSError, subprocess.SubprocessError):
        pass
    if not result['gpus'] and os.name == 'nt':
        try:
            # WMI AdapterRAM wraps on some >4 GiB cards: never use it as VRAM capacity.
            shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            raw = _run([str(shell), '-NoProfile', '-NonInteractive', '-Command',
                "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); @(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) | ConvertTo-Json -Compress"], timeout=8)
            names = json.loads(raw.lstrip('\ufeff'))
            if isinstance(names, str):
                names = [names]
            result['gpus'] = [dict(index=i, name=name, vram_gb=None, cuda=None if 'nvidia' in name.lower() else False)
                for i, name in enumerate(names or []) if isinstance(name, str)]
            result['gpu_detection'] = 'windows' if result['gpus'] else 'unknown'
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            pass
    return result


def inventory(refresh=False):
    global _cached, _at
    with _lock:
        age = time.monotonic() - _at
        if _cached is None or age > 60 or (refresh and age >= 5):
            _cached = scan()
            _at = time.monotonic()
        return copy.deepcopy(_cached)


def profiles(text_models=()):
    # Recommendations are studio starting profiles, not universal vendor minima.
    from video.video_registry import ENGINES as video
    from music.music_registry import ENGINES as music
    rows = {}
    def add(module, ident, name, vram, ram, minimum=0, minimum_ram=0, note=ui_text('hardware.hardware_add_studio_startup_profile_memory_reduction_has')):
        rows[module + ':' + ident] = dict(module=module, id=ident, name=name, recommended_vram_gb=vram,
            recommended_ram_gb=ram, minimum_free_vram_gb=minimum / 1024, minimum_ram_gb=minimum_ram, cuda=True, note=note)
    add('image', 'qwen-edit-2511', 'Qwen Image Edit 2511', 16, 64, note=ui_text('hardware.qwen_2511_verified_note'))
    add('image', 'qwen-image-21-uc-fp8', 'Qwen Image 2.1 Uncensored FP8', 16, 64,
        note=ui_text('hardware.qwen21_verified_note'))
    add('image', 'flux3-klein-int8', 'FLUX.3 Klein INT8', 16, 64,
        note=ui_text('hardware.hardware_profiles_studio_startup_profile_prompt_upsampler_tested'))
    add('image', 'z-image-turbo-int8', 'Z-Image Turbo INT8', 16, 32,
        note=ui_text('hardware.hardware_profiles_studio_startup_profile_gpu_has_not'))
    for ident, engine in video.items():
        add('video', ident, engine['name'], 16, 32 if ident == 'wan22-ti2v-5b' else 64,
            engine.get('minimum_free_vram_mb', 12000), note=engine.get('requirements', ui_text('hardware.hardware_profiles_studio_profile_512_288_16_gb')))
    for ident, engine in music.items():
        add('music', ident, engine['name'], 16, 32 if ident in ('ace-step-15-turbo', 'stable-audio-3-medium') else 64, engine.get('claim_vram_mb', 8000),
            minimum_ram=16 if ident == 'ace-step-15-turbo' else 0, note=engine['requirements'])
    for model in text_models:
        if not model.get('ok'):
            continue
        size = model.get('size', 0) / GIB
        # Planning budget, including headroom; context/KV cache varies by architecture.
        rows['text:' + model['name']] = dict(module='text', id=model['name'], name=model.get('label', model['name']),
            recommended_vram_gb=next((tier for tier in (8, 12, 16, 24, 32, 48, 64) if tier >= size + 1), math.ceil((size + 1) / 8) * 8) if size > 0 else None,
            recommended_ram_gb=max(16, 2 ** math.ceil(math.log2(size * 1.2 + 4))) if size > 0 else None,
            minimum_free_vram_gb=0, minimum_ram_gb=0, cuda=False,
            note=ui_text('hardware.hardware_profiles_estimate_based_installed_build_size_memory'))
    return rows


def assess(hardware, profile):
    reasons, blocked = [], []
    cuda_cards = [gpu for gpu in hardware['gpus'] if gpu.get('cuda') is True]
    # Native pipelines use CUDA device 0; never sum memory or silently pick a different card.
    gpu = cuda_cards[0] if cuda_cards else next((g for g in hardware['gpus'] if g.get('cuda') is None), None)
    vram = gpu.get('vram_gb') if gpu else (0 if hardware['gpu_detection'] != 'unknown' else None)
    ram = hardware.get('ram_gb')
    if hardware['os'] != 'Windows' or hardware['architecture'].lower() not in ('amd64', 'x86_64'):
        blocked.append(ui_text('hardware.hardware_compatibility_assessment_portable_build_requires_windows_x64'))
    if profile['cuda']:
        if not cuda_cards:
            if hardware['gpu_detection'] == 'unknown' or gpu is not None:
                reasons.append(ui_text('hardware.hardware_compatibility_assessment_confirm_nvidia_cuda_video_memory_check_failed'))
            else:
                blocked.append(ui_text('hardware.hardware_compatibility_assessment_engine_requires_nvidia_cuda_no_compatible'))
        if vram is not None and cuda_cards and vram < profile['minimum_free_vram_gb']:
            needed = ('%.1f' % profile['minimum_free_vram_gb']).replace('.', ',')
            blocked.append(ui_text('hardware.hardware_compatibility_assessment_video_memory_below_mandatory_engine_startup') % needed)
    elif not cuda_cards:
        reasons.append(ui_text('hardware.hardware_compatibility_assessment_text_model_run_cpu_ram_but'))
    for actual, recommended, label in ((vram, profile['recommended_vram_gb'], ui_text('hardware.hardware_compatibility_assessment_video_memory')), (ram, profile['recommended_ram_gb'], ui_text('hardware.hardware_ram_specification_label'))):
        if actual is None or recommended is None:
            reasons.append(ui_text('hardware.hardware_compatibility_assessment_insufficient_data_estimate') % label)
        elif actual + .25 < recommended:
            reasons.append((ui_text('hardware.hardware_compatibility_assessment_vram_below_recommendation_ram_cpu_offloading') if label == ui_text('hardware.hardware_compatibility_assessment_video_memory') else
                ui_text('hardware.hardware_compatibility_assessment_ram_below_recommendation_page_file_may')))
    if ram is not None and ram < profile['minimum_ram_gb']:
        blocked.append(ui_text('hardware.hardware_compatibility_assessment_ram_below_profile_s_stated_minimum') % profile['minimum_ram_gb'])
    status = 'red' if blocked else 'yellow' if reasons else 'green'
    return dict(status=status, title={'red':ui_text('hardware.hardware_compatibility_assessment_model_not_start_configuration'), 'yellow':ui_text('hardware.hardware_compatibility_assessment_limitations_slower'), 'green':ui_text('hardware.hardware_compatibility_assessment_system_meets_main_requirements')}[status],
        reasons=blocked or reasons or [ui_text('hardware.hardware_compatibility_assessment_memory_meets_recommended_startup_profile')],
        actual_vram_gb=vram, actual_ram_gb=ram, gpu_name=gpu['name'] if gpu else None,
        profile=profile, caveat=ui_text('hardware.hardware_compatibility_assessment_estimate_startup_profile_frame_size_context'))


def report(text_models=(), refresh=False, root=None):
    from model_requirements import enrich
    hardware = inventory(refresh=refresh)
    rows = enrich(profiles(text_models), text_models, root=root)
    return dict(hardware=hardware, models={key: assess(hardware, row) for key, row in rows.items()})
