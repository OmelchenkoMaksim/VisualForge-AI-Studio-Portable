"""Text module: chat store, settings, installed-model scan, streaming chat against a fake Ollama; no model, no GPU."""
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from text import routes
from text import store as store_module
from text.text_manager import Busy, TextManager
from text.store import ChatStore

FAKE = Path(__file__).with_name('fake_ollama.py')
MODEL = 'demo-model:latest'


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class FakeHandler:
    """Just enough of BaseHTTPRequestHandler for routes.send()/handle()."""
    def __init__(self, break_after=None):
        self.status, self.headers_sent, self.out, self.break_after, self.writes = None, {}, io.BytesIO(), break_after, 0
        self.wfile = self

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers_sent[key] = value

    def end_headers(self):
        pass

    def write(self, raw):
        self.writes += 1
        if self.break_after is not None and self.writes > self.break_after:
            raise BrokenPipeError
        self.out.write(raw)

    def flush(self):
        pass

    def response_json(self, status, value):
        self.status, self.json = status, value

    def events(self):
        return [json.loads(line) for line in self.out.getvalue().decode('utf-8').splitlines() if line.strip()]


def install_fake_model(root, name='demo-model', tag='latest', break_blob=False, seed=0, vision=True):
    blobs = root / 'blobs'
    blobs.mkdir(parents=True, exist_ok=True)
    layers = []
    for index, (kind, payload) in enumerate((('model', b'w' * 64), ('template', b'{{ .Prompt }}')) + ((('projector', b'p' * 16),) if vision else ())):
        digest = 'sha256:%064x' % (seed * 100 + index + 1)
        (blobs / digest.replace(':', '-')).write_bytes(payload + (b'' if not (break_blob and kind == 'model') else b'!'))
        layers.append(dict(mediaType='application/vnd.ollama.image.' + kind, digest=digest, size=len(payload)))
    config = dict(mediaType='application/vnd.docker.container.image.v1+json', digest='sha256:%064x' % (seed * 100 + 99), size=2)
    (blobs / ('sha256-%064x' % (seed * 100 + 99))).write_bytes(b'{}')
    manifest = root / 'manifests/registry.ollama.ai/library' / name / tag
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(dict(schemaVersion=2, config=config, layers=layers)), encoding='utf-8')


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='vf-text-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.log = self.root / 'requests.log'
        os.environ['FAKE_OLLAMA_LOG'] = str(self.log)
        os.environ['VISUALFORGE_TEXT_PORT'] = str(free_port())
        self.addCleanup(os.environ.pop, 'FAKE_OLLAMA_LOG', None)
        self.addCleanup(os.environ.pop, 'VISUALFORGE_TEXT_PORT', None)
        self.paths = types.SimpleNamespace(root=self.root, models=self.root / 'Models', settings=self.root / 'UserData/Settings', cache=self.root / 'UserData/Cache',
                                           logs=self.root / 'UserData/Logs', ports=self.root / 'ports.json', modern=True)
        os.environ['VISUALFORGE_TEXT_MODELS'] = str(self.root / 'Models/Text/ollama')
        self.addCleanup(os.environ.pop, 'VISUALFORGE_TEXT_MODELS', None)
        install_fake_model(self.root / 'Models/Text/ollama')
        self.manager = TextManager(self.paths, command=[sys.executable, str(FAKE)])
        self.addCleanup(self.manager.close)
        self.store = ChatStore(self.manager.chats_dir)

    def requests(self):
        return [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()] if self.log.is_file() else []

    def admit(self, chat_id):
        return self.manager.begin(chat_id)

    def send(self, chat_id, content='Привет', regenerate=False, handler: Optional[FakeHandler] = None):
        handler = handler or FakeHandler()
        data = dict(chat_id=chat_id, regenerate=True) if regenerate else dict(chat_id=chat_id, content=content)
        routes.send(handler, self.manager, self.store, data, self.admit)
        return handler


