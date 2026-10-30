"""Local cross-process event history; payloads never contain prompts, audio or images."""
from ui_strings import source as ui_text
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
from urllib.parse import parse_qs

ID = re.compile(r'[0-9a-f]{64}\Z')
STATES = ('running', 'done', 'error', 'cancelled', 'interrupted')
MAIN_TYPES = {'Image': ui_text('events.operation_type_image_label'), 'Video': ui_text('events.operation_type_video_label'), 'Music': ui_text('events.operation_type_music_label'), 'Text': ui_text('events.operation_type_text_label'), 'Agent': ui_text('events.operation_type_agent_label'),
              'Voiceover': ui_text('events.operation_type_voiceover_label'),
              'VideoExport': ui_text('events.operation_type_video_export_label'), 'VideoMix': ui_text('events.operation_type_video_mix_label')}
TECHNICAL_TYPES = {
    'Speech': ui_text('events.operation_type_speech_label'),
    'MicrophoneSession': ui_text('events.operation_type_microphone_session_label'),
    'SpeechRecognition': ui_text('events.operation_type_speech_recognition_label'),
    'SpeechRecognitionPreview': ui_text('events.operation_type_speech_recognition_preview_label'),
    'SpeechModelLoad': ui_text('events.operation_type_speech_model_load_label'),
    'SpeechModelWarmup': ui_text('events.operation_type_speech_model_warmup_label'),
    'SpeechPunctuation': ui_text('events.operation_type_speech_punctuation_label'),
    'SpeechTextPolish': ui_text('events.operation_type_speech_text_polish_label'),
    'SpeechTextPolishWarmup': ui_text('events.operation_type_speech_text_polish_warmup_label'),
    'ImageRecognition': ui_text('events.operation_type_image_recognition_label'),
    'ImagePromptExpansion': ui_text('events.operation_type_image_prompt_expansion_label'),
    'ImageUpscale': ui_text('events.operation_type_image_upscale_label'),
    'ImageRefinement': ui_text('events.operation_type_image_refinement_label'),
    'ImageControlNet': ui_text('events.operation_type_image_controlnet_label'),
    'AgentModelCall': ui_text('events.operation_type_agent_model_call_label'),
    'AgentHandoff': ui_text('events.operation_type_agent_handoff_label'),
    'SpeechSynthesis': ui_text('events.operation_type_speech_synthesis_label'),
    'VideoModelGeneration': ui_text('events.operation_type_video_model_generation_label'),
    'MusicModelGeneration': ui_text('events.operation_type_music_model_generation_label'),
    'MusicPlanning': ui_text('events.operation_type_music_planning_label'),
    'VideoPromptPlanning': ui_text('events.operation_type_video_prompt_planning_label'),
    'VideoPromptEnhancement': ui_text('events.operation_type_video_prompt_enhancement_label'),
}
TYPES = {**MAIN_TYPES, **TECHNICAL_TYPES}


