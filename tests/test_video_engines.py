"""Drift guards of the Video section: the knowledge about one engine lives in several places, and the page repeats a few server formulas.

Adding an engine (or changing a length rule) touches the registry, the component catalog, the model-requirements table, three frontend tables,
the Gemma guides and the assessment texts. Each of them is read by a different code path, so a forgotten one shows up only as a missing
label, a hidden block or a request the server refuses. These tests fail with the name of the missing piece instead.
"""
import itertools
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))

import model_requirements  # noqa: E402
from video import video_registry as registry  # noqa: E402
from video.video_prompt_enhancer import GUIDES  # noqa: E402
from video.video_adapters import ADAPTERS  # noqa: E402

PANEL = (ROOT / 'App/frontend/video-panel.js').read_text(encoding='utf-8')
RELEASES = (ROOT / 'App/frontend/studio-ui.js').read_text(encoding='utf-8')
MANAGER = (ROOT / 'App/frontend/component-manager.js').read_text(encoding='utf-8')
VIDEO_STRINGS = json.loads((ROOT / 'App/strings/video-strings.json').read_text(encoding='utf-8-sig'))
CATALOG = json.loads((ROOT / 'App/config/component-catalog.json').read_text(encoding='utf-8-sig'))

NO_GUIDE = {'freevideo-h3-int8'}          # the legacy engine: no hidden Gemma instruction, no strengths-and-limits text
ASSESSMENT_FIELDS = ('strong', 'variable', 'limits', 'evidence')


def table(source, name):
    """The `{...}` object literal assigned to `name` in a frontend source, as a dict of string pairs (single-line, string values only)."""
    body = re.search(name + r'\s*=\s*\{(.*?)\}\s*[;\[]', source, re.S)
    assert body, 'the table %s is not found: update this test together with the page' % name
    return dict(re.findall(r"'([^']+)'\s*:\s*'([^']+)'", body.group(1)))


class EngineMatrixTests(unittest.TestCase):
    def test_every_engine_is_known_to_every_table_that_needs_it(self):
        components = {row['id']: row for row in CATALOG['components']}
        installed = table(MANAGER, 'componentIds')
        assessed = table(PANEL, 'assessed')
        for engine_id, engine in registry.ENGINES.items():
            with self.subTest(engine=engine_id):
                self.assertIn(engine['adapter'], ADAPTERS)
                component = model_requirements.COMPONENTS.get('video:' + engine_id)
                self.assertIn(component, components, 'model_requirements.COMPONENTS has no catalog component for the engine')
                self.assertEqual(installed.get('video:' + engine_id), component, 'component-manager.js componentIds differs from model_requirements.COMPONENTS')
                self.assertIn("'%s': [" % engine_id, RELEASES, 'studio-ui.js has no release row for the engine')
                if engine_id in NO_GUIDE:
                    self.assertNotIn(engine_id, GUIDES)
                    self.assertNotIn(engine_id, assessed)
                    continue
                self.assertEqual(sorted(GUIDES[engine_id]), sorted(engine['prompt_enhancer']), 'the registry offers the rewrite for other modes than the guides cover')
                group = assessed.get(engine_id)
                self.assertTrue(group, 'video-panel.js: no strengths-and-limits group for the engine')
                for field in ASSESSMENT_FIELDS:
                    self.assertIn('video.assessment_%s_%s_text' % (group, field), VIDEO_STRINGS)

    def test_no_table_names_an_engine_the_registry_does_not_have(self):
        known = set(registry.ENGINES)
        named = {key.split(':', 1)[1] for key in model_requirements.COMPONENTS if key.startswith('video:')}
        named |= {key.split(':', 1)[1] for key in table(MANAGER, 'componentIds') if key.startswith('video:')}
        named |= set(table(PANEL, 'assessed')) | set(GUIDES)
        self.assertEqual(named - known, set())

    def test_the_components_of_the_catalog_that_engines_point_at_belong_to_the_video_module(self):
        components = {row['id']: row for row in CATALOG['components']}
        for key, component in model_requirements.COMPONENTS.items():
            if key.startswith('video:'):
                self.assertEqual(components[component]['module'], 'video', component)

    def test_assessment_texts_exist_in_both_languages_for_every_group(self):
        groups = set(table(PANEL, 'assessed').values())
        for group, field in itertools.product(groups, ASSESSMENT_FIELDS):
            row = VIDEO_STRINGS['video.assessment_%s_%s_text' % (group, field)]
            self.assertTrue(row['ru'].strip() and row['en'].strip(), (group, field))


class PageFormulaTests(unittest.TestCase):
    """The page lays out scenes and chains before the server is asked; the server validates the same numbers (video_registry.py)."""

    @staticmethod
    def page_constants():
        chains = re.search(r'chainSegments=n=>isLtx\(\)\?\{([^}]*)\}\[n\]:\{([^}]*)\}\[n\]', PANEL)
        layout = re.search(r'step=isLtx\(\)\?(\d+):(\d+),per=Math\.max\(isLtx\(\)\?(\d+):(\d+),.*?tooFew:per>(\d+)', PANEL, re.S)
        counts = re.search(r'n<(\d+) \|\| n>(\d+)', PANEL)
        assert chains and layout and counts, 'the page formulas moved: update this test together with video-panel.js'
        pairs = lambda text: {int(key): int(value) for key, value in re.findall(r'(\d+):(\d+)', text)}
        return dict(ltx_windows=pairs(chains.group(1)), wan_segments=pairs(chains.group(2)), steps=tuple(map(int, layout.group(1, 2))),
                    shortest=tuple(map(int, layout.group(3, 4))), longest=int(layout.group(5)), counts=tuple(map(int, counts.group(1, 2))))

    def test_chain_lengths_match_the_registry(self):
        page = self.page_constants()
        self.assertEqual(page['ltx_windows'], registry.LTX_WINDOWS)
        self.assertEqual(page['wan_segments'], registry.WAN_SEGMENTS)

    def test_scene_limits_match_the_registry(self):
        page = self.page_constants()
        self.assertEqual(page['counts'], registry.SCENE_COUNTS)
        self.assertEqual((page['shortest'][0], page['longest']), registry.LTX_SCENE_FRAMES)        # the page writes LTX first, Wan second
        self.assertEqual((page['shortest'][1], page['longest']), registry.SCENE_FRAMES)

    def test_the_scene_layout_of_the_page_gives_the_frames_the_server_validates(self):
        page = self.page_constants()
        for engine, index in (('ltx25-distilled', 0), ('wan22-ti2v-5b', 1)):
            step, shortest = page['steps'][index], page['shortest'][index]
            for total, scenes in itertools.product(registry.SCENE_TOTALS, range(registry.SCENE_COUNTS[0], registry.SCENE_COUNTS[1] + 1)):
                per = max(shortest, -(-(-(-total // scenes) - 1) // step) * step + 1)          # the page: Math.max(min, Math.ceil((Math.ceil(total/n)-1)/step)*step+1)
                with self.subTest(engine=engine, total=total, scenes=scenes):
                    if per > page['longest']:
                        with self.assertRaises(ValueError):
                            registry.scene_frames(total, scenes, engine)
                    else:
                        self.assertEqual(registry.scene_frames(total, scenes, engine), per)


if __name__ == '__main__':
    unittest.main()