class StoreTests(Base):
    def test_create_list_rename_update_delete(self):
        chat = self.store.create('Отвечай кратко', MODEL)
        self.assertEqual((chat['title'], chat['system'], chat['think'], chat['messages']), ('Новый чат', 'Отвечай кратко', True, []))
        self.assertEqual([c['id'] for c in self.store.list()], [chat['id']])
        self.assertEqual(self.store.rename(chat['id'], '  Мой   чат ')['title'], 'Мой чат')
        updated = self.store.update(chat['id'], system='', think=False)
        self.assertEqual((updated['system'], updated['think']), ('', False))
        self.store.delete(chat['id'])
        self.assertEqual(self.store.list(), [])
        with self.assertRaises(ValueError):
            self.store.get(chat['id'])

    def test_ids_cannot_escape_the_folder_and_inputs_are_bounded(self):
        for bad in ('../x', 'x' * 32, '', None, 5, 'A' * 32):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.get(bad)
        chat = self.store.create()
        for bad in ('', '   ', 'я' * 100_001, None, 7):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.add_user(chat['id'], bad)
        with self.assertRaises(ValueError):
            self.store.update(chat['id'], system='x' * 9000)
        with self.assertRaises(ValueError):
            self.store.update(chat['id'], think='yes')

    def test_title_from_first_message_and_survives_missing_index(self):
        chat = self.store.create()
        self.store.add_user(chat['id'], 'Как работает\nсортировка слиянием? ' + 'и ещё ' * 30)
        title = self.store.get(chat['id'])['title']
        self.assertTrue(title.startswith('Как работает сортировка слиянием?') and len(title) <= 80 and title.endswith('…'))
        self.store.index_path.unlink()
        self.assertEqual([c['id'] for c in self.store.list()], [chat['id']])
        self.store.index_path.write_text('{broken', encoding='utf-8')
        self.assertEqual(self.store.list()[0]['messages'], 1)

    def test_one_damaged_chat_file_does_not_rebuild_the_index_on_every_request(self):
        good, damaged = self.store.create(), self.store.create()
        path = self.store._path(damaged['id'])
        path.write_text('{damaged', encoding='utf-8')
        self.store.index_path.unlink()
        self.assertEqual([c['id'] for c in self.store.list()], [good['id']])
        loads = []
        original = self.store._load
        self.store._load = lambda ident: loads.append(ident) or original(ident)
        self.store.list()
        self.store.add_user(good['id'], 'вопрос')
        self.assertEqual([c['id'] for c in self.store.list()], [good['id']])
        self.assertEqual(loads, [good['id']])                       # add_user reads its own chat, nothing else
        self.assertEqual(path.read_text(encoding='utf-8'), '{damaged')   # the damaged file is never touched
        path.unlink()
        self.assertEqual([c['id'] for c in self.store.list()], [good['id']])

    def test_regenerate_semantics(self):
        chat = self.store.create()
        with self.assertRaises(ValueError):
            self.store.prepare_regenerate(chat['id'])
        self.store.add_user(chat['id'], 'вопрос')
        self.assertEqual(self.store.prepare_regenerate(chat['id'])['messages'][-1]['role'], 'user')
        self.store.add_assistant(chat['id'], 'ответ')
        self.assertEqual([m['role'] for m in self.store.prepare_regenerate(chat['id'])['messages']], ['user'])


