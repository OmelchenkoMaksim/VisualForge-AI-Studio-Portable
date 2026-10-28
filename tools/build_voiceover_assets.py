"""Pins what the Voiceover engines need that the Video bundle does not already provide, and adds it to the component catalog.

Writes App/config/voiceover-assets.json (wheel URLs and hashes from PyPI, model file sizes and hashes from Hugging Face) and, with
--patch-catalog, merges the two components it describes into App/config/components.json without regenerating the whole catalog
(tools/build_catalog.py needs a complete reference installation for that). `tools/build_catalog.py` reads the same manifest, so a full
regeneration keeps them.

  voiceover.runtime        the voxcpm package and its few extra dependencies, unpacked next to the Qwen3-TTS packages
  voiceover.model.voxcpm2  the VoxCPM2 weights (Apache-2.0), about 5 GB
"""
import argparse
import json
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from build_catalog import REPO, component, download, hf_file, pypi_wheel  # noqa: E402

MANIFEST = REPO / 'App/config/voiceover-assets.json'
PACKAGES = (('voxcpm', '2.0.3'), ('pydantic', '2.14.1'), ('pydantic-core', '2.50.1'), ('annotated-types', '0.8.0'),
            ('typing-inspection', '0.4.4'), ('typing-extensions', '4.16.0'))
MODEL = ('openbmb/VoxCPM2', '32279effe8c19989596f05d353d1447f51d9e915')
MODEL_FILES = ('config.json', 'model.safetensors', 'audiovae.pth', 'special_tokens_map.json', 'tokenization_voxcpm2.py', 'tokenizer.json',
               'tokenizer_config.json', 'README.md')
MODULE = dict(id='voiceover', title='Voiceover', summary='Text to speech of long texts with a cloned voice; uses the Video runtime and the Qwen3-TTS model of the Video section, VoxCPM2 is an optional second engine.')


def build():
    wheels = []
    for name, version in PACKAGES:
        wheel = pypi_wheel(name, version)
        wheels.append(download('voiceover:' + wheel['filename'], wheel['url'], wheel['filename'], wheel['bytes'], wheel['sha256']))
    runtime = component(
        id='voiceover.runtime', module='voiceover', kind='software', title='Voiceover: VoxCPM2 packages',
        description='The voxcpm package (pinned 2.0.3) and its extra dependencies, installed beside the Qwen3-TTS packages in the Video runtime. '
                    'It borrows PyTorch, librosa and transformers from the Video runtime.',
        license='Apache-2.0 (voxcpm); pydantic and the others are MIT', requires=['video.runtime'],
        present=['Runtime/Video/voxcpm-packages/voxcpm/__init__.py', 'Runtime/Video/voxcpm-packages/pydantic/__init__.py'],
        disk_bytes=sum(item['bytes'] for item in wheels) * 3, downloads=wheels,
        steps=[dict(op='unpack_wheels', downloads=[item['id'] for item in wheels], to='Runtime/Video/voxcpm-packages')])
    files = [hf_file(MODEL[0], MODEL[1], name, 'Models/Voiceover/VoxCPM2/' + name) for name in MODEL_FILES]
    model = component(
        id='voiceover.model.voxcpm2', module='voiceover', kind='model', group='Voiceover', title='VoxCPM2 (48 kHz voice cloning, 30 languages)',
        description='Second Voiceover engine: 2B parameters, 48 kHz output, Russian among 30 languages, cloning from a short sample (more exact with its transcript). '
                    'About 8 GB of video memory; voices fragments one at a time.',
        license='Apache-2.0', requires=['voiceover.runtime'], files=files,
        present=['Models/Voiceover/VoxCPM2/model.safetensors', 'Models/Voiceover/VoxCPM2/audiovae.pth'])
    return dict(repository=MODEL[0], revision=MODEL[1], module=MODULE, components=[runtime, model])


def patch_catalog(manifest):
    path = REPO / 'App/config/components.json'
    catalog = json.loads(path.read_text(encoding='utf-8'))
    catalog['components'] = [row for row in catalog['components'] if not row['id'].startswith('voiceover.')] + manifest['components']
    catalog['modules'] = [row for row in catalog['modules'] if row['id'] != 'voiceover']
    catalog['modules'].append(dict(manifest['module'], components=[row['id'] for row in catalog['components'] if row['module'] == 'voiceover']))
    sys.path.insert(0, str(REPO / 'App/server'))
    from provision import catalog as catalog_module
    from provision.sources import enrich
    enrich(catalog)
    catalog_module.validate(catalog)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('catalog patched:', len(catalog['components']), 'components')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--patch-catalog', action='store_true', help='also merge the components into App/config/components.json')
    arguments = parser.parse_args()
    manifest = build()
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('written', MANIFEST, '-', sum(len(row.get('files', [])) + len(row.get('downloads', [])) for row in manifest['components']), 'files')
    if arguments.patch_catalog:
        patch_catalog(manifest)


if __name__ == '__main__':
    main()
