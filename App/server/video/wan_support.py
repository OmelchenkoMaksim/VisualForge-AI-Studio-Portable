"""Wan model locations, independent engine readiness and bounded persistent logs."""
import hashlib
import os
from pathlib import Path
import tempfile

WAN_ID = 'wan22-ti2v-5b'
S2V_ID = 'wan22-s2v-14b'
WAN_IDS = (WAN_ID,S2V_ID)
S2V_FILES = ('diffusion_models/wan2.2_s2v_14B_fp8_scaled.safetensors',
             'audio_encoders/wav2vec2_large_english_fp16.safetensors','vae/wan_2.1_vae.safetensors')
WAN_FILES = ('diffusion_models/wan2.2_ti2v_5B_fp16.safetensors',
             'text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors', 'vae/wan2.2_vae.safetensors')

def model_root(h3_models, engine=WAN_ID):
    if engine == S2V_ID:
        return Path(os.environ.get('VISUALFORGE_S2V_MODELS',Path(h3_models).parent/'Wan22S2V14B')).resolve()
    return Path(os.environ.get('VISUALFORGE_WAN_MODELS', Path(h3_models).parent / 'Wan22TI2V5B')).resolve()

def scratch(user):
    key = hashlib.sha256(str(Path(user).resolve()).encode('utf-8')).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / 'VisualForgeWan' / key

def engine_missing(root, h3_models, engine):
    if engine == 'ltx25-distilled':
        from .ltx_support import engine_missing as ltx_missing
        return ltx_missing(root,h3_models)
    if engine in ('wan-animate2', 'scail2'):
        from .character_support import engine_missing as character_missing
        return character_missing(root,h3_models,engine,fit=True)
    root, h3_models = Path(root), Path(h3_models)
    common =[root / path for path in ('python/python.exe','ffmpeg/bin/ffprobe.exe','ffmpeg/bin/ffmpeg.exe')]
    if engine in WAN_IDS:
        common += [root/'vendor/ComfyUI/comfy_extras/nodes_wan.py']
        if engine == S2V_ID:
            common += [model_root(h3_models,engine)/name for name in S2V_FILES]
            common += [model_root(h3_models)/WAN_FILES[1],root/'vendor/ComfyUI/comfy_extras/nodes_audio_encoder.py']
        else:
            common += [model_root(h3_models)/name for name in WAN_FILES]
    else:
        common += [root/path for path in ('vendor/FreeVideo/freevideo_engine/__init__.py',
                    'vendor/vdn/src/models/hybrid_attention.py','vendor/ComfyUI/folder_paths.py')]
        common += [h3_models/path for path in ('int8-convrot/cache/root.safetensors',
            'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors','h3-base/vae/config.json')]
    return [str(path) for path in common if not path.is_file()]

def archive_log(user, folder, ident):
    """Only a bounded tail is retained on H; intermediate frames stay in C Temp."""
    path = Path(folder)/'backend.log'
    data = b''
    if path.exists():
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size - 48*1024))
            data = stream.read()
    destination = Path(user)/'Logs/Video/Wan'/(ident+'.log')
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_bytes(data)
    return destination