# The database and schema are created at runtime, without an IDE data-source dependency.
# noinspection SqlNoDataSourceInspection
class EventHistory:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.ready = False
        self.warning = ''
        self.failed_writes = 0

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2)
        try:
            connection.execute('PRAGMA busy_timeout=2000')
            if not self.ready:
                with self.lock:
                    if not self.ready:
                        # The bridge initializes the file before serving requests; workers reuse its persistent WAL mode.
                        if connection.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'wal':
                            connection.execute('PRAGMA journal_mode=WAL')
                        connection.executescript('''
                            CREATE TABLE IF NOT EXISTS events (
                                id TEXT PRIMARY KEY, category TEXT NOT NULL DEFAULT 'generation',
                                type TEXT NOT NULL, started_at TEXT NOT NULL, duration_sec REAL,
                                state TEXT NOT NULL, parent_id TEXT,
                                data TEXT NOT NULL DEFAULT '{}'
                            );
                            CREATE INDEX IF NOT EXISTS events_date ON events(started_at DESC, id DESC);
                            CREATE INDEX IF NOT EXISTS events_type_date ON events(type, started_at DESC, id DESC);
                            CREATE INDEX IF NOT EXISTS events_state_date ON events(state, started_at DESC, id DESC);
                            CREATE INDEX IF NOT EXISTS events_parent ON events(parent_id);
                        ''')
                        self.ready = True
            return connection
        except BaseException:
            connection.close()
            raise

    def prepare(self):
        try:
            self.connect().close()
        except (sqlite3.Error, OSError):
            self.warning = ui_text('events.event_history_prepare_currently_unavailable_check_user_data_folder')

    @staticmethod
    def validate(record):
        if not isinstance(record, dict) or set(record) != {'type', 'id', 'started_at', 'duration_sec'}:
            raise ValueError(ui_text('events.event_history_validation_invalid_data'))
        if not isinstance(record['type'], str) or record['type'] not in TYPES or not isinstance(record['id'], str) or not ID.fullmatch(record['id']):
            raise ValueError(ui_text('events.event_history_validation_invalid_type_identifier'))
        date = record['started_at']
        if not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z', date):
            raise ValueError(ui_text('events.event_history_validation_invalid_date'))
        duration = record['duration_sec']
        if duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
            raise ValueError(ui_text('events.event_history_validation_invalid_duration'))

    def record(self, record, state, parent_id=None):
        """Short transactions work across the bridge, Video and owned inference workers."""
        self.validate(record)
        if state not in STATES or (parent_id is not None and (not isinstance(parent_id, str) or not ID.fullmatch(parent_id))):
            raise ValueError(ui_text('events.event_history_record_invalid_state'))
        connection = self.connect()
        try:
            with connection:
                if state == 'running':
                    connection.execute('INSERT OR IGNORE INTO events(id,type,started_at,duration_sec,state,parent_id) VALUES(?,?,?,?,?,?)',
                                       (record['id'], record['type'], record['started_at'], record['duration_sec'], state, parent_id))
                else:
                    connection.execute('UPDATE events SET duration_sec=?,state=? WHERE id=?', (record['duration_sec'], state, record['id']))
        finally:
            connection.close()

    def safe_record(self, record, state, parent_id=None):
        try:
            self.record(record, state, parent_id)
        except (sqlite3.Error, OSError, ValueError):
            # Recording diagnostics is independent of GPU admission and model completion.
            with self.lock:
                self.failed_writes += 1
                self.warning = ui_text('events.event_history_safe_record_some_events_could_not_saved_check')

    def page(self, query=''):
        options = parse_qs(query.lstrip('?'), keep_blank_values=True)
        kind, scope, state = (options.get(key, [''])[0] for key in ('type', 'scope', 'state'))
        try:
            limit = int(options.get('limit', ['100'])[0])
        except ValueError:
            raise ValueError(ui_text('events.event_history_page_invalid_size')) from None
        before = options.get('before', [''])[0]
        if not 1 <= limit <= 200 or kind and kind not in TYPES or scope not in ('', 'main', 'technical') or state and state not in STATES:
            raise ValueError(ui_text('events.event_history_page_invalid_filter'))
        where, values = [], []
        if kind:
            where.append('type=?'); values.append(kind)
        if state:
            where.append('state=?'); values.append(state)
        if scope:
            names = tuple(MAIN_TYPES if scope == 'main' else TECHNICAL_TYPES)
            where.append('type IN (' + ','.join('?' for _ in names) + ')'); values.extend(names)
        if before:
            if len(before) > 100 or '|' not in before:
                raise ValueError(ui_text('events.event_history_page_invalid'))
            date, ident = before.split('|', 1)
            self.validate(dict(type='Image', id=ident, started_at=date, duration_sec=None))
            where.append('(started_at < ? OR (started_at = ? AND id < ?))'); values.extend((date, date, ident))
        sql = 'SELECT id,type,started_at,duration_sec,state,parent_id,category FROM events'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY started_at DESC,id DESC LIMIT ?'
        connection = self.connect()
        try:
            rows = connection.execute(sql, [*values, limit + 1]).fetchall()
        finally:
            connection.close()
        entries = [dict(generation=dict(type=r[1], id=r[0], started_at=r[2], duration_sec=r[3]), state=r[4], parent_id=r[5],
                        category=r[6], technical=r[1] in TECHNICAL_TYPES) for r in rows[:limit]]
        cursor = entries[-1]['generation']['started_at'] + '|' + entries[-1]['generation']['id'] if len(rows) > limit else None
        return dict(entries=entries, next=cursor, warning=self.warning, failed_writes=self.failed_writes,
                    types=[dict(id=key, label=value, technical=key in TECHNICAL_TYPES) for key, value in TYPES.items()])

    def remove_generation(self, ident):
        """Deleting a generation also removes its centrally recorded technical descendants."""
        if not ID.fullmatch(ident):
            raise ValueError(ui_text('events.event_history_remove_generation_invalid_identifier'))
        connection = self.connect()
        try:
            with connection:
                connection.execute('''WITH RECURSIVE owned(id) AS (
                    SELECT ? UNION SELECT events.id FROM events JOIN owned ON events.parent_id=owned.id
                ) DELETE FROM events WHERE id IN (SELECT id FROM owned)''', (ident,))
        finally:
            connection.close()

    def microphone(self, ident):
        if not isinstance(ident, str) or not ID.fullmatch(ident):
            raise ValueError(ui_text('events.event_history_microphone_invalid_identifier'))
        connection = self.connect()
        try:
            row = connection.execute('SELECT type,started_at,duration_sec FROM events WHERE id=? AND type=?', (ident, 'MicrophoneSession')).fetchone()
        finally:
            connection.close()
        if not row:
            raise ValueError(ui_text('events.event_history_microphone_not_found'))
        return dict(type=row[0], id=ident, started_at=row[1], duration_sec=row[2])
