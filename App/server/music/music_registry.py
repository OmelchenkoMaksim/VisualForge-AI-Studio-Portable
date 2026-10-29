"""Installed music engine contract; no optional backend is advertised as installed.

`backend` says where a job runs: "worker" is an isolated Python subprocess (ACE-Step, MiniMax), "comfy" is the Image ComfyUI
reached over HTTP (Stable Audio 3). `capabilities` lists the form fields an engine really uses; the page hides the rest.
"""
from ui_strings import source as ui_text
WORKER_CAPABILITIES = dict(mode=True, lyrics=True, bpm=True, language=True, planner=True)

ENGINE = {
    'id': 'ace-step-15-turbo', 'name': 'ACE-Step 1.5 Turbo',
    'license': 'MIT', 'source': 'https://github.com/ace-step/ACE-Step-1.5',
    'upstream_revision': 'ca1e85fe9430179831e6bc6be790c332190a3866',
    'backend': 'worker', 'capabilities': WORKER_CAPABILITIES,
    'modes': ['instrumental', 'song'], 'min_duration': 10, 'max_duration': 180,
    'sample_rate': 48000, 'format': 'wav', 'planner': 'acestep-5Hz-lm-0.6B',
    'requirements': ui_text('music.registry_engine_requirements_windows_10_11_x64_nvidia_cuda'),
}

MINIMAX = {
    'id':'minimax-music3-int8', 'name':'MiniMax Music3 · INT8',
    'license':'MiniMax-Music3 Community License', 'source':'https://huggingface.co/MiniMaxAI/MiniMax-Music3',
    'upstream_revision':'9e364ccd290a401561091fb441ed19cd7de92550', 'release_date':'2026-08-13',
    'backend':'worker', 'capabilities':dict(WORKER_CAPABILITIES, planner=False),
    'modes':['instrumental','song'], 'min_duration':10, 'max_duration':60,
    'sample_rate':32000, 'format':'wav', 'planner':None,
    'requirements':ui_text('music.registry_minimax_requirements_experimental_windows_10_11_x64_nvidia'),
    'limitations':ui_text('music.registry_minimax_limitations_duration_upper_limit_model_may_finish'),
    'prompt_guide':ui_text('music.registry_minimax_prompt_guide_describe_genre_mood_instruments_voice_precision'),
}
STABLE_AUDIO = {
    'id':'stable-audio-3-medium', 'name':'Stable Audio 3 Medium',
    'license':ui_text('music.registry_stable_audio_license_stability_ai_community_gemma_terms_use'),
    'source':'https://huggingface.co/stabilityai/stable-audio-3-medium', 'package':'https://huggingface.co/Comfy-Org/stable-audio-3',
    'upstream_revision':'75aad8836271cb5a5123fb58304fae5d3bb4b0f9', 'release_date':'2026-09-23',
    'backend':'comfy', 'capabilities':dict(mode=False, lyrics=False, bpm=False, language=False, planner=False),
    'modes':['audio'], 'min_duration':10, 'max_duration':120,
    # The rate is what the VAE reports (44.1 kHz stereo in ComfyUI 0.42.1); the stored WAV is always inspected, never assumed.
    'sample_rate':44100, 'format':'wav', 'planner':None,
    # Starting claim for the GPU wait; the real peak is measured at acceptance, so this is an estimate.
    # Measured on the RTX 5070 Ti with nvidia-smi: about 5 GB above idle for 10-20 s clips and about 12.5 GB for 120 s, because the VAE decode grows with length.
    'claim_vram_mb':8000, 'claim_vram_by_duration':((30, 8000), (60, 10000), (120, 14000)), 'workflow_version':'sa3-medium-v1',
    'requirements':ui_text('music.registry_stable_audio_requirements_experimental_windows_10_11_x64_nvidia'),
    'limitations':ui_text('music.registry_stable_audio_limitations_music_sounds_english_descriptions_stereo_wav'),
    'prompt_guide':ui_text('music.registry_stable_audio_prompt_guide_describe_genre_instruments_mood_energy_english'),
}
ENGINES = {ENGINE['id']:ENGINE, MINIMAX['id']:MINIMAX, STABLE_AUDIO['id']:STABLE_AUDIO}

