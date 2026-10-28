"""Chat history: one JSON file per chat plus a small index, written atomically; nothing leaves the portable folder."""
import base64
import binascii
import json
import os
from pathlib import Path
import re
import shutil
import struct
import threading
import time
import uuid
from generation import ensure as generation_ensure

SCHEMA_VERSION = 1
ID_PATTERN = re.compile(r'[0-9a-f]{32}\Z')
MAX_CHATS = 1000
MAX_MESSAGES = 2000
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_CONTENT = 100_000
MAX_SYSTEM = 8_000
MAX_TITLE = 80
ROLES = ('user', 'assistant')
MAX_IMAGES_PER_MESSAGE = 4
MAX_IMAGES_PER_CHAT = 60
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_SIDE = 8192
DATA_URL = re.compile(r'data:image/(?:jpeg|png);base64,')
SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def image_info(raw):
    """(mime, extension, width, height) of a PNG or JPEG, read from its own headers; anything else is refused."""
    if raw[:8] == b'\x89PNG\r\n\x1a\n' and raw[12:16] == b'IHDR' and len(raw) > 24:
        width, height = struct.unpack('>II', raw[16:24])
        mime, extension = 'image/png', '.png'
    elif raw[:3] == b'\xff\xd8\xff':
        position, width, height = 2, 0, 0
        while position + 9 < len(raw) and raw[position] == 0xFF:
            marker = raw[position + 1]
            if marker == 0xFF:
                position += 1
            elif marker in SOF_MARKERS:
                height, width = struct.unpack('>HH', raw[position + 5:position + 9])
                break
            elif marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
                position += 2
            else:
                position += 2 + struct.unpack('>H', raw[position + 2:position + 4])[0]
        mime, extension = 'image/jpeg', '.jpg'
    else:
        raise ValueError('Поддерживаются изображения PNG и JPEG')
    if not 1 <= width <= MAX_IMAGE_SIDE or not 1 <= height <= MAX_IMAGE_SIDE:
        raise ValueError('Размер изображения: от 1 до %d пикселей по стороне' % MAX_IMAGE_SIDE)
    return mime, extension, width, height


