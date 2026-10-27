"""Explicit capabilities and validation for tested local video profiles."""
import secrets

ENGINES = {'freevideo-h3-int8': {
    'id': 'freevideo-h3-int8', 'name': 'FreeVideo · MiniMax H3 int8',
    'adapter': 'freevideo_cli',
    'release': 'v0.4.19', 'commit': '0825d1ba649c433e7cd6474e5cdd4b638e74061e',
    'model_revision': 'da37681f5a1f7015fdb63482142b865111b687d3',
    'modes': ['t2v', 'i2v', 'first_last', 'characters'], 'fps': 24,
    'resolutions': [[512, 288]], 'frames': [39, 243, 1440],
    'qualities': ['preview', 'standard', 'high'],
    'qualities_by_mode': {'t2v': ['preview', 'standard', 'high'], 'i2v': ['preview'], 'first_last': ['preview'], 'characters': ['preview']},
    'limitations': '512×288, 24 FPS. Минута — сборка из шести сцен, возможны стыки. Персонажи по референсам — экспериментально. Озвучка по образцу отдельно, без синхронизации губ.',
}}
ENGINES['wan22-ti2v-5b'] = {
    'id': 'wan22-ti2v-5b', 'name': 'Wan 2.2 TI2V-5B', 'adapter': 'wan_comfy',
    'release': '2.2 · TI2V-5B', 'release_date': '2025-07-28', 'commit': None,
    'model_revision': 'ee6f4a40737a995bf5818954cfce6d59443b0f04',
    'model_repository': 'Comfy-Org/Wan-2.2', 'license': 'Apache-2.0',
    'modes': ['t2v','i2v'], 'fps': 24, 'resolutions': [[512,288],[832,480]],
    'frames': [33,81,121], 'qualities': ['preview','standard','high'],
    'qualities_by_mode': {'t2v':['preview','standard','high'], 'i2v':['preview','standard','high']},
    'steps': {'preview':12,'standard':20,'high':30}, 'minimum_free_vram_mb':6500,
    'runtime': 'Изолированная Video-среда · штатный ComfyUI 0.34.0 · выгрузка в RAM',
    'requirements': 'Windows 10/11 x64, NVIDIA CUDA; для начала рекомендуется 16 ГБ VRAM и 32 ГБ RAM. Поддержка 8 ГБ заявлена ComfyUI, но на такой видеокарте здесь не проверялась.',
    'limitations': 'Видео без звука, по тексту или одному начальному изображению. Первый/последний кадр, карточки персонажей и синхронизация губ этим движком не реализованы. Рекомендуемый начальный профиль: 832×480, 81 кадр, 20 шагов. По тексту также проверены 121 кадр и движение руки. На 512×288 возможны сильные искажения; остальные сочетания отдельно не проверены.',
}

ENGINES['wan22-s2v-14b'] = {
    'id':'wan22-s2v-14b','name':'Wan 2.2 S2V-14B · речь → видео','adapter':'wan_s2v',
    'release':'2.2 · S2V-14B','release_date':'2025-08-26','commit':None,
    'model_revision':'ee6f4a40737a995bf5818954cfce6d59443b0f04',
    'model_repository':'Comfy-Org/Wan-2.2','license':'Apache-2.0',
    'modes':['s2v'],'fps':16,'resolutions':[[512,512]],'frames':[77],
    'qualities':['standard'],'qualities_by_mode':{'s2v':['standard']},'steps':{'standard':20},
    'minimum_free_vram_mb':12000,
    'requirements':'Windows 10/11 x64, NVIDIA CUDA. Проверена короткая генерация на RTX 5070 Ti 16 ГБ VRAM и компьютере с 64 ГБ RAM, с выгрузкой в RAM. Минимальная конфигурация отдельно не проверялась.',
    'limitations':'Экспериментальный короткий профиль: 512×512, 77 кадров, 16 FPS (4,8125 с). Один персонаж по изображению и речь: образец голоса + текст либо готовая запись. Длинные реплики отклоняются, а короткие дополняются тишиной. Точность губ зависит от записи и лица; это не гарантирует идеальную синхронизацию.',
}

ENGINES['ltx25-distilled'] = {
    'id':'ltx25-distilled','name':'LTX 2.5 Distilled','adapter':'ltx_native',
    'release':'2.5 · Distilled INT8 ConvRot','release_date':'2026-08-11','commit':None,
    'model_revision':'49b093a39c19169a8a81721dcc541e07e945232f',
    'model_repository':'Lightricks/LTX-2.5','license':'ltx-2.x-community-license-agreement',
    'modes':['t2v','i2v'],'fps':24,'resolutions':[[768,512],[1024,576],[1280,704],[512,768],[576,1024],[704,1280]],
    'frames':[49,97,121,360,720,1440],'duration_labels':{360:'15 с · продолжение',720:'30 с · продолжение',1440:'60 с · продолжение'},'qualities':['preview','standard'],
    'qualities_by_mode':{'t2v':['preview','standard'],'i2v':['preview','standard']},
    'steps':{'preview':8,'standard':11},'quality_labels':{'preview':'Быстрое · 8 шагов','standard':'Два прохода · 8 + 3 шага'},
    'minimum_free_vram_mb':8000,'verification':{'summary_ru':'Экспериментально: профили требуют проверки на GPU.'},
    'requirements':'Windows x64, NVIDIA CUDA, 16 ГБ VRAM и 64 ГБ RAM рекомендуются для начального профиля. Текстовый энкодер и видеомодель загружаются по очереди.',
    'limitations':'Синхронная генерация видео и звука по тексту или первому кадру. Быстрое: один проход и облегчённый декодер. Два прохода: базовая генерация, обученное увеличение и уточнение с более детальным декодером. Стартовый профиль — 768×512, 49 кадров. Длительности 15/30/60 с — продолжение с перекрытием видеоконтекста и звука; на стыках возможны изменения деталей и звучания. Точность речи и синхронизация губ не гарантируются.',
}

