"""Stage pinned software <=300 MB for a reviewed GitHub Release; never publish or include models.

    python tools/prepare_software_release.py --output <directory> [--download]

The release retains upstream archives/wheels and their embedded license notices unchanged.
Review component licenses and the generated manifest before publishing the assets.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
from provision.download_transport import download, sha256_file
from provision.software_bundle import MAX_BYTES, asset_name


def artifacts(catalog):
    rows = {}
    for component in catalog['components']:
        if component['kind'] != 'software':
            continue
        for item in component.get('files', []) + component.get('downloads', []):
            if not 0 < item['bytes'] <= MAX_BYTES:
                continue
            name = Path(item.get('filename') or item['path']).name
            row = rows.setdefault(item['sha256'], dict(name=asset_name(name, item['sha256']), bytes=item['bytes'],
                sha256=item['sha256'], urls=item['urls'], components=[], licenses=[]))
            row['components'].append(component['id'])
            if component.get('license') not in row['licenses']:
                row['licenses'].append(component.get('license'))
    return list(rows.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.is_relative_to(ROOT):
        parser.error('Release assets must be outside the source checkout')
    output.mkdir(parents=True, exist_ok=True)
    rows = artifacts(json.loads((ROOT / 'App/config/component-catalog.json').read_text('utf-8')))
    def prepare(row):
        path = output / row['name']
        row['verified'] = False
        if args.download:
            try:
                download(row['urls'], path, row['bytes'], row['sha256'])
                if sha256_file(path) != row['sha256']:
                    raise ValueError('Existing staged artifact failed SHA-256 verification')
                row['verified'] = True
            except Exception as error:
                row['error'] = str(error)
        return row
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(prepare, rows))
    manifest = dict(published=False, license_review_required=True, assets=rows)
    (output / 'software-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', 'utf-8')
    (output / 'SHA256SUMS.txt').write_text(''.join(row['sha256']+'  '+row['name']+'\n' for row in rows), 'utf-8')
    print(json.dumps(dict(assets=len(rows), bytes=sum(row['bytes'] for row in rows), verified=sum(row['verified'] for row in rows),
                          failed=sum('error' in row for row in rows))))


if __name__ == '__main__':
    main()
