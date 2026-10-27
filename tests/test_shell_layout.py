"""The shell's side rail: every block of the page must start to the right of it.

theme.css moves the direct children of <main> clear of the fixed rail with `main > :not(header):not(nav):not(dialog)`, a
selector of tag weight only. Any class or id rule that sets a horizontal margin on one of those children outranks it, and
the block slides under the rail: `.panel{margin:20px 0}` once did this to the «Что означают специальные слова» panel."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

FRONTEND = Path(__file__).resolve().parents[1] / 'App/frontend'
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}
RAIL_EXEMPT = {'header', 'nav', 'dialog', 'script', 'template'}
HORIZONTAL_MARGIN = re.compile(r'(?:^|;)\s*margin(?:-left|-right|-inline(?:-start|-end)?)?\s*:', re.I)


class PageOutline(HTMLParser):
    """Collects the stylesheets and the classes/ids of every element directly inside <main>."""
    def __init__(self):
        super().__init__()
        self.stack, self.sheets, self.blocks = [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            self.sheets.append(attrs['href'].lstrip('/'))
        if self.stack and self.stack[-1] == 'main' and tag not in RAIL_EXEMPT:
            self.blocks.append((tag, attrs.get('id'), set((attrs.get('class') or '').split())))
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag):]


def split_selectors(text):
    parts, depth, start = [], 0, 0
    for index, char in enumerate(text):
        depth += char in '([' and 1 or char in ')]' and -1 or 0
        if char == ',' and depth == 0:
            parts.append(text[start:index]); start = index + 1
    return [part.strip() for part in parts + [text[start:]] if part.strip()]


def without_pseudo(compound):
    return re.sub(r':{1,2}[\w-]+(\([^)]*\))?', '', compound)


def main_child_subject(selector):
    """The last compound of a selector (the element the declarations land on), or None when the compounds before it
    need an ancestor other than html, body or main, so the rule cannot reach an element directly inside <main>."""
    compounds = re.split(r'\s*[>+~]\s*|\s+', selector.strip())
    if any(without_pseudo(compound) not in {'html', 'body', 'main', ''} for compound in compounds[:-1]):
        return None
    return without_pseudo(compounds[-1])


class ShellLayoutTests(unittest.TestCase):
    def setUp(self):
        self.page = PageOutline()
        self.page.feed((FRONTEND / 'index.html').read_text(encoding='utf-8'))

    def test_the_page_has_blocks_under_main_to_check(self):
        ids = {block_id for _, block_id, _ in self.page.blocks}
        self.assertTrue({'imageWorkspace', 'studioTerms'} <= ids, ids)
        self.assertIn('theme.css', self.page.sheets)

    def test_no_class_or_id_rule_pulls_a_main_block_under_the_rail(self):
        found = []
        for sheet in self.page.sheets:
            css = re.sub(r'/\*.*?\*/', '', (FRONTEND / sheet).read_text(encoding='utf-8'), flags=re.S)
            # Innermost rules only, so the rules inside @media blocks are checked like the others.
            for selectors, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
                if not HORIZONTAL_MARGIN.search(declarations):
                    continue
                for selector in split_selectors(selectors):
                    target = main_child_subject(selector)
                    if target is None:
                        continue
                    classes = set(re.findall(r'\.([\w-]+)', target))
                    ids = set(re.findall(r'#([\w-]+)', target))
                    if not classes and not ids:
                        continue
                    for tag, block_id, block_classes in self.page.blocks:
                        if (ids and block_id not in ids) or not classes <= block_classes:
                            continue
                        found.append(f'{sheet}: "{selector}" sets a horizontal margin on <{tag} id="{block_id}">')
        self.assertEqual(found, [], 'set only vertical margins (margin-block) on blocks directly inside <main>')


if __name__ == '__main__':
    unittest.main()
