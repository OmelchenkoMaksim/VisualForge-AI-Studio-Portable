"""Validate the installed FLUX Klein's optional layout, resolution and reference controls."""
import math
import re

from ui_strings import source as ui_text
from .reference_inputs import resolve_extra_images


def resolve_flux_options(engine, mode, data):
    extras = resolve_extra_images(engine, mode, data)
    boxes = data.get('layout_boxes')
    megapixels = data.get('native_megapixels')
    if engine.id == 'qwen-image-21-uc-fp8':
        if boxes is not None:
            raise ValueError(ui_text('image.flux_options_wrong_engine'))
        megapixels = 1 if megapixels is None else megapixels
        if type(megapixels) is not int or megapixels not in engine.native_megapixels:
            raise ValueError(ui_text('image.qwen21_resolution_error'))
        return extras or [], [], megapixels
    if engine.id != 'flux3-klein-int8':
        if any(value is not None for value in (boxes, megapixels)):
            raise ValueError(ui_text('image.flux_options_wrong_engine'))
        return extras, [], 1
    if mode == 'layout':
        if not isinstance(boxes, list) or not 1 <= len(boxes) <= 8:
            raise ValueError(ui_text('image.flux_layout_count_error'))
        normalized, ids = [], set()
        for index, row in enumerate(boxes):
            if not isinstance(row, dict) or set(row) - {'id', 'desc', 'bbox'}:
                raise ValueError(ui_text('image.flux_layout_box_error'))
            ident, desc, bbox = row.get('id', 'obj_' + str(index + 1)), row.get('desc'), row.get('bbox')
            if (not isinstance(ident, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', ident) or ident in ids
                    or not isinstance(desc, str) or not desc.strip() or len(desc) > 600
                    or not isinstance(bbox, list) or len(bbox) != 4
                    or any(type(value) is not int or not 0 <= value <= 1000 for value in bbox)
                    or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]):
                raise ValueError(ui_text('image.flux_layout_box_error'))
            ids.add(ident)
            normalized.append(dict(id=ident, desc=desc.strip(), bbox=list(bbox)))
        boxes = normalized
    elif boxes is not None:
        raise ValueError(ui_text('image.flux_options_wrong_mode'))
    megapixels = 1 if megapixels is None else megapixels
    if type(megapixels) is not int or megapixels not in engine.native_megapixels:
        raise ValueError(ui_text('image.flux_resolution_error'))
    if mode not in engine.text_modes and megapixels != 1:
        raise ValueError(ui_text('image.flux_resolution_text_only_error'))
    if megapixels == 16 and data.get('upscale', 1) != 1:
        raise ValueError(ui_text('image.flux_fourk_no_upscale_error'))
    return extras or [], boxes or [], megapixels


def native_dimensions(canvas_dimensions, megapixels):
    # Scale the existing aspect presets, preserving the latent grid's 16-pixel alignment.
    scale = math.sqrt(megapixels)
    scale = min(scale, 4096 / max(canvas_dimensions))
    return tuple(max(16, round(value * scale / 16) * 16) for value in canvas_dimensions)


def layout_brief(rows):
    """Keep caption expansion consistent with the centers of the validated boxes."""
    parts = []
    for row in rows:
        top, left, bottom, right = row['bbox']
        vertical = 'upper' if top + bottom < 800 else 'lower' if top + bottom > 1200 else 'middle'
        horizontal = 'left' if left + right < 800 else 'right' if left + right > 1200 else 'center'
        parts.append('One ' + row['desc'] + ' <' + row['id'] + '> in the ' + vertical + ' ' + horizontal + ' of the frame.')
    return ' '.join(parts)
