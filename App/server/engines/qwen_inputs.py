"""Controls belonging to the 2511 editor, rather than distilled CFG-1 image models."""
from ui_strings import source as ui_text


def resolve_qwen_options(engine, data):
    negative = data.get('negative_prompt')
    community = data.get('use_uncensored_lora')
    if engine.id != 'qwen-edit-2511':
        if negative is not None or community is not None:
            raise ValueError(ui_text('image.qwen_options_wrong_engine'))
        return '', False
    negative = '' if negative is None else negative
    community = False if community is None else community
    if not isinstance(negative, str) or len(negative) > 2000 or type(community) is not bool:
        raise ValueError(ui_text('image.qwen_options_invalid'))
    if data.get('quality') == 'fast' and negative.strip():
        # Lightning uses CFG 1, so accepting a negative caption would silently ignore it.
        raise ValueError(ui_text('image.qwen_negative_requires_standard'))
    return negative.strip(), community
