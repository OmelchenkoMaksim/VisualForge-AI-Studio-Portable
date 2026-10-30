"""Explicit capabilities and validation for tested local video profiles."""
from ui_strings import source as ui_text
import secrets

ENGINES = {'freevideo-h3-int8': {
    'id': 'freevideo-h3-int8', 'name': 'FreeVideo · MiniMax H3 int8', 'file_label': 'FreeVideo MiniMax H3',
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
    'id': 'wan22-ti2v-5b', 'name': 'Wan 2.2 TI2V-5B', 'file_label': 'Wan 2.2 TI2V-5B', 'adapter': 'wan_comfy',
    'release': '2.2 · TI2V-5B', 'release_date': '2025-07-28', 'commit': None,
    'model_revision': 'ee6f4a40737a995bf5818954cfce6d59443b0f04',
    'model_repository': 'Comfy-Org/Wan-2.2', 'license': 'Apache-2.0',
    # 1280x704 / 704x1280 is the model's native "720P" size; 832x480 is the ComfyUI template's lighter size.
    # 512x288 was removed: it produced severe shape artifacts and is far below the training resolution.
    'modes': ['t2v','i2v'], 'fps': 24, 'resolutions': [[832,480],[480,832],[1280,704],[704,1280]],
    # Frames above 121 are a chain of 121-frame segments (see WAN_SEGMENTS): each continues from the previous last frame.
    'frames': [33,81,121,240,360,720,1440,2880], 'qualities': ['preview','standard','high'],
    'duration_labels': {240: ui_text('video.registry_wan_10_s_chain'), 360: ui_text('video.registry_wan_15_s_chain'),
                        720: ui_text('video.registry_wan_30_s_chain'), 1440: ui_text('video.registry_wan_60_s_chain')},
    'negative_prompt': True,
    'qualities_by_mode': {'t2v':['preview','standard','high'], 'i2v':['preview','standard','high']},
    'steps': {'preview':12,'standard':20,'high':30}, 'minimum_free_vram_mb':6500,
    'runtime': ui_text('video.registry_runtime_isolated_video_stock_comfy_ui_0'),
    'requirements': ui_text('video.registry_requirements_windows_10_11_x64_nvidia_cuda'),
    'limitations': ui_text('video.registry_limitations_silent_video_text_one_starting_image'),
}

ENGINES['wan22-s2v-14b'] = {
    'id':'wan22-s2v-14b','name':ui_text('video.registry_name_wan_2_s2_v_14_b'),'file_label':'Wan 2.2 S2V-14B','adapter':'wan_s2v',
    'release':'2.2 · S2V-14B','release_date':'2025-08-26','commit':None,
    'model_revision':'ee6f4a40737a995bf5818954cfce6d59443b0f04',
    'model_repository':'Comfy-Org/Wan-2.2','license':'Apache-2.0',
    'modes':['s2v'],'fps':16,'resolutions':[[512,512],[832,480],[480,832]],'frames':[77],'negative_prompt':True,
    'qualities':['standard'],'qualities_by_mode':{'s2v':['standard']},'steps':{'standard':20},
    'minimum_free_vram_mb':12000,
    'requirements':ui_text('video.registry_requirements_windows_10_11_x64_nvidia_cuda_short_generation_tested_rtx'),
    'limitations':ui_text('video.registry_limitations_experimental_short_profile_512_77_frames'),
}

ENGINES['ltx25-distilled'] = {
    'id':'ltx25-distilled','name':'LTX 2.5 Distilled','file_label':'LTX 2.5 Distilled','adapter':'ltx_native',
    'release':'2.5 · Distilled INT8 ConvRot','release_date':'2026-08-11','commit':None,
    'model_revision':'49b093a39c19169a8a81721dcc541e07e945232f',
    'model_repository':'Lightricks/LTX-2.5','license':'ltx-2.x-community-license-agreement',
    'modes':['t2v','i2v','first_last','a2v'],'fps':24,'prompt_enhancer':['t2v','i2v','first_last','a2v'],'resolutions':[[768,512],[1024,576],[1280,704],[512,768],[576,1024],[704,1280]],
    'frames':[49,97,121,240,360,720,1440,2880],'duration_labels':{360:ui_text('video.registry_15_s_continuation'),720:ui_text('video.registry_30_s_continuation'),1440:ui_text('video.registry_60_s_continuation')},'qualities':['preview','standard'],
    'qualities_by_mode':{'t2v':['preview','standard'],'i2v':['preview','standard'],'first_last':['preview','standard'],'a2v':['preview','standard']},
    'steps':{'preview':8,'standard':11},'quality_labels':{'preview':ui_text('video.registry_preview_fast_8_steps'),'standard':ui_text('video.registry_standard_two_passes_8_3_steps')},
    'minimum_free_vram_mb':8000,'verification':{'summary_ru':ui_text('video.registry_summary_ru_experimental_profiles_require_gpu_verification')},
    'requirements':ui_text('video.registry_requirements_windows_x64_nvidia_cuda_16_gb'),
    'limitations':ui_text('video.registry_limitations_synchronized_video_audio_generation_text_first'),
}

# Total frames -> number of 121-frame segments. Every segment after the first drops its first frame (it repeats the previous
# last frame), so n segments give 121 + 120 * (n - 1) frames and the last one is trimmed to the requested total.
WAN_SEGMENTS = {240: 2, 360: 3, 720: 6, 1440: 12}
# "Scenes" mode: N independent shots joined with hard cuts. The total is a minimum: each scene is the shortest 4n+1 frame
# count (the video model's temporal grid) that reaches it, between the shortest tested clip and the model's 5 seconds.
# 2880 (two minutes) exists only here; as one continuous chain it would be 24 segments of accumulated drift.
SCENE_TOTALS = (240, 360, 720, 1440, 2880)
SCENE_COUNTS = (2, 30)
SCENE_FRAMES = (33, 121)


# LTX 2.5 has a coarser time grid (8n+1 frames) and a longer shortest clip; its chain of continuation windows
# (ltx_continuation.windows) has 4 / 8 / 15 windows for 15 / 30 / 60 seconds.
LTX_SCENE_FRAMES = (49, 121)
LTX_WINDOWS = {360: 4, 720: 8, 1440: 15}


def scene_frames(total, scenes, engine='wan22-ti2v-5b'):
    """Frames per scene for at least `total` frames in `scenes` shots; ValueError when one scene would exceed 121 frames."""
    need = -(-total // scenes)
    step, (shortest, longest) = (8, LTX_SCENE_FRAMES) if engine == 'ltx25-distilled' else (4, SCENE_FRAMES)
    length = max(shortest, -(-(need - 1) // step) * step + 1)
    if length > longest:
        raise ValueError(ui_text('video.validation_too_few_scenes') % (total / 24, -(-total // longest)))
    return length


def validate(data):
    if not isinstance(data, dict):
        raise ValueError(ui_text('video.validation_expected_video_request_object'))
    if set(data) - {'engine', 'mode', 'prompt', 'quality', 'width', 'height', 'frames', 'fps', 'seed', 'source_image', 'end_image', 'characters','speech_mode','speech_text','voice_audio','source_audio','language','negative_prompt','segment_prompts','scenes','structure','user_prompt'}:
        raise ValueError(ui_text('video.validation_request_contains_unsupported_video_parameters'))
    if not isinstance(data.get('engine', 'freevideo-h3-int8'), str):
        raise ValueError(ui_text('video.validation_invalid_video_engine'))
    engine = ENGINES.get(data.get('engine', 'freevideo-h3-int8'))
    if not engine:
        raise ValueError(ui_text('video.validation_unknown_video_engine'))
    is_s2v = engine['id'] == 'wan22-s2v-14b'
    mode = data.get('mode', engine['modes'][0])
    # LTX audio-to-video takes one recording (`source_audio`); every other speech field belongs to Wan S2V.
    if not is_s2v and any(k in data for k in ('speech_mode','speech_text','voice_audio','language') + (() if engine['id'] == 'ltx25-distilled' and mode == 'a2v' else ('source_audio',))):
        raise ValueError(ui_text('video.validation_speech_fields_intended_wan_s2_v'))
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
    if mode == 'first_last' and engine['id'] == 'ltx25-distilled' and frames > 121:
        # The two pictures are keyframes of one 121-frame window; the 15/30/60 s continuations have no ending to aim at.
        raise ValueError(ui_text('video.validation_first_last_frame_up_to_5_s'))
    if mode == 'a2v':
        # The recording decides what is heard; a picture is optional (it becomes the first frame). One 121-frame window, no continuation.
        if not isinstance(data.get('source_audio'), str) or frames > 121 or data.get('source_image') not in (None, '') and not isinstance(data.get('source_image'), str):
            raise ValueError(ui_text('video.validation_audio_to_video_needs_recording_up_to_5_s'))
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
    # `user_prompt` is what the person typed when `prompt` was rewritten by Gemma: the history and file names keep the person's words.
    typed = data.get('user_prompt', prompt)
    if not isinstance(typed, str) or not typed.strip() or len(typed) > 5000:
        raise ValueError(ui_text('video.validation_enter_video_description_up_5000_characters'))
    result = dict(engine=engine['id'], mode=mode, prompt=prompt.strip(), original_prompt=typed,
                quality=quality, width=width, height=height, frames=frames, fps=fps, seed=str(int(seed)))
    is_wan5 = engine['id'] == 'wan22-ti2v-5b'
    is_ltx = engine['id'] == 'ltx25-distilled'
    scenes = data.get('scenes')
    count = 0
    chain_optional = False
    if scenes is not None:
        if not (is_wan5 or is_ltx and mode in ('t2v', 'i2v')) or frames not in SCENE_TOTALS or type(scenes) is not int or not SCENE_COUNTS[0] <= scenes <= SCENE_COUNTS[1]:
            raise ValueError(ui_text('video.validation_invalid_scene_count') % SCENE_COUNTS)
        per_scene = scene_frames(frames, scenes, engine['id'])
        count = scenes
        result.update(requested_frames=frames, frames=per_scene * scenes, scenes=scenes, scene_frames=per_scene, join='cut')
    elif is_wan5 and frames in WAN_SEGMENTS:
        count = WAN_SEGMENTS[frames]
    elif is_ltx and frames in LTX_WINDOWS:
        # One scene continued window by window: a scenario (one prompt per window) is welcome but optional.
        count, chain_optional = LTX_WINDOWS[frames], True
    elif frames in (240, 2880):
        raise ValueError(ui_text('video.validation_two_minutes_scenes_only'))
    plan = data.get('segment_prompts')
    structure = data.get('structure', 'single')
    if is_s2v:
        # Long S2V clips take their length from the speech, so the number of segments is not known here; the worker maps the
        # prompts (any number from 1 to 20) onto the segments it plans.
        if structure not in ('single', 'scenes'):
            raise ValueError(ui_text('video.validation_invalid_s2v_structure'))
        if plan is not None and (structure == 'single' or not isinstance(plan, list) or not 1 <= len(plan) <= 20
                                 or not all(isinstance(p, str) and p.strip() and len(p) <= 1500 for p in plan)):
            raise ValueError(ui_text('video.validation_invalid_s2v_segment_prompts'))
        result['structure'] = structure
        if plan:
            result['segment_prompts'] = [p.strip() for p in plan]
    elif structure != 'single':
        raise ValueError(ui_text('video.validation_structure_only_wan_s2v'))
    elif count and not (chain_optional and plan is None):
        if not isinstance(plan, list) or len(plan) != count or not all(isinstance(p, str) and p.strip() and len(p) <= 1500 for p in plan):
            raise ValueError(ui_text('video.validation_long_clip_needs_segment_prompts') % count)
        if mode not in ('t2v', 'i2v'):
            raise ValueError(ui_text('video.validation_video_mode_not_supported_yet'))
        result['segment_prompts'] = [p.strip() for p in plan]
    elif plan is not None:
        raise ValueError(ui_text('video.validation_segment_prompts_only_long_wan_clip'))
    negative = data.get('negative_prompt', '')
    if negative not in ('', None):
        if not engine.get('negative_prompt'):
            raise ValueError(ui_text('video.validation_negative_prompt_not_supported_engine'))
        if not isinstance(negative, str) or len(negative) > 2000:
            raise ValueError(ui_text('video.validation_negative_prompt_up_2000_characters'))
        if negative.strip():
            result['negative_prompt'] = negative.strip()
    if is_s2v:
        speech_mode=data.get('speech_mode','clone')
        if speech_mode not in ('clone','audio'):
            raise ValueError(ui_text('video.validation_choose_voice_sample_text_recorded_speech'))
        if speech_mode=='clone':
            text=data.get('speech_text','')
            if not isinstance(text,str) or not text.strip() or len(text)>(160 if structure=='single' else 1000) or not isinstance(data.get('voice_audio'),str):
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