def validate(data):
    if not isinstance(data, dict):
        raise ValueError('Ожидается объект запроса Video')
    if set(data) - {'engine', 'mode', 'prompt', 'quality', 'width', 'height', 'frames', 'fps', 'seed', 'source_image', 'end_image', 'characters','speech_mode','speech_text','voice_audio','source_audio','language'}:
        raise ValueError('Запрос содержит неподдерживаемые параметры Video')
    if not isinstance(data.get('engine', 'freevideo-h3-int8'), str):
        raise ValueError('Некорректный движок Video')
    engine = ENGINES.get(data.get('engine', 'freevideo-h3-int8'))
    if not engine:
        raise ValueError('Неизвестный движок Video')
    is_s2v = engine['id'] == 'wan22-s2v-14b'
    if not is_s2v and any(k in data for k in ('speech_mode','speech_text','voice_audio','source_audio','language')):
        raise ValueError('Поля речи предназначены для Wan S2V')
    mode = data.get('mode', engine['modes'][0])
    if mode not in engine['modes']:
        raise ValueError('Этот режим Video пока не поддерживается')
    prompt = data.get('prompt', '')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 5000:
        raise ValueError('Введите описание видео длиной до 5000 символов')
    quality = data.get('quality', engine['qualities'][0])
    if quality not in engine['qualities']:
        raise ValueError('Неизвестное качество Video')
    if quality not in engine['qualities_by_mode'][mode]:
        raise ValueError('Для заданных кадров пока проверен только предпросмотр · 8 шагов')
    is_native = engine['adapter'] != 'freevideo_cli'
    width, height, frames = data.get('width', engine['resolutions'][0][0]), data.get('height', engine['resolutions'][0][1]), data.get('frames', engine['frames'][0])
    fps = data.get('fps', engine['fps'])
    if type(width) is not int or type(height) is not int or [width, height] not in engine['resolutions'] or type(frames) is not int or frames not in engine['frames'] or type(fps) is not int or fps != engine['fps']:
        raise ValueError('Выберите разрешение, длительность и FPS из профилей выбранного движка')
    if not is_native and frames != 39 and (mode not in ('t2v', 'characters') or quality != 'preview'):
        raise ValueError('Длинные ролики доступны по тексту или персонажам в качестве «Предпросмотр»')
    seed = data.get('seed')
    if seed in (None, ''):
        seed = secrets.randbits(48)
    if isinstance(seed, bool) or not str(seed).isascii() or not str(seed).isdigit() or not 0 <= int(seed) < 2**64:
        raise ValueError('Seed должен быть целым числом от 0 до 18446744073709551615')
    for key in ('source_image', 'end_image'):
        required = key == 'source_image' and mode in ('i2v', 'first_last','s2v') or key == 'end_image' and mode == 'first_last'
        if required and not isinstance(data.get(key), str):
            raise ValueError('Добавьте исходный и, при необходимости, конечный кадр')
    people = data.get('characters', [])
    if not isinstance(people, list) or len(people) > 3:
        raise ValueError('Можно добавить до трёх персонажей')
    if mode == 'characters' and not people:
        raise ValueError('Добавьте хотя бы одного персонажа')
    if people and mode != 'characters':
        raise ValueError('Для персонажей выберите режим «Персонажи по изображениям»')
    for person in people:
        if not isinstance(person, dict) or set(person) - {'name', 'image', 'voice_audio', 'speech_text', 'language'}:
            raise ValueError('Некорректная карточка персонажа')
        if not isinstance(person.get('image'), str) or not isinstance(person.get('name', ''), str) or len(person.get('name', '')) > 60:
            raise ValueError('Добавьте изображение и короткое имя персонажа')
        speech = person.get('speech_text', '')
        if not isinstance(speech, str) or len(speech) > 1000:
            raise ValueError('Текст реплики: до 1000 символов на персонажа')
        if speech.strip() and not isinstance(person.get('voice_audio'), str):
            raise ValueError('Для реплики добавьте образец голоса')
        if person.get('language', 'Russian') not in ('Russian', 'English', 'Auto'):
            raise ValueError('Неподдерживаемый язык озвучки')
    result = dict(engine=engine['id'], mode=mode, prompt=prompt.strip(), original_prompt=prompt,
                quality=quality, width=width, height=height, frames=frames, fps=fps, seed=str(int(seed)))
    if is_s2v:
        speech_mode=data.get('speech_mode','clone')
        if speech_mode not in ('clone','audio'):
            raise ValueError('Выберите образец голоса с текстом или готовую речь')
        if speech_mode=='clone':
            text=data.get('speech_text','')
            if not isinstance(text,str) or not text.strip() or len(text)>160 or not isinstance(data.get('voice_audio'),str):
                raise ValueError('Добавьте образец голоса и короткую реплику до 160 символов; она должна поместиться в 4,8125 с')
            language=data.get('language','Russian')
            if language not in ('Russian','English','Auto'):raise ValueError('Неподдерживаемый язык речи')
            result.update(speech_text=text.strip(),language=language)
        elif not isinstance(data.get('source_audio'),str):
            raise ValueError('Добавьте готовую запись речи до 4,8125 с')
        if speech_mode=='clone' and data.get('source_audio') or speech_mode=='audio' and (data.get('voice_audio') or data.get('speech_text')):
            raise ValueError('Используйте только один выбранный способ подготовки речи')
        result['speech_mode']=speech_mode
    return result
