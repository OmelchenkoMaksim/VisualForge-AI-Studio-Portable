"""Authenticated loopback backend; model execution lives in owned CLI children."""
import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import struct
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parents[1]))
from smart_filenames import build_filename_base
from generation import begin as generation_begin, ensure as generation_ensure, finish as generation_finish, append_log as generation_log, duration_between, configure as configure_events, remove as remove_event
from video.registry import validate, ENGINES
from video.runtime import environment
from video.owned import OwnedJob
from video.output import filename_base, copy_unique
from video import delivery, mixer
from video.adapters import build_command
from video.wan_support import WAN_ID, S2V_ID, WAN_IDS, engine_missing, scratch as wan_scratch, archive_log

from video.ltx_support import LTX_ID, validate_audio
SCRATCH_IDS = (*WAN_IDS,LTX_ID)

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)

def stop_owned(process):
    if process is None or process.poll() is not None:
        return
    import psutil
    try:
        parent = psutil.Process(process.pid)
        # Snapshot descendants of this exact live child, never global GPU users.
        owned = [*reversed(parent.children(recursive=True)), parent]
        for item in owned:
            try:
                item.terminate()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(owned, timeout=8)
    except psutil.NoSuchProcess:
        pass

class Jobs:
    def __init__(self, args):
        self.args = args
        self.user = Path(args.user)
        self.root, self.models, self.state = map(Path, (args.root, args.models, args.state))
        self.lock = threading.RLock()
        self.files_lock = threading.Lock()  # settings and the prompt archive (read-modify-write files), apart from the job rows
        self.rows = {}
        self.active = None
        self.process = None
        self.exports, self.export_events = {}, {}
        self.history_path = self.user / 'Settings/video-job-history.json'
        self.settings_path = self.user / 'Settings/video.settings.json'
        self.warning = ''
        self.history_writable = True
        try:
            if self.history_path.exists():
                if self.history_path.stat().st_size > 5 * 1024 * 1024:
                    raise ValueError('History exceeds bound')
                document = json.loads(self.history_path.read_text(encoding='utf-8'))
                if document.get('schema_version') != 1 or not isinstance(document.get('entries'), list) or len(document['entries']) > 100:
                    raise ValueError('Invalid history')
                for row in document['entries']:
                    if str(uuid.UUID(row['id'])) != row['id'] or row['state'] not in ('done', 'error', 'cancelled'):
                        raise ValueError('Invalid receipt')
                    for key in ('folder', 'output_path', 'log_path'):
                        if row.get(key) and not Path(row[key]).is_absolute():
                            path = (self.user / row[key]).resolve()
                            path.relative_to(self.user.resolve())
                            row[key] = str(path)
                    row['export_files'] = [str((self.user / value).absolute()) if not Path(value).is_absolute() else value for value in row.get('export_files', [])]
                    generation_ensure(row, 'Video', row['id'], row.get('submitted_at'), duration_between(row.get('submitted_at'), row.get('finished_at')))
                    self.rows[row['id']] = row
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            self.warning = 'Историю Video не удалось прочитать; исходный файл сохранён.'
            self.history_writable = False

    def settings(self):
        result = {'save_dir': str(self.user / 'Results/Video')}
        if self.settings_path.exists():
            raw = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if not isinstance(raw.get('save_dir'), str) or not raw['save_dir']:
                raise ValueError('Некорректная папка Video в настройках')
            path = Path(raw['save_dir'])
            if not path.is_absolute():
                path = (self.user / path).resolve()
                path.relative_to(self.user.resolve())
            result.update(save_dir=str(path))
        return result

    def save_settings(self, data):
        path = data.get('save_dir')
        if not isinstance(path, str) or not path.strip() or not Path(path).is_absolute() or '\x00' in path:
            raise ValueError('Укажите полный путь к папке Video')
        selected = Path(path.strip()).resolve()
        stored = selected.relative_to(self.user.resolve()).as_posix() if selected.is_relative_to(self.user.resolve()) else str(selected)
        atomic_json(self.settings_path, {'save_dir': stored})
        return self.settings()

    def persist(self):
        if not self.history_writable:
            return
        rows = copy.deepcopy([r for r in self.rows.values() if r['state'] in ('done', 'error', 'cancelled')][-100:])
        for row in rows:
            row.pop('cancel_token', None)
            for key in ('folder', 'output_path', 'log_path'):
                if row.get(key):
                    path = Path(row[key]).resolve()
                    if path.is_relative_to(self.user.resolve()):
                        row[key] = path.relative_to(self.user.resolve()).as_posix()
            row['export_files'] = [Path(value).resolve().relative_to(self.user.resolve()).as_posix() if Path(value).resolve().is_relative_to(self.user.resolve()) else value for value in row.get('export_files', [])]
        atomic_json(self.history_path, {'schema_version': 1, 'entries': rows})

    def delete(self, data):
        from job_delete import DeletePlan
        ident = data.get('id')
        if not isinstance(ident, str) or str(uuid.UUID(ident)) != ident:
            raise ValueError('Неверный идентификатор Video')
        with self.lock:
            row = self.rows.get(ident)
            if row is None or self.active == ident or row['state'] not in ('done', 'error', 'cancelled'):
                raise ValueError('Задача не найдена или ещё выполняется. Сначала остановите генерацию.')
            if not self.history_writable:
                raise ValueError('История Video недоступна для записи.')
            if any(r['state'] == 'running' for r in self.exports.values()):
                raise ValueError('Дождитесь завершения экспорта или микширования перед удалением Video.')
            plan = DeletePlan()
            output = Path(row['output_path']) if row.get('output_path') else None
            if output:
                if output.suffix.lower() != '.mp4':
                    raise ValueError('Некорректный файл результата Video')
                plan.file(output)
                plan.sidecar(output.with_suffix('.json'), 'id', ident)
                plan.sidecar(output.with_name(output.stem + '.' + ident + '.json'), 'id', ident)
                if row.get('derived_kind') == 'mix':
                    plan.sidecar(output.with_suffix('.mix.json'), 'output_file', output.name)
            # Derived videos inherit the source folder/log. Remove shared scratch only with its last receipt.
            if row.get('folder'):
                folder = Path(os.path.abspath(row['folder']))
                bases = [Path(os.path.abspath(base)) for base in (self.user / 'Cache/Video/Jobs', wan_scratch(self.user) / 'Jobs')]
                if folder.parent not in bases or str(uuid.UUID(folder.name)) != folder.name:
                    raise ValueError('Некорректная техническая папка Video')
                if not any(r.get('folder') and Path(os.path.abspath(r['folder'])) == folder for key, r in self.rows.items() if key != ident):
                    plan.tree(folder)
            log = Path(row.get('log_path') or self.user / 'Logs/Video/Wan' / (ident + '.log'))
            if log.parent != self.user / 'Logs/Video/Wan' or str(uuid.UUID(log.stem)) != log.stem or log.suffix != '.log':
                raise ValueError('Некорректный путь журнала Video')
            if not any(r.get('log_path') and Path(r['log_path']) == log for key, r in self.rows.items() if key != ident):
                plan.file(log)
            for value in row.get('export_files', []):
                path = Path(value)
                if path.suffix.lower() != '.mp4':
                    raise ValueError('Некорректный файл экспорта Video')
                plan.file(path)
                plan.sidecar(path.with_suffix('.delivery.json'), 'output_file', path.name)
            plan.execute()
            remove_event(row.get('generation'))
            del self.rows[ident]
            try:
                self.persist()
            except OSError:
                self.rows[ident] = row
                raise
            related = [key for key, value in self.exports.items() if value.get('source_id') == ident or value.get('result_id') == ident]
            for key in related:
                self.exports.pop(key, None)
                self.export_events.pop(key, None)
            return {'deleted': True}

    def public(self, row):
        result = copy.deepcopy(row)
        result.pop('folder', None)
        result.pop('output_path', None)
        if row.get('output_path') and row['state'] == 'done':
            result['saved_path'] = row['output_path']
        if row['state'] in ('done', 'error', 'cancelled'):
            result.pop('cancel_token', None)
        result['elapsed_sec'] = round((row.get('finished_at') or time.time()) - row['submitted_at'])
        return result

    def image(self, value, path):
        if not isinstance(value, str) or not value.startswith('data:image/png;base64,') or len(value) > 48 * 1024 * 1024:
            raise ValueError('Для Video нужен PNG-кадр размером до 36 МиБ')
        raw = base64.b64decode(value.partition(',')[2], validate=True)
        if raw[:8] != b'\x89PNG\r\n\x1a\n' or len(raw) < 33:
            raise ValueError('Повреждённый PNG-кадр')
        width, height = struct.unpack('>II', raw[16:24])
        if not 1 <= width <= 8192 or not 1 <= height <= 8192:
            raise ValueError('Неподдерживаемый размер исходного кадра')
        # Decode fully before committing a GPU job; private Pillow only.
        from PIL import Image
        import io
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        if path is not None:
            path.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def submit(self, data):
        spec = validate(data)
        self.check_request(data)
        with self.lock:
            if self.active is not None:
                raise ValueError('GPU занят задачей Video')
        # Decoding pictures and probing voice samples takes seconds: done outside the lock, which the busy probe needs.
        ident = str(uuid.uuid4())
        folder = (wan_scratch(self.user) / 'Jobs' / ident) if spec['engine'] in SCRATCH_IDS else self.user / 'Cache/Video/Jobs' / ident
        folder.mkdir(parents=True, exist_ok=False)
        media = {'version': 1}
        for field, key, needed in [('source_image', 'first', spec['mode'] in ('i2v', 'first_last','s2v')), ('end_image', 'last', spec['mode'] == 'first_last')]:
            if needed:
                target = folder / (key + '.png')
                spec[key + '_sha256'] = self.image(data.get(field), target)
                media[key] = str(target)
        if len(media) > 1:
            atomic_json(folder / 'media.json', media)
        if spec['engine']==S2V_ID:
            from video.s2v_audio import prepare
            spec.update(prepare(self,data,folder))
        if spec['mode'] == 'characters':
            spec['characters'] = self.people(data['characters'], folder)
            if any(p['speech_text'] for p in spec['characters']):
                spec['voice_model'] = {'id': 'Qwen/Qwen3-TTS-12Hz-0.6B-Base', 'revision': '5d83992436eae1d760afd27aff78a71d676296fc', 'package': 'qwen-tts==0.1.1', 'mode': 'speaker_embedding', 'lip_sync': False}
        spec['workflow_sha256'] = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
        save_dir = self.settings()['save_dir']
        with self.lock:
            if self.active is not None:  # another start won the slot meanwhile (the bridge serializes starts, so only a direct caller)
                raise ValueError('GPU занят задачей Video')
            row = dict(spec, id=ident, generation=generation_begin('Video'), module='video', state='queued', phase='Подготовка Video',
                submitted_at=time.time(), folder=str(folder), save_dir=save_dir,
                cancel_token=secrets.token_urlsafe(32), events=[], output_path=None,
                engine_release=ENGINES[spec['engine']]['release'], model_revision=ENGINES[spec['engine']]['model_revision'])
            row['engine_commit'] = ENGINES[spec['engine']]['commit']
            self.rows[ident] = row
            self.active = ident
            threading.Thread(target=self.run, args=(ident,), daemon=True).start()
            return self.public(row)

    def check_request(self, data):
        spec = validate(data)
        if spec['engine'] in SCRATCH_IDS and engine_missing(self.root,self.models,spec['engine']):
            raise ValueError(ENGINES[spec['engine']]['name']+': среда или модели не установлены. Скачайте их во вкладке «Компоненты».')
        if spec['mode'] in ('i2v', 'first_last','s2v'):
            self.image(data.get('source_image'), None)
        if spec['engine']==S2V_ID:
            cloning=spec['speech_mode']=='clone'
            if cloning and (not (self.root/'voice-packages/qwen_tts/__init__.py').is_file() or not (self.models.parent/'Qwen3TTS/config.json').is_file()):
                raise ValueError('Для речи по образцу нужен установленный модуль Qwen3-TTS')
            self.audio_bytes(data['voice_audio' if cloning else 'source_audio'])
        if spec['mode'] == 'first_last':
            self.image(data.get('end_image'), None)
        if spec['mode'] == 'characters':
            for person in data['characters']:
                self.image(person['image'], None)
                if person.get('speech_text', '').strip():
                    if not (self.root / 'voice-packages/qwen_tts/__init__.py').is_file() or not (self.models.parent / 'Qwen3TTS/config.json').is_file():
                        raise ValueError('Локальный модуль голоса по образцу не установлен')
                    self.audio_bytes(person['voice_audio'])
        return {'valid': True}

    @staticmethod
    def audio_bytes(value):
        if not isinstance(value, str) or not value.startswith('data:audio/') or ';base64,' not in value or len(value) > 12 * 1024 * 1024:
            raise ValueError('Образец голоса: аудиофайл до 8 МиБ, 2–30 секунд')
        raw = base64.b64decode(value.partition(';base64,')[2], validate=True)
        if not raw or len(raw) > 8 * 1024 * 1024:
            raise ValueError('Образец голоса пустой или больше 8 МиБ')
        return raw

    def people(self, entries, folder):
        people = []
        for index, person in enumerate(entries, 1):
            image = folder / ('character-%d.png' % index)
            image_sha = self.image(person['image'], image)
            row = {'name': person.get('name', '').strip() or 'Character %d' % index,
                   'image_path': str(image), 'image_sha256': image_sha,
                   'speech_text': person.get('speech_text', '').strip(), 'language': person.get('language', 'Russian')}
            if row['speech_text']:
                voice = folder / ('voice-%d.input' % index)
                voice.write_bytes(self.audio_bytes(person['voice_audio']))
                try:
                    probe = subprocess.run([str(self.root / 'ffmpeg/bin/ffprobe.exe'), '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(voice)], capture_output=True, text=True, timeout=15, check=True)
                except (subprocess.SubprocessError, OSError) as error:
                    raise ValueError('Не удалось прочитать образец голоса. Используйте WAV, MP3, FLAC или M4A.') from error
                info = json.loads(probe.stdout)
                if not any(s['codec_type'] == 'audio' for s in info['streams']) or not 2 <= float(info['format']['duration']) <= 30:
                    raise ValueError('Нужен образец голоса длительностью от 2 до 30 секунд')
                row.update(voice_path=str(voice), voice_sha256=hashlib.sha256(voice.read_bytes()).hexdigest())
            people.append(row)
        return people

    def prompts(self, data=None):
        path = self.user / 'Prompts/video-prompts.json'
        entries = []
        if path.exists():
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError('Архив Video превышает допустимый размер; файл сохранён.')
            document = json.loads(path.read_text(encoding='utf-8'))
            entries = document['entries']
            if not isinstance(entries, list) or len(entries) > 100:
                raise ValueError('Архив Video повреждён; файл сохранён.')
        if data is not None:
            if data.get('action') == 'add':
                text = data.get('prompt')
                if not isinstance(text, str) or not text.strip() or len(text) > 5000:
                    raise ValueError('Введите промпт длиной до 5000 символов')
                entries.insert(0, {'id': uuid.uuid4().hex, 'module': 'video', 'engine': 'freevideo-h3-int8', 'prompt': text.strip()})
                entries = entries[:100]
            elif data.get('action') == 'delete':
                entries = [r for r in entries if r.get('id') != data.get('id')]
            else:
                raise ValueError('Неизвестная операция с архивом Video')
            atomic_json(path, {'schema_version': 1, 'entries': entries})
        return {'entries': entries}

    def export_source(self, ident):
        with self.lock:
            row = self.rows.get(ident) if isinstance(ident, str) else None
            path = row.get('output_path') if row and row['state'] == 'done' else None
        if not path or Path(path).suffix.lower() != '.mp4' or not Path(path).is_file():
            raise ValueError('Готовое видео не найдено. Откройте ролик из истории Video.')
        return Path(path)

    def export_preview(self, query):
        """Same plan the export will use, from a real probe of the saved MP4; no files are written."""
        source = self.export_source(query.get('id', [''])[0])
        info = delivery.probe(delivery.ffmpeg_paths(self.root)[1], source)
        return delivery.plan_export(info, query.get('preset', [''])[0], query.get('audio', ['keep'])[0])

    def public_export(self, row):
        result = copy.deepcopy(row)
        if row['state'] != 'running':
            result.pop('cancel_token', None)
        result['elapsed_sec'] = round((row.get('finished_at') or time.time()) - row['started_at'])
        return result

    def export_status(self, ident):
        with self.lock:
            if ident:
                if ident not in self.exports:
                    raise ValueError('Экспорт не найден')
                return self.public_export(self.exports[ident])
            running = next((r for r in self.exports.values() if r['state'] == 'running'), None)
            return {'export': self.public_export(running) if running else None}

    def start_export(self, data):
        """CPU-only post-processing of a finished clip; it never claims the GPU, so it can run beside a generation."""
        if set(data) - {'id', 'preset', 'audio'}:
            raise ValueError('Запрос экспорта содержит неподдерживаемые параметры')
        preset, audio = data.get('preset'), data.get('audio', 'keep')
        if not isinstance(preset, str) or preset not in delivery.PRESETS:
            raise ValueError('Выберите пресет экспорта')
        if audio not in delivery.AUDIO_MODES:
            raise ValueError('Выберите режим звука')
        source = self.export_source(data.get('id'))
        blocked = self.export_preview(dict(id=[data['id']], preset=[preset], audio=[audio]))['blocked']
        if blocked:
            raise ValueError(blocked)
        directory = Path(self.settings()['save_dir'])
        with self.lock:
            if any(r['state'] == 'running' for r in self.exports.values()):
                raise ValueError('Экспорт уже выполняется. Дождитесь завершения или отмените его.')
            self.export_source(data['id'])  # Recheck after taking the lock: deletion may have won during the probe.
            ident = str(uuid.uuid4())
            row = dict(id=ident, generation=generation_begin('VideoExport'), source_id=data['id'], preset=preset, label=delivery.PRESETS[preset]['label'], audio=audio,
                       state='running', phase='Подготовка', progress=0.0, started_at=time.time(), cancel_token=secrets.token_urlsafe(24))
            event = self.export_events[ident] = threading.Event()
            self.exports[ident] = row
            self.exports = dict(list(self.exports.items())[-20:])
        threading.Thread(target=self.run_export, args=(row, source, directory, event), daemon=True).start()
        return self.public_export(row)

    def run_export(self, row, source, directory, cancel):
        ffmpeg, ffprobe = delivery.ffmpeg_paths(self.root)

        def progress(fraction):
            row['progress'], row['phase'] = round(fraction, 3), 'Кодирование: %d%%' % round(fraction * 100)

        try:
            receipt = delivery.export_clip(source, directory, row['preset'], ffmpeg, ffprobe, audio=row['audio'], cancel=cancel, progress=progress)
            self.export_identity(row, receipt, directory, '.delivery.json')
            final = dict(state='done', phase='Готово', progress=1.0, receipt=receipt, filename=receipt['output_file'],
                         saved_path=str(Path(directory) / receipt['output_file']))
        except delivery.DeliveryCancelled:
            final = dict(state='cancelled', phase='Отменено')
        except (ValueError, delivery.DeliveryError) as error:
            final = dict(state='error', phase='Ошибка экспорта', error=str(error)[-1500:])
        except Exception as error:
            final = dict(state='error', phase='Ошибка экспорта', error='Непредвиденная ошибка экспорта: ' + str(error)[-500:])
        with self.lock:
            self.export_events.pop(row['id'], None)
            generation_finish(row.get('generation'), state=final['state'])
            row.update(final, finished_at=time.time())
            if row['state'] == 'done':
                source_row = self.rows.get(row['source_id'])
                if source_row is not None:
                    source_row.setdefault('export_files', []).append(row['saved_path'])
                    try:
                        self.persist()
                    except OSError:
                        self.warning = 'Экспорт сохранён, но запись истории не удалась.'

    def export_identity(self, row, receipt, directory, suffix):
        record = generation_finish(row.get('generation'))
        if record:
            receipt['generation'] = dict(record)
            try:
                atomic_json((Path(directory) / receipt['output_file']).with_suffix(suffix), receipt)
            except OSError:
                row['metadata_warning'] = 'Результат сохранён, но идентификатор в метаданные записать не удалось.'

    def mix_plan(self, data):
        request = mixer.validate_request(data)
        source = self.export_source(request['id'])
        ffprobe = delivery.ffmpeg_paths(self.root)[1]
        video = delivery.probe(ffprobe, source)
        return request, source, mixer.plan_mix(video, request, [mixer.probe_audio(ffprobe, track['path']) for track in request['tracks']])

    def mix_preview(self, data):
        return self.mix_plan(data)[2]

    def start_mix(self, data):
        """Lay music under a finished clip. Shares the one-at-a-time slot, status and cancel routes with the export."""
        request, source, plan = self.mix_plan(data)
        if plan['blocked']:
            raise ValueError(plan['blocked'])
        directory = Path(self.settings()['save_dir'])
        with self.lock:
            if any(r['state'] == 'running' for r in self.exports.values()):
                raise ValueError('Экспорт или микширование уже выполняется. Дождитесь завершения или отмените его.')
            self.export_source(request['id'])
            ident = str(uuid.uuid4())
            row = dict(id=ident, generation=generation_begin('VideoMix'), kind='mix', source_id=request['id'], label='Музыка и звук', state='running', phase='Подготовка', progress=0.0,
                       started_at=time.time(), cancel_token=secrets.token_urlsafe(24))
            event = self.export_events[ident] = threading.Event()
            self.exports[ident] = row
            self.exports = dict(list(self.exports.items())[-20:])
        threading.Thread(target=self.run_mix, args=(row, source, directory, event, request), daemon=True).start()
        return self.public_export(row)

    def run_mix(self, row, source, directory, cancel, request):
        ffmpeg, ffprobe = delivery.ffmpeg_paths(self.root)

        def progress(fraction):
            row['progress'], row['phase'] = round(fraction, 3), 'Сведение звука: %d%%' % round(fraction * 100)

        try:
            receipt = mixer.mix_clip(source, directory, request, ffmpeg, ffprobe, cancel=cancel, progress=progress)
            self.export_identity(row, receipt, directory, '.mix.json')
            saved = Path(directory) / receipt['output_file']
            final = dict(state='done', phase='Готово', progress=1.0, receipt=receipt, filename=receipt['output_file'], saved_path=str(saved),
                         result_id=self.register_mix(row['source_id'], saved, receipt, row.get('generation')))
        except delivery.DeliveryCancelled:
            final = dict(state='cancelled', phase='Отменено')
        except (ValueError, delivery.DeliveryError) as error:
            final = dict(state='error', phase='Ошибка микширования', error=str(error)[-1500:])
        except Exception as error:
            final = dict(state='error', phase='Ошибка микширования', error='Непредвиденная ошибка микширования: ' + str(error)[-500:])
        with self.lock:
            self.export_events.pop(row['id'], None)
            generation_finish(row.get('generation'), state=final['state'])
            row.update(final, finished_at=time.time())

    def register_mix(self, source_id, target, receipt, generation=None):
        """The mixed MP4 becomes a history entry of its own, so it can be opened, exported and mixed again."""
        with Path(target).open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()  # reads the whole clip: before taking the lock
        with self.lock:
            row = copy.deepcopy(self.rows[source_id])
            ident = str(uuid.uuid4())
            row.pop('export_files', None)
            row.update(id=ident, generation=generation_finish(generation or generation_begin('Video')), state='done', phase='Готово', output_path=str(target), saved_path=str(target), filename=Path(target).name, output_sha256=digest,
                       submitted_at=time.time(), finished_at=time.time(), derived_from=source_id, derived_kind='mix',
                       mix_summary='Музыка: %s' % ', '.join(track['label'] for track in receipt['tracks']))
            row['metadata'] = dict(row.get('metadata') or {}, has_audio=True, duration=receipt['delivered']['duration'])
            row.pop('cancel_token', None)
            self.rows[ident] = row
            self.rows = dict(list(self.rows.items())[-100:])
            try:
                self.persist()
            except OSError:
                self.warning = 'Ролик со звуком сохранён, но запись истории не удалась.'
        return ident

    def cancel_export(self, data):
        with self.lock:
            ident = data.get('id')
            row = self.exports.get(ident) if isinstance(ident, str) else None
            if row is None or not secrets.compare_digest(str(data.get('cancel_token', '')), row['cancel_token']):
                raise ValueError('Нет права отменять этот экспорт')
            event = self.export_events.get(ident)
            if row['state'] != 'running' or event is None:
                return {'cancelled': False}
            row['phase'] = 'Остановка экспорта'
        event.set()
        return {'cancelled': True}

    def shutdown(self):
        with self.lock:
            if self.active and self.rows[self.active]['state'] in ('queued', 'running'):
                self.rows[self.active]['state'] = 'cancelling'
            process = self.process
            for event in self.export_events.values():
                event.set()
        stop_owned(process)
        for _ in range(100):
            if self.active is None:
                break
            time.sleep(.1)

    def event(self, row, value):
        event = value.get('event')
        if not isinstance(event, str):
            return
        if event in ('wan_metrics','s2v_metrics','ltx_metrics'):
            metrics=row.setdefault('runtime_metrics',{})
            for key in ('inference_sec','peak_allocated_vram_mb','torch','python'):
                if key in value:
                    metrics[key]=max(metrics.get(key,0),value[key]) if key=='peak_allocated_vram_mb' else value[key]
            return
        if event == 'continuation_start':
            row.update(continuation_part=value['part'],continuation_parts=value['parts'],phase='Продолжение %s / %s: подготовка' % (value['part'],value['parts']))
            return
        if event == 'scene_start':
            row.update(scene=value['scene'], scenes=value['scenes'], phase='Сцена %s / %s: подготовка' % (value['scene'], value['scenes']))
            return
        if event == 'voice_start':
            row['phase'] = 'Озвучка по образцу: ' + str(value['name'])
            return
        if event == 'assembly_start':
            row['phase'] = 'Сборка продолжения видео и звука в MP4' if row.get('continuation_part') else 'Сборка сцен и озвучки в MP4'
            return
        if event == 'sampling_plan':
            row['window_sampling_plan' if value.get('scope')=='window' else 'sampling_plan'] = copy.deepcopy(value)
        compact = {k: value[k] for k in ('event', 'stage', 'phase', 'step', 'completed_steps', 'total', 'done', 'blocks', 'remaining_seconds') if k in value}
        row['events'] = (row['events'] + [compact])[-35:]
        if event.startswith('encoder_') or event == 'encoding_start':
            row['phase'] = 'Подготовка запроса и исходных кадров'
        elif event in ('model_load_phase', 'prepared_blocks', 'loaded'):
            row['phase'] = 'Загрузка видеомодели'
        elif event == 'step':
            row['phase'] = ('Проход %s · ' % value['stage'] if value.get('stage') else '')+'Генерация: шаг %s / %s' % (value.get('completed_steps', value.get('step')), value.get('total'))
        elif event in ('sampling_start', 'sampling_prepare', 'sampling_progress'):
            row['phase'] = 'Генерация видео'
        elif event == 'decode_progress':
            row['phase'] = 'Декодирование: %s / %s кадров' % (value.get('done'), value.get('total'))
        elif event == 'decode_phase':
            row['phase'] = 'Декодирование и кодирование MP4'

    def run(self, ident):
        row = self.rows[ident]
        folder = Path(row['folder'])
        output = folder / 'result.mp4'
        offsets = {}
        owned = None
        engine_record = None
        try:
            (folder / 'prompt.txt').write_text(row['original_prompt'], encoding='utf-8')
            command = build_command(self.root, self.models, folder, row)
            atomic_json(folder / 'workflow-command.json', command)
            with self.lock:
                if row['state'] == 'cancelling':
                    row['state'] = 'cancelled'
                    return
                row['state'] = 'running'
                with (folder / 'backend.log').open('wb') as log:
                    state = wan_scratch(self.user) / 'Runtime' if row['engine'] in SCRATCH_IDS else self.state
                    env = environment(self.root,self.models,state)
                    engine_record = generation_begin('VideoModelGeneration', parent_id=(row.get('generation') or {}).get('id'))
                    env['VISUALFORGE_EVENT_PARENT'] = engine_record['id']
                    if row['engine'] in SCRATCH_IDS:
                        env['PYTHONDONTWRITEBYTECODE'] = '1'
                    self.process = subprocess.Popen(command, cwd=self.root, env=env,
                        stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                owned = OwnedJob(self.process)
            while self.process.poll() is None:
                self.read_events(row, folder, offsets)
                time.sleep(.4)
            self.read_events(row, folder, offsets)
            if row['state'] == 'cancelling':
                row['state'], row['phase'] = 'cancelled', 'Отменено'
                return
            if self.process.returncode != 0 or not output.exists():
                logs = '\n'.join(p.read_text(encoding='utf-8', errors='replace')[-6000:] for p in folder.glob('*.log'))
                raise ValueError('Движок Video завершился с ошибкой. ' + logs[-1200:])
            row['state'], row['phase'] = 'saving', 'Проверка и сохранение MP4'
            # Decoding every frame proves a short clip plays; for long ones (minutes) counting packets is the same frame count without a full decode.
            counter = '-count_frames' if row['frames'] <= 600 else '-count_packets'
            probe = subprocess.run([str(self.root / 'ffmpeg/bin/ffprobe.exe'), '-v', 'error', counter, '-show_streams', '-show_format', '-of', 'json', str(output)], capture_output=True, text=True, check=True, timeout=900)
            metadata = json.loads(probe.stdout)
            video = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
            counted = int(video['nb_read_frames' if counter == '-count_frames' else 'nb_read_packets'])
            if (video['width'], video['height'], counted) != (row['width'], row['height'], row['frames']):
                raise ValueError('Параметры MP4 отличаются от запрошенного профиля')
            row['metadata'] = dict(width=video['width'], height=video['height'], fps=video['r_frame_rate'],
                duration=float(video['duration']), frames=counted, codec=video['codec_name'],
                has_audio=any(s['codec_type'] == 'audio' for s in metadata['streams']))
            if row['engine']==S2V_ID and (video['r_frame_rate']!='16/1' or not row['metadata']['has_audio']):
                raise ValueError('S2V должен сохранить 16 FPS и аудиопоток речи')
            if row['engine']==LTX_ID:
                validate_audio(metadata['streams'],row['frames']/row['fps'])
            base = filename_base(row['original_prompt'], row['submitted_at'])
            target = copy_unique(output, row['save_dir'], base)
            row['output_path'] = str(target)
            row['saved_path'] = str(target)
            row['filename'] = target.name
            with target.open('rb') as stream:
                row['output_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
            generation_finish(row.get('generation'))
            row['state'], row['phase'] = 'done', 'Готово'
            row['finished_at'] = time.time()
            sidecar = target.with_suffix('.json')
            if sidecar.exists():
                sidecar = target.with_name(target.stem + '.' + ident + '.json')
            try:
                with sidecar.open('x', encoding='utf-8') as stream:
                    json.dump(self.public(row), stream, ensure_ascii=False, indent=2)
            except OSError as error:
                row['metadata_warning'] = 'MP4 сохранён, но отдельный файл метаданных записать не удалось: ' + str(error)
        except Exception as error:
            row['state'], row['phase'], row['error'] = 'error', 'Ошибка Video', str(error)[-1800:]
        finally:
            stop_owned(self.process)  # outside the lock: it may wait up to 8 s for the worker tree to exit
            with self.lock:
                if owned:
                    owned.close()
                self.process = None
                row['finished_at'] = time.time()
                terminal_state = 'cancelled' if row['state'] == 'cancelled' else 'error' if row['state'] == 'error' else 'done'
                generation_finish(engine_record, state=terminal_state)
                generation_finish(row.get('generation'), state=terminal_state)
                generation_log(folder / 'backend.log', row.get('generation'))
                self.active = None
                self.rows = dict(list(self.rows.items())[-100:])
                if row['engine'] in SCRATCH_IDS:
                    try:
                        row['log_path'] = str(archive_log(self.user,folder,ident))
                    except OSError:
                        row['log_warning'] = 'Не удалось сохранить короткий журнал Video.'
                try:
                    self.persist()
                except OSError:
                    self.warning = 'Видео сохранено, но запись истории не удалась.'

    def read_events(self, row, folder, offsets):
        for path in folder.rglob('*.log'):
            with path.open('rb') as stream:
                stream.seek(offsets.get(str(path), 0))
                for raw in stream:
                    if not raw.endswith(b'\n'):
                        break
                    offsets[str(path)] = stream.tell()
                    try:
                        value = json.loads(raw)
                        if isinstance(value, dict):
                            with self.lock:
                                self.event(row, value)
                    except (ValueError, UnicodeDecodeError):
                        pass

    def cancel(self, data):
        with self.lock:
            row = self.rows.get(data.get('id'))
            if not row:
                raise ValueError('Нет права отменять эту задачу Video')
            if row['state'] not in ('queued', 'running', 'cancelling'):
                return {'cancelled': False}
            if not secrets.compare_digest(str(data.get('cancel_token', '')), row['cancel_token']):
                raise ValueError('Нет права отменять эту задачу Video')
            row['state'], row['phase'] = 'cancelling', 'Остановка Video'
            process = self.process
        stop_owned(process)
        return {'cancelled': True}

def make_handler(jobs, token, stop_server):
    """The service's request handler, built apart from main() so tests can serve it in-process."""
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, value):
            raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def dispatch(self, data=None):
            if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                return self.reply(403, {'error': 'Unauthorized'})
            parsed = urlparse(self.path)
            try:
                # jobs.lock only guards the in-memory rows: answers are copied under it and files are read and written outside it,
                # so the busy probe (/active, polled every second or so) never waits behind a disk read of the hard drive.
                ident = parse_qs(parsed.query).get('id', [''])[0]
                if parsed.path == '/health':
                    return self.reply(200, {'ready': True})
                if parsed.path == '/active':
                    with jobs.lock:
                        answer = {'busy': jobs.active is not None, 'job': jobs.public(jobs.rows[jobs.active]) if jobs.active else None}
                    return self.reply(200, answer)
                if parsed.path == '/history':
                    with jobs.lock:
                        answer = {'entries': [jobs.public(r) for r in reversed(list(jobs.rows.values())) if r['state'] in ('done', 'cancelled', 'error')][:100], 'warning': jobs.warning}
                    return self.reply(200, answer)
                if parsed.path == '/settings':
                    with jobs.files_lock:
                        answer = jobs.settings() if data is None else jobs.save_settings(data)
                    return self.reply(200, answer)
                if parsed.path == '/prompts':
                    with jobs.files_lock:
                        answer = jobs.prompts(data)
                    return self.reply(200, answer)
                if parsed.path == '/validate' and data is not None:
                    return self.reply(200, jobs.check_request(data))
                if parsed.path in ('/status', '/file', '/logs'):
                    with jobs.lock:
                        row = jobs.rows.get(ident)
                        if row is not None:
                            row = jobs.public(row) if parsed.path == '/status' else {key: row[key] for key in ('state', 'output_path', 'log_path', 'folder') if key in row}
                    if row is None:
                        return self.reply(404, {'error': 'Задача Video не найдена'})
                    if parsed.path == '/status':
                        return self.reply(200, row)
                    if parsed.path == '/file':
                        return self.reply(200, {'path': row.get('output_path') if row['state'] == 'done' else None})
                    if row.get('log_path') and row['state'] in ('done','error','cancelled'):
                        path = Path(row['log_path'])
                        text = path.read_text(encoding='utf-8',errors='replace')[-48000:] if path.is_file() else 'Журнал задачи недоступен.'
                        return self.reply(200, {'text':text})
                    folder = Path(row.get('folder', jobs.user / 'Cache/Video/Jobs' / ident))
                    text = '\n\n'.join(p.name + '\n' + p.read_text(encoding='utf-8', errors='replace')[-24000:] for p in folder.glob('*.log'))
                    return self.reply(200, {'text': text[-100000:]})
                if parsed.path == '/export-preview' and data is None:
                    return self.reply(200, jobs.export_preview(parse_qs(parsed.query)))
                if parsed.path == '/export-status' and data is None:
                    return self.reply(200, jobs.export_status(ident))
                if parsed.path == '/mix-preview' and data is not None:
                    return self.reply(200, jobs.mix_preview(data))
                if parsed.path == '/mix' and data is not None:
                    return self.reply(200, jobs.start_mix(data))
                if parsed.path == '/export' and data is not None:
                    return self.reply(200, jobs.start_export(data))
                if parsed.path == '/export-cancel' and data is not None:
                    return self.reply(200, jobs.cancel_export(data))
                if parsed.path == '/delete' and data is not None:
                    return self.reply(200, jobs.delete(data))
                if parsed.path == '/generate' and data is not None:
                    return self.reply(200, jobs.submit(data))
                if parsed.path == '/cancel' and data is not None:
                    return self.reply(200, jobs.cancel(data))
                if parsed.path == '/shutdown' and data is not None:
                    jobs.shutdown()
                    stop_server()
                    return self.reply(200, {'stopped': True})
                self.reply(404, {'error': 'Not found'})
            except (ValueError, OSError, KeyError, TypeError, delivery.DeliveryError) as error:
                self.reply(400, {'error': str(error)[:1500]})
        def do_GET(self):
            self.dispatch()
        def do_POST(self):
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 100 * 1024 * 1024:
                return self.reply(413, {'error': 'Request too large'})
            try:
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('Expected object')
                self.dispatch(data)
            except ValueError as error:
                self.reply(400, {'error': str(error)})
        def log_message(self, *args):
            pass
    return Handler

def main():
    parser = argparse.ArgumentParser()
    for name in ('root', 'models', 'user', 'state'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--parent', required=True, type=int)
    args = parser.parse_args()
    configure_events(Path(args.user) / 'Settings/event-history.sqlite3', inherit=True)
    token = os.environ['VISUALFORGE_VIDEO_TOKEN']
    jobs = Jobs(args)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(jobs, token, lambda: threading.Thread(target=server.shutdown, daemon=True).start()))
    def watch_parent():
        import psutil
        try:
            parent = psutil.Process(args.parent)
            while parent.is_running():
                time.sleep(2)
        except psutil.NoSuchProcess:
            pass
        jobs.shutdown()
        server.shutdown()
    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        server.serve_forever(poll_interval=.25)
    finally:
        jobs.shutdown()
        server.server_close()

if __name__ == '__main__':
    main()
