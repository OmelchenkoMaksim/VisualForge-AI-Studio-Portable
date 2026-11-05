"""Pinned FIBO components; the small backend update keeps the existing portable Python/Torch."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMFY = 'Runtime/ComfyUI_windows_portable/ComfyUI'
PYTHON = 'Runtime/ComfyUI_windows_portable/python_embeded/python.exe'


def components():
    data = json.loads((ROOT / 'App/config/fibo2-assets.json').read_text(encoding='utf-8'))
    backend = data['backend']
    downloads = [dict(id='source', urls=[backend['url']], **{k:backend[k] for k in ('filename', 'bytes', 'sha256')})]
    downloads += [dict(id=row['name'], urls=[row['url']], **{k:row[k] for k in ('filename', 'bytes', 'sha256')}) for row in data['wheels']]
    font = data['font']
    downloads.append(dict(id='fonts', urls=[font['url']], **{k:font[k] for k in ('filename', 'bytes', 'sha256')}))
    # Updating only a node would miss the diffusion architecture, detection and text-encoder changes.
    # Keep user/custom-node/model folders and bundled frontend/template packages; our UI uses the backend API.
    runtime = dict(id='image.backend.fibo2', module='image', kind='software', title='ComfyUI 0.44.0 backend (FIBO 2)',
        description='Official pinned backend source and matching comfy-kitchen/aimdo. Reuses portable Python, Torch and the bundled frontend.',
        license='GPL-3.0 (ComfyUI); MIT (kernel packages); Bitstream Vera (DejaVu Sans)', requires=['image.runtime'],
        present=[COMFY + '/comfy_extras/nodes_fibo2.py', COMFY + '/visualforge-fibo2-backend.json'],
        disk_bytes=280000000, downloads=downloads, steps=[
            dict(op='extract', download='source', to=COMFY, kind='zip', strip=1,
                 skip=['.git/*', 'models/*', 'user/*', 'custom_nodes/*', 'input/*', 'output/*']),
            dict(op='extract', download='fonts', to=COMFY + '/comfy/text_encoders/fibo2-fonts', kind='zip', strip=1,
                 keep=['ttf/DejaVuSans.ttf', 'LICENSE']),
            dict(op='patch_text', path=COMFY + '/comfy/text_encoders/fibo2.py', **data['font_patch']),
            dict(op='pip_install', python=PYTHON, downloads=[row['name'] for row in data['wheels']]),
            dict(op='write_json', path=COMFY + '/visualforge-fibo2-backend.json',
                 value=dict(version=backend['version'], revision=backend['revision']))])
    files = [dict(path=row['path'], bytes=row['bytes'], sha256=row['sha256'],
                  urls=['https://huggingface.co/Comfy-Org/Fibo-2.0/resolve/' + data['model_revision'] + '/' + row['source']]) for row in data['files']]
    shared = dict(id='image.model.qwen3vl4b-flux2', module='image', kind='model', group='Image',
        title='Shared Qwen3-VL 4B INT8 + FLUX.2 VAE',
        description='Byte-identical encoder and VAE shared by FIBO 2.0 Dev and FLUX.3 Klein.',
        license='Apache-2.0 (Qwen encoder and FLUX.2 autoencoder)', requires=['image.runtime'], files=files[1:])
    model = dict(id='image.model.fibo2-dev', module='image', kind='model', group='Image', title='FIBO 2.0 Dev (INT8)',
        description='Non-distilled 8B, 50 steps CFG 5. Native RGB generation and editing with up to four pictures; local Gemma prepares VGL.',
        license='CC-BY-NC-4.0 (BRIA weights); Apache-2.0 (Qwen encoder)',
        note='Non-commercial model weights; commercial use requires a separate BRIA agreement. About 1 MP native output, no alpha.',
        requires=['image.backend.fibo2', shared['id']], files=files[:1])
    return [runtime, shared, model]
