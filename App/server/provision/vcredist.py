"""Take the app-local Microsoft C++ runtime DLLs out of the signed vc_redist.x64.exe without running it.

The installer is a WiX Burn bundle. It carries two Windows cabinets (CAB) after its executable part: a small container with
the bundle manifest and licenses, and an attached container with the real payloads (MSI files and their cabinets). Windows'
own expand.exe opens cabinets, so no third-party unpacker and no installation are needed. Every DLL is accepted only if its
SHA-256 matches the pin in the catalog (the package itself is pinned by SHA-256 too).
"""
import hashlib
from pathlib import Path
import shutil
import struct

from . import archives


class VcRedistError(Exception):
    pass


def _cabinets(data):
    """Slices of `data` that are Microsoft cabinet files, found by their MSCF header and declared size."""
    found, start = [], 0
    while True:
        offset = data.find(b'MSCF', start)
        if offset < 0:
            return found
        start = offset + 4
        if offset + 36 <= len(data):
            size = struct.unpack_from('<I', data, offset + 8)[0]
            if 36 < size <= len(data) - offset:
                found.append(data[offset:offset + size])


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def extract(package, target, files, temp, seven_zip=None, emit=None, license=None):
    package, target = Path(package), Path(target)
    emit = emit or (lambda *a, **k: None)
    work = Path(temp) / 'vcredist'
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    cabinets = _cabinets(package.read_bytes())
    if not cabinets:
        raise VcRedistError('No cabinet found inside ' + package.name)
    candidates = []
    for number, blob in enumerate(cabinets):
        cab = work / ('container%d.cab' % number)
        cab.write_bytes(blob)
        out = work / ('container%d' % number)
        archives.extract(cab, out, 'cab')
        candidates.append(out)
        for inner in sorted(out.iterdir()):
            if inner.is_file() and inner.read_bytes()[:4] == b'MSCF':
                nested = work / ('%s-%s' % (out.name, inner.name))
                try:
                    archives.extract(inner, nested, 'cab')
                except archives.ArchiveError:
                    continue
                candidates.append(nested)
    index = {}
    for folder in candidates:
        for path in folder.iterdir():
            if path.is_file():
                index.setdefault(_sha256(path), path)
    target.mkdir(parents=True, exist_ok=True)
    for item in files:
        source = index.get(item['sha256'])
        if source is None:
            raise VcRedistError('%s with the expected SHA-256 is not inside %s; Microsoft may have changed the package.' % (item['name'], package.name))
        shutil.copyfile(source, target / item['name'])
    if license:
        source = index.get(license['sha256'])
        if source is None:
            raise VcRedistError('The runtime license text was not found in ' + package.name)
        destination = target.parent / license['name']
        shutil.copyfile(source, destination)
    shutil.rmtree(work, ignore_errors=True)
    emit('note', text='C++ runtime: %d DLLs from %s' % (len(files), package.name))
