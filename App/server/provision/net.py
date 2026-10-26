"""Resumable, hash-verified downloads using only the standard library.

A file is written to "<name>.part", hashed while it is written and renamed into place only when its size and SHA-256
match. A broken connection resumes with a Range request; a wrong hash discards the part file and raises ChecksumError.
"""
import hashlib
import os
from pathlib import Path
import socket
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = 'VisualForge-Setup/1'
CHUNK = 1024 * 1024
TIMEOUT = 60
RETRIES = 6


class DownloadError(Exception):
    """A download that cannot succeed by retrying (HTTP 4xx, missing file, bad size)."""


class ChecksumError(DownloadError):
    """The bytes arrived but do not match the pinned SHA-256."""


class Cancelled(Exception):
    """The user asked to stop; partial files are kept for a later resume."""


def import_file(source, destination, size, sha256, progress=None, cancel=None):
    """Copy a user-selected pinned artifact; never move or modify the user's original."""
    source, destination = Path(source), Path(destination)
    if not source.is_file() or source.stat().st_size != size:
        raise ChecksumError('Размер выбранного файла не совпадает с требуемым')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
    digest, done = hashlib.sha256(), 0
    try:
        with source.open('rb') as incoming, temporary.open('xb') as outgoing:
            while True:
                if cancel and cancel():
                    raise Cancelled()
                block = incoming.read(CHUNK)
                if not block:
                    break
                outgoing.write(block)
                digest.update(block)
                done += len(block)
                if progress:
                    progress(done, size)
        if done != size or digest.hexdigest() != sha256.lower():
            raise ChecksumError('SHA-256 выбранного файла не совпадает с требуемым')
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *arguments, **keywords):
        return None


_OPENER = build_opener(_NoRedirect)


def sha256_file(path, progress=None, cancel=None, limit=None):
    digest = hashlib.sha256()
    done = 0
    with open(path, 'rb') as stream:
        while True:
            if cancel and cancel():
                raise Cancelled()
            block = stream.read(CHUNK if limit is None else min(CHUNK, limit - done))
            if not block:
                break
            digest.update(block)
            done += len(block)
            if progress:
                progress(done)
            if limit is not None and done >= limit:
                break
    return digest.hexdigest()


def _auth_headers(url, environ):
    host = (urlsplit(url).hostname or '').lower()
    token = environ.get('HF_TOKEN') or environ.get('HUGGING_FACE_HUB_TOKEN')
    if token and (host == 'huggingface.co' or host.endswith('.huggingface.co')):
        return {'Authorization': 'Bearer ' + token}
    token = environ.get('GITHUB_TOKEN')
    if token and host in ('api.github.com', 'github.com'):
        return {'Authorization': 'Bearer ' + token}
    return {}


def open_url(url, headers=None, environ=None, opener=None):
    """GET with manual redirects; credentials are only sent to the host they were meant for."""
    environ = os.environ if environ is None else environ
    opener = opener or _OPENER
    current = url
    for _ in range(10):
        binary = {'Accept': 'application/octet-stream'} if urlsplit(current).hostname == 'api.github.com' and '/releases/assets/' in urlsplit(current).path else {}
        request = Request(current, headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity', **binary, **_auth_headers(current, environ), **(headers or {})})
        try:
            return opener.open(request, timeout=TIMEOUT)
        except HTTPError as error:
            if error.code in (301, 302, 303, 307, 308) and error.headers.get('Location'):
                current = urljoin(current, error.headers['Location'])
                error.close()
                continue
            raise
    raise DownloadError('Too many redirects: ' + url)


def fetch_json(url, environ=None, opener=None):
    import json
    with open_url(url, {'Accept': 'application/json'}, environ, opener) as response:
        return json.loads(response.read().decode('utf-8'))


def download(urls, destination, size=None, sha256=None, progress=None, cancel=None, environ=None, opener=None, sleep=time.sleep):
    """Download the first working URL of `urls` to `destination` (resuming a previous .part) and verify it.

    progress(done_bytes, total_bytes_or_None) is called about twice a second. Returns the destination path.
    """
    destination = Path(destination)
    urls = [urls] if isinstance(urls, str) else list(urls)
    if destination.is_file() and (size is None or destination.stat().st_size == size):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + '.part')
    last_error = None
    for url in urls:
        failures = 0
        while True:
            before = part.stat().st_size if part.is_file() else 0
            try:
                _transfer(url, part, size, sha256, progress, cancel, environ, opener)
                os.replace(part, destination)
                return destination
            except ChecksumError as error:
                # A corrupt mirror must not stop the next pinned source; _finish already discarded its partial file.
                last_error = error
                break
            except Cancelled:
                raise
            except DownloadError as error:
                last_error = error
                break
            except HTTPError as error:
                if error.code in (401, 403, 404, 410, 451):
                    last_error = DownloadError('HTTP %d for %s' % (error.code, url))
                    break
                failures += 1
                last_error = error
            except (URLError, OSError, socket.timeout, ConnectionError) as error:
                # Counting all disconnects exhausted retries even when each attempt added gigabytes.
                # Reset the streak when bytes arrived; only consecutive stalls consume the budget.
                failures = 1 if part.is_file() and part.stat().st_size > before else failures + 1
                last_error = error
            if failures >= RETRIES:
                break
            sleep(min(30, 2 ** failures))
    if isinstance(last_error, DownloadError):
        raise last_error
    raise DownloadError('Could not download %s: %s' % (destination.name, last_error))


def _transfer(url, part, size, sha256, progress, cancel, environ, opener):
    have = part.stat().st_size if part.is_file() else 0
    if size is not None and have > size:
        part.unlink()
        have = 0
    digest = hashlib.sha256()
    if have:
        if size is not None and have == size:
            digest_hex = sha256_file(part, cancel=cancel)
            return _finish(part, size, sha256, digest_hex, have)
        with open(part, 'rb') as stream:
            while True:
                if cancel and cancel():
                    raise Cancelled()
                block = stream.read(CHUNK)
                if not block:
                    break
                digest.update(block)
    headers = {'Range': 'bytes=%d-' % have} if have else {}
    try:
        response = open_url(url, headers, environ, opener)
    except HTTPError as error:
        if error.code == 416 and have:
            # The server says the range is beyond the end: the part is already complete or garbage.
            error.close()
            return _finish(part, size, sha256, sha256_file(part, cancel=cancel), have)
        raise
    with response:
        status = getattr(response, 'status', 200)
        if have and status != 206:
            have = 0
            digest = hashlib.sha256()
            part.unlink()
        total = size
        if total is None:
            length = response.headers.get('Content-Length')
            total = have + int(length) if length and length.isdigit() else None
        last = 0.0
        with open(part, 'ab') as out:
            while True:
                if cancel and cancel():
                    raise Cancelled()
                block = response.read(CHUNK)
                if not block:
                    break
                out.write(block)
                digest.update(block)
                have += len(block)
                now = time.monotonic()
                if progress and now - last >= 0.5:
                    progress(have, total)
                    last = now
    if progress:
        progress(have, total)
    if size is not None and have < size:
        raise ConnectionError('The connection closed after %d of %d bytes' % (have, size))
    _finish(part, size, sha256, digest.hexdigest(), have)


def _finish(part, size, sha256, digest_hex, have):
    if size is not None and have != size:
        part.unlink()
        raise ChecksumError('Wrong size %d (expected %d) for %s' % (have, size, part.name[:-5]))
    if sha256 and digest_hex.lower() != sha256.lower():
        part.unlink()
        raise ChecksumError('SHA-256 mismatch for %s: got %s, expected %s' % (part.name[:-5], digest_hex, sha256))