def get_engine(ident=None):
    try:
        return ENGINES[ident or ENGINE['id']]
    except (KeyError, TypeError):
        raise ValueError(ui_text('music.get_engine_unknown_music')) from None

def _seed(value):
    import secrets
    seed = secrets.randbelow(2**32) if value in ('', None, -1, '-1') else int(value)
    if not 0 <= seed < 2**32:
        raise ValueError(ui_text('music.seed_integer_0_4294967295_empty'))
    return seed

def _validate_audio(engine, data):
    """Stable Audio takes a prompt, a duration and a seed; fields of the song engines are ignored, never stored."""
    caption = data.get('caption', '')
    if not isinstance(caption, str) or not 1 <= len(caption.strip()) <= 2000:
        raise ValueError(ui_text('music.validate_audio_describe_sound_music_1_2000_characters'))
    try:
        duration = int(data.get('duration', 30))
    except (TypeError, ValueError):
        raise ValueError(ui_text('music.validate_audio_duration_must_whole_number_seconds')) from None
    if type(data.get('duration', 30)) is bool or not engine['min_duration'] <= duration <= engine['max_duration']:
        raise ValueError(ui_text('music.validate_audio_duration_seconds', f"{engine['name']}", f"{engine['min_duration']}", f"{engine['max_duration']}"))
    return dict(engine=engine['id'], caption=caption.strip(), lyrics='', mode='audio', duration=duration,
                seed=_seed(data.get('seed', '')), bpm=None, language='unknown', planner=False)

def claim_vram_mb(engine, request):
    """Free video memory to wait for before a job starts; some engines need more for a longer clip."""
    for limit, megabytes in engine.get('claim_vram_by_duration', ()):
        if request.get('duration', 0) <= limit:
            return megabytes
    return engine.get('claim_vram_mb', 8000)

def validate(data):
    """Normalize a bounded request before any runtime or GPU is touched."""
    import secrets
    engine = get_engine(data.get('engine', ENGINE['id']))
    if engine['id'] == STABLE_AUDIO['id']:
        return _validate_audio(engine, data)
    caption = data.get('caption', '')
    lyrics = data.get('lyrics', '')
    if not isinstance(caption, str) or not 1 <= len(caption.strip()) <= 2000:
        raise ValueError(ui_text('music.validation_describe_music_1_2000_characters'))
    if not isinstance(lyrics, str) or len(lyrics) > 6000:
        raise ValueError(ui_text('music.validation_lyrics_no_more_than_6000_characters'))
    mode = data.get('mode', 'instrumental')
    if mode not in ENGINE['modes']:
        raise ValueError(ui_text('music.validation_unknown_music_mode'))
    if mode == 'song' and not lyrics.strip():
        raise ValueError(ui_text('music.validation_add_lyrics'))
    duration = int(data.get('duration', 30))
    if not engine['min_duration'] <= duration <= engine['max_duration']:
        raise ValueError(ui_text('music.validate_audio_duration_seconds', f"{engine['name']}", f"{engine['min_duration']}", f"{engine['max_duration']}"))
    seed = data.get('seed', '')
    seed = secrets.randbelow(2**32) if seed in ('', None, -1, '-1') else int(seed)
    if not 0 <= seed < 2**32:
        raise ValueError(ui_text('music.seed_integer_0_4294967295_empty'))
    bpm = data.get('bpm', '')
    bpm = None if bpm in ('', None) else int(bpm)
    if bpm is not None and not 30 <= bpm <= 300:
        raise ValueError(ui_text('music.validation_tempo_30_300_beats_per_minute'))
    language = data.get('language', 'unknown')
    if language not in ('unknown', 'ru', 'en', 'zh', 'ja', 'ko', 'es', 'fr', 'de', 'it', 'pt'):
        raise ValueError(ui_text('music.validation_unknown_vocal_language'))
    planner = data.get('planner', True)
    if not isinstance(planner, bool):
        raise ValueError(ui_text('music.validation_invalid_music_planner_setting'))
    if engine['id'] == MINIMAX['id']:
        planner = False
    return dict(engine=engine['id'], caption=caption.strip(), lyrics=lyrics.strip(), mode=mode,
                duration=duration, seed=seed, bpm=bpm, language=language, planner=planner)
