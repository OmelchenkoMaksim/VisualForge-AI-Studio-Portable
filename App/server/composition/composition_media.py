"""Persistent optional composition materials, with bounded intake and opaque identifiers."""
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

from PIL import Image, ImageFilter, ImageOps

from image_convert import convert
from ui_strings import source as ui_text
from .composition_plan import GROUPS

IDENT = re.compile(r'[0-9a-f]{32}\Z')
MAX_BYTES = 100 * 1024 * 1024
TOTAL_BYTES = 200 * 1024 * 1024
CANVAS = dict(square=(2048, 2048), landscape=(2048, 1365), portrait=(1365, 2048))
FILL_TOLERANCE = 0.06
PAD_NOTE = ('The first picture sits on a larger canvas with blurred margins: fill the whole frame by continuing the scene naturally into the margins, '
            'and keep the people and objects of the picture exactly as they are.')
TRANSPARENT_NOTE = ('Complete any empty canvas padding coherently with the requested scene, preserving the actual source content. '
                    'Keep genuinely transparent space only when the requested output needs transparency.')


def atomic_json(path, value):
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    pending.replace(path)


class Materials:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def folder(self, ident):
        if not isinstance(ident, str) or not IDENT.fullmatch(ident):
            raise ValueError(ui_text('composition.panel_invalid_draft_text'))
        folder = self.root / ident
        if not (folder / 'materials.json').is_file():
            raise ValueError(ui_text('composition.panel_invalid_draft_text'))
        return folder

    def draft(self, ident=None):
        with self.lock:
            if not ident:
                ident = uuid.uuid4().hex
                folder = self.root / ident
                folder.mkdir()
                atomic_json(folder / 'materials.json', [])
            return dict(id=ident, items=json.loads((self.folder(ident) / 'materials.json').read_text(encoding='utf-8')))

    def add(self, ident, raw, group, name):
        limits = {row['id']: row['limit'] for row in GROUPS}
        if group not in limits or not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
            raise ValueError(ui_text('composition.panel_invalid_attachment_text'))
        converted = convert(raw, 'image')
        with self.lock:
            draft = self.draft(ident)
            if sum(item['group'] == group for item in draft['items']) >= limits[group] or sum(item['bytes'] for item in draft['items']) + len(converted.data) > TOTAL_BYTES:
                raise ValueError(ui_text('composition.panel_material_limit_text'))
            item = dict(id=uuid.uuid4().hex, group=group, label=str(name or '')[:120], bytes=len(converted.data), mime=converted.mime)
            (self.folder(ident) / (item['id'] + '.image')).write_bytes(converted.data)
            draft['items'].append(item)
            atomic_json(self.folder(ident) / 'materials.json', draft['items'])
            return draft

    def remove(self, ident, material):
        with self.lock:
            draft = self.draft(ident)
            self.path(ident, material)
            draft['items'] = [item for item in draft['items'] if item['id'] != material]
            atomic_json(self.folder(ident) / 'materials.json', draft['items'])
            (self.folder(ident) / (material + '.image')).unlink(missing_ok=True)
            return draft

    def label(self, ident, material, text):
        if not isinstance(text, str) or len(text) > 120:
            raise ValueError(ui_text('composition.panel_invalid_attachment_text'))
        with self.lock:
            draft = self.draft(ident)
            self.path(ident, material)
            for item in draft['items']:
                if item['id'] == material:
                    item['label'] = text.strip()
            atomic_json(self.folder(ident) / 'materials.json', draft['items'])
            return draft

    def path(self, ident, material):
        items = json.loads((self.folder(ident) / 'materials.json').read_text(encoding='utf-8'))      # `draft()` would make a new draft for an empty id
        if not any(item['id'] == material for item in items):
            raise ValueError(ui_text('composition.panel_invalid_attachment_text'))
        return self.folder(ident) / (material + '.image')


def prune_drafts(root, days=7):
    """Drop the material folders nobody has touched for `days` days (a composition that was started touches its draft, see `CompositionManager.start`)."""
    cutoff = time.time() - days * 86400
    for folder in Path(root).glob('*'):
        if folder.is_dir() and IDENT.fullmatch(folder.name):
            index = folder / 'materials.json'
            if max(folder.stat().st_mtime, index.stat().st_mtime if index.exists() else 0) < cutoff:
                shutil.rmtree(folder, ignore_errors=True)


def prune_runs(root, keep=30):
    """Keep the newest `keep` run folders (the panel lists 30; every finished image is also in the ordinary Image output folder and history)."""
    folders = sorted((path.parent for path in Path(root).glob('*/run.json')), key=lambda folder: (folder / 'run.json').stat().st_mtime, reverse=True)
    for folder in folders[keep:]:
        shutil.rmtree(folder, ignore_errors=True)


def prepare(raws, canvas):
    """The pictures of one step as the Image route takes them -> (PNG bytes list, a sentence for the prompt or '').

    Edit models draw on the shape of the FIRST picture and normalise it to about one megapixel, so the first picture is brought to the canvas: stretched to it when its shape is within
    `FILL_TOLERANCE` (a sliver is cropped), else shrunk or enlarged to fit and put over a blurred copy of itself (a picture with transparency over a transparent frame, so that its alpha
    survives). The first version only ever shrank a picture onto a flat grey frame: a 1152x768 photo was left with 69 % grey around it, and the model had to invent most of the frame.
    A blurred margin is what Klein continued into a real scene in the Director's keyframe tests (2026-11-01); flat grey was never tried."""
    target = CANVAS[canvas]
    images, note = [], ''
    for index, raw in enumerate(raws):
        with Image.open(BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert('RGBA')
        if index:
            image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        elif abs(image.width / image.height / (target[0] / target[1]) - 1) <= FILL_TOLERANCE:
            image = ImageOps.fit(image, target, Image.Resampling.LANCZOS)
        else:
            fitted = ImageOps.contain(image, target, Image.Resampling.LANCZOS)
            if image.getchannel('A').getextrema()[0] == 255:
                frame, note = ImageOps.fit(image.convert('RGB'), target, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(40)).convert('RGBA'), PAD_NOTE
            else:
                frame, note = Image.new('RGBA', target, (127, 127, 127, 0)), TRANSPARENT_NOTE
            frame.alpha_composite(fitted, ((target[0] - fitted.width) // 2, (target[1] - fitted.height) // 2))
            image = frame
        images.append(image)
    for _ in range(8):
        result = []
        for image in images:
            output = BytesIO()
            image.save(output, 'PNG')
            result.append(output.getvalue())
        if sum(map(len, result)) <= 95 * 1024 * 1024:
            return result, note
        images = [image.resize((max(1, int(image.width * .85)), max(1, int(image.height * .85))), Image.Resampling.LANCZOS) for image in images]
    raise ValueError(ui_text('composition.panel_invalid_attachment_text'))


def prepare_inputs(raws, canvas):
    return prepare(raws, canvas)[0]
