"""Read-only model facts and catalog-backed storage budgets; never downloads or hashes weights."""
from pathlib import Path
from provision import catalog as catalog_module
from provision.engine import Engine as Installer, SPACE_MARGIN

COMPONENTS = {
    'image:qwen-edit-2511': 'image.model.qwen-edit-2511',
    'image:qwen-image-21-uc-fp8': 'image.model.qwen-image-21-uc',
    'image:z-image-turbo-int8': 'image.model.z-image-turbo',
    'image:flux3-klein-int8': 'image.model.flux3-klein',
    'video:freevideo-h3-int8': 'video.model.h3',
    'video:wan22-ti2v-5b': 'video.model.wan-ti2v-5b',
    'video:wan22-s2v-14b': 'video.model.wan-s2v-14b',
    'video:ltx25-distilled': 'video.model.ltx25',
    'music:ace-step-15-turbo': 'music.model.ace-step',
    'music:minimax-music3-int8': 'music.model.minimax-music3',
    'music:stable-audio-3-medium': 'music.model.stable-audio-3',
}
MODES = dict(t2v='Видео по тексту', i2v='Оживление изображения', first_last='Первый / последний кадр',
    characters='Персонажи по референсам', s2v='Речь → видео', instrumental='Инструментал', song='Песня с вокалом',
    single='Редактирование', reference='Редактирование с референсом', prompt='Изображение по тексту',
    audio='Музыка и звуки по описанию')


def facts(module, ident, model=None):
    if module == 'image':
        from engines.registry import ENGINES, CANVASES
        engine = ENGINES[ident]
        weights = {'qwen-image-21-uc-fp8': 'FP8; текстовый энкодер INT8',
                   'z-image-turbo-int8': 'Трансформер INT8; текстовый энкодер Qwen3-4B FP8',
                   'flux3-klein-int8': 'Distilled INT8; энкодер Qwen3-VL-4B INT8; upsampler промпта Qwen3.5-9B INT8'}.get(ident, 'FP8; текстовый энкодер FP8')
        rows = [('Возможности', ', '.join(MODES[m] + (' (экспериментально)' if m == 'prompt' and not engine.native_prompt else '') for m in engine.modes)), ('Формат результата', 'PNG'),
            ('Качество', ' / '.join(str(n) for n in engine.quality_steps.values()) + ' шагов'),
            ('Кадр с нуля', ', '.join('%d×%d' % size for size in dict.fromkeys(CANVASES.values()))),
            ('Веса', weights)]
        if 'single' not in engine.modes:
            rows.append(('Особенности', 'Только создание по тексту: нет редактирования, референсов и вариантов; негативный промпт не используется (CFG 1).'))
        if ident == 'flux3-klein-int8':
            rows.append(('Лицензия', 'FLUX Kommunity License: некоммерческое использование; коммерческое — только при выручке до 5 млн долл. в год и с проверкой результатов'))
        return rows
    if module == 'video':
        from video.registry import ENGINES
        engine = ENGINES[ident]
        return [('Возможности', ', '.join(MODES[m] for m in engine['modes'])),
            ('Профиль', engine['release']),
            ('Размеры кадра', ', '.join('%d×%d' % tuple(size) for size in engine['resolutions'])),
            ('Частота', '%g FPS' % engine['fps']),
            ('Длительности', ' · '.join(('%g' % round(n / engine['fps'], 3)).replace('.', ',') for n in engine['frames']) + ' с'),
            ('Звук', 'Совместная генерация видео и звука' if ident == 'ltx25-distilled' else 'Синхронизация с речью' if ident == 'wan22-s2v-14b' else 'Без генерации звука' if ident == 'wan22-ti2v-5b' else 'Видео и звук; клонирование голоса — отдельно'),
            ('Особенности', engine['limitations'])]
    if module == 'music':
        from music.registry import ENGINES
        engine = ENGINES[ident]
        return [('Возможности', ', '.join(MODES[m] for m in engine['modes'])),
            ('Длительность', '%g–%g с' % (engine['min_duration'], engine['max_duration'])),
            ('Результат', '%s · %g кГц' % (engine['format'].upper(), engine['sample_rate'] / 1000)),
            ('Лицензия', engine['license']),
            ('Планировщик', engine['planner'] or 'Не используется'),
            ('Особенности', engine.get('limitations', 'Локальная генерация инструментальной музыки и песен; без клонирования голоса.'))]
    model = model or {}
    return [('Возможности', 'Текст и изображения' if model.get('vision') else 'Текст'),
        ('Параметры модели', model.get('parameter_size') or 'Нет данных'),
        ('Квантизация', model.get('quantization') or 'Нет данных'),
        ('Контекст сборки', '%s токенов' % model['parameters']['num_ctx'] if model.get('parameters', {}).get('num_ctx') else 'По настройкам Ollama'),
        ('Исполнение', 'Ollama · GPU и/или CPU с ОЗУ')]


