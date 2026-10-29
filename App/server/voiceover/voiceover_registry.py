"""Voiceover engines and request validation.

Each engine is plain data: where its model and Python packages live (relative to `Models/` and `Runtime/Video/`), which worker script
runs it and what it needs from the video card. Only engines whose files are installed are offered. `languages` use the names the
Qwen3-TTS API expects; VoxCPM2 detects the language from the text and ignores the field.
"""
from ui_strings import source as ui_text
import re
import secrets

LANGUAGES = ('Russian', 'English', 'German', 'French', 'Spanish', 'Italian', 'Portuguese', 'Chinese', 'Japanese', 'Korean')
FORMATS = ('wav', 'mp3', 'm4b')
QWEN_ID = 'qwen3-tts-12hz-0.6b-base'
VOXCPM_ID = 'voxcpm2'
ENGINES = {
    QWEN_ID: {
        'id': QWEN_ID, 'name': 'Qwen3-TTS 0.6B Base',
        'license': 'Apache-2.0', 'source': 'https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base',
        'upstream_revision': '5d83992436eae1d760afd27aff78a71d676296fc',
        'sample_rate': 24000, 'languages': LANGUAGES, 'formats': FORMATS,
        # Weights are about 2.5 GB in bfloat16; the claim leaves room for activations and the CUDA context (batches take more, see the worker).
        'claim_vram_mb': 4000, 'batch': True,
        'worker': 'qwen_tts_worker.py', 'packages': ('voice-packages',), 'model': 'Video/Qwen3TTS', 'package_check': 'voice-packages/qwen_tts/__init__.py',
        'model_files': ('config.json', 'model.safetensors'), 'component': 'video.model.qwen3-tts',
        'requirements': ui_text('voiceover.registry_engine_requirements_windows_nvidia_cuda_voice_sample'),
    },
    VOXCPM_ID: {
        'id': VOXCPM_ID, 'name': 'VoxCPM2 · 48 kHz',
        'license': 'Apache-2.0', 'source': 'https://huggingface.co/openbmb/VoxCPM2',
        'upstream_revision': '32279effe8c19989596f05d353d1447f51d9e915',
        'sample_rate': 48000, 'languages': LANGUAGES, 'formats': FORMATS,
        'claim_vram_mb': 9000, 'batch': False,
        # The package is installed next to the Qwen one and borrows its librosa, soundfile and transformers.
        'worker': 'voxcpm_worker.py', 'packages': ('voxcpm-packages', 'voice-packages'), 'model': 'Voiceover/VoxCPM2',
        'package_check': 'voxcpm-packages/voxcpm/__init__.py',
        'model_files': ('config.json', 'model.safetensors', 'audiovae.pth', 'tokenizer.json'), 'component': 'voiceover.model.voxcpm2',
        'requirements': ui_text('voiceover.registry_voxcpm_requirements_windows_nvidia_cuda_8gb'),
    },
}
DEFAULT_ENGINE = QWEN_ID
ENGINE = ENGINES[DEFAULT_ENGINE]  # kept for callers that predate the engine choice
ID = re.compile(r'[0-9a-f]{32}\Z')


def validate(data):
    """Returns the normalized job request or raises ValueError with a message for the page."""
    if not isinstance(data, dict):
        raise ValueError(ui_text('voiceover.registry_request_must_be_object_error'))
    for field in ('book_id', 'voice_id'):
        if not isinstance(data.get(field), str) or not ID.fullmatch(data[field]):
            raise ValueError(ui_text('voiceover.registry_choose_text_and_voice_first_error'))
    engine = data.get('engine', DEFAULT_ENGINE)
    if engine not in ENGINES:
        raise ValueError(ui_text('voiceover.registry_unknown_engine_error'))
    language = data.get('language', 'Russian')
    if language not in ENGINES[engine]['languages']:
        raise ValueError(ui_text('voiceover.registry_unsupported_language_error'))
    fmt = data.get('format', 'm4b')
    if fmt not in FORMATS:
        raise ValueError(ui_text('voiceover.registry_unsupported_format_error'))
    seed = data.get('seed')
    if seed in (None, ''):
        seed = secrets.randbelow(2 ** 32)
    try:
        seed = int(seed)
    except (TypeError, ValueError):
        raise ValueError(ui_text('voiceover.registry_seed_must_be_number_error')) from None
    if not 0 <= seed < 2 ** 32:
        raise ValueError(ui_text('voiceover.registry_seed_must_be_number_error'))
    replacements = data.get('replacements', [])
    if not isinstance(replacements, list) or len(replacements) > 500:
        raise ValueError(ui_text('voiceover.registry_pronunciation_dictionary_invalid_error'))
    pairs = []
    for row in replacements:
        if not (isinstance(row, (list, tuple)) and len(row) == 2 and all(isinstance(item, str) and len(item) <= 200 for item in row)):
            raise ValueError(ui_text('voiceover.registry_pronunciation_dictionary_invalid_error'))
        if row[0]:
            pairs.append([row[0], row[1]])
    verify, normalize, dictionary = data.get('verify', True), data.get('normalize', True), data.get('dictionary', True)
    if not all(isinstance(flag, bool) for flag in (verify, normalize, dictionary)):
        raise ValueError(ui_text('voiceover.registry_request_must_be_object_error'))
    return dict(engine=engine, book_id=data['book_id'], voice_id=data['voice_id'], language=language, format=fmt, seed=seed,
                replacements=pairs, verify=verify, normalize=normalize, dictionary=dictionary)
