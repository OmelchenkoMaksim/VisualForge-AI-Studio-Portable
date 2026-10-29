"""Validate additional references against each installed encoder's actual input capacity."""
from ui_strings import source as ui_text


def resolve_extra_images(engine, mode, data):
    extras = data.get('extra_images')
    if mode != 'multi':
        if extras is not None:
            raise ValueError(ui_text('image.flux_options_wrong_mode'))
        return []
    maximum = engine.max_reference_images - 2
    if 'multi' not in engine.modes or not isinstance(extras, list) or not 1 <= len(extras) <= maximum:
        raise ValueError(ui_text('image.extra_images_count_error', maximum))
    return extras
