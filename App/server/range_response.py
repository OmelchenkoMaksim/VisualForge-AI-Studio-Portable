"""Serve a local media file to the page with HTTP byte ranges, so that <audio> and <video> can seek and start playing before the file is read.

One implementation for the Music, Voiceover and Video routes of the bridge (they used to carry three copies). `handler` is the bridge's request
handler: it provides `headers`, `send_response`, `send_header`, `end_headers`, `wfile` and `response_json`.
"""
import re

CHUNK = 65536


def parse_range(header, size):
    """`Range: bytes=a-b` -> (start, end) inclusive, or None when the header is malformed or outside the file."""
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
    if not match or not any(match.groups()):
        return None
    first, last = match.groups()
    start = int(first) if first else max(0, size - int(last))
    end = min(size - 1, int(last)) if first and last else size - 1
    return (start, end) if 0 <= start <= end < size else None


def serve_file(handler, path, content_type, filename, cache_control=None):
    """200 with the whole file, 206 with the requested range, 416 for a bad range. `filename` is the name the browser shows when the person saves it."""
    size = path.stat().st_size
    start, end, partial = 0, size - 1, False
    requested = handler.headers.get('Range')
    if requested:
        found = parse_range(requested, size)
        if found is None:
            return handler.response_json(416, {'error': 'Invalid byte range'})
        (start, end), partial = found, True
    handler.send_response(206 if partial else 200)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(end - start + 1))
    handler.send_header('Accept-Ranges', 'bytes')
    if cache_control:
        handler.send_header('Cache-Control', cache_control)
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Content-Disposition', 'inline; filename="' + filename.replace('"', '') + '"')
    if partial:
        handler.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
    handler.end_headers()
    try:
        with path.open('rb') as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = stream.read(min(CHUNK, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)
    except (BrokenPipeError, ConnectionResetError):
        pass     # the player stopped reading (a seek, a closed page): nothing to report
