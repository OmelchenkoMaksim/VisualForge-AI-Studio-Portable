"""Stable Audio 3 Medium on the Image ComfyUI, against a fake ComfyUI: no GPU, no models, no network beyond loopback."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
from gpu import Busy, GpuCoordinator
from music.comfy_backend import ComfyBackend, ComfyClient, flac_to_wav, inspect_wav
from music.manager import MusicManager
from music.registry import ENGINES, STABLE_AUDIO, validate
from music.routes import handle
from portable_paths import PortablePaths

CHECKPOINT, ENCODER = 'stable_audio_3_medium.safetensors', 't5gemma_b_b_ul2.safetensors'


def fake_convert(duration=2.0, rate=44100, channels=2):
    """Writes a real WAV so the backend inspects an actual file; the FLAC the fake ComfyUI serves is only a marker."""
    def convert(source, target):
        assert Path(source).read_bytes() == b'fake-flac'
        with wave.open(str(target), 'wb') as output:
            output.setnchannels(channels)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes(b'\x00\x00' * channels * int(rate * duration))
    return convert


class FakeComfy:
    """The few ComfyUI routes Stable Audio uses. `mode`: ok, error, reject, hang (runs until it is cancelled) or traversal
    (finishes with a result path that leaves ComfyUI's folder)."""

    def __init__(self, template, mode='ok', ready_after=2):
        self.mode, self.ready_after = mode, ready_after
        self.requests, self.prompts, self.cancelled = [], {}, set()
        self.polls = 0
        self.info = self.object_info(template)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, body, kind='application/json'):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                url = urlparse(self.path)
                outer.requests.append(('GET', url.path))
                if url.path == '/object_info':
                    return self.reply(200, outer.info)
                if url.path == '/queue':
                    running = [[0, ident] for ident in outer.prompts if ident not in outer.cancelled and not outer.finished(ident)]
                    return self.reply(200, {'queue_running': running, 'queue_pending': []})
                if url.path.startswith('/history/'):
                    return self.reply(200, outer.history(url.path.rsplit('/', 1)[-1]))
                if url.path == '/view':
                    outer.requests.append(('VIEW', parse_qs(url.query)['filename'][0]))
                    return self.reply(200, b'fake-flac', 'audio/flac')
                self.reply(404, {})

            def do_POST(self):
                url = urlparse(self.path)
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
                outer.requests.append(('POST', url.path))
                if url.path == '/prompt':
                    if outer.mode == 'reject':
                        return self.reply(400, {'error': {'message': 'bad graph'}})
                    ident = uuid.uuid4().hex
                    outer.prompts[ident] = body['prompt']
                    return self.reply(200, {'prompt_id': ident})
                if url.path.startswith('/api/jobs/') and url.path.endswith('/cancel'):
                    ident = url.path.split('/')[3]
                    outer.cancelled.add(ident)
                    return self.reply(200, {'cancelled': True})
                if url.path == '/free':
                    return self.reply(200, {})
                self.reply(404, {})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:%d' % self.server.server_address[1]

    def finished(self, ident):
        return self.mode in ('ok', 'error', 'traversal') and self.polls >= self.ready_after

    def history(self, ident):
        if ident not in self.prompts:
            return {}
        if ident in self.cancelled:
            return {ident: {'status': {'status_str': 'error', 'completed': False, 'messages': [['execution_interrupted', {}]]}, 'outputs': {}}}
        if self.mode == 'hang':
            return {}
        self.polls += 1
        if self.polls < self.ready_after:
            return {}
        if self.mode == 'error':
            return {ident: {'status': {'status_str': 'error', 'completed': False,
                                       'messages': [['execution_error', {'exception_message': 'CUDA out of memory'}]]}, 'outputs': {}}}
        filename = '..\\..\\secret.flac' if self.mode == 'traversal' else 'ComfyUI_temp_abcde_00001_.flac'
        return {ident: {'status': {'status_str': 'success', 'completed': True, 'messages': []},
                        'outputs': {'90': {'audio': [{'filename': filename, 'subfolder': '', 'type': 'temp'}]}}}}

    @staticmethod
    def object_info(template):
        info = {}
        for node in template.values():
            declared = info.setdefault(node['class_type'], {'input': {'optional': {}}})['input']['optional']
            for key in node['inputs']:
                declared[key] = ['*']
        info['CheckpointLoaderSimple']['input']['optional']['ckpt_name'] = [[CHECKPOINT]]
        info['CLIPLoader']['input']['optional']['clip_name'] = [[ENCODER]]
        return info

    def count(self, method, path):
        return sum(1 for entry in self.requests if entry == (method, path))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Portable:
    """A throw-away portable root with the real layout and workflow, and the two model files as empty placeholders."""

    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(dir=tempfile.gettempdir())
        self.root = Path(self.directory.name) / 'Studio'
        (self.root / 'App/config').mkdir(parents=True)
        shutil.copy(ROOT / 'App/config/portable-layout.json', self.root / 'App/config/portable-layout.json')
        shutil.copytree(ROOT / 'App/workflows/stable-audio-3-medium', self.root / 'App/workflows/stable-audio-3-medium')
        for folder, name in (('checkpoints', CHECKPOINT), ('text_encoders', ENCODER)):
            (self.root / 'Models' / folder).mkdir(parents=True, exist_ok=True)
            (self.root / 'Models' / folder / name).write_bytes(b'x')
        main = self.root / 'Runtime/ComfyUI_windows_portable/ComfyUI/main.py'
        main.parent.mkdir(parents=True)
        main.write_text('# placeholder', encoding='utf-8')
        (self.root / 'UserData/Settings').mkdir(parents=True)
        document = json.loads((self.root / 'App/config/portable-layout.json').read_text(encoding='utf-8-sig'))
        self.paths = PortablePaths(self.root, document)

    def close(self):
        self.directory.cleanup()


