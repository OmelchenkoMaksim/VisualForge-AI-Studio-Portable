"""The Components panel back end: background installation, state snapshots, refusals and the HTTP routes."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
sys.path.insert(0, str(ROOT / 'tests'))
from fake_web import FakeWeb
from provision import routes
from provision.manager import ComponentsManager
from test_provision import tiny_catalog


class FakeHandler:
    def __init__(self):
        self.status = None
        self.body = None

    def response_json(self, status, body):
        self.status, self.body = status, body


class FakeGpu:
    def __init__(self):
        self.busy = False

    def snapshot(self):
        return dict(busy=self.busy, modules=['video'] if self.busy else [], labels=[])


def wait_idle(manager, seconds=30):
    deadline = time.monotonic() + seconds
    while manager.running() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not manager.running()


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.web = FakeWeb().__enter__()
        data, self.model_a, self.model_b = tiny_catalog(self.web)
        root = Path(self.temp.name)
        self.catalog = root / 'components.json'
        self.catalog.write_text(json.dumps(data), encoding='utf-8')
        self.gpu = FakeGpu()
        self.manager = ComponentsManager(root / 'inst', self.catalog, self.gpu)

    def tearDown(self):
        self.web.__exit__()
        self.temp.cleanup()

    def test_describe_lists_modules_and_missing_programs(self):
        data = self.manager.describe()
        self.assertEqual([m['id'] for m in data['modules']], ['demo'])
        self.assertEqual(data['missing_software'], ['demo.runtime'])
        self.assertFalse(data['install']['running'])
        self.assertIn('everything', data['presets'])

    def test_install_runs_in_background_and_reports_results(self):
        plan = self.manager.start(models=['demo.model.a'])
        self.assertEqual(plan['todo'], ['demo.runtime', 'demo.model.a'])
        wait_idle(self.manager)
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot['results'], {'demo.runtime': 'installed', 'demo.model.a': 'installed'})
        self.assertEqual(snapshot['error'], '')
        described = self.manager.describe()
        states = {row['id']: row['state'] for m in described['modules'] for row in m['components']}
        self.assertEqual(states, {'demo.runtime': 'installed', 'demo.model.a': 'installed', 'demo.model.b': 'missing'})
        self.assertEqual(described['missing_software'], [])
        self.assertTrue(described['first_run_done'])

    def test_refused_while_a_generation_runs_or_nothing_to_do(self):
        self.gpu.busy = True
        with self.assertRaises(routes.Busy):
            self.manager.start(models=['demo.model.a'])
        self.gpu.busy = False
        self.manager.start()
        wait_idle(self.manager)
        with self.assertRaises(routes.ProvisionError):
            self.manager.start()

    def test_routes(self):
        handler = FakeHandler()
        routes.handle(handler, self.manager, '', None)
        self.assertEqual(handler.status, 200)
        routes.handle(handler, self.manager, 'install', dict(models=['demo.model.b']))
        self.assertEqual(handler.status, 202)
        wait_idle(self.manager)
        self.assertTrue((Path(self.temp.name) / 'inst/Models/Demo/b.bin').is_file())
        routes.handle(handler, self.manager, 'install', dict(models='demo.model.b'))
        self.assertEqual(handler.status, 400)
        routes.handle(handler, self.manager, 'install', dict(models=['nope']))
        self.assertEqual(handler.status, 400)
        routes.handle(handler, self.manager, 'cancel', {})
        self.assertEqual((handler.status, handler.body), (200, {'cancelled': False}))
        routes.handle(handler, self.manager, 'bogus', {})
        self.assertEqual(handler.status, 404)
        routes.handle(handler, None, '', None)
        self.assertEqual(handler.status, 503)
        self.gpu.busy = True
        routes.handle(handler, self.manager, 'install', dict(models=['demo.model.a']))
        self.assertEqual(handler.status, 409)

    def test_cancel_stops_a_running_install(self):
        import threading
        from provision import net
        original = net.download
        started, release = threading.Event(), threading.Event()

        def slow(urls, destination, size=None, sha256=None, progress=None, cancel=None, *rest, **keywords):
            started.set()
            release.wait(10)
            if cancel and cancel():
                raise net.Cancelled()
            return original(urls, destination, size, sha256, progress, cancel, *rest, **keywords)
        net.download = slow
        try:
            self.manager.start(models=['demo.model.a'])
            self.assertTrue(started.wait(10))
            self.assertTrue(self.manager.cancel())
            release.set()
            wait_idle(self.manager)
        finally:
            net.download = original
        self.assertTrue(all(v.startswith('failed') for v in self.manager.snapshot()['results'].values()))


if __name__ == '__main__':
    unittest.main()
