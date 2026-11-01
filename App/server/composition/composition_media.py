"""Persistent optional composition materials, with bounded intake and opaque identifiers."""
from io import BytesIO
import json
from pathlib import Path
import re
import threading
import uuid

from PIL import Image, ImageOps

from image_convert import convert
from ui_strings import source as ui_text
from .composition_plan import GROUPS

IDENT = re.compile(r'[0-9a-f]{32}\Z')
MAX_BYTES = 100 * 1024 * 1024
TOTAL_BYTES = 200 * 1024 * 1024


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
        if not any(item['id'] == material for item in self.draft(ident)['items']):
            raise ValueError(ui_text('composition.panel_invalid_attachment_text'))
        return self.folder(ident) / (material + '.image')


def prepare_inputs(raws, canvas):
    """Preserve full first-image content on the requested aspect; respect the route's total input-byte budget."""
    target = dict(square=(2048, 2048), landscape=(2048, 1365), portrait=(1365, 2048))[canvas]
    images = []
    for index, raw in enumerate(raws):
        with Image.open(BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert('RGBA')
            image.thumbnail(target if index == 0 else (2048, 2048), Image.Resampling.LANCZOS)
            if index == 0:
                # Like Director keyframes, edit output follows the first source aspect; padding prevents a later crop of a portrait hero.
                frame = Image.new('RGBA', target, (127, 127, 127, 255 if image.getchannel('A').getextrema()[0] == 255 else 0))
                frame.alpha_composite(image, ((target[0] - image.width) // 2, (target[1] - image.height) // 2))
                image = frame
            images.append(image)
    for _ in range(8):
        result = []
        for image in images:
            output = BytesIO()
            image.save(output, 'PNG')
            result.append(output.getvalue())
        if sum(map(len, result)) <= 95 * 1024 * 1024:
            return result
        images = [image.resize((max(1, int(image.width * .85)), max(1, int(image.height * .85))), Image.Resampling.LANCZOS) for image in images]
    raise ValueError(ui_text('composition.panel_invalid_attachment_text'))
