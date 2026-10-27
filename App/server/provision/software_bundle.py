"""Optional GitHub Release mirror for pinned software artifacts up to 300 MB."""
import json
from pathlib import Path
from urllib.parse import quote

MAX_BYTES = 300_000_000


def asset_name(name, sha256):
    return sha256 + '-' + Path(name).name


def bundled_source(root, component, name, size, sha256):
    if not component or component['kind'] != 'software' or not 0 < size <= MAX_BYTES:
        return None
    path = Path(root) / 'App/config/software-release.json'
    if not path.is_file():
        return None
    config = json.loads(path.read_text('utf-8-sig'))
    # A prepared release is inactive until its assets have actually been published and verified.
    if config.get('published') is not True:
        return None
    base = config['url'].rstrip('/')
    if not base.startswith('https://github.com/') or '/releases/download/' not in base:
        raise ValueError('Invalid software release URL')
    return base + '/' + quote(asset_name(name, sha256))
