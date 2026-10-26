"""The repository stays source-only: .gitignore keeps payloads out and tools/check_repo.py catches what slips past."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import check_repo

HAVE_GIT = shutil.which('git') is not None


def git(directory, *arguments, check=True):
    return subprocess.run(['git', '-c', 'safe.directory=*', '-C', str(directory), *arguments], capture_output=True, text=True, check=check)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))
        return relative

    def test_clean_source_passes(self):
        files = [self.put('App/server/a.py', 'print(1)\n'), self.put('README.md', 'Contact: noreply@anthropic.com\n'),
                 self.put('docs/x.md', 'Use C:\\Users\\...\\Pictures and C:\\Users\\<you>\\x and %USERPROFILE%\n')]
        self.assertEqual(check_repo.check(self.root, files), [])

    def test_payloads_and_big_files_are_rejected(self):
        files = [self.put('App/model.safetensors', b'x'), self.put('App/big.json', b'0' * (check_repo.MAX_BYTES + 1)),
                 self.put('Models/Video/a.json', '{}'), self.put('Runtime/readme.txt', 'x'), self.put('Support/Diagnostics/a.json', '{}')]
        text = '\n'.join(check_repo.check(self.root, files))
        for needle in ('model.safetensors: forbidden', 'big.json', 'top-level item "Models"', 'top-level item "Runtime"', 'only Support/Licenses'):
            self.assertIn(needle, text)

    def test_private_data_is_rejected(self):
        folder = '\u0421\u043e\u0444\u0442 - \u041f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u044b'
        samples = {
            # Each sample is assembled from pieces so that this file itself stays clean for the guard.
            'docs/a.md': 'path C:\\Users\\' + 'jane\\Documents\\x',
            'docs/b.md': 'H:\\' + folder + '\\Studio',
            'docs/c.md': 'mail me at jane.doe@' + 'gmail.com',
            'docs/d.md': 'token hf_' + 'a' * 30,
            'docs/e.md': '-----BEGIN RSA ' + 'PRIVATE KEY-----',
        }
        problems = check_repo.check(self.root, [self.put(name, text) for name, text in samples.items()])
        for name in samples:
            self.assertTrue(any(line.startswith(name + ':') for line in problems), name)

    def test_license_contact_email_is_allowed_only_in_its_file(self):
        body = 'Contact legal@' + 'vendor.test\n'
        self.assertEqual(check_repo.check(self.root, [self.put('Support/Licenses/Qwen-Research.txt', body)]), [])
        self.assertTrue(check_repo.check(self.root, [self.put('Support/Licenses/Other.txt', body)]))


@unittest.skipUnless(HAVE_GIT, 'git not found')
class GitignoreTests(unittest.TestCase):
    def test_real_hooks_block_forced_payload_and_unsafe_history(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            work, remote = base/'source', base/'remote.git'
            work.mkdir()
            git(work, 'init', '-q')
            git(base, 'init', '--bare', '-q', str(remote))
            git(work, 'config', 'user.name', 'Source test')
            git(work, 'config', 'user.email', 'source@example.com')
            git(work, 'config', 'core.hooksPath', '.githooks')
            (work/'tools').mkdir()
            (work/'.githooks').mkdir()
            shutil.copyfile(ROOT/'tools/check_repo.py', work/'tools/check_repo.py')
            shutil.copyfile(ROOT/'.gitignore', work/'.gitignore')
            for hook in ('pre-commit', 'pre-push'):
                target = work/'.githooks'/hook
                shutil.copyfile(ROOT/'.githooks'/hook, target)
                target.chmod(0o755)
            (work/'README.md').write_text('Source only', encoding='utf-8')
            git(work, 'add', '.')
            git(work, 'commit', '-qm', 'source')
            git(work, 'remote', 'add', 'origin', str(remote))
            git(work, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
            (work/'Models').mkdir()
            (work/'Models/test.bin').write_bytes(b'fake weights')
            git(work, 'add', '-f', 'Models/test.bin')
            refused = git(work, 'commit', '-qm', 'forced payload', check=False)
            self.assertNotEqual(refused.returncode, 0)
            self.assertTrue((work/'Models/test.bin').exists())
            git(work, '-c', 'core.hooksPath=missing-hooks', 'commit', '-qm', 'unsafe historical payload')
            git(work, 'rm', '--cached', 'Models/test.bin')
            git(work, 'commit', '-qm', 'remove from current tree')
            refused = git(work, 'push', '-q', 'origin', 'HEAD:refs/heads/main', check=False)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('Models/test.bin', refused.stderr + refused.stdout)

    def test_staged_blob_cannot_be_hidden_by_editing_the_working_file(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            git(work, 'init', '-q')
            (work / 'docs').mkdir()
            file = work / 'docs/note.md'
            file.write_text('hf_' + 'a' * 30, encoding='utf-8')
            git(work, 'add', 'docs/note.md')
            file.write_text('Clean working file', encoding='utf-8')
            problems, count = check_repo.check_index(work)
            self.assertEqual(count, 1)
            self.assertTrue(any('token' in problem for problem in problems))

    def test_removed_payload_in_history_still_blocks_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            git(work, 'init', '-q')
            git(work, 'config', 'user.name', 'Source test')
            git(work, 'config', 'user.email', 'source@example.com')
            (work / 'Models').mkdir()
            payload = work / 'Models/payload.txt'
            payload.write_text('private model data', encoding='utf-8')
            git(work, 'add', '.')
            git(work, 'commit', '-qm', 'old payload')
            payload.unlink()
            (work / 'README.md').write_text('Source only now', encoding='utf-8')
            git(work, 'add', '-A')
            git(work, 'commit', '-qm', 'remove payload')
            problems, summary = check_repo.check_revision(work, 'HEAD')
            self.assertEqual(summary['commits'], 2)
            self.assertTrue(any('Models/payload.txt' in problem for problem in problems))

    def test_ignore_rules_keep_payloads_out_and_source_in(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            git(work, 'init', '-q')
            shutil.copyfile(ROOT / '.gitignore', work / '.gitignore')
            ignored = ['Models/Video/H3/a.safetensors', 'Runtime/Music/python/python.exe', 'UserData/Settings/x.json', 'UserData/Text/Chats/c.json',
                       'Support/Diagnostics/InstalledRelease.json', 'Notes.txt', 'App/server/__pycache__/a.pyc', 'App/bin/tool.exe',
                       'App/frontend/clip.mp4', 'tools/cache/model.gguf', 'VisualForge AI Studio.exe', 'reports/x.json']
            kept = ['README.md', 'Setup-VisualForge.cmd', 'App/server/server.py', 'App/frontend/app.js', 'App/frontend/visualforge-icon.png',
                    'docs/Text.md', 'tests/test_text.py', 'tools/check_repo.py', 'Support/Licenses/ComfyUI.txt', '.githooks/pre-commit']
            for relative in ignored + kept:
                path = work / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'x')
            listed = set(git(work, 'ls-files', '-co', '--exclude-standard').stdout.split('\n'))
            for relative in ignored:
                self.assertNotIn(relative, listed, relative)
            for relative in kept:
                self.assertIn(relative, listed, relative)

    def test_current_repository_passes_the_guard(self):
        if not (ROOT / '.git').exists():
            self.skipTest('not a Git checkout')
        files = check_repo.git_files(ROOT, 'candidates')
        self.assertEqual(check_repo.check(ROOT, files), [])


if __name__ == '__main__':
    unittest.main()
