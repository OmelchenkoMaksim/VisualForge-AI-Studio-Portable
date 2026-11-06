"""Bounded VoxCPM2 conditioning; source narration and voice identity stay separate."""

# Publisher VoxCPM2 examples and app.py use a trained '(control)text' prefix.
# The maintainer confirms in issue #210 that transcript continuation cannot use it.
STYLES = {
    'none': '',
    'storytelling': 'conversational storytelling',
    'calm': 'calm, intimate, normal speaking volume',
    'clear': 'clear diction, matter-of-fact delivery',
    'lively': 'lively, cheerful tone',
    'brisk': 'slightly faster, clear diction',
}
STYLE_VERSION = 'voxcpm2-style-v1'


def style_available(request):
    return (request.get('style_control') is True and request.get('engine') == 'voxcpm2'
            and request.get('reference_mode') == 'reference')


def conditioned_text(text, style='none', transcript=''):
    """Serialize only a validated control at the backend boundary, never into saved narration."""
    if not isinstance(style, str) or style not in STYLES:
        raise ValueError('Unknown VoxCPM2 delivery style')
    if style != 'none' and transcript:
        raise ValueError('VoxCPM2 style control requires reference-only cloning')
    return f'({STYLES[style]}){text}' if STYLES[style] else text