def decode_image(value, name=''):
    """A data URL from the browser -> a checked record ready to be stored."""
    if not isinstance(value, str) or not DATA_URL.match(value):
        raise ValueError('Изображение должно быть PNG или JPEG')
    try:
        raw = base64.b64decode(value.partition(',')[2], validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('Повреждённое изображение') from None
    if not 32 < len(raw) <= MAX_IMAGE_BYTES:
        raise ValueError('Изображение: до %d МиБ после сжатия' % (MAX_IMAGE_BYTES // 1024 // 1024))
    mime, extension, width, height = image_info(raw)
    return dict(raw=raw, mime=mime, extension=extension, width=width, height=height, name=str(name or 'image')[:120])


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(temporary, path)


def _title_from(text):
    title = ' '.join(str(text).split())
    return (title[:MAX_TITLE - 1].rstrip() + '…') if len(title) > MAX_TITLE else title


class ChatStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.index_path = self.directory / 'index.json'
        self.active = set()  # agent tasks include time spent on tools and permission cards

    def _path(self, ident):
        if not isinstance(ident, str) or not ID_PATTERN.match(ident):
            raise ValueError('Некорректный номер чата')
        return self.directory / (ident + '.json')

    def _summary(self, chat):
        last = next((m for m in reversed(chat['messages']) if m['role'] == 'assistant'), None)
        return dict(id=chat['id'], title=chat['title'], created=chat['created'], updated=chat['updated'], messages=len(chat['messages']),
                    model=chat.get('model', ''), preview=_title_from((last or {}).get('content', ''))[:60], kind=chat.get('kind', 'chat'))

    def _read_index(self):
        try:
            document = json.loads(self.index_path.read_text(encoding='utf-8'))
            if document.get('schema_version') == SCHEMA_VERSION and isinstance(document.get('chats'), list):
                # Unreadable chat files are remembered too: without that, one damaged file made every request re-read all chats.
                known = {item['id'] for item in document['chats']} | set(document.get('unreadable') or [])
                present = {path.stem for path in self.directory.glob('*.json') if ID_PATTERN.match(path.stem)}
                if known == present:
                    return document['chats']
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return self._rebuild_index()

    def _rebuild_index(self):
        chats, unreadable = [], []
        for path in self.directory.glob('*.json'):
            if not ID_PATTERN.match(path.stem):
                continue
            try:
                chats.append(self._summary(self._load(path.stem)))
            except (OSError, ValueError, KeyError, TypeError):
                unreadable.append(path.stem)  # left on disk untouched, only not listed
        chats.sort(key=lambda item: item['updated'], reverse=True)
        self._write_index(chats, unreadable)
        return chats

    def _write_index(self, chats, unreadable=None):
        if unreadable is None:
            try:
                unreadable = json.loads(self.index_path.read_text(encoding='utf-8')).get('unreadable') or []
            except (OSError, ValueError, AttributeError):
                unreadable = []
        document = dict(schema_version=SCHEMA_VERSION, chats=chats)
        if unreadable:
            document['unreadable'] = sorted(set(unreadable) - {item['id'] for item in chats})
        _write_json(self.index_path, document)

    def _load(self, ident):
        path = self._path(ident)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('Файл чата слишком большой')
        chat = json.loads(path.read_text(encoding='utf-8'))
        if chat.get('schema_version') != SCHEMA_VERSION or chat.get('id') != ident or not isinstance(chat.get('messages'), list):
            raise ValueError('Файл чата повреждён')
        for index, message in enumerate(chat['messages']):
            if message.get('role') == 'assistant':
                generation_ensure(message, 'Agent' if chat.get('kind') == 'agent' else 'Text', message.get('id', ident + ':' + str(index)))
        return chat

    def _save(self, chat):
        chat['updated'] = time.time()
        # Never replace a readable history with a document that _load refuses.
        if len(json.dumps(chat, ensure_ascii=False, indent=1).encode('utf-8')) > MAX_FILE_BYTES or len(chat['messages']) > MAX_MESSAGES:
            recovery = self.directory / (chat['id'] + '.overflow') / (uuid.uuid4().hex + '.json')
            _write_json(recovery, chat)
            raise ValueError('История достигла лимита. Начните новый чат. Полный результат сохранён: ' + str(recovery))
        _write_json(self._path(chat['id']), chat)
        chats = [item for item in self._read_index() if item['id'] != chat['id']]
        chats.insert(0, self._summary(chat))
        chats.sort(key=lambda item: item['updated'], reverse=True)
        self._write_index(chats)

    def list(self):
        with self.lock:
            return self._read_index()

    def create(self, system='', model='', think=True, agent=None):
        """A chat; with `agent` (id, workspace, permission, session) it is an agent chat of the Text tab's «Агент» mode."""
        with self.lock:
            if len(self._read_index()) >= MAX_CHATS:
                raise ValueError('Слишком много чатов: удалите ненужные')
            now = time.time()
            chat = dict(schema_version=SCHEMA_VERSION, id=uuid.uuid4().hex, title='Новый чат', title_auto=True, created=now, updated=now,
                        system=self._system(system), model=str(model)[:200], think=bool(think), messages=[])
            if agent is not None:
                chat.update(kind='agent', agent=dict(agent))
            self._save(chat)
            return chat

    def set_agent(self, ident, **changes):
        with self.lock:
            chat = self.get(ident)
            if chat.get('kind') != 'agent':
                raise ValueError('Это обычный чат, а не чат агента')
            chat['agent'].update(changes)
            self._save(chat)
            return chat

    @staticmethod
    def _system(value):
        if not isinstance(value, str) or len(value) > MAX_SYSTEM:
            raise ValueError('Системная инструкция: текст до %d символов' % MAX_SYSTEM)
        return value

    def get(self, ident):
        with self.lock:
            try:
                return self._load(ident)
            except FileNotFoundError:
                raise ValueError('Чат не найден') from None

    def rename(self, ident, title):
        if not isinstance(title, str) or not title.strip():
            raise ValueError('Введите название чата')
        with self.lock:
            chat = self.get(ident)
            chat.update(title=_title_from(title), title_auto=False)
            self._save(chat)
            return chat

    def update(self, ident, system=None, think=None, model=None):
        with self.lock:
            if ident in self.active:
                raise ValueError('Агент работает в этом чате: сначала остановите задачу')
            chat = self.get(ident)
            if system is not None:
                chat['system'] = self._system(system)
            if think is not None:
                if not isinstance(think, bool):
                    raise ValueError('Размышление: да или нет')
                chat['think'] = think
            if model is not None:
                chat['model'] = str(model)[:200]
            self._save(chat)
            return chat

    def delete(self, ident):
        with self.lock:
            if ident in self.active:
                from gpu import Busy
                raise Busy('Агент работает в этом чате: сначала остановите задачу')
            path = self._path(ident)
            if not path.is_file():
                raise ValueError('Чат не найден')
            path.unlink()
            shutil.rmtree(self._images(ident), ignore_errors=True)
            self._write_index([item for item in self._read_index() if item['id'] != ident])

    def _images(self, ident):
        return self.directory / (self._path(ident).stem + '.images')

    def image_path(self, ident, image_id):
        """Path of a stored image of this chat; ids and extensions come from the chat file, never from the request."""
        if not isinstance(image_id, str) or not ID_PATTERN.match(image_id):
            raise ValueError('Некорректный номер изображения')
        with self.lock:
            for message in self.get(ident)['messages']:
                for meta in message.get('images', []):
                    if meta['id'] == image_id:
                        path = self._images(ident) / (image_id + ('.png' if meta['mime'] == 'image/png' else '.jpg'))
                        if path.is_file():
                            return path, meta['mime']
        raise ValueError('Изображение не найдено')

    def images_b64(self, ident, message):
        """The message's images as base64 text, the form Ollama expects."""
        result = []
        for meta in message.get('images', []):
            path, _ = self.image_path(ident, meta['id'])
            result.append(base64.b64encode(path.read_bytes()).decode('ascii'))
        return result

    def add_user(self, ident, content, images=None, generation=None):
        images = images or []
        if not isinstance(content, str) or len(content) > MAX_CONTENT or not (content.strip() or images):
            raise ValueError('Сообщение: текст до %d символов или хотя бы одно изображение' % MAX_CONTENT)
        if len(images) > MAX_IMAGES_PER_MESSAGE:
            raise ValueError('К сообщению можно прикрепить до %d изображений' % MAX_IMAGES_PER_MESSAGE)
        with self.lock:
            chat = self.get(ident)
            if len(chat['messages']) + 2 > MAX_MESSAGES:
                raise ValueError('В чате слишком много сообщений: начните новый чат')
            if sum(len(m.get('images', [])) for m in chat['messages']) + len(images) > MAX_IMAGES_PER_CHAT:
                raise ValueError('В чате уже много изображений (не больше %d): начните новый чат' % MAX_IMAGES_PER_CHAT)
            message = dict(id=uuid.uuid4().hex, role='user', content=content, created=time.time())
            if generation:
                message['generation'] = dict(generation)
            written = []
            try:
                for image in images:
                    meta = dict(id=uuid.uuid4().hex, mime=image['mime'], width=image['width'], height=image['height'], size=len(image['raw']), name=image['name'])
                    target = self._images(ident) / (meta['id'] + image['extension'])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(image['raw'])
                    written.append(target)
                    message.setdefault('images', []).append(meta)
                chat['messages'].append(message)
                if chat.get('title_auto') and sum(1 for m in chat['messages'] if m['role'] == 'user') == 1:
                    chat['title'] = _title_from(content) or ('Изображение' if images else chat['title'])
                self._save(chat)
            except BaseException:
                for path in written:
                    path.unlink(missing_ok=True)
                raise
            return chat

    def update_generation(self, ident, generation, request=False):
        """Finalize all messages of one task; loop iterations share one operation ID."""
        with self.lock:
            chat = self.get(ident)
            if request:
                for message in reversed(chat['messages']):
                    if message['role'] == 'user':
                        message['generation'] = dict(generation)
                        break
            for message in chat['messages']:
                if (message.get('generation') or {}).get('id') == generation['id']:
                    message['generation'] = dict(generation)
            self._save(chat)
            return chat

    def prepare_regenerate(self, ident):
        """Regeneration or retry: drop a trailing assistant answer so the chat ends with the user message to answer."""
        with self.lock:
            chat = self.get(ident)
            if chat['messages'] and chat['messages'][-1]['role'] == 'assistant':
                chat['messages'].pop()
                self._save(chat)
            if not chat['messages'] or chat['messages'][-1]['role'] != 'user':
                raise ValueError('В чате нет вопроса, на который можно ответить')
            return chat

    def add_assistant(self, ident, content, thinking='', model='', stats=None, stopped=False, error='', steps=None, generation=None):
        with self.lock:
            chat = self.get(ident)
            message = dict(id=uuid.uuid4().hex, role='assistant', content=content[:MAX_CONTENT * 4], created=time.time(), model=model)
            if generation:
                message['generation'] = dict(generation)
            if thinking:
                message['thinking'] = thinking[:MAX_CONTENT * 4]
            if steps:
                message['steps'] = steps  # an agent's commands and edits, already bounded by the agent manager
            if stats:
                message['stats'] = stats
            if stopped:
                message['stopped'] = True
            if error:
                message['error'] = error[:500]
            chat['messages'].append(message)
            self._save(chat)
            return chat, message
