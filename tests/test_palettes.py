"""Color schemes: every scheme the dialog offers has a complete, readable block for both themes, the page and the bridge ship the new files,
and every string the dialog uses exists in both languages."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'App/frontend'
# Tokens a scheme owns; status colors, shadows, the overlay and the select chevron are shared by all schemes on purpose.
TOKENS = ['bg', 'shell', 'surface', 'surface-2', 'surface-3', 'raised', 'border', 'border-strong', 'text', 'text-2', 'muted', 'faint',
          'accent', 'accent-hover', 'accent-ink', 'accent-soft', 'accent-line', 'nav', 'nav-ink', 'link', 'focus', 'code-bg']
TEXT_PAIRS = [('text', 'bg'), ('text', 'surface'), ('text', 'surface-3'), ('text-2', 'surface'), ('muted', 'bg'), ('muted', 'surface'),
              ('muted', 'surface-3'), ('muted', 'raised'), ('faint', 'surface'), ('faint', 'bg'), ('accent-ink', 'accent'),
              ('accent-ink', 'accent-hover'), ('nav-ink', 'nav'), ('link', 'surface'), ('link', 'bg')]
GRAPHICS_PAIRS = [('accent', 'bg'), ('accent', 'surface'), ('focus', 'bg')]


def luminance(color):
    def linear(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = (linear(int(color[i:i + 2], 16)) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def blocks():
    """{(theme, scheme): {token: value}} read from palettes.css."""
    found = {}
    for theme, scheme, body in re.findall(r'\[data-theme="(\w+)"\]\[data-palette="(\w+)"\]\{([^}]*)\}', (FRONTEND / 'palettes.css').read_text(encoding='utf-8')):
        found[(theme, scheme)] = dict(re.findall(r'--([\w-]+):([^;]+)', body))
    return found


def schemes():
    """The ids offered by the dialog, in the order of palettes.js."""
    return re.findall(r"\{id: '(\w+)', name: '", (FRONTEND / 'palettes.js').read_text(encoding='utf-8'))


class ColorSchemes(unittest.TestCase):
    def test_every_offered_scheme_has_a_block_for_both_themes(self):
        offered, found = schemes(), blocks()
        self.assertEqual(offered[0], 'indigo', 'the first scheme is the default defined by theme.css itself')
        self.assertGreaterEqual(len(offered), 4)
        for scheme in offered[1:]:
            for theme in ('dark', 'light'):
                self.assertIn((theme, scheme), found)
        self.assertEqual({scheme for _, scheme in found}, set(offered[1:]), 'palettes.css has no scheme the dialog does not offer')

    def test_every_block_sets_all_tokens(self):
        for key, tokens in blocks().items():
            self.assertEqual([name for name in TOKENS if name not in tokens], [], key)

    def test_text_is_readable_and_controls_are_visible_in_every_scheme(self):
        for (theme, scheme), tokens in blocks().items():
            for fg, bg in TEXT_PAIRS:
                self.assertGreaterEqual(contrast(tokens[fg], tokens[bg]), 4.5, (theme, scheme, fg, bg))
            for fg, bg in GRAPHICS_PAIRS:
                self.assertGreaterEqual(contrast(tokens[fg], tokens[bg]), 3.0, (theme, scheme, fg, bg))

    def test_a_scheme_changes_the_dark_background_and_the_light_one_in_the_right_direction(self):
        for (theme, scheme), tokens in blocks().items():
            self.assertEqual(luminance(tokens['bg']) < 0.05, theme == 'dark', (theme, scheme))

    def test_the_page_and_the_bridge_ship_the_files(self):
        page = (FRONTEND / 'index.html').read_text(encoding='utf-8')
        server = (ROOT / 'App/server/server.py').read_text(encoding='utf-8')
        self.assertLess(page.index('/theme.css'), page.index('/palettes.css'), 'a scheme must come after the defaults it overrides')
        self.assertLess(page.index('/palettes.js'), page.index('/shell.js'))
        for name in ('palettes.css', 'palettes.js'):
            self.assertIn("STATIC['/%s']" % name, server)
            self.assertTrue((FRONTEND / name).is_file())
        for element in ('openPalettes', 'palettesDialog', 'palettesDark', 'palettesLight', 'palettesReset', 'palettesDone', 'palettesClose'):
            self.assertIn('id="%s"' % element, page)

    def test_the_saved_choice_is_applied_before_the_first_paint_for_the_theme_in_use(self):
        init = (FRONTEND / 'theme-init.js').read_text(encoding='utf-8')
        self.assertIn("'vf-palette-' + theme", init)
        self.assertIn('dataset.palette', init)
        self.assertIn("'vf-palette-' + mode", (FRONTEND / 'palettes.js').read_text(encoding='utf-8'), 'both scripts must use the same storage key')

    def test_the_preview_tiles_get_their_own_theme_tokens(self):
        css = (FRONTEND / 'theme.css').read_text(encoding='utf-8')
        self.assertIn('.palette-preview[data-theme="dark"]', css)
        self.assertIn('.palette-preview[data-theme="light"]', css)

    def test_every_string_of_the_dialog_exists_in_both_languages(self):
        catalog = json.loads((ROOT / 'App/strings/settings.json').read_text(encoding='utf-8'))
        used = set(re.findall(r"settings\.color_schemes?_\w+", (FRONTEND / 'index.html').read_text(encoding='utf-8')))
        used |= set(re.findall(r"'(settings\.color_scheme_\w+)'", (FRONTEND / 'palettes.js').read_text(encoding='utf-8')))
        self.assertGreaterEqual(len(used), 14)
        for key in used:
            self.assertTrue(catalog.get(key, {}).get('ru') and catalog[key].get('en'), key)


if __name__ == '__main__':
    unittest.main()
