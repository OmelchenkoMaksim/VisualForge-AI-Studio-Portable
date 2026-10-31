"""One converter for every picture that enters the studio: any common format in, the form the chosen neural network understands out.

People paste screenshots and attach photos in whatever format their phone, camera or editor made (WebP, AVIF, HEIC, GIF, BMP, TIFF, PSD, TGA, ICO,
CMYK or 16-bit files, pictures turned by EXIF, files with a colour profile). The networks behind the studio take PNG or JPEG of a limited size, so
instead of refusing a file the studio converts it with Pillow, the open-source imaging library that is already in every runtime:

* the format is found from the file itself (the name and the declared type are not trusted);
* a picture that is already acceptable is returned untouched (`unchanged`), a different one is converted: first frame of an animation, EXIF
  rotation applied, colour profile converted to sRGB, CMYK / palette / grey / 16-bit modes turned into RGB(A), transparency kept or flattened
  on a neutral ground, a long side above the limit scaled down, and the file re-encoded smaller when it is over the byte limit;
* a file nobody can decode is refused with a message that says why (HEIC without its plugin gets its own hint).

HEIC/HEIF (iPhone) is read when the optional `pillow-heif` package is installed; everything else comes with Pillow itself.
"""
from ui_strings import source as ui_text
import io
from dataclasses import dataclass, field

MIB = 1024 * 1024
MAX_PIXELS = 250_000_000          # a decompression bomb guard: a larger picture is refused before it is decoded
NEUTRAL = (238, 238, 238)         # what transparency is flattened on when the target has no alpha (the same grey the Video panel used before)


@dataclass(frozen=True)
class Profile:
    """What one consumer accepts. `keep` lists the formats that are passed through untouched when everything else is fine."""
    target: str = 'PNG'
    keep: tuple = ('PNG',)
    max_side: int = 8192
    max_bytes: int = 36 * MIB
    alpha: bool = False           # False: transparency is flattened on NEUTRAL
    allow_jpeg: bool = False      # a picture that does not fit the byte limit as PNG may become a JPEG
    quality: int = 92


PROFILES = {
    # Video frames: LTX, Wan, S2V, Animate 2 and SCAIL-2 read a PNG frame up to 8192 px a side and 36 MiB; they have no use for alpha.
    'video': Profile(),
    # Image section (ComfyUI inputs): PNG, JPEG and WebP go in as they are, up to 100 MiB; alpha is kept for the edit modes.
    'image': Profile(keep=('PNG', 'JPEG', 'WEBP'), max_bytes=100 * MIB, alpha=True),
    # Text chat with a vision model: PNG or JPEG, long side 2560 px, 6 MiB (the chat store limit).
    'chat': Profile(keep=('PNG', 'JPEG'), max_side=2560, max_bytes=6 * MIB, allow_jpeg=True),
}
MIMES = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}
SUFFIXES = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}
HEIF_BRANDS = (b'heic', b'heix', b'hevc', b'hevx', b'heim', b'heis', b'mif1', b'msf1', b'avci')


class ImageConvertError(ValueError):
    """The picture cannot be used; the message is meant for the person."""


@dataclass
class Result:
    data: bytes
    format: str
    width: int
    height: int
    unchanged: bool
    source: dict = field(default_factory=dict)
    changes: list = field(default_factory=list)

    @property
    def mime(self):
        return MIMES[self.format]

    @property
    def suffix(self):
        return SUFFIXES[self.format]

    def info(self):
        return dict(format=self.format, width=self.width, height=self.height, bytes=len(self.data), unchanged=self.unchanged, source=self.source, changes=self.changes)


def _pillow():
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:                          # iPhone photos: only when the optional plugin is installed
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    return Image


def looks_like_heif(raw):
    return raw[4:8] == b'ftyp' and raw[8:12] in HEIF_BRANDS


def _open(raw):
    Image = _pillow()
    try:
        return Image.open(io.BytesIO(raw))
    except Image.DecompressionBombError:
        raise ImageConvertError(ui_text('common.image_convert_too_many_pixels')) from None
    except Exception:
        if looks_like_heif(raw):
            raise ImageConvertError(ui_text('common.image_convert_heic_needs_plugin')) from None
        raise ImageConvertError(ui_text('common.image_convert_unreadable_file')) from None


def _foreign_profile(image):
    """True when the picture carries a colour profile that is not sRGB (a wide-gamut screenshot would look washed out without conversion)."""
    profile = image.info.get('icc_profile')
    if not profile or image.mode in ('L', 'LA'):
        return False
    try:
        from PIL import ImageCms
        return 'srgb' not in (ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(profile))) or '').lower()
    except Exception:
        return False


def _to_srgb(image, changes):
    """Convert a picture with a non-sRGB colour profile to sRGB."""
    profile = image.info.get('icc_profile')
    if not profile or image.mode not in ('RGB', 'RGBA', 'CMYK', 'L', 'LA'):
        return image
    try:
        from PIL import ImageCms
        source = ImageCms.ImageCmsProfile(io.BytesIO(profile))
        if not _foreign_profile(image):
            return image
        mode = 'RGBA' if image.mode in ('RGBA', 'LA') else 'RGB'
        converted = ImageCms.profileToProfile(image, source, ImageCms.createProfile('sRGB'), outputMode=mode)
        changes.append('colour profile converted to sRGB')
        return converted
    except Exception:
        return image          # a broken profile must not cost the picture


