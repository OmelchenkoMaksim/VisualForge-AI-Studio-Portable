"""Explicit capabilities and validation for tested local video profiles."""
from ui_strings import source as ui_text
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
    'limitations': ui_text('video.registry_engines_limitations_512_288_24_fps_one_minute'),
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
    'runtime': ui_text('video.registry_runtime_isolated_video_stock_comfy_ui_0'),
    'requirements': ui_text('video.registry_requirements_windows_10_11_x64_nvidia_cuda'),
    'limitations': ui_text('video.registry_limitations_silent_video_text_one_starting_image'),
}

ENGINES['wan22-s2v-14b'] = {
    'id':'wan22-s2v-14b','name':ui_text('video.registry_name_wan_2_s2_v_14_b'),'adapter':'wan_s2v',
    'release':'2.2 · S2V-14B','release_date':'2025-08-26','commit':None,
    'model_revision':'ee6f4a40737a995bf5818954cfce6d59443b0f04',
    'model_repository':'Comfy-Org/Wan-2.2','license':'Apache-2.0',
    'modes':['s2v'],'fps':16,'resolutions':[[512,512]],'frames':[77],
    'qualities':['standard'],'qualities_by_mode':{'s2v':['standard']},'steps':{'standard':20},
    'minimum_free_vram_mb':12000,
    'requirements':ui_text('video.registry_requirements_windows_10_11_x64_nvidia_cuda_short_generation_tested_rtx'),
    'limitations':ui_text('video.registry_limitations_experimental_short_profile_512_77_frames'),
}

ENGINES['ltx25-distilled'] = {
    'id':'ltx25-distilled','name':'LTX 2.5 Distilled','adapter':'ltx_native',
    'release':'2.5 · Distilled INT8 ConvRot','release_date':'2026-08-11','commit':None,
    'model_revision':'49b093a39c19169a8a81721dcc541e07e945232f',
    'model_repository':'Lightricks/LTX-2.5','license':'ltx-2.x-community-license-agreement',
    'modes':['t2v','i2v'],'fps':24,'resolutions':[[768,512],[1024,576],[1280,704],[512,768],[576,1024],[704,1280]],
    'frames':[49,97,121,360,720,1440],'duration_labels':{360:ui_text('video.registry_15_s_continuation'),720:ui_text('video.registry_30_s_continuation'),1440:ui_text('video.registry_60_s_continuation')},'qualities':['preview','standard'],
    'qualities_by_mode':{'t2v':['preview','standard'],'i2v':['preview','standard']},
    'steps':{'preview':8,'standard':11},'quality_labels':{'preview':ui_text('video.registry_preview_fast_8_steps'),'standard':ui_text('video.registry_standard_two_passes_8_3_steps')},
    'minimum_free_vram_mb':8000,'verification':{'summary_ru':ui_text('video.registry_summary_ru_experimental_profiles_require_gpu_verification')},
    'requirements':ui_text('video.registry_requirements_windows_x64_nvidia_cuda_16_gb'),
    'limitations':ui_text('video.registry_limitations_synchronized_video_audio_generation_text_first'),
}

