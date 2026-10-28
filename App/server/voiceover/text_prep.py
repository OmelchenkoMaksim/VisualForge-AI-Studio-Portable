"""Turns pasted text or a book file into chapters and speakable fragments. Standard library only.

A voiceover job is a list of fragments (a few sentences each, at most MAX_FRAGMENT characters) with the pause that follows
every one. Fragments are the unit of synthesis, of resuming after a failure and of later repair, so their identity is a
hash of everything that influences the audio (see `fragment_key`).
"""
from ui_strings import source as ui_text
import hashlib
from html.parser import HTMLParser
import io
import re
import zipfile
from xml.etree import ElementTree

MAX_CHARS = 2_000_000
MAX_FRAGMENT = 300
MAX_ARCHIVE_BYTES = 120 * 1024 * 1024
# Planning estimate only (Russian narration is about 14 characters per second); the real length is measured after synthesis.
CHARS_PER_SECOND = 14.0
PAUSE = {'sentence': 0.25, 'paragraph': 0.6, 'chapter': 1.5}

# A full stop after these is not the end of a sentence. Single letters (г., в., д.) are included on purpose: a long
# sentence is harmless (fragments are length-limited anyway), a sentence cut in the middle is audible.
ABBREVIATIONS = frozenset((
    'т.е', 'т.д', 'т.п', 'т.к', 'т.н', 'и.о', 'см', 'рис', 'тыс', 'руб', 'коп', 'др', 'пр', 'проф', 'акад', 'доц', 'гр',
    'ст', 'стр', 'напр', 'англ', 'нем', 'франц', 'лат', 'им', 'ул', 'пер', 'пл', 'обл', 'гг', 'вв', 'г', 'в', 'д', 'кв',
    'тел', 'млн', 'млрд', 'мин', 'сек', 'ч', 'мр', 'мрс', 'mr', 'mrs', 'dr', 'st', 'vs', 'etc'))
HEADING = re.compile(r'^\s*(?:(?:глава|часть|книга|раздел|chapter|part|book)\s+[\w.\-]+.{0,60}|пролог|эпилог|предисловие|послесловие|prologue|epilogue|[IVXLC]{1,7}\.?)\s*$', re.I)
TERMINATOR = re.compile(r'[.!?…]+["»”)\]]*\s+')


def decode_bytes(raw):
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16')
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('cp1251', errors='replace')  # the common legacy encoding of Russian text files


def _reject_entities(raw):
    # ElementTree expands entity declarations; books never need them, so refuse instead of defending against expansion bombs.
    if b'<!ENTITY' in raw:
        raise ValueError(ui_text('voiceover.text_prep_unsafe_xml_error'))


def _read_member(archive, name):
    info = archive.getinfo(name)
    if info.file_size > MAX_ARCHIVE_BYTES:
        raise ValueError(ui_text('voiceover.text_prep_archive_too_large_error'))
    return archive.read(info)


def _checked_archive(raw):
    archive = zipfile.ZipFile(io.BytesIO(raw))
    if sum(info.file_size for info in archive.infolist()) > MAX_ARCHIVE_BYTES:
        raise ValueError(ui_text('voiceover.text_prep_archive_too_large_error'))
    return archive


def _local(tag):
    return tag.rsplit('}', 1)[-1]


