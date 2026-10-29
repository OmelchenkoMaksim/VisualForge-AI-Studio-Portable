"""Alternative publisher endpoints for the same pinned bytes; never change revisions or checksums."""
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def alternatives(url):
    parts = urlsplit(url)
    host, path = parts.netloc.lower(), parts.path
    hosts = {'huggingface.co': 'hf.co', 'files.pythonhosted.org': 'pypi.org',
             'download.pytorch.org': 'download-r2.pytorch.org', 'download-r2.pytorch.org': 'download.pytorch.org',
             'www.python.org': 'python.org', 'registry.npmjs.org': 'registry.yarnpkg.com'}
    if host in hosts:
        return [urlunsplit(('https', hosts[host], path, parts.query, ''))]
    if host == 'nodejs.org' and path.startswith('/dist/'):
        return ['https://nodejs.org/download/release/' + path[len('/dist/'):]]
    segments = path.strip('/').split('/')
    if host == 'codeload.github.com' and len(segments) >= 4 and segments[2] == 'zip':
        return ['https://github.com/%s/%s/archive/%s.zip' % (segments[0], segments[1], '/'.join(segments[3:]))]
    if host == 'raw.githubusercontent.com' and len(segments) >= 4:
        return ['https://github.com/%s/%s/raw/%s' % (segments[0], segments[1], '/'.join(segments[2:]))]
    return []


def enrich(catalog, release_assets=None):
    if release_assets is None:
        manifest = Path(__file__).resolve().parents[2] / 'config/download-sources.json'
        release_assets = json.loads(manifest.read_text('utf-8')).get('release_assets', {}) if manifest.is_file() else {}
    for component in catalog['components']:
        for row in component.get('files', []) + component.get('downloads', []):
            urls = list(row['urls'])
            for url in list(urls):
                urls.extend(alternatives(url))
                if url in release_assets:
                    urls.append(release_assets[url])
            row['urls'] = list(dict.fromkeys(urls))
        if any(step.get('op') == 'ollama_pull' for step in component.get('after', [])):
            component['cloud_sources'] = ['https://registry.ollama.ai', 'https://ollama.com/library']
    return catalog