class ManagerTests(Base):
    def test_installed_models_and_availability(self):
        models = self.manager.installed_models()
        self.assertEqual([(m['name'], m['ok']) for m in models], [(MODEL.replace('demo-model:latest', 'demo-model:latest'), True)])
        install_fake_model(self.root / 'Models/Text/ollama', 'broken-one', break_blob=True, seed=1)
        names = {m['name']: m['ok'] for m in self.manager.installed_models()}
        self.assertEqual(names, {'demo-model:latest': True, 'broken-one:latest': False})
        status = self.manager.availability()
        self.assertTrue(status['available'])
        self.assertEqual(self.manager.default_model(), 'demo-model:latest')

    def test_missing_runtime_and_models_are_explained(self):
        plain = TextManager(self.paths)
        self.assertIn('ollama.exe', plain.availability()['reason'])
        shutil.rmtree(self.root / 'Models')
        self.assertIn('Нет установленной текстовой модели', self.manager.availability()['reason'])

    def test_settings_validation_and_persistence(self):
        self.assertEqual(self.manager.settings()['num_ctx'], 32768)
        saved = self.manager.settings(dict(num_ctx=16384, temperature=0.7, keep_alive='30m', model=MODEL))
        self.assertEqual((saved['num_ctx'], saved['temperature'], saved['keep_alive']), (16384, 0.7, '30m'))
        self.assertEqual(TextManager(self.paths, command=[]).settings()['num_ctx'], 16384)
        for bad in (dict(num_ctx=10), dict(num_ctx=4096.5), dict(temperature=float('nan')), dict(top_p=True), dict(keep_alive='forever'), dict(model='ghost:1'), dict(unknown=1)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.manager.settings(bad)

    def test_private_environment(self):
        os.environ['OLLAMA_HOST'] = '0.0.0.0:11434'
        self.addCleanup(os.environ.pop, 'OLLAMA_HOST', None)
        env = self.manager.environment()
        self.assertEqual(env['OLLAMA_HOST'], '127.0.0.1:%d' % self.manager.port)
        self.assertEqual((env['OLLAMA_NOPRUNE'], env['OLLAMA_NO_CLOUD'], env['OLLAMA_NUM_PARALLEL']), ('1', '1', '1'))
        self.assertTrue(env['OLLAMA_MODELS'].endswith(os.path.join('Text', 'ollama')))
        self.assertNotEqual(env['USERPROFILE'], os.environ.get('USERPROFILE'))

    def test_chunked_decoding_survives_any_split(self):
        wire = b'5\r\nhello\r\n6;ext=1\r\n world\r\n0\r\n\r\n'
        for cut in range(1, len(wire)):
            with self.subTest(cut=cut):
                self.assertEqual(b''.join(TextManager._unchunk(b'', [wire[:cut], wire[cut:]])), b'hello world')
        self.assertEqual(b''.join(TextManager._unchunk(wire[:9], [wire[9:]])), b'hello world')
        self.assertEqual(b''.join(TextManager._unchunk(b'', [b'5\r\nhel'])), b'')

    def test_port_conflicts_are_refused(self):
        os.environ['VISUALFORGE_TEXT_PORT'] = os.environ.get('QWEN_UI_PORT', '8189')
        with self.assertRaises(ValueError):
            TextManager(self.paths)


PNG = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + (32).to_bytes(4, 'big') + (16).to_bytes(4, 'big') + b'\x08\x02\x00\x00\x00' + b'\x00' * 20
JPEG = (b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00' + b'\xff\xc0\x00\x11\x08' + (48).to_bytes(2, 'big') + (64).to_bytes(2, 'big') +
        b'\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01' + b'\xff\xd9')


def data_url(raw, mime='image/png'):
    return 'data:%s;base64,%s' % (mime, __import__('base64').b64encode(raw).decode())


class ImageTests(Base):
    def test_headers_are_read_and_everything_else_is_refused(self):
        self.assertEqual(store_module.image_info(PNG), ('image/png', '.png', 32, 16))
        self.assertEqual(store_module.image_info(JPEG), ('image/jpeg', '.jpg', 64, 48))
        bad = {'gif': b'GIF89a' + b'\x00' * 40, 'text': b'hello world' * 10, 'huge png': PNG[:16] + (9000).to_bytes(4, 'big') + PNG[20:], 'zero jpeg': JPEG.replace((64).to_bytes(2, 'big') + b'\x03', b'\x00\x00\x03')}
        for name, raw in bad.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                store_module.image_info(raw)

    def test_data_urls(self):
        record = store_module.decode_image(data_url(PNG), 'shot.png')
        self.assertEqual((record['mime'], record['width'], record['name'], record['raw']), ('image/png', 32, 'shot.png', PNG))
        for bad in (None, 5, 'data:image/gif;base64,AAAA', 'data:text/plain;base64,AAAA', 'data:image/png;base64,@@@@', data_url(b'x' * 100),
                    data_url(PNG + b'\x00' * (6 * 1024 * 1024)), data_url(b'tiny')):
            with self.subTest(bad=str(bad)[:30]), self.assertRaises(ValueError):
                store_module.decode_image(bad)

    def test_store_keeps_files_next_to_the_chat_and_removes_them_with_it(self):
        chat = self.store.create('', MODEL)
        pictures = [store_module.decode_image(data_url(PNG), 'a.png'), store_module.decode_image(data_url(JPEG, 'image/jpeg'), 'b.jpg')]
        saved = self.store.add_user(chat['id'], '', pictures)
        message = saved['messages'][0]
        self.assertEqual((saved['title'], [m['mime'] for m in message['images']]), ('Изображение', ['image/png', 'image/jpeg']))
        self.assertEqual(self.store.images_b64(chat['id'], message), [__import__('base64').b64encode(PNG).decode(), __import__('base64').b64encode(JPEG).decode()])
        path, mime = self.store.image_path(chat['id'], message['images'][1]['id'])
        self.assertEqual((mime, path.suffix, path.parent.name), ('image/jpeg', '.jpg', chat['id'] + '.images'))
        self.assertNotIn('raw', json.dumps(self.store.get(chat['id'])))
        for bad in ('../x', 'z' * 32, '0' * 32, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.image_path(chat['id'], bad)
        with self.assertRaises(ValueError):
            self.store.image_path('0' * 32, message['images'][0]['id'])
        self.store.delete(chat['id'])
        self.assertFalse((self.store.directory / (chat['id'] + '.images')).exists())

    def test_limits_and_empty_messages(self):
        chat = self.store.create('', MODEL)
        one = store_module.decode_image(data_url(PNG))
        for args in (('', []), ('   ', None), ('', [one] * 5)):
            with self.subTest(args=len(args[1] or [])), self.assertRaises(ValueError):
                self.store.add_user(chat['id'], *args)
        for _ in range(15):
            self.store.add_user(chat['id'], 'q', [one] * 4)
            self.store.add_assistant(chat['id'], 'a')
        with self.assertRaisesRegex(ValueError, 'много изображений'):
            self.store.add_user(chat['id'], 'q', [one])
        self.assertEqual(len(list((self.store.directory / (chat['id'] + '.images')).iterdir())), 60)

    def test_failed_write_leaves_no_orphan_file(self):
        chat = self.store.create('', MODEL)
        one = store_module.decode_image(data_url(PNG))
        original = self.store._save
        self.store._save = lambda chat: (_ for _ in ()).throw(OSError('disk full'))
        with self.assertRaises(OSError):
            self.store.add_user(chat['id'], 'q', [one])
        self.store._save = original
        self.assertEqual(list((self.store.directory / (chat['id'] + '.images')).iterdir()), [])

    def send_with(self, chat_id, content='', images=(), model_ok=True):
        handler = FakeHandler()
        routes.send(handler, self.manager, self.store, dict(chat_id=chat_id, content=content, images=[dict(name='x.png', data=data_url(raw, mime)) for raw, mime in images]), self.admit)
        return handler

    def test_pictures_reach_the_model_with_the_whole_history(self):
        chat = self.store.create('', MODEL)
        first = self.send_with(chat['id'], 'что это?', [(PNG, 'image/png')]).events()
        self.assertEqual(first[-1]['message']['content'], 'Эхо: что это? [изображений: 1]')
        second = self.send_with(chat['id'], 'а теперь?', [(JPEG, 'image/jpeg'), (PNG, 'image/png')]).events()
        self.assertTrue(second[-1]['message']['content'].endswith('[изображений: 2]'))
        body = [r for r in self.requests() if r['path'] == '/api/chat'][-1]['body']
        sent = [(m['role'], len(m.get('images', []))) for m in body['messages']]
        self.assertEqual(sent, [('user', 1), ('assistant', 0), ('user', 2)])
        self.assertEqual(body['messages'][0]['images'][0], __import__('base64').b64encode(PNG).decode())
        saved = self.store.get(chat['id'])
        self.assertEqual([len(m.get('images', [])) for m in saved['messages']], [1, 0, 2, 0])
        again = self.send(chat['id'], regenerate=True).events()
        self.assertTrue(again[-1]['message']['content'].endswith('[изображений: 2]'))

    def test_image_only_message_and_refusals(self):
        chat = self.store.create('', MODEL)
        self.assertEqual(self.send_with(chat['id'], '', [(PNG, 'image/png')]).events()[-1]['type'], 'done')
        for kwargs in (dict(content='', images=[]), dict(content='x', images=[(PNG, 'image/png')] * 5)):
            with self.subTest(kwargs=len(kwargs['images'])), self.assertRaises(ValueError):
                self.send_with(chat['id'], **kwargs)
        with self.assertRaises(ValueError):
            routes.send(FakeHandler(), self.manager, self.store, dict(chat_id=chat['id'], images=['data:image/png;base64,AAAA']), self.admit)
        with self.assertRaises(ValueError):
            routes.send(FakeHandler(), self.manager, self.store, dict(chat_id=chat['id'], regenerate=True, content='новое'), self.admit)
        self.assertEqual(self.manager.active, {})
        install_fake_model(self.root / 'Models/Text/ollama', 'textonly', seed=2, vision=False)
        plain = self.store.create('', 'textonly:latest')
        with self.assertRaisesRegex(ValueError, 'не умеет работать с изображениями'):
            self.send_with(plain['id'], 'смотри', [(PNG, 'image/png')])
        self.assertEqual(self.store.get(plain['id'])['messages'], [])
        self.assertEqual(self.send_with(plain['id'], 'просто текст').events()[-1]['type'], 'done')

    def test_availability_reports_vision_and_the_image_route_serves_only_known_files(self):
        self.assertEqual({m['name']: m['vision'] for m in self.manager.installed_models()}, {MODEL: True})
        chat = self.store.create('', MODEL)
        self.send_with(chat['id'], 'смотри', [(PNG, 'image/png')])
        meta = self.store.get(chat['id'])['messages'][0]['images'][0]
        handler = FakeHandler()
        routes.image(handler, self.store, '?chat=%s&id=%s' % (chat['id'], meta['id']))
        self.assertEqual((handler.status, handler.headers_sent['Content-Type'], handler.headers_sent['X-Content-Type-Options']), (200, 'image/png', 'nosniff'))
        self.assertEqual(handler.out.getvalue(), PNG)
        self.assertIn("default-src 'none'", handler.headers_sent['Content-Security-Policy'])
        for query in ('?chat=%s&id=%s' % (chat['id'], '0' * 32), '?chat=../x&id=' + meta['id'], '', '?chat=%s' % chat['id']):
            missing = FakeHandler()
            routes.image(missing, self.store, query)
            self.assertEqual(missing.status, 404, query)


class ChatFlowTests(Base):
    def test_streamed_answer_is_saved_with_thinking_and_stats(self):
        chat = self.store.create('Отвечай кратко', MODEL)
        handler = self.send(chat['id'], 'Привет, мир')
        events = handler.events()
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.headers_sent['Content-Type'], 'application/x-ndjson; charset=utf-8')
        self.assertEqual([e['type'] for e in events][:2], ['status', 'status'])
        self.assertEqual(''.join(e['content'] for e in events if e['type'] == 'delta'), 'Эхо: Привет, мир')
        self.assertEqual(''.join(e['thinking'] for e in events if e['type'] == 'delta'), 'Думаю над ответом')
        done = events[-1]
        self.assertEqual(done['type'], 'done')
        self.assertEqual((done['message']['content'], done['message']['stats']['completion_tokens'], done['message']['stats']['tokens_per_sec']), ('Эхо: Привет, мир', 3, 3.0))
        saved = self.store.get(chat['id'])
        self.assertEqual([m['role'] for m in saved['messages']], ['user', 'assistant'])
        self.assertEqual(saved['messages'][1]['thinking'], 'Думаю над ответом')
        self.assertEqual(done['message']['generation'], saved['messages'][1]['generation'])
        self.assertEqual(saved['messages'][0]['generation'], saved['messages'][1]['generation'])
        self.assertIsNotNone(saved['messages'][1]['generation']['duration_sec'])
        body = [r for r in self.requests() if r['path'] == '/api/chat'][0]['body']
        self.assertEqual([m['role'] for m in body['messages']], ['system', 'user'])
        self.assertEqual((body['model'], body['think'], body['options'], body['keep_alive']), (MODEL, True, {}, '10m'))
        self.assertEqual(self.manager.active, {})

    def test_history_is_sent_and_think_can_be_off(self):
        chat = self.store.create('', MODEL, think=False)
        self.send(chat['id'], 'первый')
        self.send(chat['id'], 'второй')
        body = [r for r in self.requests() if r['path'] == '/api/chat'][-1]['body']
        self.assertEqual([(m['role'], m['content']) for m in body['messages']], [('user', 'первый'), ('assistant', 'Эхо: первый'), ('user', 'второй')])
        self.assertFalse(body['think'])

    def test_regenerate_replaces_the_last_answer(self):
        chat = self.store.create('', MODEL)
        self.send(chat['id'], 'вопрос')
        first = self.store.get(chat['id'])['messages'][-1]['generation']['id']
        handler = self.send(chat['id'], regenerate=True)
        self.assertEqual(handler.events()[-1]['type'], 'done')
        self.assertNotEqual(handler.events()[-1]['message']['generation']['id'], first)
        self.assertEqual([m['role'] for m in self.store.get(chat['id'])['messages']], ['user', 'assistant'])

    def test_error_keeps_the_question_and_retry_works(self):
        chat = self.store.create('', MODEL)
        handler = self.send(chat['id'], 'FAIL please')
        last = handler.events()[-1]
        self.assertEqual(last['type'], 'error')
        self.assertIn('out of memory', last['error'])
        self.assertIsNotNone(last['generation']['duration_sec'])
        self.assertEqual(self.store.get(chat['id'])['messages'][0]['generation'], last['generation'])
        self.assertEqual([m['role'] for m in self.store.get(chat['id'])['messages']], ['user'])
        again = self.send(chat['id'], regenerate=True).events()[-1]
        self.assertEqual(again['type'], 'error')
        self.assertEqual([m['role'] for m in self.store.get(chat['id'])['messages']], ['user'])
        self.assertEqual(self.manager.active, {})

    def test_error_in_the_middle_saves_the_partial_answer(self):
        chat = self.store.create('', MODEL)
        events = self.send(chat['id'], 'MIDERR').events()
        self.assertEqual(events[-1]['type'], 'done')
        self.assertIn('runner crashed', events[-1]['error'])
        self.assertEqual(self.store.get(chat['id'])['messages'][-1]['content'], 'Эхо')

    def test_stop_button_saves_the_partial_answer(self):
        chat = self.store.create('', MODEL)
        result = {}
        thread = threading.Thread(target=lambda: result.setdefault('handler', self.send(chat['id'], 'SLOW answer')))
        thread.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not self.manager.active:
            time.sleep(0.05)
        time.sleep(1.0)
        self.assertEqual(self.manager.stop_chat(chat['id']), dict(stopped=True))
        thread.join(15)
        self.assertFalse(thread.is_alive())
        events = result['handler'].events()
        self.assertEqual(events[-1]['type'], 'done')
        self.assertTrue(events[-1]['message']['stopped'])
        self.assertTrue(self.store.get(chat['id'])['messages'][-1]['content'].startswith('слово0 '))
        self.assertEqual(self.manager.active, {})

    def test_stop_works_while_the_model_is_still_loading(self):
        chat = self.store.create('', MODEL)
        result = {}
        thread = threading.Thread(target=lambda: result.setdefault('handler', self.send(chat['id'], 'LOADSLOW question')))
        thread.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not self.manager.active:
            time.sleep(0.05)
        time.sleep(1.0)
        started = time.monotonic()
        self.assertEqual(self.manager.stop_chat(chat['id']), dict(stopped=True))
        thread.join(3)
        self.assertFalse(thread.is_alive(), 'Stop must not wait for the load to finish')
        self.assertLess(time.monotonic() - started, 2.5)
        last = result['handler'].events()[-1]
        self.assertEqual(last['type'], 'error')
        self.assertIn('остановлен', last['error'])
        self.assertEqual([m['role'] for m in self.store.get(chat['id'])['messages']], ['user'])
        self.assertEqual(self.manager.active, {})

    def test_browser_disconnect_stops_generation_and_saves_what_arrived(self):
        chat = self.store.create('', MODEL)
        self.send(chat['id'], 'SLOW answer', handler=FakeHandler(break_after=6))
        saved = self.store.get(chat['id'])['messages'][-1]
        self.assertTrue(saved['stopped'])
        self.assertTrue(saved['content'])
        self.assertEqual(self.manager.active, {})

    def test_gpu_admission_and_release(self):
        chat = self.store.create('', MODEL)
        self.send(chat['id'], 'привет')
        self.assertEqual([m['name'] for m in self.manager.loaded()], [MODEL])
        self.assertEqual(self.manager.release_gpu(), dict(unloaded=[MODEL]))
        self.assertEqual(self.manager.loaded(), [])
        event = self.manager.begin(chat['id'])
        with self.assertRaises(Busy):
            self.manager.release_gpu()
        with self.assertRaises(Busy):
            self.manager.begin('other')
        self.manager.end(chat['id'])
        self.assertFalse(event.is_set())

    def test_slow_unload_does_not_block_busy_checks_or_stop(self):
        # An unload may take up to 50 s (Ollama dropping a 15 GB model); the busy poll, Stop and status must not wait behind it.
        self.manager.alive = lambda: True
        gate = threading.Event()

        def slow_call(path, data=None, timeout=15):
            gate.wait(5)
            return {'models': []}
        self.manager.call = slow_call
        worker = threading.Thread(target=self.manager.release_gpu, daemon=True)
        worker.start()
        time.sleep(0.2)
        started = time.monotonic()
        self.assertFalse(self.manager.busy())
        self.assertEqual(self.manager.stop_chat('none'), dict(stopped=False))
        event = self.manager.begin('chat')
        self.manager.end('chat')
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(event.is_set())
        gate.set()
        worker.join(5)

    def test_refusal_before_streaming_leaves_the_chat_untouched(self):
        chat = self.store.create('', MODEL)

        def refuse(chat_id):
            raise Busy('GPU занят Video')
        with self.assertRaises(Busy):
            routes.send(FakeHandler(), self.manager, self.store, dict(chat_id=chat['id'], content='привет'), refuse)
        self.assertEqual(self.store.get(chat['id'])['messages'], [])
        for data in (dict(chat_id=chat['id'], content=''), dict(chat_id=chat['id'], content='x', extra=1), dict(chat_id='bad', content='x')):
            with self.subTest(data=data), self.assertRaises(ValueError):
                routes.send(FakeHandler(), self.manager, self.store, data, self.admit)
        self.assertEqual(self.manager.active, {})

    def test_handle_routes(self):
        def call(route, data=None, query=''):
            handler = FakeHandler()
            routes.handle(handler, self.manager, self.store, route, data, query)
            return handler.status, handler.json
        status, created = call('chats', dict(system='Будь краток'))
        self.assertEqual((status, created['model']), (200, MODEL))
        self.assertEqual(call('chat', query='?id=' + created['id'])[1]['system'], 'Будь краток')
        self.assertEqual(call('chat-rename', dict(id=created['id'], title='Новое имя'))[1]['title'], 'Новое имя')
        self.assertEqual(call('chat-update', dict(id=created['id'], think=False))[1]['think'], False)
        self.assertEqual(call('chats', dict(bogus=1))[0], 400)
        self.assertEqual(call('chat-delete', dict(id='0' * 32))[0], 400)
        self.assertEqual(call('nothing')[0], 404)
        self.manager.begin(created['id'])
        self.assertEqual(call('chat-delete', dict(id=created['id']))[0], 409)
        self.manager.end(created['id'])
        self.assertEqual(call('chat-delete', dict(id=created['id']))[0], 200)
        availability = call('availability')[1]
        self.assertTrue(availability['available'])
        self.assertEqual(availability['default_model'], MODEL)


if __name__ == '__main__':
    unittest.main()
