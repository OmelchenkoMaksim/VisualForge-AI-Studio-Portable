"""Hash-gated offline component installer. Explicit target; never auto-deploy."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import time
import uuid


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def confined(root, relative):
    pure = PurePosixPath(relative)
    if not relative or '\\' in relative or ':' in relative or pure.is_absolute() or '..' in pure.parts:
        raise ValueError('Unsafe package path')
    path = (Path(root) / relative).resolve()
    path.relative_to(Path(root).resolve())
    return path


def atomic_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.partial')
    try:
        shutil.copyfile(source, temporary)
        if digest(temporary) != digest(source):
            raise ValueError('Copy integrity failure')
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def plan(package, root, manifest):
    rows = []
    seen = set()
    for row in manifest['files']:
        name = row['path']
        if name.casefold() in seen or not name.startswith(('App/', 'Runtime/Video/', 'Models/Video/H3/')):
            raise ValueError('Duplicate or forbidden payload entry')
        seen.add(name.casefold())
        source, target = confined(package, name), confined(root, name)
        if not source.is_file() or source.stat().st_size != row['bytes'] or digest(source) != row['sha256']:
            raise ValueError('Package integrity failure: ' + name)
        current = digest(target) if target.is_file() else None
        if current == row['sha256']:
            continue
        if current != row.get('baseline_sha256'):
            raise ValueError('Unknown/newer installed file; refusing overwrite: ' + name)
        rows.append({**row, 'previous_sha256': current})
    return rows


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def apply(package, root, manifest):
    rows = plan(package, root, manifest)
    backup = root / 'Support/Backups' / (manifest['release_version'] + '-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True, exist_ok=False)
    diagnostic_name = 'Support/Diagnostics/InstalledRelease.json'
    diagnostic_path = confined(root, diagnostic_name)
    previous = json.loads(diagnostic_path.read_text(encoding='utf-8-sig')) if diagnostic_path.is_file() else {}
    old_files = {r['path']: r for r in previous.get('files', [])}
    old_files.update({r['path']: {'path': r['path'], 'size': r['bytes'], 'sha256': r['sha256']} for r in manifest['files'] if r['path'].startswith('App/')})
    document = {'release_version': manifest['release_version'], 'status': 'installed_pending_startup_validation',
                'base_release_version': previous.get('release_version'), 'git_commit': manifest.get('source_commit'),
                'upstream_commit': manifest.get('upstream_commit'), 'files': list(old_files.values()),
                'video_payload_files': len(manifest['files']), 'video_payload_bytes': manifest.get('payload_bytes'),
                'package_manifest_sha256': hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()}
    generated = backup / 'generated/InstalledRelease.json'
    write_json(generated, document)
    rows.append({'path': diagnostic_name, 'bytes': generated.stat().st_size, 'sha256': digest(generated),
                 'previous_sha256': digest(diagnostic_path) if diagnostic_path.is_file() else None, 'generated': True})
    receipt = {'schema_version': 1, 'release_version': manifest['release_version'], 'root': str(root.resolve()), 'state': 'prepared', 'files': rows, 'applied': []}
    for row in rows:
        if row['previous_sha256']:
            atomic_copy(confined(root, row['path']), confined(backup / 'files', row['path']))
    write_json(backup / 'receipt.json', receipt)
    try:
        for row in rows:
            target = confined(root, row['path'])
            current = digest(target) if target.is_file() else None
            if current != row['previous_sha256']:
                raise ValueError('File changed after plan: ' + row['path'])
            # Journal intent before mutation so an interrupted run remains recoverable.
            receipt['applied'].append(row['path'])
            write_json(backup / 'receipt.json', receipt)
            atomic_copy(generated if row.get('generated') else confined(package, row['path']), target)
        receipt['state'] = 'installed'
        write_json(backup / 'receipt.json', receipt)
    except Exception:
        receipt['state'] = 'interrupted'
        write_json(backup / 'receipt.json', receipt)
        raise
    return backup


def rollback(root, backup):
    receipt = json.loads((backup / 'receipt.json').read_text(encoding='utf-8'))
    if root.resolve() != Path(receipt['root']).resolve():
        raise ValueError('Backup belongs to another installation')
    applied = set(receipt['applied'])
    rows = [r for r in receipt['files'] if r['path'] in applied]
    # Complete validation before any restore; never erase post-install edits.
    for row in rows:
        if not row['path'].startswith(('App/', 'Runtime/Video/', 'Models/Video/H3/')) and row['path'] != 'Support/Diagnostics/InstalledRelease.json':
            raise ValueError('Forbidden rollback path')
        target = confined(root, row['path'])
        current = digest(target) if target.is_file() else None
        if current not in (row['sha256'], row['previous_sha256']):
            raise ValueError('Post-install edit; rollback refused: ' + row['path'])
        if row['previous_sha256'] and digest(confined(backup / 'files', row['path'])) != row['previous_sha256']:
            raise ValueError('Rollback backup is damaged')
    for row in reversed(rows):
        target = confined(root, row['path'])
        if row['previous_sha256']:
            atomic_copy(confined(backup / 'files', row['path']), target)
        elif target.is_file():
            target.unlink()
    receipt['state'] = 'rolled_back'
    write_json(backup / 'receipt.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['plan', 'apply', 'rollback'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    if args.mode == 'rollback':
        print(json.dumps(rollback(args.root, args.backup)))
        return
    manifest = json.loads((args.package / 'manifest.json').read_text(encoding='utf-8'))
    if args.mode == 'plan':
        print(json.dumps({'files_to_change': len(plan(args.package, args.root, manifest)), 'writes': False}))
    else:
        print(json.dumps({'backup': str(apply(args.package, args.root, manifest))}))


if __name__ == '__main__':
    main()