def storage(installer, statuses, ident):
    order = catalog_module.closure(installer.catalog, [ident])
    models = [installer.by_id[key] for key in order if installer.by_id[key]['kind'] == 'model']
    software = [installer.by_id[key] for key in order if installer.by_id[key]['kind'] == 'software']
    # Shared encoders/models occur once in a dependency closure. File paths are deduplicated too.
    files = {item['path']: item for component in models for item in component.get('files', [])}
    model_bytes = sum(item['bytes'] for item in files.values())
    runtime_download = sum(catalog_module.download_bytes(component) for component in software)
    runtime_disk = sum(max(component.get('disk_bytes', 0), catalog_module.download_bytes(component)) for component in software)
    missing = [key for key in order if statuses[key]['state'] != 'installed']
    remaining = sum(statuses[key]['remaining_bytes'] for key in missing)
    additional = int(sum(installer._disk_need(installer.by_id[key], statuses[key]) for key in missing) * SPACE_MARGIN)
    # Cold installs need both unpacked software and its download cache, plus the installer reserve.
    # Keep full-copy budgets separate from what this installation still needs to download.
    return dict(
        source='catalog',
        model_bytes=model_bytes,
        model_download_bytes=model_bytes,
        full_download_bytes=model_bytes + runtime_download,
        runtime_disk_bytes=runtime_disk,
        clean_install_free_bytes=int((model_bytes + runtime_disk + runtime_download) * SPACE_MARGIN),
        remaining_download_bytes=remaining,
        additional_free_bytes=additional,
        installed=not missing,
        shared_model_components=[component['title'] for component in models if component['id'] != ident],
    )


def enrich(profiles, text_models=(), root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    catalog_path = root/'App/config/components.json'
    try:
        installer = Installer(root, catalog_path=catalog_path) if catalog_path.is_file() else None
    except (OSError, ValueError, catalog_module.CatalogError):
        installer = None
    # Read relevant components only; do not stat every optional model in Components.
    keys = set()
    statuses = {}
    if installer:
        for key in COMPONENTS.values():
            # A component can be absent while its gated download is not pinned yet; its card then shows unknown storage.
            if key in installer.by_id:
                keys.update(catalog_module.closure(installer.catalog, [key]))
        statuses = {key: installer.component_status(installer.by_id[key]) for key in keys}
    texts = {model['name']: model for model in text_models}
    for key, profile in profiles.items():
        model = texts.get(profile['id'], {})
        profile['characteristics'] = facts(profile['module'], profile['id'], model)
        if key in COMPONENTS and installer and COMPONENTS[key] in installer.by_id:
            profile['storage'] = storage(installer, statuses, COMPONENTS[key])
            optional = 'image.model.upscale' if profile['module'] == 'image' else 'video.model.qwen3-tts' if profile['id'] in ('freevideo-h3-int8', 'wan22-s2v-14b') else None
            if optional:
                component = installer.by_id[optional]
                profile['optional_download'] = dict(name=component['title'], bytes=catalog_module.download_bytes(component))
        elif profile['module'] == 'text':
            profile['storage'] = dict(source='local_ollama', model_bytes=model.get('disk_size', model.get('size')),
                model_download_bytes=None, full_download_bytes=None, runtime_disk_bytes=installer.by_id['text.runtime'].get('disk_bytes') if installer else None,
                clean_install_free_bytes=None, remaining_download_bytes=0, additional_free_bytes=0, installed=True,
                shared_model_components=[])
        else:
            profile['storage'] = dict(source='unknown', model_bytes=None)
    return profiles
