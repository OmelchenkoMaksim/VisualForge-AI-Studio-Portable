"""Installed music engine contract; no optional backend is advertised as installed."""
ENGINE = {
    'id': 'ace-step-15-turbo', 'name': 'ACE-Step 1.5 Turbo',
    'license': 'MIT', 'source': 'https://github.com/ace-step/ACE-Step-1.5',
    'upstream_revision': 'ca1e85fe9430179831e6bc6be790c332190a3866',
    'modes': ['instrumental', 'song'], 'min_duration': 10, 'max_duration': 180,
    'sample_rate': 48000, 'format': 'wav', 'planner': 'acestep-5Hz-lm-0.6B',
    'requirements': 'Windows 10/11 x64 · NVIDIA CUDA · не менее 8 ГБ свободной видеопамяти и 16 ГБ RAM с выгрузкой в RAM; рекомендуется видеокарта с 16 ГБ VRAM и 32 ГБ RAM.',
}

MINIMAX = {
    'id':'minimax-music3-int8', 'name':'MiniMax Music3 · INT8',
    'license':'MiniMax-Music3 Community License', 'source':'https://huggingface.co/MiniMaxAI/MiniMax-Music3',
    'upstream_revision':'9e364ccd290a401561091fb441ed19cd7de92550', 'release_date':'2026-08-13',
    'modes':['instrumental','song'], 'min_duration':10, 'max_duration':60,
    'sample_rate':32000, 'format':'wav', 'planner':None,
    'requirements':'Экспериментально: Windows 10/11 x64, NVIDIA CUDA, 16 ГБ VRAM, 32 ГБ RAM; рекомендуется 64 ГБ RAM. INT8 и выгрузка в RAM. Начните с 10–30 секунд.',
    'limitations':'Длительность задаёт верхний предел: модель может закончить раньше. Начальные профили: 10–60 секунд; длинные песни пока не включены. Темп и язык задаются в описании, их соблюдение не гарантируется.',
    'prompt_guide':'Опишите жанр, настроение, инструменты и голос. Для точного замысла используйте три части: Global Metadata (стиль и темп), Vocal Details (голос), Arrangement (развитие инструментов). В тексте песни разделяйте части тегами [Verse], [Chorus], [Bridge], [Outro]. Для инструментала текст песни не нужен.',
}
ENGINES = {ENGINE['id']:ENGINE, MINIMAX['id']:MINIMAX}

def get_engine(ident=None):
    try:
        return ENGINES[ident or ENGINE['id']]
    except (KeyError, TypeError):
        raise ValueError('Неизвестный движок Music') from None

def validate(data):
    """Normalize a bounded request before any runtime or GPU is touched."""
    import secrets
    engine = get_engine(data.get('engine', ENGINE['id']))
    caption = data.get('caption', '')
    lyrics = data.get('lyrics', '')
    if not isinstance(caption, str) or not 1 <= len(caption.strip()) <= 2000:
        raise ValueError('Опишите музыку: от 1 до 2000 символов')
    if not isinstance(lyrics, str) or len(lyrics) > 6000:
        raise ValueError('Текст песни: не более 6000 символов')
    mode = data.get('mode', 'instrumental')
    if mode not in ENGINE['modes']:
        raise ValueError('Неизвестный режим Music')
    if mode == 'song' and not lyrics.strip():
        raise ValueError('Добавьте текст песни')
    duration = int(data.get('duration', 30))
    if not engine['min_duration'] <= duration <= engine['max_duration']:
        raise ValueError(f"Длительность {engine['name']}: от {engine['min_duration']} до {engine['max_duration']} секунд")
    seed = data.get('seed', '')
    seed = secrets.randbelow(2**32) if seed in ('', None, -1, '-1') else int(seed)
    if not 0 <= seed < 2**32:
        raise ValueError('Seed: целое число от 0 до 4294967295 или пустое поле')
    bpm = data.get('bpm', '')
    bpm = None if bpm in ('', None) else int(bpm)
    if bpm is not None and not 30 <= bpm <= 300:
        raise ValueError('Темп: от 30 до 300 ударов в минуту')
    language = data.get('language', 'unknown')
    if language not in ('unknown', 'ru', 'en', 'zh', 'ja', 'ko', 'es', 'fr', 'de', 'it', 'pt'):
        raise ValueError('Неизвестный язык вокала')
    planner = data.get('planner', True)
    if not isinstance(planner, bool):
        raise ValueError('Неверная настройка музыкального планировщика')
    if engine['id'] == MINIMAX['id']:
        planner = False
    return dict(engine=engine['id'], caption=caption.strip(), lyrics=lyrics.strip(), mode=mode,
                duration=duration, seed=seed, bpm=bpm, language=language, planner=planner)
