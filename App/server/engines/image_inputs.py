"""Small CPU input checks for optional Image workflows; no inference or model loading."""
from io import BytesIO
from ui_strings import source as ui_text


def validate_edit_mask(source_bytes, mask_bytes):
    from PIL import Image, ImageChops, ImageFilter, ImageOps
    try:
        with Image.open(BytesIO(source_bytes)) as source, Image.open(BytesIO(mask_bytes)) as mask:
            source = ImageOps.exif_transpose(source)
            mask = ImageOps.exif_transpose(mask)
            if source.size != mask.size or max(source.size) > 8192 or source.width * source.height > 32_000_000:
                raise ValueError(ui_text('image.inpaint_mask_dimensions_message'))
            if mask.convert('RGBA').getchannel('A').getextrema() != (255, 255):
                raise ValueError(ui_text('image.inpaint_mask_grayscale_message'))
            red, green, blue = mask.convert('RGB').split()
            if ImageChops.difference(red, green).getbbox() or ImageChops.difference(red, blue).getbbox():
                raise ValueError(ui_text('image.inpaint_mask_grayscale_message'))
            # The graph thresholds the red channel at 0.5; a dark-only mask would produce an unchanged result.
            if red.getextrema()[1] < 128:
                raise ValueError(ui_text('image.inpaint_mask_empty_message'))
            binary = red.point(lambda value: 255 if value >= 128 else 0)
            # A hard composite exposes decoder/color seams. Feather inward only, leaving every keep pixel exactly black.
            eroded = binary.filter(ImageFilter.MinFilter(9))
            feathered = ImageChops.multiply(eroded.filter(ImageFilter.GaussianBlur(2)), binary) if eroded.getbbox() else binary
            output = BytesIO()
            feathered.convert('RGB').save(output, format='PNG')
            return output.getvalue()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError(ui_text('image.inpaint_mask_decode_message')) from exc


def grayscale_png(source_bytes):
    from PIL import Image, ImageOps
    try:
        with Image.open(BytesIO(source_bytes)) as source:
            if max(source.size) > 8192 or source.width * source.height > 32_000_000:
                raise ValueError(ui_text('image.inpaint_mask_dimensions_message'))
            output = BytesIO()
            ImageOps.grayscale(ImageOps.exif_transpose(source)).convert('RGB').save(output, format='PNG')
            return output.getvalue()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError(ui_text('image.inpaint_mask_decode_message')) from exc
