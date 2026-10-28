"""Portable private-process environment; never modify the Image environment."""
from ui_strings import source as ui_text
import os
import hashlib
from pathlib import Path

def environment(root, models, state):
    root, models, state = map(Path, (root, models, state))
    compiler = state
    if not str(state).isascii():
        # CUDA's module loader uses a narrow file path for generated cubins.
        # Models, outputs and settings stay portable; only disposable kernels
        # use the current host's user temp folder, recomputed on every launch.
        identity = hashlib.sha256(str(state.resolve()).encode('utf-8')).hexdigest()[:16]
        compiler = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Temp/VisualForgeVideo/Compiler' / identity
        if not str(compiler).isascii():
            raise ValueError(ui_text('video.environment_cuda_cache_requires_accessible_temporary_path'))
    for parent, names in ((state, ('hf', 'pip', 'torch')), (compiler, ('tmp', 'triton', 'inductor', 'cuda', 'numba'))):
        for name in names:
            (parent / name).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(FREEVIDEO_HOME=str(state), FREEVIDEO_MODEL_ROOT=str(models),
        FREEVIDEO_VDN_ROOT=str(root / 'vendor/vdn'), FREEVIDEO_COMFY_ROOT=str(root / 'vendor/ComfyUI'),
        FREEVIDEO_COMFY_PYTHON=str(root / 'python/python.exe'),
        FREEVIDEO_COMPILER_TEMP_ROOT=str(compiler / 'tmp'), TEMP=str(compiler / 'tmp'), TMP=str(compiler / 'tmp'), TMPDIR=str(compiler / 'tmp'),
        TRITON_CACHE_DIR=str(compiler / 'triton'), TORCHINDUCTOR_CACHE_DIR=str(compiler / 'inductor'), CUDA_CACHE_PATH=str(compiler / 'cuda'), HF_HOME=str(state / 'hf'), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        NUMBA_CACHE_DIR=str(compiler / 'numba'),
        PIP_NO_INDEX='1', PYTHONNOUSERSITE='1', PYTHONUTF8='1', TORCH_HOME=str(state / 'torch'),
        PYTHONPATH=os.pathsep.join((str(Path(__file__).parent / 'offline_guard'), str(root / 'vendor/FreeVideo'), str(Path(__file__).parents[1]))),
        VISUALFORGE_OFFLINE_LOG=str(state / 'offline-denials.jsonl'),
        PATH=str(root / 'ffmpeg/bin') + os.pathsep + str(root / 'python/Scripts') + os.pathsep + env.get('PATH', ''))
    return env
