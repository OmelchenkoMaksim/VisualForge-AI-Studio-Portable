"""The Director's page against the server's rules: the same limits and transitions, every string it shows, and the files the bridge serves."""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))

from director import plan_rules as rules  # noqa: E402

SCRIPT = (ROOT / 'App/frontend/director-panel.js').read_text(encoding='utf-8')
CATALOG = json.loads((ROOT / 'App/strings/director-strings.json').read_text(encoding='utf-8'))


class PanelTests(unittest.TestCase):
    def test_every_string_the_panel_uses_exists_in_both_languages(self):
        used = set(re.findall(r"'(director\.[a-z0-9_]+)'", SCRIPT)) - {'director.transition_kind_', 'director.panel_advice_'}
        used |= {'director.transition_kind_' + name for name in rules.TRANSITIONS}              # built at run time: one per transition
        used |= {'director.panel_advice_' + code for code in rules.ADVICE_CODES}                  # one sentence for every reason the server can give
        for row in rules.GROUPS:                                                                  # and a title, a hint, a button and three slot names for every group
            used |= {'director.group_%s_%s' % (row['id'], word) for word in ('title', 'hint', 'add')}
            used |= {'director.slot_%s_%s' % (row['id'], word) for word in ('first', 'second', 'other')}
        used |= set(re.findall(r'"(director\.[a-z0-9_]+)"', (ROOT / 'App/frontend/video-panel.js').read_text(encoding='utf-8')))
        self.assertEqual(sorted(key for key in used if key not in CATALOG), [])

    def test_no_string_of_the_catalog_is_left_unused(self):
        """A string nobody asks for is dead weight in two languages: it goes when the thing that used it goes."""
        source = ''.join(path.read_text(encoding='utf-8') for path in list((ROOT / 'App/server/director').glob('*.py')) + [ROOT / 'App/server/video/video_service.py', ROOT / 'App/server/event_history.py',
                                                                                                                          ROOT / 'App/server/studio_server.py', ROOT / 'App/frontend/video-panel.js'])
        wanted = set(re.findall(r"""['"](director\.[a-z0-9_]+)['"]""", source + SCRIPT))
        wanted |= {'director.transition_kind_' + name for name in rules.TRANSITIONS} | {'director.panel_advice_' + code for code in rules.ADVICE_CODES}
        for row in rules.GROUPS:
            wanted |= {'director.group_%s_%s' % (row['id'], word) for word in ('title', 'hint', 'add')} | {'director.slot_%s_%s' % (row['id'], word) for word in ('first', 'second', 'other')}
        self.assertEqual(sorted(key for key in CATALOG if key not in wanted), [])

    def test_every_name_in_the_catalog_says_the_same_in_both_languages(self):
        for key, row in CATALOG.items():
            with self.subTest(key=key):
                self.assertEqual(sorted(re.findall(r'\{\d+\}', row['ru'])), sorted(re.findall(r'\{\d+\}', row['en'])))
                self.assertTrue(row['ru'].strip() and row['en'].strip())

    @unittest.skipUnless(shutil.which('node'), 'Node is required to run the page formula')
    def test_the_page_names_the_language_of_an_idea_as_the_server_does(self):
        from director import plan
        formula = re.search(r'const ideaLanguage = [^\n]+', SCRIPT)
        self.assertIsNotNone(formula, 'the page formula moved: update this test together with director-panel.js')
        samples = ['Фильм про дорогу', 'A road film', 'Фильм про road trip across the desert', 'Road trip про дорогу и ещё про закат', '', '1234 !!!', 'ёЁ', 'ab', 'Привет, Anna!', 'Hola señor']
        program = formula.group(0) + '; console.log(JSON.stringify(%s.map(ideaLanguage)));' % json.dumps(samples)
        answer = subprocess.run([shutil.which('node'), '-e', program], capture_output=True, text=True, check=True, timeout=30, encoding='utf-8')
        self.assertEqual(json.loads(answer.stdout), [plan.idea_language(text) for text in samples])

    def test_the_reasons_of_the_advice_are_the_servers(self):
        found = re.search(r"const ADVICE = \[([^\]]*)\]", SCRIPT)
        self.assertIsNotNone(found)
        self.assertEqual(re.findall(r"'(\w+)'", found.group(1)), list(rules.ADVICE_CODES))

    def test_the_plan_editor_offers_the_ranges_the_plan_check_keeps(self):
        # The editor's number boxes once kept a saturation floor of 0.7 after the server had learnt that 0 is a black-and-white film.
        code = (ROOT / 'App/server/director/plan.py').read_text(encoding='utf-8')
        for name in ('warmth', 'contrast', 'saturation'):
            with self.subTest(name=name):
                shown = re.search(r'%s = number\(plan\.style\.%s, (-?[\d.]+), (-?[\d.]+)' % (name, name), SCRIPT)
                kept = re.search(r"%s=_number\(style\.get\('%s'\), (-?[\d.]+), (-?[\d.]+)" % (name, name), code)
                self.assertTrue(shown and kept)
                self.assertEqual([float(value) for value in shown.groups()], [float(value) for value in kept.groups()])

    def test_the_page_has_ultra_on_by_default_and_sends_the_mode_it_planned_for(self):
        self.assertRegex(SCRIPT, r"const ultra = h\('input', \{type: 'checkbox', id: 'directorUltra', checked: true\}\)")                # on until the person turns it off
        self.assertRegex(SCRIPT, r"ultra\.checked = localStorage\.getItem\(ULTRA_KEY\) !== '0'")                                       # and only an explicit '0' remembered turns it off
        self.assertIn('ultra: ultra.checked', SCRIPT)                                                                                  # the plan is written for the toggle as it stands
        self.assertIn('ultra: request.ultra === true', SCRIPT)                                                                         # the film is shot in the mode of the plan

    def test_the_transitions_of_the_page_are_the_servers(self):
        found = re.search(r"const TRANSITIONS = \[([^\]]*)\]", SCRIPT)
        self.assertIsNotNone(found)
        self.assertEqual(re.findall(r"'(\w+)'", found.group(1)), list(rules.TRANSITIONS))

    @unittest.skipUnless(shutil.which('node'), 'Node is required to run the page formula')
    def test_the_page_and_the_server_agree_on_the_scenes_of_every_length(self):
        constants = re.search(r'const MIN_SECONDS = (\d+), MAX_SECONDS = (\d+), MIN_SCENES = (\d+), SCENES_PER_MINUTE = (\d+);', SCRIPT)
        formula = re.search(r'const sceneRange = [^\n]+', SCRIPT)
        self.assertTrue(constants and formula, 'the page formula moved: update this test together with director-panel.js')
        self.assertEqual(tuple(map(int, constants.groups())), (rules.MIN_SECONDS, rules.MAX_SECONDS, rules.MIN_SCENES, rules.SCENES_PER_MINUTE))
        program = constants.group(0) + formula.group(0) + '; console.log(JSON.stringify(Array.from({length: %d}, (_, i) => sceneRange(i + 1))));' % rules.MAX_SECONDS
        answer = subprocess.run([shutil.which('node'), '-e', program], capture_output=True, text=True, check=True, timeout=30)
        page = json.loads(answer.stdout)
        for seconds in range(rules.MIN_SECONDS, rules.MAX_SECONDS + 1):
            with self.subTest(seconds=seconds):
                self.assertEqual(tuple(page[seconds - 1]), rules.scene_range(seconds))

    def test_the_page_includes_its_files_and_the_bridge_serves_them(self):
        page = (ROOT / 'App/frontend/studio-index.html').read_text(encoding='utf-8-sig')
        self.assertIn('id="directorPanel"', page)
        self.assertLess(page.index('/video-panel.js'), page.index('/director-panel.js'))
        self.assertIn('/director-panel.css', page)
        bridge = (ROOT / 'App/server/studio_server.py').read_text(encoding='utf-8-sig')
        for name in ('/director-panel.js', '/director-panel.css'):
            self.assertIn("STATIC['%s']" % name, bridge)
        for script in ('studio-ui.js', 'studio-ui.css'):                                    # both start buttons obey the global lock, and the dimming rule names them too
            self.assertIn('#directorPanel #directorPlan, #directorPanel #directorStart', (ROOT / 'App/frontend' / script).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
