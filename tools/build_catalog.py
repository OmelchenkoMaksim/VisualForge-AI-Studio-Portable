"""Generate App/config/component-catalog.json, the catalog the setup installer works from.

This is a maintainer tool, not part of a normal installation. It combines
  * a working reference installation (--installed): the manifests and lock files recorded when it was assembled,
  * the pinned model catalog and dependency lock kept in docs/,
  * the pinned sampling-table locks of the Video cache (--work),
  * a few live lookups (GitHub release digests, PyPI wheel hashes, Hugging Face file listings),
and writes one JSON document in which every downloadable file has an exact size and SHA-256.

    python tools/build_catalog.py --installed "D:/VisualForge" --work "D:/work" --out App/config/component-catalog.json

Review `git diff` before committing a regenerated catalog.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[1]
CATALOG_VERSION = '2026.10.27'
UA = {'User-Agent': 'visualforge-catalog/1'}
HF_URL = 'https://huggingface.co/%s/resolve/%s/%s'


# ---- network helpers -----------------------------------------------------------------------------------------------
def hf_headers(url):
    """The Hugging Face read token, read from HF_TOKEN and sent only to huggingface.co; never printed or stored."""
    token = os.environ.get('HF_TOKEN')
    if token and urllib.parse.urlparse(url).hostname in ('huggingface.co', 'hf.co'):
        return {'Authorization': 'Bearer ' + token}
    return {}


def http_json(url, accept='application/json'):
    with urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **hf_headers(url), 'Accept': accept}), timeout=90) as response:
        return json.load(response)


def http_bytes(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **hf_headers(url)}), timeout=300) as response:
        return response.read()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def tree_size(path):
    total = 0
    for folder, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total


def unquote_name(url):
    return urllib.parse.unquote(url.rsplit('/', 1)[-1])


_TREES = {}


def hf_listing(repo, revision):
    """{path: (size, lfs sha256 or None)} for a repository revision from the public file listing."""
    key = (repo, revision)
    if key not in _TREES:
        listing, url = {}, 'https://huggingface.co/api/models/%s/tree/%s?recursive=1&limit=1000' % (repo, revision)
        while url:
            with urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **hf_headers(url)}), timeout=90) as response:
                for item in json.load(response):
                    if item.get('type') == 'file':
                        listing[item['path']] = (item['size'], (item.get('lfs') or {}).get('oid'))
                link = response.headers.get('Link', '')
            match = re.search(r'<([^>]+)>;\s*rel="next"', link)
            url = match.group(1) if match else None
        _TREES[key] = listing
    return _TREES[key]


def hf_url(repo, revision, path):
    return HF_URL % (repo, revision, urllib.parse.quote(path))


def hf_file(repo, revision, path, target, mirrors=()):
    """A model file entry. Small non-LFS files are fetched once to learn their SHA-256."""
    size, sha = hf_listing(repo, revision)[path]
    if sha is not None and not re.fullmatch(r'[0-9a-f]{64}', sha):
        # Gated repositories show an unauthenticated reader a masked digest; a catalog must never pin that.
        raise RuntimeError('%s/%s: Hugging Face hides the SHA-256 without a token; accept the repository terms and set HF_TOKEN' % (repo, path))
    if sha is None:
        data = http_bytes(hf_url(repo, revision, path))
        assert len(data) == size, (repo, path)
        sha = hashlib.sha256(data).hexdigest()
    return dict(path=target, bytes=size, sha256=sha, urls=[hf_url(repo, revision, path)] + [hf_url(r, v, path) for r, v in mirrors])


def github_asset(repo, tag, name):
    data = http_json('https://api.github.com/repos/%s/releases/tags/%s' % (repo, tag))
    asset = next(a for a in data['assets'] if a['name'] == name)
    digest = asset.get('digest')
    if not digest:
        raise RuntimeError('GitHub published no digest for %s' % name)
    return dict(url=asset['browser_download_url'], filename=name, bytes=asset['size'], sha256=digest.split(':', 1)[1])


def pypi_wheel(name, version):
    """Best cp312 win_amd64 (else pure) wheel of a PyPI release."""
    data = http_json('https://pypi.org/pypi/%s/%s/json' % (name, version))

    def score(f):
        n = f['filename']
        if f['packagetype'] != 'bdist_wheel' or f.get('yanked'):
            return 9
        if 'win_amd64' in n and ('cp312' in n or 'abi3' in n):
            return 0
        return 1 if n.endswith('none-any.whl') else 5
    best = min(data['urls'], key=score)
    if score(best) >= 5:
        raise RuntimeError('No suitable wheel for %s==%s' % (name, version))
    return dict(url=best['url'], filename=best['filename'], bytes=best['size'], sha256=best['digests']['sha256'])


def download(ident, url, filename, size, sha):
    return dict(id=ident, urls=[url] if isinstance(url, str) else list(url), filename=filename, bytes=size, sha256=sha)


def norm(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def component(**keywords):
    return {key: value for key, value in keywords.items() if value is not None}


# ---- core + Image --------------------------------------------------------------------------------------------------
def build_core():
    tool = github_asset('ip7z/7zip', '25.01', '7zr.exe')
    return [component(
        id='core.tools', module='core', kind='software', title='7-Zip console unpacker (7zr.exe)',
        description='Small official 7-Zip tool, used only to unpack the ComfyUI archive.', license='LGPL (7-Zip)',
        present=['Runtime/Tools/7zr.exe'], disk_bytes=tool['bytes'],
        downloads=[download('7zr', tool['url'], tool['filename'], tool['bytes'], tool['sha256'])],
        steps=[dict(op='place', download='7zr', to='Runtime/Tools/7zr.exe')])]


def build_image(installed, skip_gated=False):
    comfy = github_asset('Comfy-Org/ComfyUI', 'v0.42.1', 'ComfyUI_windows_portable_nvidia.7z')
    runtime = component(
        id='image.runtime', module='image', kind='software', title='ComfyUI 0.42.1 portable (NVIDIA): the Image engine',
        description='ComfyUI with its own embedded Python and PyTorch. Runs the Image section and its workflows.', license='GPL-3.0 (ComfyUI)', requires=['core.tools'],
        present=['Runtime/ComfyUI_windows_portable/python_embeded/python.exe', 'Runtime/ComfyUI_windows_portable/ComfyUI/main.py'],
        disk_bytes=tree_size(installed / 'Runtime/ComfyUI_windows_portable') + 100 * 10 ** 6,
        downloads=[download('comfy', comfy['url'], comfy['filename'], comfy['bytes'], comfy['sha256'])],
        steps=[dict(op='extract', download='comfy', to='Runtime', kind='7z', skip=['ComfyUI_windows_portable/ComfyUI/.git/*']),
               dict(op='move_tree', **{'from': 'Runtime/ComfyUI_windows_portable/ComfyUI/models', 'to': 'Models'})])
    edit_repo = ('Comfy-Org/Qwen-Image-Edit_ComfyUI', 'f68ace85e60b4a02a323e394253731947657b7d2')
    base_repo = ('Comfy-Org/Qwen-Image_ComfyUI', '1f12b17be14c89b026c51a91d67c32f84bb047bc')
    lora = ('ScottzillaSystems/qwen-image-edit-plus-nsfw-lora', '66e89e998dd4ea1a359c4bf0dd5e17d2f0b06ef0')
    lora_mirrors = [('aiunivers/qwen-image-edit-plus-nsfw-lora', 'e426d878fa16f2e341784effeb992e16c5ee3ea7'), ('horrifying/qwen-image-edit-plus-nsfw-lora', '16b3a9b170af420a70621cb5d4d92f8cbe3a70a7')]
    uc = ('abenzerps/Qwen-Image-2.1-Uncensored-Quantized', '7959e658915f52274897fb573ffeda49de8dc16b')
    uc_mirror = [('Comfy-Org/Qwen-Image-2.1', '0217efd1d9c9452e84f6448903d6e21bfa171ffd')]
    edit = component(
        id='image.model.qwen-edit-2511', module='image', kind='model', group='Image', title='Qwen Image Edit 2511 (FP8)',
        description='Instruction editing with one to three images, or a prompt on a neutral canvas.', license='Apache-2.0',
        requires=['image.runtime'], files=[
            hf_file(*edit_repo, 'split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors', 'Models/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors'),
            hf_file(*base_repo, 'split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors', 'Models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors'),
            hf_file(*base_repo, 'split_files/vae/qwen_image_vae.safetensors', 'Models/vae/qwen_image_vae.safetensors')])
    community = component(id='image.model.qwen-edit-uncensored', module='image', kind='model', group='Image',
        title='Qwen 2511 optional NSFW LoRA', description='Optional community adapter; base Qwen 2511 does not require it.',
        license='OpenRAIL++', requires=['image.runtime', 'image.model.qwen-edit-2511'], files=[
            hf_file(*lora, 'qwen-image-edit-plus-nsfw-lora.safetensors', 'Models/loras/qwen-image-edit-plus-nsfw-lora.safetensors', lora_mirrors)])
    quantized = component(
        id='image.model.qwen-image-21-uc', module='image', kind='model', group='Image', title='Qwen Image 2.1 Uncensored (FP8)',
        description='Native text-to-image plus editing with reference images.', license='Qwen Research License (non-commercial research use)',
        note='Uncensored community quantization; use responsibly and within your local law.', requires=['image.runtime'], files=[
            hf_file(*uc, 'qwen-image-2.1-UC-fp8.safetensors', 'Models/diffusion_models/qwen-image-2.1-UC-fp8.safetensors'),
            hf_file(*uc, 'text_encoders/qwen3vl_8b_int8_convrot.safetensors', 'Models/text_encoders/qwen3vl_8b_int8_convrot.safetensors', uc_mirror),
            hf_file(*uc, 'vae/qwen_image_2.1_vae_bf16.safetensors', 'Models/vae/qwen_image_2.1_vae_bf16.safetensors', uc_mirror)])
    # The official ComfyUI INT8 packaging pairs the INT8 transformer with the FP8 text encoder.
    zimage = ('Comfy-Org/Z-Image-Turbo', '6fc90a3b1b653e935a0d175e260736de25b84df5')
    zimage_turbo = component(
        id='image.model.z-image-turbo', module='image', kind='model', group='Image', title='Z-Image Turbo (INT8)',
        description='Fast text-to-image only: no editing and no reference images.', license='Apache-2.0',
        requires=['image.runtime'], files=[
            hf_file(*zimage, 'split_files/diffusion_models/z_image_turbo_int8_convrot.safetensors', 'Models/diffusion_models/z_image_turbo_int8_convrot.safetensors'),
            hf_file(*zimage, 'split_files/text_encoders/qwen_3_4b_fp8_mixed.safetensors', 'Models/text_encoders/qwen_3_4b_fp8_mixed.safetensors'),
            hf_file(*zimage, 'split_files/vae/ae.safetensors', 'Models/vae/ae.safetensors')])
    # Gated repository: the listing is public but the weights need accepted terms and an HF_TOKEN at download time.
    flux3 = ('black-forest-labs/flux-3-image-klein-comfy', 'feef60b0aaf63a4773ace96636ebbcf43b881312')
    flux3_klein = None if skip_gated else component(
        id='image.model.flux3-klein', module='image', kind='model', group='Image', title='FLUX.3 Klein distilled (INT8) with prompt upsampler',
        description='Text-to-image, single-image editing and editing with a reference image; the official Qwen3.5 prompt upsampler writes the detailed caption.',
        license='FLUX Kommunity License v1.0 (model); Apache-2.0 (text encoder, upsampler)',
        note='LICENSE CHECK: non-commercial use, or commercial use of outputs only as a Qualifying User (gross annualized revenue under US$5M) with content filtering or output review. '
             'Accept the terms of black-forest-labs/flux-3-image-klein-comfy on Hugging Face and give the installer a read token as HF_TOKEN. Read Models/Image/FLUX3-Klein/LICENSE.md.',
        requires=['image.runtime'], files=[
            hf_file(*flux3, 'diffusion_models/flux3-image-klein-int8-convrot.safetensors', 'Models/diffusion_models/flux3-image-klein-int8-convrot.safetensors'),
            hf_file(*flux3, 'text_encoders/qwen3vl_4b_int8_convrot.safetensors', 'Models/text_encoders/qwen3vl_4b_int8_convrot.safetensors'),
            hf_file(*flux3, 'text_encoders/qwen3.5_9b_flux3_prompt_upsampler_int8_convrot.safetensors', 'Models/text_encoders/qwen3.5_9b_flux3_prompt_upsampler_int8_convrot.safetensors'),
            hf_file(*flux3, 'vae/flux2-vae.safetensors', 'Models/vae/flux2-vae.safetensors'),
            hf_file(*flux3, 'LICENSE.md', 'Models/Image/FLUX3-Klein/LICENSE.md')])
    upscale = component(
        id='image.model.upscale', module='image', kind='model', group='Image', title='Real-ESRGAN x2 (upscaling)',
        description='Separate 2x/4x picture upscaling.', license='BSD-3-Clause (Real-ESRGAN)', requires=['image.runtime'],
        files=[dict(path='Models/upscale_models/RealESRGAN_x2plus.pth', bytes=67061725, sha256='49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb',
                    urls=['https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth'])])
    return [runtime, edit, quantized, zimage_turbo] + ([flux3_klein] if flux3_klein else []) + [upscale, community] + build_zimage_addons() + build_qwen_lightning() + build_qwen21_enhancers()


def build_qwen_lightning():
    # Keep the step-distilled adapter independent: its absence must never disable standard 40-step editing.
    revision = '107d238d68b8387e32843d0b4a2e24ceb0d3ad86'
    filename = 'Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors'
    return [component(id='image.model.qwen-edit-lightning', module='image', kind='model', group='Image',
        title='Qwen Image Edit 2511 Lightning (4 steps)', description='Optional 4-step editing profile at CFG 1; negative prompts are unused.',
        license='Apache-2.0', requires=['image.runtime', 'image.model.qwen-edit-2511'], files=[dict(
            path='Models/loras/' + filename, bytes=849608296,
            sha256='22226e8d05d354bb356627d428809f5afd7819399b077238a2b70a82883a904f',
            urls=['https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/' + revision + '/' + filename])])]


def build_qwen21_enhancers():
    """Separate pinned T2I/I2I rewriters; neither is required for the installed base model."""
    revision = '0217efd1d9c9452e84f6448903d6e21bfa171ffd'
    hashes = {'t2i':'9182abae56fe05459840a86d22abd21f972061c92fce032630af680c8c5178d3',
              'i2i':'32707d01b427e488af252b95c551989aad59f9fec611a694f5db6bde7f0f1f6c'}
    result = []
    for task, digest in hashes.items():
        name = 'qwen3.5_9b_qwen_image_2.1_pe_' + task + '.int8_convrot.safetensors'
        result.append(component(id='image.model.qwen21-pe-' + task, module='image', kind='model', group='Image',
            title='Qwen Image 2.1 prompt enhancer (' + task.upper() + ')',
            description='Optional official prompt rewriting for ' + ('text generation.' if task == 't2i' else 'image editing.'),
            license='Qwen Research', requires=['image.runtime','image.model.qwen-image-21-uc'],
            files=[dict(path='Models/text_encoders/' + name, bytes=9471072252, sha256=digest,
                        urls=['https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/' + revision + '/text_encoders/' + name])]))
    return result


def build_zimage_addons():
    """Optional distilled Lite patches: installing Turbo alone must not pull another four GB."""
    repo = 'alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1'
    revision = '5155fc56d17821007d6f62ac192c09e0f0e72016'
    variants = [
        ('union', 'Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite-2602-8steps.safetensors',
         'Z-Image Turbo: ControlNet Union Lite', 'Guided image generation and masked inpainting; accepts Canny, pose, depth, scribble and gray maps.'),
        ('tile', 'Z-Image-Turbo-Fun-Controlnet-Tile-2.1-lite-2601-8steps.safetensors',
         'Z-Image Turbo: ControlNet Tile Lite', 'Image-conditioned enlargement and detail refinement.')]
    return [component(id='image.model.z-image-controlnet-' + ident, module='image', kind='model', group='Image', title=title,
                      description=description, license='Apache-2.0', requires=['image.runtime', 'image.model.z-image-turbo'],
                      files=[hf_file(repo, revision, filename, 'Models/model_patches/' + filename)])
            for ident, filename, title, description in variants]


# ---- Video ---------------------------------------------------------------------------------------------------------
def patched_files(installed):
    names = set()
    folder = installed / 'Runtime/Video/vendor/vdn/diffusers_patches'
    for patch in sorted(folder.glob('*.patch')):
        for line in patch.read_text(encoding='utf-8', errors='replace').splitlines():
            if line.startswith('+++ b/'):
                names.add(line[6:].strip())
    return ['Runtime/Video/vendor/vdn/diffusers/' + n for n in sorted(names)], ['Runtime/Video/vendor/vdn/diffusers_patches/' + p.name for p in sorted(folder.glob('*.patch'))]


def build_video(installed, work):
    lock = read_json(REPO / 'docs/video-dependency-lock.json')
    voice = read_json(installed / 'Runtime/Video/voice-dependency-lock.json')
    downloads, wheels = [], []
    for artifact in lock['artifacts']:
        path = artifact['path'].replace('\\', '/')
        name = unquote_name(artifact['url'])
        if path.startswith('video-cache/wheels/'):
            downloads.append(download('wheel:' + name, artifact['url'], name, artifact['size_bytes'], artifact['sha256']))
            wheels.append('wheel:' + name)
        elif path.startswith('video-cache/runtime/') and name.endswith('.tar.gz'):
            downloads.append(download('python', artifact['url'], name, artifact['size_bytes'], artifact['sha256']))
        elif path.startswith('video-cache/runtime/') and name.endswith('.zip'):
            downloads.append(download('ffmpeg', artifact['url'], name, artifact['size_bytes'], artifact['sha256']))
        elif path.startswith('video-cache/source/'):
            local = path.rsplit('/', 1)[-1]
            key = re.match(r'(vdn|diffusers|ComfyUI|antlr4)', local).group(1)
            ident = dict(vdn='src:vdn', diffusers='src:diffusers', ComfyUI='src:comfyui', antlr4='sdist:antlr4')[key]
            filename = local
            downloads.append(download(ident, artifact['url'], filename, artifact['size_bytes'], artifact['sha256']))
        elif path == 'upstream/freevideo-v0.4.19.zip':
            downloads.append(download('src:freevideo', artifact['url'], 'freevideo-v0.4.19.zip', artifact['size_bytes'], artifact['sha256']))
    voice_ids = []
    for wheel in voice['wheels']:
        name = unquote_name(wheel['url'])
        downloads.append(download('voice:' + name, wheel['url'], name, wheel['bytes'], wheel['sha256']))
        voice_ids.append('voice:' + name)
    toolchain = [i for i in wheels if re.match(r'wheel:(pip|setuptools|wheel)-', i)]
    rest = [i for i in wheels if i not in toolchain]
    changed, patches = patched_files(installed)
    python = 'Runtime/Video/python/python.exe'
    runtime = component(
        id='video.video_runtime', module='video', kind='software', title='Video runtime: Python 3.12, PyTorch (CUDA 13), FreeVideo, ComfyUI-native Wan, ffmpeg',
        description='What the Video section and MiniMax Music run on: an isolated Python with PyTorch and SageAttention, the pinned FreeVideo / VDN / ComfyUI sources, ffmpeg 9.0.2 and the voice packages.',
        license='Open-source components, each under its own license (see Support/Licenses)',
        present=['Runtime/Video/python/python.exe', 'Runtime/Video/ffmpeg/bin/ffmpeg.exe', 'Runtime/Video/ffmpeg/bin/ffprobe.exe', 'Runtime/Video/vendor/FreeVideo/pyproject.toml',
                 'Runtime/Video/vendor/vdn/pyproject.toml', 'Runtime/Video/vendor/ComfyUI/main.py', 'Runtime/Video/python/Lib/site-packages/torch/__init__.py',
                 'Runtime/Video/python/Lib/site-packages/sageattention/__init__.py', 'Runtime/Video/python/Lib/site-packages/diffusers/__init__.py',
                 'Runtime/Video/python/Lib/site-packages/freevideo_engine/__init__.py', 'Runtime/Video/voice-packages/qwen_tts/__init__.py'],
        disk_bytes=tree_size(installed / 'Runtime/Video'), downloads=downloads, steps=[
            dict(op='extract', download='python', to='Runtime/Video'),
            dict(op='extract', download='ffmpeg', to='Runtime/Video/ffmpeg', strip=1, skip=['bin/ffplay.exe']),
            dict(op='extract', download='src:freevideo', to='Runtime/Video/vendor/FreeVideo', strip=1),
            dict(op='extract', download='src:vdn', to='Runtime/Video/vendor/vdn', strip=1),
            dict(op='extract', download='src:diffusers', to='Runtime/Video/vendor/vdn/diffusers', strip=1),
            dict(op='extract', download='src:comfyui', to='Runtime/Video/vendor/ComfyUI', strip=1),
            dict(op='git_apply', cwd='Runtime/Video/vendor/vdn/diffusers', patches=patches),
            dict(op='verify_files', files=[dict(path=p, sha256=hashlib.sha256((installed / p).read_bytes()).hexdigest()) for p in changed]),
            dict(op='ensurepip', python=python),
            dict(op='pip_install', python=python, downloads=toolchain),
            dict(op='pip_install', python=python, downloads=rest + ['sdist:antlr4'], args=['--no-build-isolation']),
            dict(op='pip_install_source', python=python, path='Runtime/Video/vendor/vdn/diffusers'),
            dict(op='pip_install_source', python=python, path='Runtime/Video/vendor/FreeVideo'),
            dict(op='unpack_wheels', downloads=voice_ids, to='Runtime/Video/voice-packages', sdist_keep='sox/')])
    return [runtime] + video_models(installed, voice, work)


def video_models(installed, voice, work):
    h3 = read_json(REPO / 'docs/freevideo-model-catalog.json')
    h3_root = installed / 'Models/Video/H3'
    files, seen = [], {}
    for item in h3['files']:
        sha = item.get('sha256') or item['download_sha256']
        target = 'Models/Video/H3/' + item['file']
        entry = dict(path=target, bytes=item['bytes'], sha256=sha, urls=[item['url']])
        if target in seen:
            if seen[target]['sha256'] != sha:
                entry['path'] = 'Models/Video/H3/provenance/%s/%s/%s' % (item['repo'], item['revision'], item['file'])
                files.append(entry)
            continue
        seen[target] = entry
        files.append(entry)
    after = []
    for lock_name, directory in (('high-16-table-lock.json', 'adaln-tables-v2-8bc7926b84e3f4341037e3a7'), ('refine-3-table-lock.json', 'adaln-tables-v2-4beffd97ccb4abf3aa201df8')):
        base = h3_root / 'sampling-cache' / directory
        if lock_name.startswith('high'):
            for row in read_json(Path(work) / 'video-cache' / lock_name)['files']:
                files.append(dict(path='Models/Video/H3/sampling-cache/%s' % row['file'], bytes=row['bytes'], sha256=row['sha256'], urls=[row['url']]))
        else:
            after.append(dict(op='copy', **{'from': 'Models/Video/H3/community-sigma3-v1-20261005/' + directory, 'to': 'Models/Video/H3/sampling-cache/' + directory}))
        for receipt in sorted(base.glob('*.json')):
            if receipt.name != 'identity.json':
                after.append(dict(op='write_text', path='Models/Video/H3/sampling-cache/%s/%s' % (directory, receipt.name), content=receipt.read_text(encoding='utf-8')))
        after.append(dict(op='write_text', path='Models/Video/H3/sampling-cache/%s/identity.json' % directory, content=(base / 'identity.json').read_text(encoding='utf-8')))
        after.append(dict(op='write_text', path='Models/Video/H3/int8-convrot/cache/%s/identity.json' % directory,
                          content=(h3_root / 'int8-convrot/cache' / directory / 'identity.json').read_text(encoding='utf-8')))
    after.append(dict(op='copy', **{'from': 'Models/Video/H3/LICENSE', 'to': 'Models/Video/H3/provenance/OpenVDN/vdn-minimax-h3-edge/da37681f5a1f7015fdb63482142b865111b687d3/LICENSE'}))
    h3 = component(
        id='video.model.h3', module='video', kind='model', group='Video', title='FreeVideo H3 (MiniMax H3 INT8): text and image to video with sound',
        description='The main Video engine: 512x288 clips of 1.6 or 10 seconds with sound, up to three character references, speech and a 60-second multi-scene option.',
        license='Model: OpenVDN / MiniMax H3 license (text is downloaded into Models/Video/H3/LICENSE); code: Apache-2.0',
        note='LICENSE CHECK: the MiniMax H3 Community License limits its territory and excludes the European Union, the United Kingdom, South Korea and the United States. Read Models/Video/H3/LICENSE before using this model.',
        requires=['video.video_runtime'], files=files, after=after,
        present=['Models/Video/H3/int8-convrot/cache/root.safetensors', 'Models/Video/H3/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors', 'Models/Video/H3/h3-base/vae/config.json'])
    tts_repo = ('Qwen/Qwen3-TTS-12Hz-0.6B-Base', voice['models'][0]['revision'])
    tts = component(
        id='video.model.qwen3-tts', module='video', kind='model', group='Video', title='Qwen3-TTS 0.6B (character speech, voice cloning)',
        description='Speech for FreeVideo characters from a 2 to 30 second voice sample and typed lines.', license='Apache-2.0',
        requires=['video.video_runtime'], files=[hf_file(*tts_repo, row['file'], 'Models/Video/Qwen3TTS/' + row['file']) for row in voice['models']])

    def manifest_files(name, strip_prefix):
        rows = read_json(installed / 'Support/Diagnostics' / name)['entries']
        return [dict(path=row['path'].replace('\\', '/'), bytes=row['bytes'], sha256=row['sha256'], urls=[row['url']]) for row in rows if row['path'].replace('\\', '/').startswith(strip_prefix)]
    wan_all = manifest_files('WanDownloadManifest.json', 'Models/Video/')
    s2v_all = manifest_files('S2VDownloadManifest.json', 'Models/Video/')
    encoder = [f for f in wan_all if f['path'].endswith('umt5_xxl_fp8_e4m3fn_scaled.safetensors')]
    wan_only = [f for f in wan_all if f not in encoder]
    s2v_only = [f for f in s2v_all if f['path'].startswith('Models/Video/Wan22S2V14B/')]
    shared = component(
        id='video.model.wan-text-encoder', module='video', kind='model', group='Video', title='UMT5-XXL text encoder (shared by both Wan models)',
        description='Text encoder used by Wan 2.2 TI2V-5B and Wan 2.2 S2V-14B; downloaded once.', license='Apache-2.0', requires=['video.video_runtime'], files=encoder)
    wan = component(
        id='video.model.wan-ti2v-5b', module='video', kind='model', group='Video', title='Wan 2.2 TI2V-5B: text/image to video (silent)',
        description='Silent 832x480 video from text or from a first image (81 or 121 frames).', license='Apache-2.0',
        requires=['video.video_runtime', 'video.model.wan-text-encoder'], files=wan_only)
    s2v = component(
        id='video.model.wan-s2v-14b', module='video', kind='model', group='Video', title='Wan 2.2 S2V-14B: character video from a voice (experimental)',
        description='A character picture plus a voice sample (or finished speech) becomes a 512x512 talking video of up to 4.8 seconds.', license='Apache-2.0',
        requires=['video.video_runtime', 'video.model.wan-text-encoder'], files=s2v_only)
    return [h3, tts, shared, wan, s2v]


# ---- Music ---------------------------------------------------------------------------------------------------------
def build_music(installed):
    manifest = read_json(installed / 'Support/Diagnostics/MusicDownloadManifest.json')
    mini = read_json(installed / 'Support/Diagnostics/MiniMaxMusicInstallManifest.json')
    upstream = read_json(installed / 'Runtime/Music/upstream.json')
    entries = manifest['entries']
    by_pkg = {(norm(e['package']), e['version']): e for e in entries if 'package' in e}
    by_name = {unquote_name(e['url']): e for e in entries if 'package' not in e}
    downloads = []
    embed = by_name['python-3.12.10-embed-amd64.zip']
    downloads.append(download('python', embed['url'], 'python-3.12.10-embed-amd64.zip', embed['bytes'], embed['sha256']))
    pip_pin = next(line.split('==')[1] for line in (installed / 'Runtime/Music/requirements-lock.txt').read_text().split() if line.lower().startswith('pip=='))
    pip = pypi_wheel('pip', pip_pin)
    downloads.append(download('pip', pip['url'], pip['filename'], pip['bytes'], pip['sha256']))
    wheel_ids = []
    for line in (installed / 'Runtime/Music/requirements-lock.txt').read_text(encoding='utf-8').split():
        name, version = line.split('==')
        if norm(name) == 'pip':
            continue
        if (norm(name), version) in by_pkg:
            row = by_pkg[(norm(name), version)]
            info = dict(url=row['url'], filename=unquote_name(row['url']), bytes=row['bytes'], sha256=row['sha256'])
        elif any(unquote_name(u).startswith('%s-%s-' % (name, version)) for u in by_name):
            row = by_name[next(u for u in by_name if unquote_name(u).startswith('%s-%s-' % (name, version)))]
            info = dict(url=row['url'], filename=unquote_name(row['url']), bytes=row['bytes'], sha256=row['sha256'])
        else:
            info = pypi_wheel(name, version)
        downloads.append(download('wheel:' + info['filename'], info['url'], info['filename'], info['bytes'], info['sha256']))
        wheel_ids.append('wheel:' + info['filename'])
    source_url = 'https://codeload.github.com/ace-step/ACE-Step-1.5/zip/' + upstream['revision']
    data = http_bytes(source_url)
    downloads.append(download('src:acestep', source_url, 'ACE-Step-1.5-%s.zip' % upstream['revision'][:12], len(data), hashlib.sha256(data).hexdigest()))
    crt = read_json(installed / 'Runtime/Music/crt-provenance.json')
    downloads.append(download('vcredist', crt['resolved_source_url'], 'vc_redist.x64.exe', crt['package_bytes'], crt['package_sha256']))
    pth = (installed / 'Runtime/Music/python/python312._pth').read_bytes().decode('utf-8')
    python = 'Runtime/Music/python/python.exe'
    runtime = component(
        id='music.runtime', module='music', kind='software', title='Music runtime: Python 3.12 (embeddable), PyTorch CUDA 12.8, ACE-Step 1.5 code',
        description='Isolated Windows Python with PyTorch and the pinned ACE-Step 1.5 sources, plus the Microsoft C++ runtime DLLs unpacked from the signed official package (no system-wide install).',
        license='MIT (ACE-Step); Python PSF; Microsoft VC++ runtime redistributable terms',
        present=[python, 'Runtime/Music/python/msvcp140.dll', 'Runtime/Music/vendor/ACE-Step/acestep/handler.py', 'Runtime/Music/requirements-lock.txt', 'Runtime/Music/python/Lib/site-packages/torch/__init__.py'],
        disk_bytes=tree_size(installed / 'Runtime/Music'), downloads=downloads, steps=[
            dict(op='extract', download='python', to='Runtime/Music/python'),
            dict(op='write_text', path='Runtime/Music/python/python312._pth', content=pth),
            dict(op='pip_bootstrap', python=python, download='pip'),
            dict(op='pip_install', python=python, downloads=wheel_ids),
            dict(op='extract', download='src:acestep', to='Runtime/Music/vendor/ACE-Step', strip=1,
                 keep=['acestep/*', 'LICENSE', 'README.md', 'pyproject.toml'], skip=['*/__pycache__/*', '*_test.py', '*/test_*.py']),
            dict(op='crt_from_vcredist', download='vcredist', to='Runtime/Music/python', files=[dict(name=f['name'], bytes=f['bytes'], sha256=f['sha256']) for f in crt['files']],
                 license=dict(name='MSVC-LICENSE.rtf', sha256=hashlib.sha256((installed / 'Runtime/Music/MSVC-LICENSE.rtf').read_bytes()).hexdigest())),
            dict(op='write_text', path='Runtime/Music/requirements-lock.txt', content=(installed / 'Runtime/Music/requirements-lock.txt').read_text(encoding='utf-8'))])
    ace_rows = [e for e in entries if 'huggingface.co' in e['url'] and '/resolve/' in e['url']]
    ace_files = [dict(path=e['path'].replace('\\', '/'), bytes=e['bytes'], sha256=e['sha256'], urls=[e['url']]) for e in ace_rows]
    # Two model-code files are replaced by the ones of the pinned ACE-Step sources (see `after`), so they are not downloaded.
    ace_files = [f for f in ace_files if not re.search(r'acestep-v15-turbo/(configuration_acestep_v15|modeling_acestep_v15_turbo)\.py$', f['path'])]
    ace = component(
        id='music.model.ace-step', module='music', kind='model', group='Music', title='ACE-Step 1.5 Turbo (2B) with the 0.6B planner',
        description='Songs from lyrics or instrumentals, 10 to 180 seconds, 48 kHz stereo WAV.', license='MIT',
        requires=['music.runtime'], files=ace_files,
        after=[dict(op='copy_glob', **{'from': 'Runtime/Music/vendor/ACE-Step/acestep/models/turbo', 'to': 'Models/Music/ACE-Step-1.5/acestep-v15-turbo', 'pattern': '*.py'})],
        present=['Models/Music/ACE-Step-1.5/acestep-v15-turbo/modeling_acestep_v15_turbo.py'])
    mini_files = [dict(path=e['path'].replace('\\', '/'), bytes=e['bytes'], sha256=e['sha256'], urls=[e['url']]) for e in mini['downloads']['entries']]
    minimax = component(
        id='music.model.minimax-music3', module='music', kind='model', group='Music', title='MiniMax Music3 INT8 (songs with vocals)',
        description='A second Music engine: 32 kHz stereo WAV, up to 60 seconds. Runs on the Video runtime.', license='MiniMax Music3 Community License (read the LICENSE file)',
        note='Read the LICENSE file that is downloaded with the model: it is a community license with its own conditions.',
        requires=['video.video_runtime'], files=mini_files)
    # Public package of Comfy-Org (no token needed) plus the public Stability AI license text; the Gemma Terms file is gated and only linked.
    sa3_package = ('Comfy-Org/stable-audio-3', '75aad8836271cb5a5123fb58304fae5d3bb4b0f9')
    sa3_license = ('stabilityai/stable-audio-3-medium', 'd4d3579395a8ac0df4ab13b5b1d7138ce7290f81')
    stable_audio = component(
        id='music.model.stable-audio-3', module='music', kind='model', group='Music', title='Stable Audio 3 Medium (music and sound effects)',
        description='Music and sound effects from an English description: 44.1 kHz stereo WAV, 10 to 120 seconds. Runs in the Image ComfyUI.',
        license='Stability AI Community License; Gemma Terms of Use (text encoder)',
        note='LICENSE CHECK: the Stability AI Community License limits commercial use (see Models/Music/StableAudio3/LICENSE.md and stability.ai/license); '
             'the T5Gemma text encoder is under the Gemma Terms of Use, https://ai.google.dev/gemma/terms. Using the model means accepting both; the package itself is public and needs no token.',
        requires=['image.runtime'], files=[
            hf_file(*sa3_package, 'checkpoints/stable_audio_3_medium.safetensors', 'Models/checkpoints/stable_audio_3_medium.safetensors'),
            hf_file(*sa3_package, 'text_encoders/t5gemma_b_b_ul2.safetensors', 'Models/text_encoders/t5gemma_b_b_ul2.safetensors'),
            hf_file(*sa3_license, 'LICENSE.md', 'Models/Music/StableAudio3/LICENSE.md')])
    return [runtime, ace, minimax, stable_audio]


# ---- Text ----------------------------------------------------------------------------------------------------------
def build_text(installed):
    ollama = github_asset('ollama/ollama', 'v0.33.3', 'ollama-windows-amd64.zip')
    runtime = component(
        id='text.runtime', module='text', kind='software', title='Ollama 0.33.3 (private copy for the Text section)',
        description='A separate copy of Ollama used only by VisualForge (own port, own model folder). It never touches an Ollama you installed yourself. AMD ROCm libraries are left out.',
        license='MIT (Ollama) and bundled libraries', present=['Runtime/Text/ollama/ollama.exe'], disk_bytes=tree_size(installed / 'Runtime/Text'),
        downloads=[download('ollama', ollama['url'], ollama['filename'], ollama['bytes'], ollama['sha256'])],
        steps=[dict(op='extract', download='ollama', to='Runtime/Text/ollama', skip=['lib/ollama/rocm/*'])])
    opencode = github_asset('anomalyco/opencode', 'v1.19.4', 'opencode-windows-x64.zip')
    agent = component(
        id='text.agent.opencode', module='text', kind='software', title='OpenCode 1.19.4 (agent mode of the Text section)',
        description='Coding and git agent that works in a chosen folder through the private Ollama model. Runs offline; edits and commands follow the permission level of the chat, git push is always refused.',
        license='MIT (OpenCode)', requires=['text.runtime'], present=['Runtime/Agents/opencode/opencode.exe'], disk_bytes=tree_size(installed / 'Runtime/Agents/opencode'),
        downloads=[download('opencode', opencode['url'], opencode['filename'], opencode['bytes'], opencode['sha256'])],
        steps=[dict(op='extract', download='opencode', to='Runtime/Agents/opencode')])
    models = []
    for tag, title, size, note in (
            ('gemma4:e2b', 'Gemma 4 E2B (small, vision)', 4.59e9, 'Fits comfortably on 8 GB cards.'),
            ('gemma4:e4b', 'Gemma 4 E4B (recommended, vision)', 6.58e9, 'Good quality for 12-16 GB cards.'),
            ('gemma4:26b', 'Gemma 4 26B (large, vision)', 18.73e9, 'Needs about 16 GB of video memory; the biggest option.')):
        model, _, variant = tag.partition(':')
        models.append(component(
            id='text.model.' + tag.replace(':', '-'), module='text', kind='model', group='Text', title=title, description=note,
            license='Gemma terms of use / Apache-2.0 (see the model license shown by Ollama)', requires=['text.runtime'], download_bytes=int(size), disk_bytes=int(size),
            present=['Models/Text/ollama/manifests/registry.ollama.ai/library/%s/%s' % (model, variant)], after=[dict(op='ollama_pull', model=tag)]))
    return [runtime, agent, build_aider(installed), build_dsh(installed)] + models


def build_dsh(installed):
    """DeepSeek Harness: portable Node.js plus the npm tarballs of the resolved win32-x64 tree, pinned in App/config/dsh-assets.json.

    Each tarball is unpacked to its own node_modules path of the lockfile (npm's `package/` root stripped); install scripts are
    not run: on Windows they only rebuild native modules that ship prebuilt (node-pty, koffi) or do nothing."""
    assets = read_json(REPO / 'App/config/dsh-assets.json')
    base = 'Runtime/Agents/dsh'
    node = assets['node']
    downloads = [download('node', node['urls'], node['filename'], node['bytes'], node['sha256'])]
    steps = [dict(op='extract', download='node', to=base + '/node', strip=1, keep=['node.exe', 'LICENSE']),
             dict(op='write_text', path=base + '/app/package.json',
                  content='{"name": "visualforge-dsh", "private": true, "dependencies": {"%s": "%s"}}\r\n' % (assets['package'], assets['version']))]
    for item in assets['packages']:
        ident = 'npm:' + item['path']
        downloads.append(download(ident, item['url'], item['path'].replace('/', '+') + '-' + item['version'] + '.tgz', item['bytes'], item['sha256']))
        steps.append(dict(op='extract', download=ident, to='%s/app/%s' % (base, item['path']), strip=1))
    return component(
        id='text.agent.dsh', module='text', kind='software', title='DeepSeek Harness %s (agent mode of the Text section)' % assets['version'],
        description='DeepSeek\'s plugin-based coding agent (developer preview) with its own Node.js %s, talking to the private Ollama model. '
                    'Runs offline: DeepSeek cloud models, web search and telemetry are switched off; edits and commands follow the permission '
                    'level of the chat, git push is always refused.' % node['version'],
        license='MIT (DeepSeek Harness, Node.js); each npm package its own license', requires=['text.runtime'],
        present=[base + '/node/node.exe', base + '/app/package.json', base + '/app/node_modules/@deepseek-ai/dsh/lib/bin.js'],
        disk_bytes=tree_size(installed / base), downloads=downloads, steps=steps)


def build_aider(installed):
    """Aider in its own embeddable Python; the wheel set was resolved once for cp312/win_amd64 and is pinned in App/config/aider-assets.json."""
    assets = read_json(REPO / 'App/config/aider-assets.json')
    base, python = 'Runtime/Agents/aider', 'Runtime/Agents/aider/python/python.exe'
    downloads = [download('python', assets['python']['urls'], assets['python']['filename'], assets['python']['bytes'], assets['python']['sha256']),
                 download('pip', assets['pip']['urls'], assets['pip']['filename'], assets['pip']['bytes'], assets['pip']['sha256'])]
    downloads += [download('wheel:' + w['filename'], w['url'], w['filename'], w['bytes'], w['sha256']) for w in assets['wheels']]
    lock = ''.join('%s==%s\r\n' % (w['name'], w['version']) for w in assets['wheels'])
    return component(
        id='text.agent.aider', module='text', kind='software', title='Aider %s (agent mode of the Text section)' % assets['version'],
        description='Coding agent that edits files of a chosen git folder through the private Ollama model, with its own Python 3.12. Runs offline; '
                    'edits and commands follow the permission level of the chat, git push is always refused.',
        license='Apache-2.0 (Aider); Python PSF; each package its own license', requires=['text.runtime'],
        present=[python, base + '/python/Lib/site-packages/aider/__init__.py', base + '/requirements-lock.txt'],
        disk_bytes=tree_size(installed / base), downloads=downloads, steps=[
            dict(op='extract', download='python', to=base + '/python'),
            dict(op='write_text', path=base + '/python/python312._pth', content='python312.zip\r\n.\r\nLib/site-packages\r\nimport site\r\n'),
            dict(op='pip_bootstrap', python=python, download='pip'),
            dict(op='pip_install', python=python, downloads=['wheel:' + w['filename'] for w in assets['wheels']]),
            dict(op='write_text', path=base + '/requirements-lock.txt', content=lock)])


# ---- Speech (voice input) ------------------------------------------------------------------------------------------
SPEECH_FILES = ('config.json', 'generation_config.json', 'model.safetensors', 'preprocessor_config.json', 'tokenizer.json', 'tokenizer_config.json',
                'vocab.json', 'merges.txt', 'normalizer.json', 'added_tokens.json', 'special_tokens_map.json')


def build_speech():
    """Whisper models for voice input; they run in the Music runtime (PyTorch + transformers are already there)."""
    rows = [
        ('turbo', 'openai/whisper-large-v3-turbo', '41f01f3fe87f28c78e2fbf8b568835947dd65ed9', 'Whisper large-v3-turbo (recommended)',
         'Fast and accurate speech recognition for Russian and about 90 other languages, used by the microphone buttons. About 1.6 GB.', 'MIT'),
        ('small', 'openai/whisper-small', '973afd24965f72e36ca33b3055d56a652f456b4d', 'Whisper small (light)',
         'Smaller and faster, good for a processor-only computer; less accurate with names and noise. About 1 GB.', 'Apache-2.0'),
        ('large-v3', 'openai/whisper-large-v3', '06f233fe06e710322aca913c1bc4249a0d71fce1', 'Whisper large-v3 (most accurate)',
         'The most accurate of the three, slower and heavier (about 3.1 GB); best with a video card.', 'Apache-2.0')]
    components = []
    for key, repo, revision, title, description, license_name in rows:
        files = [hf_file(repo, revision, name, 'Models/Speech/whisper-%s/%s' % (key, name)) for name in SPEECH_FILES]
        components.append(component(id='speech.model.whisper-' + key, module='speech', kind='model', group='Speech', title=title, description=description,
                                    license=license_name, requires=['music.runtime'], files=files))
    punct_files = ('config.json', 'pytorch_model.bin', 'special_tokens_map.json', 'tokenizer.json', 'tokenizer_config.json', 'vocab.txt')
    punct = ('RUPunct/RUPunct_big', 'd05f73afd84b57a45b83238c35b866bc625fe247')
    components.append(component(
        id='speech.model.rupunct-big', module='speech', kind='model', group='Speech', title='RUPunct big (punctuation and capitals for Russian)',
        description='Turns "доктор пеппер вертолет летает" into a properly written sentence: capital letters, commas, full stops, question marks. Used automatically after recognition when installed. About 0.7 GB.',
        license='MIT', requires=['music.runtime'], files=[hf_file(*punct, name, 'Models/Speech/rupunct-big/' + name) for name in punct_files]))
    return components


MODULES = [
    dict(id='image', title='Image', summary='Image generation and editing with Qwen, Z-Image and FLUX.3 (ComfyUI).'),
    dict(id='video', title='Video', summary='FreeVideo H3, Wan 2.2 and character speech; also provides export and the sound mixer.'),
    dict(id='music', title='Music', summary='ACE-Step 1.5, MiniMax Music3 and Stable Audio 3.'),
    dict(id='text', title='Text', summary='Private chat model through a separate Ollama, plus the OpenCode, Aider and DeepSeek Harness agent modes.'),
    dict(id='speech', title='Voice input', summary='Speech recognition for the microphone buttons in every text field.'),
    dict(id='voiceover', title='Voiceover', summary='Text to speech of long texts with a cloned voice; uses the Video runtime and the Qwen3-TTS model of the Video section, VoxCPM2 is an optional second engine.'),
]

PRESETS = {
    'software-only': dict(title='Only the programs, no models (download models later)', models=[]),
    'light': dict(title='Light: smaller models of every section', models=[
        'image.model.qwen-image-21-uc', 'image.model.upscale', 'video.model.wan-ti2v-5b', 'video.model.wan-text-encoder', 'music.model.ace-step', 'text.model.gemma4-e2b', 'speech.model.whisper-small', 'speech.model.rupunct-big']),
    'everything': dict(title='Everything (about 160 GB)', models='all'),
}


def build_ltx25():
    manifest=read_json(REPO/'App/config/ltx25-assets.json')
    files=[dict(entry,path='Models/Video/LTX25/'+entry['path']) for entry in manifest['files']]
    size=sum(entry['bytes'] for entry in files)
    return component(**manifest['component'],files=files,download_bytes=size,disk_bytes=size)


def build_voiceover():
    """The Voiceover components pinned by tools/build_voiceover_assets.py."""
    return read_json(REPO / 'App/config/voiceover-assets.json')['components']


def build_all(installed, work, skip_gated=False):
    components = (build_core() + build_image(installed, skip_gated) + build_video(installed, work) + build_music(installed) + build_text(installed)
                  + build_speech() + [build_ltx25()] + build_voiceover())
    modules = []
    for module in MODULES:
        ids = [c['id'] for c in components if c['module'] == module['id']]
        modules.append(dict(module, components=ids))
    core = [c['id'] for c in components if c['module'] == 'core']
    modules.insert(0, dict(id='core', title='Tools', summary='Small helper programs.', components=core))
    return dict(schema_version=1, catalog_version=CATALOG_VERSION, modules=modules, presets=PRESETS, components=components)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--installed', required=True, help='a complete reference installation to read manifests from')
    parser.add_argument('--work', required=True, help='folder holding video-cache/high-16-table-lock.json')
    parser.add_argument('--out', default=str(REPO / 'App/config/component-catalog.json'))
    parser.add_argument('--skip-gated', action='store_true', help='leave out components whose weights sit in a gated repository (their digests need HF_TOKEN)')
    arguments = parser.parse_args(argv)
    catalog = build_all(Path(arguments.installed), Path(arguments.work), arguments.skip_gated)
    sys.path.insert(0, str(REPO / 'App/server'))
    from provision import component_catalog as catalog_module
    from provision.download_sources import enrich
    enrich(catalog)
    catalog_module.validate(catalog)
    Path(arguments.out).write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    total = sum(sum(f['bytes'] for f in c.get('files', [])) + sum(d['bytes'] for d in c.get('downloads', [])) + c.get('download_bytes', 0) for c in catalog['components'])
    print('%d components, %.1f GB to download in total, written to %s' % (len(catalog['components']), total / 1e9, arguments.out))


if __name__ == '__main__':
    main()