class StableAudioBase(unittest.TestCase):
    mode = 'ok'
    ready_after = 2

    def setUp(self):
        self.portable = Portable()
        self.addCleanup(self.portable.close)
        self.template = json.loads((ROOT / 'App/workflows/stable-audio-3-medium/text.api.json').read_text(encoding='utf-8'))
        self.fake = FakeComfy(self.template, self.mode, self.ready_after)
        self.addCleanup(self.fake.close)
        state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, state, True)
        import os
        os.environ['VISUALFORGE_MUSIC_STATE_ROOT'] = state
        self.addCleanup(os.environ.pop, 'VISUALFORGE_MUSIC_STATE_ROOT', None)
        self.manager = MusicManager(self.portable.paths)
        self.addCleanup(self.manager.close)
        self.manager.comfy.attach(ComfyClient(self.fake.base))
        self.manager.comfy.convert = fake_convert()
        self.manager.comfy.sleep = lambda seconds: time.sleep(0.02)

    def request(self, **extra):
        return dict(engine=STABLE_AUDIO['id'], caption='Warm ambient pad with slow piano', duration=20, seed=7, **extra)

    def wait(self, ident, states=('done', 'error', 'cancelled'), timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.manager.status(ident)
            if row.get('state') in states:
                return row
            time.sleep(0.02)
        self.fail('job did not reach %s: %s' % (states, self.manager.status(ident)))


class RegistryTests(unittest.TestCase):
    def test_song_fields_are_ignored_and_never_stored(self):
        row = validate(dict(engine=STABLE_AUDIO['id'], caption=' deep bass hit ', mode='song', lyrics='la la', bpm='500', language='xx', planner=True, duration='45', seed='5'))
        self.assertEqual((row['mode'], row['lyrics'], row['bpm'], row['language'], row['planner']), ('audio', '', None, 'unknown', False))
        self.assertEqual((row['caption'], row['duration'], row['seed']), ('deep bass hit', 45, 5))

    def test_duration_prompt_and_seed_are_bounded(self):
        for bad in (dict(duration=9), dict(duration=121), dict(duration='abc'), dict(duration=True), dict(caption=''), dict(caption='x' * 2001),
                    dict(caption=['a']), dict(seed=2 ** 32), dict(seed='-5')):
            data = {**dict(engine=STABLE_AUDIO['id'], caption='ok', duration=30), **bad}
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate(data)
        self.assertEqual(validate(dict(engine=STABLE_AUDIO['id'], caption='ok', duration=10))['duration'], 10)
        self.assertEqual(validate(dict(engine=STABLE_AUDIO['id'], caption='ok', duration=120))['duration'], 120)

    def test_profile_declares_what_the_page_may_show(self):
        self.assertEqual(STABLE_AUDIO['backend'], 'comfy')
        self.assertEqual(set(STABLE_AUDIO['capabilities'].values()), {False})
        for ident, engine in ENGINES.items():
            if ident != STABLE_AUDIO['id']:
                self.assertEqual(engine['backend'], 'worker')
                self.assertTrue(engine['capabilities']['lyrics'])

    def test_song_engines_keep_their_rules(self):
        with self.assertRaises(ValueError):
            validate(dict(caption='x', mode='song'))
        self.assertEqual(validate(dict(caption='x'))['mode'], 'instrumental')


class GraphTests(StableAudioBase):
    def test_prompt_duration_and_seed_reach_the_graph(self):
        graph, meta = self.manager.comfy.build(validate(self.request()))
        self.assertEqual(graph['40']['inputs']['text'], 'Warm ambient pad with slow piano')
        self.assertEqual((graph['50']['inputs']['seconds'], graph['60']['inputs']['seed']), (20.0, 7))
        self.assertEqual(graph['41']['inputs']['text'], '')
        self.assertEqual((graph['60']['inputs']['steps'], graph['60']['inputs']['cfg'], graph['60']['inputs']['sampler_name']), (8, 1, 'lcm'))
        self.assertEqual(meta['workflow_version'], 'sa3-medium-v1')
        self.assertEqual(self.manager.comfy.template()['40']['inputs']['text'], '__PROMPT__', 'the stored template is never mutated')

    def test_every_link_points_at_a_node_and_the_result_node_saves_nothing(self):
        for node in self.template.values():
            for value in node['inputs'].values():
                if isinstance(value, list):
                    self.assertIn(value[0], self.template)
        self.assertEqual(self.template['90']['class_type'], 'PreviewAudio')
        self.assertEqual(self.template['20']['inputs']['ckpt_name'], CHECKPOINT)

    def test_preflight_accepts_the_declared_schema(self):
        self.manager.comfy.preflight(validate(self.request()))

    def test_preflight_names_a_missing_model_and_a_missing_node(self):
        self.fake.info['CheckpointLoaderSimple']['input']['optional']['ckpt_name'] = [['other.safetensors']]
        with self.assertRaisesRegex(ValueError, CHECKPOINT):
            self.manager.comfy.preflight(validate(self.request()))
        self.fake.info = FakeComfy.object_info(self.template)
        del self.fake.info['PreviewAudio']
        with self.assertRaisesRegex(ValueError, 'PreviewAudio'):
            self.manager.comfy.preflight(validate(self.request()))

    def test_preflight_refuses_a_sampler_or_clip_type_this_comfyui_does_not_offer(self):
        # Only the model names get the Components hint; any other missing option must stop the job before the GPU.
        for node, key, value in (('KSampler', 'sampler_name', 'lcm'), ('CLIPLoader', 'type', 'stable_audio')):
            with self.subTest(key=key):
                self.fake.info = FakeComfy.object_info(self.template)
                self.fake.info[node]['input']['optional'][key] = [['euler', 'other']]
                with self.assertRaisesRegex(ValueError, 'несовместим.*%s=%s' % (key, value)):
                    self.manager.comfy.preflight(validate(self.request()))

    def test_preflight_reports_an_unreachable_comfyui(self):
        self.manager.comfy.attach(ComfyClient('http://127.0.0.1:9'))
        with self.assertRaisesRegex(ValueError, 'не отвечает'):
            self.manager.comfy.preflight(validate(self.request()))


class AvailabilityTests(StableAudioBase):
    def test_files_decide_and_missing_ones_point_to_components(self):
        profile = self.manager.availability(STABLE_AUDIO['id'])
        self.assertTrue(profile['available'])
        self.assertEqual(profile['engine']['capabilities']['lyrics'], False)
        (self.portable.root / 'Models/checkpoints' / CHECKPOINT).unlink()
        missing = self.manager.availability(STABLE_AUDIO['id'])
        self.assertFalse(missing['available'])
        self.assertIn('Компоненты', missing['reason'])
        with self.assertRaisesRegex(ValueError, 'Компоненты'):
            self.manager.submit(self.request())

    def test_the_profile_list_carries_all_three_engines(self):
        ids = [row['id'] for row in self.manager.availability()['engines']]
        self.assertEqual(ids, ['ace-step-15-turbo', 'minimax-music3-int8', STABLE_AUDIO['id']])


class SuccessTests(StableAudioBase):
    def test_a_job_publishes_a_wav_with_facts_read_from_the_file(self):
        job = self.manager.submit(self.request())
        row = self.wait(job['id'])
        self.assertEqual(row['state'], 'done', row.get('error'))
        saved = Path(row['saved_path'])
        self.assertTrue(saved.is_file() and saved.suffix == '.wav')
        facts = inspect_wav(saved)
        self.assertEqual((row['metadata']['sample_rate'], row['metadata']['channels'], row['metadata']['bits']), (facts['sample_rate'], facts['channels'], facts['bits']))
        self.assertEqual(row['metadata']['duration'], 2.0, 'the fake wrote 2 s, so the history must say 2 s and not the requested 20 s')
        self.assertEqual(row['metadata']['requested_duration'], 20)
        self.assertEqual((row['backend'], row['mode'], row['lyrics']), ('comfy', 'audio', ''))
        self.assertEqual(len(row['metadata']['workflow_sha256']), 64)
        sidecar = json.loads(saved.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(sidecar['generation'], row['generation'])
        self.assertEqual(row['generation']['id'], job['generation']['id'])
        self.assertIsNotNone(row['generation']['duration_sec'])
        self.assertEqual(sidecar['seed'], 7)
        self.assertEqual(self.manager.file(job['id']), saved)

    def test_the_slot_is_held_through_the_job_and_released_after(self):
        coordinator = GpuCoordinator(dict(image=lambda: False, video=lambda: False, music=self.manager.busy, text=lambda: False))
        self.fake.ready_after = 10 ** 6
        job = self.manager.submit(self.request())
        self.wait(job['id'], ('running',))
        self.assertTrue(self.manager.busy())
        with self.assertRaisesRegex(Busy, 'Music'):
            coordinator.claim('image')
        self.assertIn('music', coordinator.snapshot()['modules'])
        self.assertEqual(coordinator.snapshot()['labels'], ['Music'])
        self.manager.cancel({'id': job['id'], 'token': job['token']})
        self.wait(job['id'])
        self.assertFalse(self.manager.busy())
        coordinator.claim('image')

    def test_comfyui_is_told_to_drop_its_models_after_the_job(self):
        job = self.manager.submit(self.request())
        self.wait(job['id'])
        self.assertEqual(self.fake.count('POST', '/free'), 1)
        self.assertEqual(self.manager.owned_comfy_prompt_ids(), set())

    def test_the_job_log_records_the_steps_without_paths_to_secrets(self):
        job = self.manager.submit(self.request())
        self.wait(job['id'])
        text = self.manager.log(job['id'])['text']
        for expected in ('prompt_id', 'FLAC', 'WAV: 44100 Гц', 'выгружены'):
            self.assertIn(expected, text)


class FailureTests(StableAudioBase):
    mode = 'error'

    def test_a_comfyui_error_ends_in_error_state_and_still_frees_everything(self):
        job = self.manager.submit(self.request())
        row = self.wait(job['id'])
        self.assertEqual(row['state'], 'error')
        self.assertIn('CUDA out of memory', row['error'])
        self.assertFalse(self.manager.busy())
        self.assertEqual(self.fake.count('POST', '/free'), 1)
        self.assertEqual(self.manager.entries()['entries'][0]['state'], 'error')


class AbandonedPromptBase(StableAudioBase):
    def watch_cancels(self):
        """Record which prompt ids Music still owned when each cancel went out: after the release they would look like Image's."""
        seen, original = [], self.manager.comfy._remote_cancel

        def remote_cancel(prompt_id, log):
            seen.append((prompt_id, self.manager.owned_comfy_prompt_ids()))
            return original(prompt_id, log)
        self.manager.comfy._remote_cancel = remote_cancel
        return seen

    def assert_cancelled_while_owned(self, seen):
        self.assertEqual(len(self.fake.prompts), 1)
        prompt_id = next(iter(self.fake.prompts))
        self.assertEqual(seen, [(prompt_id, {prompt_id})])
        self.assertEqual(self.fake.cancelled, {prompt_id})
        self.assertEqual(self.fake.count('POST', '/free'), 1)
        self.assertFalse(self.manager.busy())
        self.assertEqual(self.manager.owned_comfy_prompt_ids(), set())


class TimeoutTests(AbandonedPromptBase):
    mode = 'hang'

    def test_a_job_past_the_time_limit_is_cancelled_in_comfyui_not_left_running(self):
        from music.comfy_backend import JOB_TIMEOUT_SEC
        seen = self.watch_cancels()
        ticks = iter([0.0, 0.0])  # the start, then one poll inside the limit; every later reading is past it
        self.manager.comfy.clock = lambda: next(ticks, JOB_TIMEOUT_SEC + 1.0)
        job = self.manager.submit(self.request())
        row = self.wait(job['id'])
        self.assertEqual(row['state'], 'error')
        self.assertIn('за час', row['error'])
        self.assert_cancelled_while_owned(seen)


class HostileResultTests(AbandonedPromptBase):
    mode = 'traversal'

    def test_a_result_path_outside_comfyui_is_refused_and_never_fetched(self):
        seen = self.watch_cancels()
        job = self.manager.submit(self.request())
        row = self.wait(job['id'])
        self.assertEqual(row['state'], 'error')
        self.assertIn('непонятный путь', row['error'])
        self.assertEqual([entry for entry in self.fake.requests if entry[0] == 'VIEW'], [])
        self.assert_cancelled_while_owned(seen)


class RejectTests(StableAudioBase):
    mode = 'reject'

    def test_a_rejected_graph_is_an_error_not_a_hang(self):
        job = self.manager.submit(self.request())
        row = self.wait(job['id'])
        self.assertEqual(row['state'], 'error')
        self.assertIn('отклонил', row['error'])
        self.assertFalse(self.manager.busy())


class CancelTests(StableAudioBase):
    mode = 'hang'

    def test_stop_cancels_only_its_own_prompt_and_returns_to_idle(self):
        job = self.manager.submit(self.request())
        self.wait(job['id'], ('running',))
        deadline = time.monotonic() + 5
        while not self.manager.owned_comfy_prompt_ids() and time.monotonic() < deadline:
            time.sleep(0.02)
        owned = self.manager.owned_comfy_prompt_ids()
        self.assertEqual(len(owned), 1)
        with self.assertRaises(ValueError):
            self.manager.cancel({'id': job['id'], 'token': 'wrong'})
        self.manager.cancel({'id': job['id'], 'token': job['token']})
        row = self.wait(job['id'])
        self.assertEqual(row['state'], 'cancelled')
        self.assertEqual(self.fake.cancelled, owned)
        self.assertEqual(self.fake.count('POST', '/interrupt'), 0, 'a global interrupt could stop somebody else\'s job')
        self.assertEqual(self.fake.count('POST', '/free'), 1)
        self.assertFalse(self.manager.busy())
        self.assertEqual(self.manager.owned_comfy_prompt_ids(), set())
        self.assertEqual(self.manager.entries()['entries'][0]['state'], 'cancelled')
        self.assertEqual(self.fake.count('VIEW', 'ComfyUI_temp_abcde_00001_.flac'), 0)


class BridgeTests(unittest.TestCase):
    def test_music_prompts_do_not_make_image_look_busy_or_get_stopped(self):
        import server
        original = server.MUSIC
        server.MUSIC = Mock()
        server.MUSIC.owned_comfy_prompt_ids.return_value = {'mine'}
        self.addCleanup(setattr, server, 'MUSIC', original)
        queue = {'queue_running': [[0, 'mine']], 'queue_pending': [[1, 'image-job']]}
        self.assertEqual(server._foreign_queue(queue), [[1, 'image-job']])
        self.assertEqual(server._foreign_queue({'queue_running': [[0, 'mine']], 'queue_pending': []}), [])
        self.assertEqual(len(server._foreign_queue({'queue_running': [[0]], 'queue_pending': []})), 1, 'an entry without an id still counts as busy')
        server.MUSIC = None
        self.assertEqual(len(server._foreign_queue(queue)), 2)

    def test_the_start_route_claims_the_engines_own_estimate(self):
        handler, manager, gpu = Mock(), Mock(), Mock()
        manager.busy.return_value = False
        manager.availability.return_value = {'available': True, 'engine': {'claim_vram_mb': 5000}}
        manager.submit.return_value = {'id': 'job'}
        handle(handler, manager, gpu, 'generate', {'engine': STABLE_AUDIO['id'], 'caption': 'rain on a tin roof', 'duration': 20}, '')
        gpu.claim.assert_called_once_with('music', 5000)
        manager.availability.return_value = {'available': True}
        gpu.reset_mock()
        handle(handler, manager, gpu, 'generate', {'caption': 'piano'}, '')
        gpu.claim.assert_called_once_with('music', 8000)

    def test_the_claim_grows_with_the_clip_length_where_the_engine_says_so(self):
        from music.registry import claim_vram_mb
        self.assertEqual([claim_vram_mb(STABLE_AUDIO, {'duration': d}) for d in (10, 30, 31, 60, 61, 120)], [8000, 8000, 10000, 10000, 14000, 14000])
        self.assertEqual(claim_vram_mb(ENGINES['ace-step-15-turbo'], {'duration': 180}), 8000)
        handler, manager, gpu = Mock(), Mock(), Mock()
        manager.busy.return_value = False
        manager.availability.return_value = {'available': True, 'engine': STABLE_AUDIO}
        handle(handler, manager, gpu, 'generate', {'engine': STABLE_AUDIO['id'], 'caption': 'x', 'duration': 120}, '')
        gpu.claim.assert_called_once_with('music', 14000)

    def test_the_comfy_model_path_map_lists_checkpoints(self):
        text = (ROOT / 'App/config/extra_model_paths.yaml').read_text(encoding='utf-8')
        self.assertIn('checkpoints: |', text)


class PageTests(unittest.TestCase):
    def test_the_page_offers_the_engine_and_hides_unused_fields_by_capability(self):
        page = (ROOT / 'App/frontend/index.html').read_text(encoding='utf-8')
        script = (ROOT / 'App/frontend/music.js').read_text(encoding='utf-8')
        self.assertIn('<option value="stable-audio-3-medium">', page)
        for block in ('musicModeBlock', 'musicBpmBlock', 'musicLanguageBlock', 'musicPlannerBlock'):
            self.assertIn('id="%s"' % block, page)
            self.assertIn("el('%s').hidden" % block, script)
        self.assertIn("'stable-audio-3-medium'", (ROOT / 'App/frontend/studio-ui.js').read_text(encoding='utf-8'))
        self.assertIn("'music:stable-audio-3-medium':'music.model.stable-audio-3'", (ROOT / 'App/frontend/components.js').read_text(encoding='utf-8'))


class CatalogTests(unittest.TestCase):
    def test_component_is_pinned_depends_on_the_image_runtime_and_ships_the_license(self):
        from model_requirements import COMPONENTS
        catalog = json.loads((ROOT / 'App/config/components.json').read_text(encoding='utf-8'))
        component = next(c for c in catalog['components'] if c['id'] == COMPONENTS['music:' + STABLE_AUDIO['id']])
        self.assertEqual(component['requires'], ['image.runtime'])
        paths = {Path(f['path']).name: f for f in component['files']}
        self.assertEqual(set(paths), {CHECKPOINT, ENCODER, 'LICENSE.md'})
        for item in component['files']:
            self.assertRegex(item['sha256'], r'^[0-9a-f]{64}$')
        self.assertIn('/resolve/75aad8836271cb5a5123fb58304fae5d3bb4b0f9/', paths[CHECKPOINT]['urls'][0])
        self.assertIn('Gemma', component['note'])
        self.assertFalse(any('base' in name.lower() for name in paths), 'the Base checkpoint is not offered')


def av_available():
    try:
        import av  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(av_available(), 'PyAV is only in the ComfyUI Python; run this file with it to exercise the real conversion')
class RealConversionTests(unittest.TestCase):
    def test_flac_to_wav_keeps_every_sample(self):
        import av
        import numpy
        rate, seconds = 44100, 1
        t = numpy.arange(rate * seconds) / rate
        left, right = numpy.sin(2 * numpy.pi * 440 * t) * 0.5, numpy.sin(2 * numpy.pi * 660 * t) * 0.25
        interleaved = numpy.stack([left, right], axis=1).reshape(1, -1).astype('float32')
        with tempfile.TemporaryDirectory() as directory:
            flac, wav = Path(directory) / 'a.flac', Path(directory) / 'a.wav'
            # The same encode ComfyUI's AudioSaveHelper does: float frames into a FLAC stream.
            container = av.open(str(flac), mode='w', format='flac')
            stream = container.add_stream('flac', rate=rate, layout='stereo')
            frame = av.AudioFrame.from_ndarray(interleaved, format='flt', layout='stereo')
            frame.sample_rate, frame.pts = rate, 0
            container.mux(stream.encode(frame))
            container.mux(stream.encode(None))
            container.close()
            flac_to_wav(flac, wav)
            facts = inspect_wav(wav)
            self.assertEqual((facts['sample_rate'], facts['channels']), (rate, 2))
            self.assertEqual(facts['frames'], rate * seconds)
            with av.open(str(flac)) as source:
                decoded = numpy.concatenate([f.to_ndarray().reshape(-1) for f in source.decode(audio=0)])
            with wave.open(str(wav), 'rb') as written:
                pcm = numpy.frombuffer(written.readframes(written.getnframes()), dtype='<i2' if written.getsampwidth() == 2 else '<i4')
            self.assertEqual(pcm.size, decoded.size)
            # Same samples as the decoder gives for the FLAC itself, not merely close.
            self.assertTrue(numpy.array_equal(pcm, decoded.astype(pcm.dtype)), 'WAV samples differ from the FLAC decode')


if __name__ == '__main__':
    unittest.main()
