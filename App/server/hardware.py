"""Local hardware inventory and conservative compatibility for shipped inference profiles.

No inference, network, identifiers, disk scan or persisted machine report. Memory is GiB.
Red means a known runtime/launch requirement is unmet, not an estimated speed verdict.
"""
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
    result = dict(os=platform.system(), architecture=platform.machine(), cpu=platform.processor() or 'Не определён',
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
    from video.registry import ENGINES as video
    from music.registry import ENGINES as music
    rows = {}
    def add(module, ident, name, vram, ram, minimum=0, minimum_ram=0, note='Стартовый профиль студии; уменьшение памяти отдельно не проверялось.'):
        rows[module + ':' + ident] = dict(module=module, id=ident, name=name, recommended_vram_gb=vram,
            recommended_ram_gb=ram, minimum_free_vram_gb=minimum / 1024, minimum_ram_gb=minimum_ram, cuda=True, note=note)
    add('image', 'qwen-edit-2511', 'Qwen Image Edit 2511', 16, 64)
    add('image', 'qwen-image-21-uc-fp8', 'Qwen Image 2.1 Uncensored FP8', 16, 64)
    add('image', 'flux3-klein-int8', 'FLUX.3 Klein INT8', 16, 64,
        note='Стартовый профиль студии. Prompt с upsampler проверен один раз: пик 15,5 из 16,3 ГБ видеопамяти, запас очень мал. Single и Reference не измерены.')
    add('image', 'z-image-turbo-int8', 'Z-Image Turbo INT8', 16, 32,
        note='Стартовый профиль студии; работа на этой видеокарте ещё не проверена, потребление памяти не измерено.')
    for ident, engine in video.items():
        add('video', ident, engine['name'], 16, 32 if ident == 'wan22-ti2v-5b' else 64,
            engine.get('minimum_free_vram_mb', 12000), note=engine.get('requirements', 'Профиль студии: 512×288, 16 ГБ VRAM и 64 ГБ ОЗУ.'))
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
            note='Оценка по объёму установленной сборки с запасом памяти. Длинный контекст требует больше памяти; Ollama может использовать ОЗУ и CPU.')
    return rows


def assess(hardware, profile):
    reasons, blocked = [], []
    cuda_cards = [gpu for gpu in hardware['gpus'] if gpu.get('cuda') is True]
    # Native pipelines use CUDA device 0; never sum memory or silently pick a different card.
    gpu = cuda_cards[0] if cuda_cards else next((g for g in hardware['gpus'] if g.get('cuda') is None), None)
    vram = gpu.get('vram_gb') if gpu else (0 if hardware['gpu_detection'] != 'unknown' else None)
    ram = hardware.get('ram_gb')
    if hardware['os'] != 'Windows' or hardware['architecture'].lower() not in ('amd64', 'x86_64'):
        blocked.append('Эта portable-сборка требует Windows x64.')
    if profile['cuda']:
        if not cuda_cards:
            if hardware['gpu_detection'] == 'unknown' or gpu is not None:
                reasons.append('Не удалось подтвердить NVIDIA CUDA и объём видеопамяти. Проверьте драйвер и обновите данные ПК.')
            else:
                blocked.append('Этот движок требует NVIDIA CUDA; совместимая видеокарта не обнаружена.')
        if vram is not None and cuda_cards and vram < profile['minimum_free_vram_gb']:
            needed = ('%.1f' % profile['minimum_free_vram_gb']).replace('.', ',')
            blocked.append('Объём видеопамяти меньше обязательного порога запуска движка: нужно не менее %s ГБ свободной VRAM.' % needed)
    elif not cuda_cards:
        reasons.append('Текстовая модель может работать на процессоре и ОЗУ, но ответы будут медленнее.')
    for actual, recommended, label in ((vram, profile['recommended_vram_gb'], 'видеопамяти'), (ram, profile['recommended_ram_gb'], 'ОЗУ')):
        if actual is None or recommended is None:
            reasons.append('Недостаточно данных для оценки %s.' % label)
        elif actual + .25 < recommended:
            reasons.append(('Объём видеопамяти ниже рекомендуемого. Возможны выгрузка в ОЗУ / CPU, замедление или ошибка нехватки памяти.' if label == 'видеопамяти' else
                'Объём ОЗУ ниже рекомендуемого. Может потребоваться файл подкачки; вероятны замедление или ошибка нехватки памяти.'))
    if ram is not None and ram < profile['minimum_ram_gb']:
        blocked.append('ОЗУ ниже заявленного минимума этого профиля: %g ГБ.' % profile['minimum_ram_gb'])
    status = 'red' if blocked else 'yellow' if reasons else 'green'
    return dict(status=status, title={'red':'Модель не запустится в этой конфигурации', 'yellow':'С ограничениями / медленнее', 'green':'Система подходит по основным требованиям'}[status],
        reasons=blocked or reasons or ['Память соответствует рекомендуемому стартовому профилю.'],
        actual_vram_gb=vram, actual_ram_gb=ram, gpu_name=gpu['name'] if gpu else None,
        profile=profile, caveat='Оценка для стартового профиля. Размер кадра, контекст и занятая другими программами память влияют на запуск и скорость.')


def report(text_models=(), refresh=False, root=None):
    from model_requirements import enrich
    hardware = inventory(refresh=refresh)
    rows = enrich(profiles(text_models), text_models, root=root)
    return dict(hardware=hardware, models={key: assess(hardware, row) for key, row in rows.items()})
