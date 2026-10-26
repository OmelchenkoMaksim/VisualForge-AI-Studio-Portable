"""The component installer: downloads, archives, catalog rules, engine behaviour and the command line, all against a local server."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
sys.path.insert(0, str(ROOT / 'tests'))
from fake_web import FakeWeb
from provision import archives, catalog, cli, engine as engine_module, net, steps, vcredist
from provision.engine import Engine


def make_zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as handle:
        for name, data in entries.items():
            handle.writestr(name, data)
    return buffer.getvalue()


def make_targz(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as handle:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            handle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class TempCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()


class DownloadTests(TempCase):
    def setUp(self):
        super().setUp()
        self.web = FakeWeb().__enter__()
        self.data = os.urandom(300000)
        self.info = self.web.add('/file.bin', self.data)

    def tearDown(self):
        self.web.__exit__()
        super().tearDown()

    def test_downloads_and_verifies(self):
        seen = []
        path = net.download(self.info['url'], self.root / 'a/file.bin', self.info['bytes'], self.info['sha256'], lambda done, total: seen.append((done, total)))
        self.assertEqual(path.read_bytes(), self.data)
        self.assertEqual(seen[-1], (len(self.data), len(self.data)))
        self.assertFalse((self.root / 'a/file.bin.part').exists())

    def test_existing_file_is_not_downloaded_again(self):
        target = self.root / 'file.bin'
        target.write_bytes(self.data)
        net.download(self.info['url'], target, self.info['bytes'], self.info['sha256'])
        self.assertEqual(self.web.requests, [])

    def test_corrupt_first_mirror_falls_back_to_identical_pinned_bytes(self):
        bad = self.web.add('/corrupt.bin', b'x' * len(self.data))
        target = net.download([bad['url'], self.info['url']], self.root / 'fallback.bin', self.info['bytes'], self.info['sha256'])
        self.assertEqual(target.read_bytes(), self.data)
        self.assertFalse((self.root / 'fallback.bin.part').exists())

    def test_resumes_after_a_dropped_connection(self):
        self.web.drop_once['/file.bin'] = 120000
        net.download(self.info['url'], self.root / 'file.bin', self.info['bytes'], self.info['sha256'], sleep=lambda s: None)
        self.assertEqual((self.root / 'file.bin').read_bytes(), self.data)
        ranges = [r for _, r in self.web.requests]
        self.assertEqual(ranges[0], None)
        self.assertEqual(ranges[1], 'bytes=120000-')

    def test_a_flaky_line_that_keeps_making_progress_still_finishes(self):
        # Large models take many reconnects on a home line; only attempts that bring nothing count towards giving up.
        self.web.drop_each['/file.bin'] = 30000
        waits = []
        net.download(self.info['url'], self.root / 'file.bin', self.info['bytes'], self.info['sha256'], sleep=waits.append)
        self.assertEqual((self.root / 'file.bin').read_bytes(), self.data)
        self.assertGreater(len(self.web.requests), net.RETRIES)
        self.assertEqual(set(waits), {2})

    def test_resumes_from_an_old_part_file(self):
        (self.root / 'file.bin.part').write_bytes(self.data[:1000])
        net.download(self.info['url'], self.root / 'file.bin', self.info['bytes'], self.info['sha256'])
        self.assertEqual((self.root / 'file.bin').read_bytes(), self.data)
        self.assertEqual(self.web.requests[0][1], 'bytes=1000-')

    def test_wrong_hash_is_rejected_and_not_kept(self):
        with self.assertRaises(net.ChecksumError):
            net.download(self.info['url'], self.root / 'file.bin', self.info['bytes'], '0' * 64)
        self.assertFalse((self.root / 'file.bin').exists())
        self.assertFalse((self.root / 'file.bin.part').exists())

    def test_wrong_size_is_rejected(self):
        with self.assertRaises(net.DownloadError):
            net.download(self.info['url'], self.root / 'file.bin', len(self.data) - 5, self.info['sha256'])

    def test_missing_file_fails_fast_and_next_mirror_is_tried(self):
        with self.assertRaises(net.DownloadError):
            net.download(self.web.base + '/missing', self.root / 'x.bin', None, None, sleep=lambda s: None)
        net.download([self.web.base + '/missing', self.info['url']], self.root / 'y.bin', self.info['bytes'], self.info['sha256'], sleep=lambda s: None)
        self.assertTrue((self.root / 'y.bin').is_file())

    def test_follows_redirects_and_keeps_the_token_to_its_own_host(self):
        self.web.redirects['/old'] = '/file.bin'
        net.download(self.web.base + '/old', self.root / 'r.bin', self.info['bytes'], self.info['sha256'], environ={'HF_TOKEN': 'secret'})
        self.assertTrue((self.root / 'r.bin').is_file())
        self.assertTrue(all(auth is None for _, auth in self.web.auth_seen))   # 127.0.0.1 is not huggingface.co

    def test_cancel_keeps_the_partial_file(self):
        big = self.web.add('/big.bin', os.urandom(3 * 1024 * 1024 + 5))
        calls = []

        def cancel():
            calls.append(1)
            return len(calls) > 2
        with self.assertRaises(net.Cancelled):
            net.download(big['url'], self.root / 'big.bin', big['bytes'], big['sha256'], cancel=cancel)
        self.assertFalse((self.root / 'big.bin').exists())
        self.assertTrue((self.root / 'big.bin.part').is_file())


class ArchiveTests(TempCase):
    def test_zip_strip_keep_skip(self):
        blob = make_zip({'top/a.txt': b'a', 'top/sub/b.txt': b'b', 'top/test_x.py': b'x', 'top/other/c.txt': b'c'})
        archive = self.root / 'a.zip'
        archive.write_bytes(blob)
        out = self.root / 'out'
        archives.extract(archive, out, strip=1, skip=lambda r: r.startswith('test_') or r.startswith('other/'))
        self.assertEqual(sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()), ['a.txt', 'sub/b.txt'])

    def test_path_traversal_is_refused(self):
        archive = self.root / 'bad.zip'
        archive.write_bytes(make_zip({'../evil.txt': b'x'}))
        with self.assertRaises(archives.ArchiveError):
            archives.extract(archive, self.root / 'out')
        self.assertFalse((self.root / 'evil.txt').exists())

    def test_zip_symlinks_become_plain_files_and_cannot_escape(self):
        def link_zip(name, target):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as handle:
                info = zipfile.ZipInfo(name)
                info.external_attr = (0o120777 << 16)
                handle.writestr(info, target)
                handle.writestr('top/real.md', 'real')
            return buffer.getvalue()
        archive = self.root / 'l.zip'
        archive.write_bytes(link_zip('top/AGENTS.md', 'real.md'))
        archives.extract(archive, self.root / 'ok', strip=1)
        self.assertEqual((self.root / 'ok/AGENTS.md').read_text(), 'real.md')
        self.assertFalse((self.root / 'ok/AGENTS.md').is_symlink())
        archive.write_bytes(link_zip('top/AGENTS.md', '../../outside'))
        with self.assertRaises(archives.ArchiveError):
            archives.extract(archive, self.root / 'bad', strip=1)

    def test_tar_gz_and_links(self):
        archive = self.root / 'a.tar.gz'
        archive.write_bytes(make_targz({'python/python.exe': b'exe', 'python/Lib/x.py': b'x'}))
        out = self.root / 'out'
        archives.extract(archive, out)
        self.assertEqual((out / 'python/python.exe').read_bytes(), b'exe')
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as handle:
            info = tarfile.TarInfo('link')
            info.type = tarfile.SYMTYPE
            info.linkname = '/etc/passwd'
            handle.addfile(info)
        link = self.root / 'link.tar.gz'
        link.write_bytes(buffer.getvalue())
        with self.assertRaises(archives.ArchiveError):
            archives.extract(link, self.root / 'out2')


def tiny_catalog(web, with_broken=False):
    zip_info = web.add('/runtime.zip', make_zip({'pkg/bin/tool.exe': b'TOOL', 'pkg/readme.txt': b'hello', 'pkg/rocm/skip.dll': b'x'}))
    model_a, model_b = os.urandom(5000), os.urandom(7000)
    a, b = web.add('/a.bin', model_a), web.add('/b.bin', model_b)
    runtime = dict(id='demo.runtime', module='demo', kind='software', title='Demo runtime', disk_bytes=1000, present=['Runtime/Demo/bin/tool.exe'],
                   downloads=[dict(id='zip', urls=[zip_info['url']], filename='runtime.zip', bytes=zip_info['bytes'], sha256=zip_info['sha256'])],
                   steps=[dict(op='extract', download='zip', to='Runtime/Demo', strip=1, skip=['rocm/*']), dict(op='write_text', path='Runtime/Demo/marker.txt', content='ok')])
    model1 = dict(id='demo.model.a', module='demo', kind='model', title='Model A', requires=['demo.runtime'],
                  files=[dict(path='Models/Demo/a.bin', bytes=a['bytes'], sha256=a['sha256'], urls=[a['url']])], after=[dict(op='copy', **{'from': 'Models/Demo/a.bin', 'to': 'Models/Demo/a-copy.bin'})])
    model2 = dict(id='demo.model.b', module='demo', kind='model', title='Model B', requires=['demo.runtime'],
                  files=[dict(path='Models/Demo/b.bin', bytes=b['bytes'], sha256=b['sha256'], urls=[b['url'] if not with_broken else web.base + '/gone'])])
    return dict(schema_version=1, catalog_version='t', modules=[dict(id='demo', title='Demo', components=['demo.runtime', 'demo.model.a', 'demo.model.b'])],
                presets={'everything': dict(title='x', models='all'), 'only-a': dict(title='a', models=['demo.model.a'])}, components=[runtime, model1, model2]), model_a, model_b


class CatalogRuleTests(unittest.TestCase):
    def test_closure_orders_requirements_first(self):
        with FakeWeb() as web:
            data, _, _ = tiny_catalog(web)
        self.assertEqual(catalog.closure(data, ['demo.model.b']), ['demo.runtime', 'demo.model.b'])

    def test_validation_rejects_bad_catalogs(self):
        with FakeWeb() as web:
            data, _, _ = tiny_catalog(web)
        for mutate in (lambda d: d['components'][1]['files'][0].update(path='../x'), lambda d: d['components'][1]['files'][0].update(sha256='abc'),
                       lambda d: d['components'][1].update(requires=['nope']), lambda d: d['components'][0]['steps'][0].update(download='missing'),
                       lambda d: d['components'].append(dict(d['components'][0]))):
            broken = json.loads(json.dumps(data))
            mutate(broken)
            with self.assertRaises(catalog.CatalogError):
                catalog.validate(broken)

    def test_shipped_catalog_is_consistent(self):
        path = ROOT / 'App/config/components.json'
        if not path.is_file():
            self.skipTest('catalog not generated yet')
        data = catalog.load(path)
        paths, ids = {}, {c['id'] for c in data['components']}
        for component in data['components']:
            for item in component.get('files', []):
                self.assertTrue(all(url.startswith('https://') for url in item['urls']), item['path'])
                self.assertNotIn(item['path'], paths, 'two components write ' + item['path'])
                paths[item['path']] = component['id']
            for item in component.get('downloads', []):
                self.assertTrue(all(url.startswith('https://') for url in item['urls']), item['id'])
            for step in component.get('steps', []) + component.get('after', []):
                self.assertIn(step['op'], steps.OPS)
        software = [c for c in data['components'] if c['kind'] == 'software']
        self.assertTrue(software)
        for component in data['components']:
            if component['kind'] == 'model':
                self.assertTrue(component.get('requires'), component['id'] + ' must require its runtime')
                self.assertTrue(any(r in {s['id'] for s in software} for r in component['requires']))
        for module in data['modules']:
            self.assertTrue(set(module['components']) <= ids)


class EngineTests(TempCase):
    def setUp(self):
        super().setUp()
        self.web = FakeWeb().__enter__()
        self.data, self.model_a, self.model_b = tiny_catalog(self.web)
        self.catalog_path = self.root / 'App/config/components.json'
        self.catalog_path.parent.mkdir(parents=True)
        self.catalog_path.write_text(json.dumps(self.data), encoding='utf-8')
        self.events = []
        self.install_root = self.root / 'inst'
        self.install_root.mkdir()

    def tearDown(self):
        self.web.__exit__()
        super().tearDown()

    def engine(self, **keywords):
        return Engine(self.install_root, self.catalog_path, emit=self.events.append, **keywords)

    def test_software_is_always_selected_models_are_optional(self):
        engine = self.engine()
        self.assertEqual(engine.select(), ['demo.runtime'])
        self.assertEqual(engine.select(models=['demo.model.a']), ['demo.runtime', 'demo.model.a'])
        self.assertEqual(engine.select(all_models=True), ['demo.runtime', 'demo.model.a', 'demo.model.b'])
        with self.assertRaises(engine_module.ProvisionError):
            engine.select(models=['unknown'])

    def test_install_status_and_idempotence(self):
        engine = self.engine()
        self.assertEqual({k: v['state'] for k, v in engine.status().items()}, dict.fromkeys(self.data_ids(), 'missing'))
        result = engine.install(engine.select(models=['demo.model.a']))
        self.assertEqual(result, {'demo.runtime': 'installed', 'demo.model.a': 'installed'})
        root = self.install_root
        self.assertEqual((root / 'Runtime/Demo/bin/tool.exe').read_bytes(), b'TOOL')
        self.assertFalse((root / 'Runtime/Demo/rocm/skip.dll').exists())
        self.assertEqual((root / 'Models/Demo/a-copy.bin').read_bytes(), self.model_a)
        self.assertFalse((root / 'UserData/Cache/Downloads/demo.runtime').exists(), 'downloads are removed after success')
        states = {k: v['state'] for k, v in engine.status().items()}
        self.assertEqual(states, {'demo.runtime': 'installed', 'demo.model.a': 'installed', 'demo.model.b': 'missing'})
        count = len(self.web.requests)
        self.assertEqual(set(engine.install(engine.select(models=['demo.model.a'])).values()), {'skipped'})
        self.assertEqual(len(self.web.requests), count)
        self.assertTrue(engine.read_state()['components']['demo.model.a'])

    def data_ids(self):
        return [c['id'] for c in self.data['components']]

    def test_model_later_needs_no_software_download(self):
        engine = self.engine()
        engine.install(engine.select())
        before = len(self.web.requests)
        engine.install(engine.select(models=['demo.model.b']))
        paths = {p for p, _ in self.web.requests[before:]}
        self.assertEqual(paths, {'/b.bin'})

    def test_partial_model_reports_remaining_bytes_and_resumes(self):
        engine = self.engine()
        engine.install(engine.select())
        part = self.install_root / 'Models/Demo/b.bin.part'
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(self.model_b[:3000])
        self.assertEqual(engine.status()['demo.model.b']['remaining_bytes'], len(self.model_b))
        engine.install(['demo.model.b'])
        self.assertEqual((self.install_root / 'Models/Demo/b.bin').read_bytes(), self.model_b)
        self.assertIn('bytes=3000-', [r for _, r in self.web.requests])

    def test_failed_dependency_blocks_dependants_but_not_others(self):
        data, _, _ = tiny_catalog(self.web, with_broken=True)
        self.catalog_path.write_text(json.dumps(data), encoding='utf-8')
        engine = self.engine()
        result = engine.install(engine.select(models=['demo.model.a', 'demo.model.b']))
        self.assertEqual(result['demo.model.a'], 'installed')
        self.assertTrue(result['demo.model.b'].startswith('failed'))
        self.assertTrue(any(e['type'] == 'component_failed' for e in self.events))

    def test_space_check_refuses_before_downloading(self):
        engine = self.engine()
        real = shutil.disk_usage
        shutil.disk_usage = lambda path: shutil._ntuple_diskusage(10 ** 12, 10 ** 12 - 100, 100)
        try:
            with self.assertRaises(engine_module.NotEnoughSpace):
                engine.install(engine.select())
        finally:
            shutil.disk_usage = real
        self.assertEqual(self.web.requests, [])

    def test_second_installer_is_refused_while_one_runs(self):
        engine = self.engine()
        started, release = threading.Event(), threading.Event()
        original = net.download

        def slow(*arguments, **keywords):
            started.set()
            release.wait(10)
            return original(*arguments, **keywords)
        net.download = slow
        outcome = {}
        thread = threading.Thread(target=lambda: outcome.update(engine.install(engine.select())))
        try:
            thread.start()
            self.assertTrue(started.wait(10))
            with self.assertRaises(engine_module.Busy):
                self.engine().install(self.engine().select(), force=True)
        finally:
            release.set()
            thread.join(20)
            net.download = original
        self.assertEqual(outcome.get('demo.runtime'), 'installed')

    def test_verify_finds_damage(self):
        engine = self.engine()
        engine.install(engine.select(models=['demo.model.a']))
        self.assertEqual(engine.verify(['demo.model.a']), {'demo.runtime': [], 'demo.model.a': []})
        target = self.install_root / 'Models/Demo/a.bin'
        damaged = bytearray(target.read_bytes())
        damaged[10] ^= 1
        target.write_bytes(bytes(damaged))
        self.assertEqual(engine.verify(['demo.model.a'])['demo.model.a'], ['sha256: Models/Demo/a.bin'])

    def test_ad_hoc_text_models(self):
        engine = self.engine()
        ident = engine.text_model_component('gemma4:e4b')
        self.assertEqual(ident, 'text.model.gemma4-e4b')
        self.assertEqual(engine.by_id[ident]['present'], ['Models/Text/ollama/manifests/registry.ollama.ai/library/gemma4/e4b'])
        imported = engine.ollama_import_component('my/model:latest', str(self.root / 'store'))
        self.assertIn('my', engine.by_id[imported]['present'][0])


class OllamaToolTests(TempCase):
    """Text models: pulled through a stand-in `ollama serve`, or copied (and re-hashed) from another store."""

    def setUp(self):
        super().setUp()
        self.web = FakeWeb().__enter__()
        archive = self.web.add('/ollama.zip', make_zip({'ollama.exe': b'MZ-fake', 'lib/ollama/rocm/x.dll': b'x'}))
        runtime = dict(id='text.runtime', module='text', kind='software', title='Ollama', present=['Runtime/Text/ollama/ollama.exe'],
                       downloads=[dict(id='z', urls=[archive['url']], filename='ollama.zip', bytes=archive['bytes'], sha256=archive['sha256'])],
                       steps=[dict(op='extract', download='z', to='Runtime/Text/ollama', skip=['lib/ollama/rocm/*'])])
        data = dict(schema_version=1, catalog_version='t', modules=[dict(id='text', title='Text', components=['text.runtime'])], presets={}, components=[runtime])
        self.catalog_path = self.root / 'components.json'
        self.catalog_path.write_text(json.dumps(data), encoding='utf-8')
        self.install_root = self.root / 'inst'
        self.events = []
        self.engine = Engine(self.install_root, self.catalog_path, emit=self.events.append, ollama_command=[sys.executable, str(ROOT / 'tests/fake_ollama.py')])

    def tearDown(self):
        self.web.__exit__()
        super().tearDown()

    def test_pull_puts_the_model_into_the_private_store(self):
        ident = self.engine.text_model_component('gemma4:e4b')
        result = self.engine.install([ident])
        self.assertEqual(result, {'text.runtime': 'installed', ident: 'installed'}, result)
        self.assertTrue((self.install_root / 'Models/Text/ollama/manifests/registry.ollama.ai/library/gemma4/e4b').is_file())
        self.assertFalse((self.install_root / 'Runtime/Text/ollama/lib/ollama/rocm').exists())
        self.assertTrue(any(e['type'] == 'progress' and e['total'] == 1000 for e in self.events))
        self.assertEqual(self.engine.status()[ident]['state'], 'installed')

    def test_a_registry_error_is_reported_as_a_failed_component(self):
        ident = self.engine.text_model_component('missing-model:1b')
        result = self.engine.install([ident])
        self.assertTrue(result[ident].startswith('failed:') and 'does not exist' in result[ident], result)
        self.assertEqual(result['text.runtime'], 'installed')

    def test_import_copies_and_verifies_blobs(self):
        import hashlib as hashing
        source = self.root / 'theirs'
        blob = b'weights' * 1000
        digest = 'sha256:' + hashing.sha256(blob).hexdigest()
        (source / 'blobs').mkdir(parents=True)
        (source / 'blobs' / digest.replace(':', '-')).write_bytes(blob)
        manifest = source / 'manifests/registry.ollama.ai/library/mine'
        manifest.mkdir(parents=True)
        (manifest / 'latest').write_text(json.dumps({'config': {'digest': digest, 'size': len(blob)}, 'layers': [{'digest': digest, 'size': len(blob), 'mediaType': 'x.model'}]}))
        ident = self.engine.ollama_import_component('mine:latest', str(source))
        result = self.engine.install([ident])
        self.assertEqual(result[ident], 'installed', result)
        self.assertEqual((self.install_root / 'Models/Text/ollama/blobs' / digest.replace(':', '-')).read_bytes(), blob)
        # a damaged source blob is refused
        (source / 'blobs' / digest.replace(':', '-')).write_bytes(b'x' * len(blob))
        shutil.rmtree(self.install_root / 'Models/Text/ollama')
        result = self.engine.install([ident], force=True)
        self.assertTrue(result[ident].startswith('failed') and 'damaged' in result[ident], result)
        self.assertFalse((self.install_root / 'Models/Text/ollama/manifests/registry.ollama.ai/library/mine/latest').exists())

    def test_model_names_are_validated(self):
        from provision.ollama_tools import split_name
        self.assertEqual(split_name('gemma4:e4b'), ('registry.ollama.ai', 'library', 'gemma4', 'e4b'))
        self.assertEqual(split_name('user/model'), ('registry.ollama.ai', 'user', 'model', 'latest'))
        for bad in ('', 'a:b:c/../x', '..', 'x\\y', 'a/b/c/d', 'host:80/ns/m', 'm:../t', '-m'):
            with self.assertRaises(steps.StepError):
                split_name(bad)


class StepTests(TempCase):
    def context(self, downloads=None):
        return steps.Context(self.root, downloads or {}, self.root / 'cache')

    def test_paths_cannot_leave_the_root(self):
        context = self.context()
        for bad in ('../x', 'C:/x', '/x'):
            with self.assertRaises(Exception):
                context.path(bad)

    def test_move_tree_does_not_overwrite(self):
        (self.root / 'src/sub').mkdir(parents=True)
        (self.root / 'src/a.txt').write_text('new')
        (self.root / 'src/sub/b.txt').write_text('b')
        (self.root / 'dst').mkdir()
        (self.root / 'dst/a.txt').write_text('old')
        steps.run_step(self.context(), dict(op='move_tree', **{'from': 'src', 'to': 'dst'}))
        self.assertEqual((self.root / 'dst/a.txt').read_text(), 'old')
        self.assertEqual((self.root / 'dst/sub/b.txt').read_text(), 'b')
        self.assertFalse((self.root / 'src').exists())

    def test_verify_files_detects_a_wrong_hash(self):
        (self.root / 'f.txt').write_text('x')
        steps.run_step(self.context(), dict(op='verify_files', files=[dict(path='f.txt', sha256=hashlib.sha256(b'x').hexdigest())]))
        with self.assertRaises(steps.StepError):
            steps.run_step(self.context(), dict(op='verify_files', files=[dict(path='f.txt', sha256='0' * 64)]))

    @unittest.skipUnless(shutil.which('git'), 'git not found')
    def test_git_apply_patches_a_plain_folder(self):
        folder = self.root / 'src'
        folder.mkdir()
        (folder / 'a.txt').write_bytes(b'one\ntwo\nthree\n')
        patch = ('--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n').encode()
        (self.root / 'p.patch').write_bytes(patch)
        steps.run_step(self.context(), dict(op='git_apply', cwd='src', patches=['p.patch']))
        self.assertEqual((folder / 'a.txt').read_bytes(), b'one\nTWO\nthree\n')

    def test_unpack_wheels_and_sdist(self):
        wheel = self.root / 'dl/a-1-py3-none-any.whl'
        sdist = self.root / 'dl/sox-1.tar.gz'
        wheel.parent.mkdir()
        wheel.write_bytes(make_zip({'pkg/__init__.py': b'1', 'pkg-1.dist-info/METADATA': b'm'}))
        sdist.write_bytes(make_targz({'sox-1/sox/__init__.py': b'2', 'sox-1/setup.py': b'x'}))
        steps.run_step(self.context({'w': wheel, 's': sdist}), dict(op='unpack_wheels', downloads=['w', 's'], to='voice', sdist_keep='sox/'))
        self.assertEqual((self.root / 'voice/pkg/__init__.py').read_bytes(), b'1')
        self.assertEqual((self.root / 'voice/sox/__init__.py').read_bytes(), b'2')
        self.assertFalse((self.root / 'voice/setup.py').exists())


@unittest.skipUnless(os.name == 'nt' and shutil.which('makecab'), 'needs Windows makecab/expand')
class VcRedistTests(TempCase):
    def cab(self, name, files):
        source = self.root / ('src-' + name)
        source.mkdir()
        directive = ['.OPTION EXPLICIT', '.Set CabinetNameTemplate=%s.cab' % name, '.Set DiskDirectoryTemplate=%s' % (self.root / 'out'), '.Set Cabinet=on', '.Set Compress=on']
        for file, data in files.items():
            (source / file).write_bytes(data)
            directive.append('"%s" "%s"' % (source / file, file))
        ddf = self.root / (name + '.ddf')
        ddf.write_text('\n'.join(directive), encoding='ascii')
        subprocess.run(['makecab', '/F', str(ddf)], check=True, capture_output=True, cwd=str(self.root))
        return (self.root / 'out' / (name + '.cab')).read_bytes()

    def test_extracts_pinned_dlls_from_nested_cabinets(self):
        dll, text = b'MZ' + os.urandom(2000), b'{\\rtf1 license}'
        inner = self.cab('inner', {'msvcp140.dll_amd64': dll})
        outer = self.cab('attached', {'a12': inner, 'a0': b'noise'})
        ux = self.cab('ux', {'u4': text, '0': b'<xml/>'})
        package = self.root / 'vc_redist.x64.exe'
        package.write_bytes(b'MZ' + b'\0' * 700 + ux + b'\0' * 33 + outer)
        files = [dict(name='msvcp140.dll', bytes=len(dll), sha256=hashlib.sha256(dll).hexdigest())]
        vcredist.extract(package, self.root / 'Runtime/Music/python', files, self.root / 'tmp', None, None, dict(name='MSVC-LICENSE.rtf', sha256=hashlib.sha256(text).hexdigest()))
        self.assertEqual((self.root / 'Runtime/Music/python/msvcp140.dll').read_bytes(), dll)
        self.assertEqual((self.root / 'Runtime/Music/MSVC-LICENSE.rtf').read_bytes(), text)
        with self.assertRaises(vcredist.VcRedistError):
            vcredist.extract(package, self.root / 'x', [dict(name='msvcp140.dll', bytes=1, sha256='0' * 64)], self.root / 'tmp2')


class CommandLineTests(TempCase):
    def setUp(self):
        super().setUp()
        self.web = FakeWeb().__enter__()
        self.data, self.model_a, self.model_b = tiny_catalog(self.web)
        self.catalog_path = self.root / 'components.json'
        self.catalog_path.write_text(json.dumps(self.data), encoding='utf-8')
        self.install_root = self.root / 'inst'

    def tearDown(self):
        self.web.__exit__()
        super().tearDown()

    def run_cli(self, *arguments, stdin=''):
        out = io.StringIO()
        code = cli.run(['--root', str(self.install_root), '--catalog', str(self.catalog_path), '--lang', 'en', *arguments], io.StringIO(stdin), out, interactive=None if not stdin else True)
        return code, out.getvalue()

    def test_software_only_installs_no_model(self):
        code, text = self.run_cli('--software-only')
        self.assertEqual(code, 0, text)
        self.assertTrue((self.install_root / 'Runtime/Demo/bin/tool.exe').is_file())
        self.assertFalse((self.install_root / 'Models/Demo').exists())
        self.assertIn('Models can be downloaded later', text)
        self.assertTrue(engine_module.Engine(self.install_root, self.catalog_path).first_run_done())

    def test_models_flag_and_preset(self):
        code, _ = self.run_cli('--models', 'demo.model.b')
        self.assertEqual(code, 0)
        self.assertTrue((self.install_root / 'Models/Demo/b.bin').is_file())
        self.assertFalse((self.install_root / 'Models/Demo/a.bin').exists())
        code, _ = self.run_cli('--preset', 'only-a')
        self.assertEqual(code, 0)
        self.assertTrue((self.install_root / 'Models/Demo/a.bin').is_file())

    def test_status_dry_run_and_unknown_component(self):
        code, text = self.run_cli('--status')
        self.assertEqual(code, 0)
        self.assertIn('demo.model.a', text)
        code, text = self.run_cli('--all', '--dry-run')
        self.assertIn('demo.runtime', text)
        self.assertFalse(self.install_root.exists() and (self.install_root / 'Runtime').exists())
        with self.assertRaises(engine_module.ProvisionError):
            self.run_cli('--models', 'nope')

    def test_menu_toggles_models_then_installs(self):
        code, text = self.run_cli(stdin='1\ni\n')
        self.assertEqual(code, 0, text)
        self.assertTrue((self.install_root / 'Models/Demo/a.bin').is_file())
        self.assertFalse((self.install_root / 'Models/Demo/b.bin').exists())

    def test_menu_quit_installs_nothing_and_no_terminal_is_explained(self):
        code, text = self.run_cli(stdin='q\n')
        self.assertEqual(code, 0)
        self.assertFalse(self.install_root.exists() and (self.install_root / 'Runtime').exists())
        code, text = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn('No terminal', text)

    def test_number_parsing(self):
        self.assertEqual(cli.parse_numbers('1,3-4', 5), [1, 3, 4])
        for bad in ('0', '6', 'a', '3-2'):
            with self.assertRaises(ValueError):
                cli.parse_numbers(bad, 5)


if __name__ == '__main__':
    unittest.main()
