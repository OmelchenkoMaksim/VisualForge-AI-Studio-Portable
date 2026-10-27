"""Optional branding assets. Decode failures never block application startup."""
import struct
import zlib

FALLBACK_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#182230"/><path d="M12 16L30 50L52 16L40 20L30 37L23 20Z" fill="#58bfff"/></svg>'
_WARNED=set()

def valid_png(data):
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):return False
    offset=8;header=False
    while offset+12<=len(data):
        size=struct.unpack_from('>I',data,offset)[0];kind=data[offset+4:offset+8];end=offset+8+size
        if size>16*1024*1024 or end+4>len(data):return False
        if zlib.crc32(data[offset+4:end])&0xffffffff!=struct.unpack_from('>I',data,end)[0]:return False
        if not header:
            if kind!=b'IHDR' or size!=13:return False
            width,height=struct.unpack_from('>II',data,offset+8)
            if not 1<=width<=4096 or not 1<=height<=4096:return False
            header=True
        if kind==b'IEND':return header and size==0 and end+4==len(data)
        offset=end+4
    return False

def valid_ico(data):
    if len(data)<6 or data[:4]!=b'\0\0\1\0':return False
    count=struct.unpack_from('<H',data,4)[0]
    if not 1<=count<=32 or len(data)<6+count*16:return False
    for i in range(count):
        size,offset=struct.unpack_from('<II',data,6+i*16+8)
        if offset<6+16*count or size==0 or offset+size>len(data) or not valid_png(data[offset:offset+size]):return False
    return True

def optional_icon(path,kind):
    try:
        with path.open('rb') as stream:data=stream.read(16*1024*1024+1)
        if len(data)>16*1024*1024 or not (valid_png(data) if kind=='png' else valid_ico(data)):raise ValueError('Invalid branding image')
        return data, 'image/png' if kind=='png' else 'image/x-icon'
    except (OSError,ValueError,struct.error):
        key=(str(path),kind)
        if key not in _WARNED:
            print('[branding] optional '+kind+' unavailable; using embedded fallback',flush=True);_WARNED.add(key)
        return FALLBACK_SVG,'image/svg+xml'