def validate(data):
    if not isinstance(data, dict):
        raise ValueError(ui_text('video.validation_expected_video_request_object'))
    if set(data) - {'engine', 'mode', 'prompt', 'quality', 'width', 'height', 'frames', 'fps', 'seed', 'source_image', 'end_image', 'characters','speech_mode','speech_text','voice_audio','source_audio','language'}:
        raise ValueError(ui_text('video.validation_request_contains_unsupported_video_parameters'))
    if not isinstance(data.get('engine', 'freevideo-h3-int8'), str):
        raise ValueError(ui_text('video.validation_invalid_video_engine'))
    engine = ENGINES.get(data.get('engine', 'freevideo-h3-int8'))
    if not engine:
        raise ValueError(ui_text('video.validation_unknown_video_engine'))
    is_s2v = engine['id'] == 'wan22-s2v-14b'
    if not is_s2v and any(k in data for k in ('speech_mode','speech_text','voice_audio','source_audio','language')):
        raise ValueError(ui_text('video.validation_speech_fields_intended_wan_s2_v'))
    mode = data.get('mode', engine['modes'][0])
    if mode not in engine['modes']:
        raise ValueError(ui_text('video.validation_video_mode_not_supported_yet'))
    prompt = data.get('prompt', '')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 5000:
        raise ValueError(ui_text('video.validation_enter_video_description_up_5000_characters'))
    quality = data.get('quality', engine['qualities'][0])
    if quality not in engine['qualities']:
        raise ValueError(ui_text('video.validation_unknown_video_quality'))
    if quality not in engine['qualities_by_mode'][mode]:
        raise ValueError(ui_text('video.validation_preview_8_steps_verified_supplied_frames'))
    is_native = engine['adapter'] != 'freevideo_cli'
    width, height, frames = data.get('width', engine['resolutions'][0][0]), data.get('height', engine['resolutions'][0][1]), data.get('frames', engine['frames'][0])
    fps = data.get('fps', engine['fps'])
    if type(width) is not int or type(height) is not int or [width, height] not in engine['resolutions'] or type(frames) is not int or frames not in engine['frames'] or type(fps) is not int or fps != engine['fps']:
        raise ValueError(ui_text('video.validation_select_resolution_duration_fps_engine_s'))
    if not is_native and frames != 39 and (mode not in ('t2v', 'characters') or quality != 'preview'):
        raise ValueError(ui_text('video.validation_long_clips_support_text_characters_preview'))
    seed = data.get('seed')
    if seed in (None, ''):
        seed = secrets.randbits(48)
    if isinstance(seed, bool) or not str(seed).isascii() or not str(seed).isdigit() or not 0 <= int(seed) < 2**64:
        raise ValueError(ui_text('video.validation_seed_must_integer_0_18446744073709551615'))
    for key in ('source_image', 'end_image'):
        required = key == 'source_image' and mode in ('i2v', 'first_last','s2v') or key == 'end_image' and mode == 'first_last'
        if required and not isinstance(data.get(key), str):
            raise ValueError(ui_text('video.validation_add_source_frame_if_required_ending'))
    people = data.get('characters', [])
    if not isinstance(people, list) or len(people) > 3:
        raise ValueError(ui_text('video.validation_up_three_characters_added'))
    if mode == 'characters' and not people:
        raise ValueError(ui_text('video.validation_add_least_one_character'))
    if people and mode != 'characters':
        raise ValueError(ui_text('video.validation_select_characters_images_mode_character_cards'))
    for person in people:
        if not isinstance(person, dict) or set(person) - {'name', 'image', 'voice_audio', 'speech_text', 'language'}:
            raise ValueError(ui_text('video.validation_invalid_character_card'))
        if not isinstance(person.get('image'), str) or not isinstance(person.get('name', ''), str) or len(person.get('name', '')) > 60:
            raise ValueError(ui_text('video.validation_add_image_short_character_name'))
        speech = person.get('speech_text', '')
        if not isinstance(speech, str) or len(speech) > 1000:
            raise ValueError(ui_text('video.validation_dialogue_up_1000_characters_per_character'))
        if speech.strip() and not isinstance(person.get('voice_audio'), str):
            raise ValueError(ui_text('video.validation_add_voice_sample_dialogue'))
        if person.get('language', 'Russian') not in ('Russian', ui_text('video.speech_language_english_option_label'), 'Auto'):
            raise ValueError(ui_text('video.validation_unsupported_synthesis_language'))
    result = dict(engine=engine['id'], mode=mode, prompt=prompt.strip(), original_prompt=prompt,
                quality=quality, width=width, height=height, frames=frames, fps=fps, seed=str(int(seed)))
    if is_s2v:
        speech_mode=data.get('speech_mode','clone')
        if speech_mode not in ('clone','audio'):
            raise ValueError(ui_text('video.validation_choose_voice_sample_text_recorded_speech'))
        if speech_mode=='clone':
            text=data.get('speech_text','')
            if not isinstance(text,str) or not text.strip() or len(text)>160 or not isinstance(data.get('voice_audio'),str):
                raise ValueError(ui_text('video.validation_add_voice_sample_short_line_up'))
            language=data.get('language','Russian')
            if language not in ('Russian',ui_text('video.speech_language_english_option_label'),'Auto'):raise ValueError(ui_text('video.validation_unsupported_speech_language'))
            result.update(speech_text=text.strip(),language=language)
        elif not isinstance(data.get('source_audio'),str):
            raise ValueError(ui_text('video.validation_add_recorded_speech_up_4_8125'))
        if speech_mode=='clone' and data.get('source_audio') or speech_mode=='audio' and (data.get('voice_audio') or data.get('speech_text')):
            raise ValueError(ui_text('video.validation_use_selected_speech_preparation_method'))
        result['speech_mode']=speech_mode
    return result
