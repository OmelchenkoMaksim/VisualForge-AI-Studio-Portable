"""The component catalog: what can be installed, where each piece comes from and how to check that it is present.

A *component* is either
  kind "software": the programs around the models (runtimes, libraries, ffmpeg...). Always installed.
  kind "model":    neural-network weights (large, optional, can be downloaded later).

    {"id": "video.video_runtime", "module": "video", "kind": "software", "title": ..., "description": ...,
     "requires": [ids], "download_bytes": n, "disk_bytes": n, "license": "...",
     "present": ["Runtime/Video/python/python.exe", ...],      # all must exist -> installed
     "downloads": [{"id", "urls": [...], "filename", "bytes", "sha256"}],   # fetched into the download cache
     "steps": [{"op": "extract", ...}, ...],                    # see install_steps.py
     "files": [{"path", "bytes", "sha256", "urls": [...]}],     # models: fetched straight to their final place
     "after": [steps]}                                          # models: steps run after the files

Paths are relative to the installation root and always use "/".
"""
import json
from pathlib import Path, PurePosixPath

SCHEMA = 1
KINDS = ('software', 'model')
FILE_KEYS = ('path', 'bytes', 'sha256', 'urls')
DOWNLOAD_KEYS = ('id', 'urls', 'filename', 'bytes', 'sha256')


class CatalogError(ValueError):
    pass


def safe_relative(path, what='path'):
    text = str(path).replace('\\', '/')
    parts = PurePosixPath(text).parts
    if not text or text.startswith('/') or ':' in text or '..' in parts or parts[0] in ('', '.'):
        raise CatalogError('Unsafe %s in catalog: %r' % (what, path))
    return text


def load(path):
    catalog = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    validate(catalog)
    return catalog


def validate(catalog):
    if catalog.get('schema_version') != SCHEMA:
        raise CatalogError('Unsupported catalog schema')
    ids = set()
    for component in catalog['components']:
        ident = component['id']
        if ident in ids:
            raise CatalogError('Duplicate component id: ' + ident)
        ids.add(ident)
        if component['kind'] not in KINDS:
            raise CatalogError('Bad kind for ' + ident)
        for path in component.get('present', []):
            safe_relative(path, ident + ' present')
        for item in component.get('files', []):
            if not set(FILE_KEYS) <= set(item):
                raise CatalogError('File entry of %s lacks %s' % (ident, sorted(set(FILE_KEYS) - set(item))))
            safe_relative(item['path'], ident + ' file')
            if not (isinstance(item['bytes'], int) and item['bytes'] >= 0 and len(item['sha256']) == 64 and item['urls']):
                raise CatalogError('Bad file entry in %s: %s' % (ident, item['path']))
        seen = set()
        for item in component.get('downloads', []):
            if not set(DOWNLOAD_KEYS) <= set(item) or item['id'] in seen or len(item['sha256']) != 64 or not item['urls']:
                raise CatalogError('Bad download entry in %s: %s' % (ident, item.get('id')))
            seen.add(item['id'])
        for step in list(component.get('steps', [])) + list(component.get('after', [])):
            if 'op' not in step:
                raise CatalogError('Step without op in ' + ident)
            if 'download' in step and step['download'] not in seen:
                raise CatalogError('Step of %s refers to an unknown download %s' % (ident, step['download']))
    for component in catalog['components']:
        for required in component.get('requires', []):
            if required not in ids:
                raise CatalogError('%s requires unknown %s' % (component['id'], required))
    for module in catalog['modules']:
        for ident in module.get('components', []):
            if ident not in ids:
                raise CatalogError('Module %s lists unknown %s' % (module['id'], ident))
    return catalog


def index(catalog):
    return {component['id']: component for component in catalog['components']}


def closure(catalog, wanted):
    """The wanted component ids plus everything they require, in installation order (requirements first)."""
    by_id = index(catalog)
    order, visiting = [], set()

    def visit(ident):
        if ident in order:
            return
        if ident not in by_id:
            raise CatalogError('Unknown component: ' + ident)
        if ident in visiting:
            raise CatalogError('Requirement cycle at ' + ident)
        visiting.add(ident)
        for required in by_id[ident].get('requires', []):
            visit(required)
        visiting.discard(ident)
        order.append(ident)

    for ident in wanted:
        visit(ident)
    return order


def download_bytes(component):
    if 'download_bytes' in component:
        return component['download_bytes']
    return sum(item['bytes'] for item in component.get('downloads', [])) + sum(item['bytes'] for item in component.get('files', []))
