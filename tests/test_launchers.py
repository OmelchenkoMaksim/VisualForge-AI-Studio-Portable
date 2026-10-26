"""The launch scripts must agree with each other and with the catalog; nothing here runs them."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_bootstrap_pins_the_same_python_as_the_catalog(self):
        script = (ROOT / 'App/Setup-Bootstrap.ps1').read_text(encoding='utf-8')
        pinned = re.search(r"\$embedSha256 = '([0-9a-f]{64})'", script).group(1)
        url = re.search(r"\$embedUrl = '([^']+)'", script).group(1)
        catalog = json.loads((ROOT / 'App/config/components.json').read_text(encoding='utf-8'))
        python = next(d for c in catalog['components'] if c['id'] == 'music.runtime' for d in c['downloads'] if d['id'] == 'python')
        self.assertEqual(pinned, python['sha256'])
        self.assertEqual(url, python['urls'][0])

    def test_launchers_point_at_existing_files(self):
        start = (ROOT / 'Start-VisualForge.cmd').read_text(encoding='utf-8')
        setup = (ROOT / 'Setup-VisualForge.cmd').read_text(encoding='utf-8')
        for relative in re.findall(r'%~dp0([\w\\.-]+)', start + setup):
            self.assertTrue((ROOT / relative.replace('\\', '/')).is_file(), relative)
        self.assertIn('Runtime\\ComfyUI_windows_portable\\python_embeded\\python.exe', start)
        entry = (ROOT / 'App/Setup-VisualForge.py').read_text(encoding='utf-8')
        self.assertIn('provision.cli', entry)

    def test_every_model_component_requires_programs_that_the_catalog_lists_as_software(self):
        catalog = json.loads((ROOT / 'App/config/components.json').read_text(encoding='utf-8'))
        software = {c['id'] for c in catalog['components'] if c['kind'] == 'software'}
        for component in catalog['components']:
            if component['kind'] == 'model':
                self.assertTrue(software & set(component['requires']), component['id'])

    def test_license_sensitive_models_carry_a_note(self):
        catalog = json.loads((ROOT / 'App/config/components.json').read_text(encoding='utf-8'))
        notes = {c['id']: c.get('note', '') for c in catalog['components']}
        self.assertIn('European Union', notes['video.model.h3'])
        self.assertTrue(notes['music.model.minimax-music3'])
        self.assertTrue(notes['image.model.qwen-image-21-uc'])

    def test_voice_input_is_wired_into_the_page_and_the_bridge(self):
        page = (ROOT / 'App/frontend/index.html').read_text(encoding='utf-8')
        server = (ROOT / 'App/server/server.py').read_text(encoding='utf-8')
        for asset in ('voice.js', 'voice.css'):
            self.assertIn('/' + asset, page)
            self.assertIn("STATIC['/%s']" % asset, server)
            self.assertTrue((ROOT / 'App/frontend' / asset).is_file())
        for route in ('/api/speech/transcribe', '/api/speech/prepare', '/api/speech/'):
            self.assertIn(route, server)
        catalog = json.loads((ROOT / 'App/config/components.json').read_text(encoding='utf-8'))
        models = [c for c in catalog['components'] if c['module'] == 'speech']
        self.assertEqual(sorted(c['id'] for c in models), ['speech.model.rupunct-big', 'speech.model.whisper-large-v3', 'speech.model.whisper-small', 'speech.model.whisper-turbo'])
        self.assertTrue(all(c['requires'] == ['music.runtime'] and any(f['path'].endswith(('model.safetensors', 'pytorch_model.bin')) for f in c['files']) for c in models))

    def test_the_generation_lock_blocks_only_start_buttons_and_every_one_exists(self):
        script = (ROOT / 'App/frontend/studio-ui.js').read_text(encoding='utf-8')
        page = (ROOT / 'App/frontend/index.html').read_text(encoding='utf-8')
        blocked = re.search(r"const BLOCKED = '([^']+)'", script).group(1)
        selectors = [part.strip() for part in blocked.split(',')]
        for selector in selectors:
            if selector.startswith('#') and ' ' not in selector:
                self.assertIn('id="%s"' % selector[1:], page, selector)
        for must_block in ('#generate', '#videoGenerate', '#musicGenerate', '#textSend', '#textRegenerate', '#videoExportStart', '#videoMixStart', '#componentsStart'):
            self.assertIn(must_block, selectors)
        for free in ('#imageTab', '#videoTab', '#musicTab', '#textTab', '#componentsTab', '#engineSelect', '#videoEngine', '#musicEngine', '#textInput', '#prompt', '#textStop', '#cancel'):
            self.assertNotIn(free, selectors)
        css = (ROOT / 'App/frontend/studio-ui.css').read_text(encoding='utf-8')
        self.assertIn(blocked, css, 'the dimming rule must list the same buttons as the lock')
        for form in ('#form', '#videoForm', '#musicForm', '#textForm'):
            self.assertIn(form, script)

    def test_launch_splash_is_shown_at_once_and_closed_on_every_path(self):
        script = (ROOT / 'App/Start-VisualForge.ps1').read_text(encoding='utf-8-sig').replace('\r\n', '\n')
        for setting in ('Topmost="True"', 'ShowActivated="False"', 'ShowInTaskbar="False"', 'Width="100"', 'Height="100"', 'RepeatBehavior="Forever"'):
            self.assertIn(setting, script)
        main = script.split("\ntry {\n    Log ('START v3", 1)[1]
        self.assertTrue(main.startswith(" mode='+$Mode)\n    if ($Mode -ne 'Cleanup') { Start-Splash }\n"))   # first thing, before any check
        self.assertIn("'?launch='+$launchToken", main)
        self.assertIn('Close-Splash\n            Maximize-QwenBrowserWindow', main)                    # an already open window: no wait
        self.assertIn('} finally {Unlock-Lifecycle}\n    Wait-LaunchReady 180\n    Close-Splash', main)   # waits outside the lifecycle mutex
        self.assertIn('} catch {\n    Close-Splash', main)                                          # before the error box
        self.assertIn('} finally {\n    Close-Splash', main)
        page = (ROOT / 'App/frontend/app.js').read_text(encoding='utf-8')
        self.assertIn('check().then(reportLaunch);', page)
        self.assertIn("'/api/launch-ready'", (ROOT / 'App/server/server.py').read_text(encoding='utf-8'))

    def test_supervisor_parses_in_windows_powershell(self):
        import shutil
        import subprocess
        shell = shutil.which('powershell.exe')
        if not shell:
            self.skipTest('Windows PowerShell is not available')
        command = ("$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('%s', [ref]$null, [ref]$e); "
                   "if ($e) { $e | ForEach-Object { $_.Message }; exit 1 }" % str(ROOT / 'App/Start-VisualForge.ps1').replace("'", "''"))
        done = subprocess.run([shell, '-NoProfile', '-Command', command], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout[-500:] + done.stderr[-500:])

    def test_frontend_scripts_parse(self):
        import shutil
        import subprocess
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        for script in ('voice.js', 'components.js', 'text.js', 'studio-ui.js'):
            done = subprocess.run([node, '--check', str(ROOT / 'App/frontend' / script)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, script + ': ' + done.stderr[:300])


if __name__ == '__main__':
    unittest.main()