class _XhtmlText(HTMLParser):
    """Paragraphs of an EPUB page; the first heading becomes the chapter title."""
    BLOCKS = {'p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs, self.title, self._buffer, self._heading, self._skip = [], '', [], False, 0

    def _flush(self):
        text = re.sub(r'\s+', ' ', ''.join(self._buffer)).strip()
        self._buffer = []
        if text:
            if self._heading and not self.title:
                self.title = text
            else:
                self.paragraphs.append(text)
        self._heading = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self._skip += 1
        elif tag in self.BLOCKS:
            self._flush()
            self._heading = tag in ('h1', 'h2', 'h3')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCKS:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            self._buffer.append(data)

    def close(self):
        super().close()
        self._flush()


def read_epub(raw):
    archive = _checked_archive(raw)
    container = _read_member(archive, 'META-INF/container.xml')
    _reject_entities(container)
    root = ElementTree.fromstring(container)
    opf_path = next(node.get('full-path') for node in root.iter() if _local(node.tag) == 'rootfile')
    opf_raw = _read_member(archive, opf_path)
    _reject_entities(opf_raw)
    opf = ElementTree.fromstring(opf_raw)
    manifest = {node.get('id'): node.get('href') for node in opf.iter() if _local(node.tag) == 'item'}
    base = opf_path.rsplit('/', 1)[0] + '/' if '/' in opf_path else ''
    title = next((node.text.strip() for node in opf.iter() if _local(node.tag) == 'title' and node.text), '')
    chapters = []
    for node in opf.iter():
        if _local(node.tag) != 'itemref' or manifest.get(node.get('idref')) is None:
            continue
        href = manifest[node.get('idref')].split('#')[0]
        try:
            page = decode_bytes(_read_member(archive, base + href))
        except KeyError:
            continue
        parser = _XhtmlText()
        parser.feed(page)
        parser.close()
        text = '\n\n'.join(parser.paragraphs)
        if len(text) >= 20:
            chapters.append({'title': parser.title, 'text': text})
    return title, chapters


def read_fb2(raw):
    _reject_entities(raw)
    root = ElementTree.fromstring(raw)
    title = next((' '.join(''.join(p.itertext()).strip() for p in node.iter() if _local(p.tag) == 'book-title') for node in root.iter() if _local(node.tag) == 'title-info'), '')
    chapters = []

    def walk(section, inherited):
        own = next((' '.join(' '.join(''.join(p.itertext()).split()) for p in child) for child in section if _local(child.tag) == 'title'), '') or inherited
        children = [child for child in section if _local(child.tag) == 'section']
        if children:
            for child in children:
                walk(child, own)
            return
        paragraphs = [' '.join(''.join(p.itertext()).split()) for p in section if _local(p.tag) in ('p', 'v', 'text-author', 'subtitle')]
        text = '\n\n'.join(p for p in paragraphs if p)
        if text:
            chapters.append({'title': own, 'text': text})

    for body in root:
        if _local(body.tag) == 'body' and body.get('name') != 'notes':
            for section in body:
                if _local(section.tag) == 'section':
                    walk(section, '')
    return title.strip(), chapters


def read_docx(raw):
    archive = _checked_archive(raw)
    document = _read_member(archive, 'word/document.xml')
    _reject_entities(document)
    root = ElementTree.fromstring(document)
    chapters, title, lines = [], '', []

    def close_chapter():
        text = '\n\n'.join(lines).strip()
        if text:
            chapters.append({'title': title, 'text': text})
        lines.clear()

    for paragraph in root.iter():
        if _local(paragraph.tag) != 'p':
            continue
        style = next((node.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val', '') for node in paragraph.iter() if _local(node.tag) == 'pStyle'), '')
        text = ''.join(node.text or '' if _local(node.tag) == 't' else ' ' if _local(node.tag) in ('tab', 'br') else '' for node in paragraph.iter()).strip()
        if not text:
            continue
        if style.lower().startswith(('heading', 'заголовок', 'title')):
            close_chapter()
            title = text
        else:
            lines.append(text)
    close_chapter()
    return '', chapters


def split_chapters(text, title=''):
    """Plain text: chapters start at lines such as «Глава 3», «Пролог» or a lone Roman numeral; otherwise one chapter."""
    chapters, heading, lines = [], title, []

    def close():
        body = '\n'.join(lines).strip()
        if body:
            chapters.append({'title': heading, 'text': body})
        lines.clear()

    for line in text.split('\n'):
        if len(line) <= 80 and HEADING.match(line):
            close()
            heading = line.strip()
        else:
            lines.append(line)
    close()
    return chapters


def clean(text, replacements=()):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    for old, new in ((' ', ' '), ('­', ''), ('​', ''), ('﻿', '')):
        text = text.replace(old, new)
    text = re.sub(r'\[\d{1,3}\]', '', text)  # footnote markers such as [12] are not read aloud
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    for old, new in replacements:
        if old:
            text = text.replace(old, new)  # the user's pronunciation dictionary: plain, case-sensitive
    return text.strip()


def parse_input(name, raw, text, title=''):
    """Returns {'title', 'chapters'} from an uploaded file or pasted text. `name` only selects the reader."""
    if raw is not None:
        suffix = (name or '').lower().rsplit('.', 1)[-1] if '.' in (name or '') else ''
        try:
            if suffix == 'epub':
                book_title, chapters = read_epub(raw)
            elif suffix == 'fb2':
                book_title, chapters = read_fb2(raw)
            elif suffix == 'docx':
                book_title, chapters = read_docx(raw)
            elif suffix in ('txt', 'text', 'md', ''):
                book_title, chapters = '', None
                text = decode_bytes(raw)
            else:
                raise ValueError(ui_text('voiceover.text_prep_unsupported_file_type_error'))
        except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, StopIteration) as error:
            raise ValueError(ui_text('voiceover.text_prep_file_unreadable_error') % str(error)[:200]) from None
        title = title or book_title or (name or '').rsplit('.', 1)[0]
    else:
        chapters = None
    if chapters is None:
        chapters = split_chapters(clean(text or ''), title)
    chapters = [{'title': item['title'], 'text': clean(item['text'])} for item in chapters]
    chapters = [item for item in chapters if re.search(r'\w', item['text'])]
    total = sum(len(item['text']) for item in chapters)
    if not chapters:
        raise ValueError(ui_text('voiceover.text_prep_text_empty_error'))
    if total > MAX_CHARS:
        raise ValueError(ui_text('voiceover.text_prep_text_too_long_error') % (total, MAX_CHARS))
    for index, item in enumerate(chapters, 1):
        item['title'] = item['title'] or ui_text('voiceover.text_prep_untitled_chapter_title') % index
    # Without a title the file is named after the opening words, not after «Глава 1», which every book has.
    opening = ' '.join(re.findall(r'\w+', chapters[0]['text'])[:6])
    return {'title': title or opening, 'chapters': chapters}


def paragraphs(text):
    # Hard-wrapped text files have no blank lines: every line is then a paragraph, otherwise single breaks are just wrapping.
    if '\n\n' not in text:
        return [line.strip() for line in text.split('\n') if line.strip()]
    return [' '.join(block.split()) for block in re.split(r'\n\s*\n', text) if block.strip()]


def sentences(paragraph):
    result, start = [], 0
    for match in TERMINATOR.finditer(paragraph):
        if paragraph[match.end():match.end() + 1].islower():
            continue  # the text goes on in lower case («в 1990 г. он вернулся»): not the end of a sentence
        before = paragraph[start:match.start()].split()
        word = before[-1].lower().strip('"«“(') if before else ''
        if match.group().strip() == '.' and (word in ABBREVIATIONS or re.fullmatch(r'[a-zа-яё]', word)):
            continue  # an abbreviation or an initial such as «А. С. Пушкин»
        result.append(paragraph[start:match.end()].strip())
        start = match.end()
    rest = paragraph[start:].strip()
    if rest:
        result.append(rest)
    return result


def _break(sentence, limit):
    """Cuts one long sentence into pieces of at most `limit` characters at the most natural place."""
    pieces = []
    while len(sentence) > limit:
        window = sentence[:limit + 1]
        cut = -1
        for separator in ('; ', ': ', ', ', ' — ', ' '):
            index = window.rfind(separator)
            if index >= limit * 0.4:
                cut = index + len(separator.rstrip()) if separator != ' ' else index
                break
        if cut <= 0:
            cut = limit
        pieces.append(sentence[:cut].strip())
        sentence = sentence[cut:].strip()
    if sentence:
        pieces.append(sentence)
    return pieces


def fragments(chapters, limit=MAX_FRAGMENT):
    """Fragments in reading order: {'chapter', 'text', 'pause'}; `pause` names the silence that follows the fragment."""
    result = []
    for chapter_index, chapter in enumerate(chapters):
        mark = len(result)
        for paragraph in paragraphs(chapter['text']):
            start = len(result)
            current = ''
            for sentence in sentences(paragraph):
                for piece in _break(sentence, limit):
                    if current and len(current) + 1 + len(piece) > limit:
                        result.append({'chapter': chapter_index, 'text': current, 'pause': 'sentence'})
                        current = piece
                    else:
                        current = (current + ' ' + piece).strip()
            if current:
                result.append({'chapter': chapter_index, 'text': current, 'pause': 'sentence'})
            if len(result) > start:
                result[-1]['pause'] = 'paragraph'
        if len(result) > mark:
            result[-1]['pause'] = 'chapter'
    # A piece without a letter or digit (a lone dash, a row of dots) cannot be spoken; its pause moves to the previous piece.
    speakable = []
    for item in result:
        if re.search(r'\w', item['text']):
            speakable.append(item)
        elif speakable:
            speakable[-1]['pause'] = item['pause']
    return speakable


def fragment_key(text, voice_hash, language, seed, model):
    """Identity of one synthesized fragment: the same inputs always map to the same cached audio file."""
    payload = '\x1f'.join((model, language, str(seed), voice_hash, text))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]


def summary(chapters, limit=MAX_FRAGMENT):
    rows = []
    for index, chapter in enumerate(chapters):
        count = len(fragments([chapter], limit))
        rows.append({'title': chapter['title'], 'chars': len(chapter['text']), 'fragments': count,
                     'estimate_sec': round(len(chapter['text']) / CHARS_PER_SECOND)})
    return {'chapters': rows, 'chars': sum(row['chars'] for row in rows), 'fragments': sum(row['fragments'] for row in rows),
            'estimate_sec': sum(row['estimate_sec'] for row in rows)}