def _normalize_mode(image, profile, changes):
    from PIL import Image
    mode = image.mode
    if mode in ('I;16', 'I;16L', 'I;16B', 'I'):
        image = image.point(lambda value: value * (1 / 256)).convert('L')
        changes.append('16-bit turned into 8-bit')
        mode = 'L'
    elif mode == 'F':
        low, high = image.getextrema()
        scale = 255.0 / (high - low) if high > low else 1.0
        image = image.point(lambda value: (value - low) * scale).convert('L')
        mode = 'L'
    if mode in ('P', 'PA'):
        transparent = 'transparency' in image.info or mode == 'PA'
        image = image.convert('RGBA' if transparent else 'RGB')
        mode = image.mode
        changes.append('palette turned into RGB')
    if mode in ('LA', 'La'):
        image = image.convert('RGBA')
        mode = 'RGBA'
    if mode in ('CMYK', 'YCbCr', 'LAB', 'HSV', 'RGBX', 'RGBa', 'L', '1'):
        image = image.convert('RGB')
        if mode in ('CMYK', 'YCbCr', 'LAB', 'HSV'):
            changes.append('%s turned into RGB' % mode)
        mode = 'RGB'
    if mode == 'RGBA' and not profile.alpha:
        ground = Image.new('RGB', image.size, NEUTRAL)
        ground.paste(image, mask=image.getchannel('A'))
        image = ground
        changes.append('transparency flattened on grey')
    return image


def _encode(image, kind, quality):
    buffer = io.BytesIO()
    if kind == 'JPEG':
        image.convert('RGB').save(buffer, 'JPEG', quality=quality, optimize=True)
    else:
        image.save(buffer, 'PNG', compress_level=6)
    return buffer.getvalue()


def convert(raw, profile='video'):
    """Bytes of any picture -> a `Result` the consumer `profile` (a name of PROFILES or a Profile) accepts.

    Raises ImageConvertError when the file cannot be read or cannot be made small enough."""
    profile = PROFILES[profile] if isinstance(profile, str) else profile
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise ImageConvertError(ui_text('common.image_convert_unreadable_file'))
    raw = bytes(raw)
    image = _open(raw)
    try:
        try:
            source = dict(format=image.format, width=image.width, height=image.height, mode=image.mode, bytes=len(raw), frames=getattr(image, 'n_frames', 1))
            exif = image.getexif() if hasattr(image, 'getexif') else {}
            turned = exif.get(0x0112, 1) not in (0, 1) if exif else False
        except Exception:                 # a truncated file may only show it when the metadata is read
            raise ImageConvertError(ui_text('common.image_convert_unreadable_file')) from None
        passthrough = (image.format in profile.keep and source['frames'] == 1 and not turned and max(image.size) <= profile.max_side and len(raw) <= profile.max_bytes
                       and image.mode in ('RGB',) + (('RGBA',) if profile.alpha else ()) and not _foreign_profile(image))
        if passthrough:
            try:
                image.load()          # a truncated or corrupt file is found here, not by the neural network
            except Exception:
                raise ImageConvertError(ui_text('common.image_convert_unreadable_file')) from None
            return Result(raw, image.format, image.width, image.height, True, source, [])
        changes = []
        try:
            if source['frames'] > 1:
                image.seek(0)
                changes.append('first frame of an animation')
            image.load()
        except Exception:
            raise ImageConvertError(ui_text('common.image_convert_unreadable_file')) from None
        from PIL import ImageOps
        if turned:
            image = ImageOps.exif_transpose(image)
            changes.append('EXIF rotation applied')
        image = _to_srgb(image, changes)
        image = _normalize_mode(image, profile, changes)
        changes.insert(0, 'decoded %s' % (source['format'] or 'picture'))
        scale = min(1.0, profile.max_side / max(image.size))
        if scale < 1.0:
            from PIL import Image
            size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            image = image.resize(size, Image.LANCZOS)
            changes.append('scaled %dx%d -> %dx%d' % (source['width'], source['height'], size[0], size[1]))
        kind = 'PNG'
        data = _encode(image, kind, profile.quality)
        for _ in range(8):
            if len(data) <= profile.max_bytes:
                break
            if profile.allow_jpeg and kind == 'PNG':
                kind = 'JPEG'
                changes.append('re-encoded as JPEG to fit %.1f MiB' % (profile.max_bytes / MIB))
            else:
                from PIL import Image
                image = image.resize((max(1, round(image.width * 0.8)), max(1, round(image.height * 0.8))), Image.LANCZOS)
                changes.append('scaled down to fit %.1f MiB' % (profile.max_bytes / MIB))
            data = _encode(image, kind, profile.quality)
        if len(data) > profile.max_bytes:
            raise ImageConvertError(ui_text('common.image_convert_still_too_large') % (profile.max_bytes / MIB))
        changes.append('saved as %s' % kind)
        return Result(data, kind, image.width, image.height, False, source, changes)
    finally:
        image.close()
