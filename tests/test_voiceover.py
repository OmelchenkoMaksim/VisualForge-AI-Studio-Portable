"""CPU tests for Voiceover: text preparation, assembly with the studio's ffmpeg and the job manager with a fake worker."""
import base64
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import wave
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App/server'))
from voiceover import audio_assembly as assemble, text_prep
from voiceover.voiceover_registry import ENGINE, validate

FFMPEG = ROOT / 'Runtime/Video/ffmpeg/bin/ffmpeg.exe'
FFPROBE = ROOT / 'Runtime/Video/ffmpeg/bin/ffprobe.exe'
BOOK = 'Глава 1\n\nПервый абзац. Он состоит из двух предложений.\n\nВторой абзац главы.\n\nГлава 2\n\nНачало второй главы, в 1990 г. он вернулся домой. А. С. Пушкин родился давно.'


class TextPreparation(unittest.TestCase):
    def test_plain_text_is_split_into_chapters(self):
        book = text_prep.parse_input(None, None, BOOK, 'Тест')
        self.assertEqual([item['title'] for item in book['chapters']], ['Глава 1', 'Глава 2'])
        self.assertIn('Второй абзац', book['chapters'][0]['text'])

    def test_text_without_headings_is_one_chapter_named_after_the_title(self):
        book = text_prep.parse_input(None, None, 'Просто текст без заголовков.', 'Моя книга')
        self.assertEqual(len(book['chapters']), 1)
        self.assertEqual(book['chapters'][0]['title'], 'Моя книга')

    def test_missing_title_uses_the_opening_words_not_the_first_chapter_heading(self):
        self.assertEqual(text_prep.parse_input(None, None, BOOK, '')['title'], 'Первый абзац Он состоит из двух')

    def test_abbreviations_and_initials_do_not_end_a_sentence(self):
        parts = text_prep.sentences('В 1990 г. он вернулся домой. А. С. Пушкин родился давно. Это т.е. правда.')
        self.assertEqual(parts, ['В 1990 г. он вернулся домой.', 'А. С. Пушкин родился давно.', 'Это т.е. правда.'])

    def test_fragments_respect_the_limit_and_mark_pauses(self):
        long_sentence = ', '.join('слово номер %d' % n for n in range(80)) + '.'
        chapters = [{'title': 'A', 'text': long_sentence + '\n\nКороткий абзац.'}, {'title': 'B', 'text': 'Вторая глава.'}]
        result = text_prep.fragments(chapters, limit=120)
        self.assertTrue(all(len(item['text']) <= 120 for item in result))
        self.assertGreater(len(result), 5)
        self.assertEqual(result[-1]['pause'], 'chapter')
        self.assertEqual([item['pause'] for item in result if item['chapter'] == 0][-1], 'chapter')
        self.assertEqual(sum(1 for item in result if item['pause'] == 'paragraph'), 1)
        self.assertEqual(' '.join(item['text'] for item in result if item['chapter'] == 1), 'Вторая глава.')

    def test_text_is_never_lost_or_reordered(self):
        sentences = ['Предложение номер %d есть здесь.' % n for n in range(60)]
        chapters = [{'title': 'A', 'text': ' '.join(sentences)}]
        joined = ' '.join(item['text'] for item in text_prep.fragments(chapters, limit=150))
        self.assertEqual(joined, ' '.join(sentences))

    def test_unspeakable_pieces_are_dropped_and_their_pause_moves_back(self):
        result = text_prep.fragments([{'title': 'A', 'text': 'Слова здесь.\n\n—'}])
        self.assertEqual([item['text'] for item in result], ['Слова здесь.'])
        self.assertEqual(result[-1]['pause'], 'chapter')

    def test_hard_wrapped_text_keeps_every_line_as_a_paragraph(self):
        self.assertEqual(text_prep.paragraphs('первая строка\nвторая строка'), ['первая строка', 'вторая строка'])
        self.assertEqual(text_prep.paragraphs('одна\nабзаца\n\nдругой'), ['одна абзаца', 'другой'])

    def test_replacements_footnotes_and_invisible_characters(self):
        cleaned = text_prep.clean('Ёлкин пришёл[12] домой­.', [['Ёлкин', 'Йолкин']])
        self.assertEqual(cleaned, 'Йолкин пришёл домой.')

    def test_fragment_key_depends_on_every_input(self):
        base = text_prep.fragment_key('текст', 'voice', 'Russian', 1, 'model')
        self.assertEqual(base, text_prep.fragment_key('текст', 'voice', 'Russian', 1, 'model'))
        for changed in (('другой', 'voice', 'Russian', 1, 'model'), ('текст', 'other', 'Russian', 1, 'model'), ('текст', 'voice', 'English', 1, 'model'),
                        ('текст', 'voice', 'Russian', 2, 'model'), ('текст', 'voice', 'Russian', 1, 'newer')):
            self.assertNotEqual(base, text_prep.fragment_key(*changed))

    def test_limits(self):
        with self.assertRaises(ValueError):
            text_prep.parse_input(None, None, '   ', '')
        with mock.patch.object(text_prep, 'MAX_CHARS', 10):
            with self.assertRaises(ValueError):
                text_prep.parse_input(None, None, 'очень длинный текст для проверки', '')

    def test_legacy_encoding_is_decoded(self):
        self.assertEqual(text_prep.decode_bytes('Привет'.encode('cp1251')), 'Привет')
        self.assertEqual(text_prep.decode_bytes('Привет'.encode('utf-8-sig')), 'Привет')

    def test_epub(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
            archive.writestr('OEBPS/content.opf', '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Книга</dc:title></metadata>'
                             '<manifest><item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
            archive.writestr('OEBPS/a.xhtml', '<html><body><h1>Первая</h1><p>Текст первой главы достаточно длинный.</p><script>x()</script></body></html>')
            archive.writestr('OEBPS/b.xhtml', '<html><body><h2>Вторая</h2><p>Текст второй главы тоже длинный.</p></body></html>')
        book = text_prep.parse_input('book.epub', buffer.getvalue(), None)
        self.assertEqual(book['title'], 'Книга')
        self.assertEqual([item['title'] for item in book['chapters']], ['Первая', 'Вторая'])
        self.assertNotIn('x()', book['chapters'][0]['text'])

    def test_fb2_skips_notes_and_nested_sections_become_chapters(self):
        xml = ('<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info><book-title>Роман</book-title></title-info></description>'
               '<body><section><title><p>Часть первая</p></title><section><title><p>Глава 1</p></title><p>Текст главы один.</p></section>'
               '<section><title><p>Глава 2</p></title><p>Текст главы два.</p></section></section></body><body name="notes"><section><p>Сноска</p></section></body></FictionBook>')
        book = text_prep.parse_input('book.fb2', xml.encode('utf-8'), None)
        self.assertEqual(book['title'], 'Роман')
        self.assertEqual([item['title'] for item in book['chapters']], ['Глава 1', 'Глава 2'])
        self.assertNotIn('Сноска', ' '.join(item['text'] for item in book['chapters']))

    def test_docx_headings_start_chapters(self):
        ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        body = ''.join('<w:p>%s<w:r><w:t>%s</w:t></w:r></w:p>' % ('<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if style else '', text)
                       for style, text in ((True, 'Первая'), (False, 'Текст первой главы.'), (True, 'Вторая'), (False, 'Текст второй главы.')))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document %s><w:body>%s</w:body></w:document>' % (ns, body))
        book = text_prep.parse_input('book.docx', buffer.getvalue(), None, 'Док')
        self.assertEqual([item['title'] for item in book['chapters']], ['Первая', 'Вторая'])

    def test_xml_entity_declarations_are_refused(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><FictionBook><body><section><p>&a;</p></section></body></FictionBook>'
        with self.assertRaises(ValueError):
            text_prep.parse_input('book.fb2', bomb, None)

    def test_unsupported_and_corrupt_files(self):
        with self.assertRaises(ValueError):
            text_prep.parse_input('book.exe', b'MZ', None)
        with self.assertRaises(ValueError):
            text_prep.parse_input('book.epub', b'not a zip', None)


class Validation(unittest.TestCase):
    GOOD = dict(book_id='a' * 32, voice_id='b' * 32)

    def test_engine_choice(self):
        self.assertEqual(validate(dict(self.GOOD, engine='voxcpm2'))['engine'], 'voxcpm2')
        with self.assertRaises(ValueError):
            validate(dict(self.GOOD, engine='nope'))

    def test_defaults_and_seed(self):
        request = validate(dict(self.GOOD))
        self.assertEqual((request['language'], request['format'], request['engine']), ('Russian', 'm4b', ENGINE['id']))
        self.assertTrue(0 <= request['seed'] < 2 ** 32)
        self.assertEqual(validate(dict(self.GOOD, seed='42'))['seed'], 42)

    def test_rejects_bad_input(self):
        for bad in (dict(self.GOOD, book_id='x'), dict(self.GOOD, voice_id=None), dict(self.GOOD, language='Klingon'), dict(self.GOOD, format='ogg'),
                    dict(self.GOOD, seed='-1'), dict(self.GOOD, seed='abc'), dict(self.GOOD, replacements='x'), dict(self.GOOD, replacements=[['a']]), None):
            with self.assertRaises(ValueError):
                validate(bad)

    def test_dictionary_pairs_without_a_source_word_are_dropped(self):
        self.assertEqual(validate(dict(self.GOOD, replacements=[['', 'x'], ['a', 'b']]))['replacements'], [['a', 'b']])


class Normalization(unittest.TestCase):
    def say(self, text):
        from voiceover.speech_normalization import normalize
        return normalize(text)

    def test_cardinals(self):
        from voiceover.speech_normalization import cardinal
        for number, words in ((0, 'ноль'), (5, 'пять'), (11, 'одиннадцать'), (21, 'двадцать один'), (100, 'сто'), (112, 'сто двенадцать'),
                              (1000, 'тысяча'), (1998, 'тысяча девятьсот девяносто восемь'), (21000, 'двадцать одна тысяча'),
                              (2000000, 'два миллиона'), (1000001, 'один миллион один'), (5000, 'пять тысяч')):
            self.assertEqual(cardinal(number), words)
        self.assertEqual((cardinal(2, 'f'), cardinal(1, 'n'), cardinal(21, 'fa')), ('две', 'одно', 'двадцать одну'))

    def test_ordinals_in_every_case(self):
        from voiceover.speech_normalization import ordinal
        self.assertEqual(ordinal(1998), 'тысяча девятьсот девяносто восьмой')
        self.assertEqual(ordinal(1998, 'prep'), 'тысяча девятьсот девяносто восьмом')
        self.assertEqual(ordinal(2005, 'gen'), 'две тысячи пятого')
        self.assertEqual((ordinal(2000), ordinal(1900), ordinal(40), ordinal(200, 'gen')), ('двухтысячный', 'тысяча девятисотый', 'сороковой', 'двухсотого'))
        self.assertEqual((ordinal(3, 'gen'), ordinal(3, 'prep', 'f'), ordinal(2, 'nom', 'f'), ordinal(2, 'ins', 'm', True)), ('третьего', 'третьей', 'вторая', 'вторыми'))
        self.assertIsNone(ordinal(0))
        self.assertIsNone(ordinal(12000))

    def test_plural_forms(self):
        from voiceover.speech_normalization import plural
        self.assertEqual([plural(n, 'рубль', 'рубля', 'рублей') for n in (1, 2, 5, 11, 12, 21, 22, 25, 101, 111)],
                         ['рубль', 'рубля', 'рублей', 'рублей', 'рублей', 'рубль', 'рубля', 'рублей', 'рубль', 'рублей'])

    def test_years_follow_the_noun_and_the_preposition(self):
        self.assertEqual(self.say('Он родился в 1998 году.'), 'Он родился в тысяча девятьсот девяносто восьмом году.')
        self.assertEqual(self.say('С 1998 года.'), 'С тысяча девятьсот девяносто восьмого года.')
        self.assertEqual(self.say('К 2000 году.'), 'К двухтысячному году.')
        self.assertEqual(self.say('В 1990 г. и до 1991 г.'), 'В тысяча девятьсот девяностом году и до тысяча девятьсот девяносто первого года.')
        self.assertEqual(self.say('1900 год'), 'тысяча девятисотый год')
        self.assertEqual(self.say('Цена 4000 г.'), 'Цена четыре тысячи г.', 'not a plausible year: only the number is spoken')

    def test_the_full_stop_of_a_closing_abbreviation_is_kept(self):
        self.assertEqual(self.say('Были книги и т.д. Потом всё стихло.'), 'Были книги и так далее. Потом всё стихло.')
        self.assertEqual(self.say('Это было в 1991 г.\n\nНа следующий день'), 'Это было в тысяча девятьсот девяносто первом году.\n\nНа следующий день')
        self.assertEqual(self.say('Мы жили в XIX в. и ушли.'), 'Мы жили в девятнадцатом веке и ушли.')
        self.assertEqual(self.say('То есть: т.е. Анна пришла.'), 'То есть: то есть Анна пришла.', 'a dot inside a sentence is not a stop')

    def test_centuries(self):
        self.assertEqual(self.say('В XIX веке и в XX в. всё менялось.'), 'В девятнадцатом веке и в двадцатом веке всё менялось.')

    def test_ordinal_suffixes(self):
        self.assertEqual(self.say('5-го числа, в 3-м классе, на 2-й день, 1-я часть, в 21-м веке'),
                         'пятого числа, в третьем классе, на второй день, первая часть, в двадцать первом веке')

    def test_percent_units_decimals_time_minus(self):
        self.assertEqual(self.say('50%, 21 руб., 3 км, 5 кг, 12 тыс. человек, 3 млн рублей'),
                         'пятьдесят процентов, двадцать один рубль, три километра, пять килограммов, двенадцать тысяч человек, три миллиона рублей')
        self.assertEqual(self.say('3,5 кг и 3,5% и 0,05'), 'три целых пять десятых килограмма и три целых пять десятых процента и ноль целых пять сотых')
        self.assertEqual(self.say('в 14:30, в 9:05, в 18:00'), 'в четырнадцать тридцать, в девять ноль пять, в восемнадцать ноль ноль')
        self.assertEqual(self.say('-5 °C'), 'минус пять градусов Цельсия')

    def test_gender_of_one_and_two_follows_the_next_word(self):
        self.assertEqual(self.say('1 минуту, 1 минута, 1 окно, 1 день, 2 минуты, 2 дома, 2 окна, 22 дня, 21 неделя'),
                         'одну минуту, одна минута, одно окно, один день, две минуты, два дома, два окна, двадцать два дня, двадцать одна неделя')
        self.assertEqual(self.say('11 часов, 12 минут'), 'одиннадцать часов, двенадцать минут')

    def test_abbreviations(self):
        self.assertEqual(self.say('Т.е. всё ясно, т.к. дождь, в т.ч. на улице, и т.д. и т.п.'),
                         'То есть всё ясно, так как дождь, в том числе на улице, и так далее и тому подобное.')
        self.assertEqual(self.say('Пришли Анна, Пётр и др. Были книги и пр.'), 'Пришли Анна, Пётр и другие. Были книги и прочее.')
        self.assertEqual(self.say('а также др. мелочи'), 'а также другие мелочи')

    def test_page_figure_and_chapter_references_take_their_case(self):
        self.assertEqual(self.say('См. рис. 5 на стр. 17.'), 'Смотри рисунок пять на странице семнадцать.')
        self.assertEqual(self.say('См. гл. 3 и табл. 2. Живу на ул. Ленина.'), 'Смотри главу три и таблица два. Живу на улице Ленина.')
        self.assertEqual(self.say('Номер №5.'), 'Номер номер пять.')

    def test_centimetres_with_a_number_are_not_a_reference(self):
        self.assertEqual(self.say('Длина 5 см, потом см. выше.'), 'Длина пять сантиметров, потом смотри выше.')

    def test_text_without_numbers_or_abbreviations_is_untouched(self):
        plain = 'Утром Анна открыла окно. Это была обычная фраза — с тире, запятыми и «кавычками».'
        self.assertEqual(self.say(plain), plain)

    def test_tokens_attached_to_letters_and_leading_zeros(self):
        self.assertEqual(self.say('mp3, A4, COVID-19 и 007'), 'mp3, A4, COVID-девятнадцать и ноль ноль семь')

    def test_the_worst_real_sentence(self):
        self.assertEqual(self.say('доски по ГОСТу 8486-86, олифа и т.д. и т.п., см. рис. 5 на стр. 17, а также др. необходимые мелочи.'),
                         'доски по ГОСТу восемь тысяч четыреста восемьдесят шесть-восемьдесят шесть, олифа и так далее и тому подобное, смотри рисунок пять на странице семнадцать, '
                         'а также другие необходимые мелочи.')

    def test_changes_lists_only_rewritten_sentences(self):
        from voiceover.speech_normalization import changes
        places, examples = changes('Первое предложение обычное. Во втором 5 яблок. В третьем нет ничего.\n\nА в 1998 году было иначе.')
        self.assertEqual(places, 2)
        self.assertEqual([item['before'] for item in examples], ['Во втором 5 яблок.', 'А в 1998 году было иначе.'])
        self.assertEqual(examples[0]['after'], 'Во втором пять яблок.')

    def test_recognition_writing_abbreviations_does_not_count_as_errors(self):
        from voiceover.voice_comparison import compare
        spoken = 'олифа и так далее и тому подобное, смотри рисунок пять на странице семнадцать'
        heard = 'олифа и т.д. и т.п., смотри рисунок 5 на странице 17'
        self.assertGreater(compare(spoken, heard)[0], 0, 'without expansion the abbreviations look like wrong words')
        self.assertEqual(compare(spoken, heard, expand=True), (0, 11))

    def test_number_words_are_known_to_the_comparison(self):
        from voiceover.voice_comparison import compare
        self.assertEqual(compare('в тысяча девятьсот девяносто восьмом году он ушёл', 'в 1998 году он ушел'), (0, 4))
        self.assertEqual(compare('в тысяча девятьсот девяносто восьмом году он ушёл', 'в 1998 году он ушли'), (1, 4))


class Comparison(unittest.TestCase):
    def test_case_punctuation_and_yo_are_ignored(self):
        from voiceover.voice_comparison import compare
        self.assertEqual(compare('Пришёл ЕЁ брат, и всё.', 'пришел её брат и все'), (0, 5))

    def test_wrong_and_missing_words_are_counted(self):
        from voiceover.voice_comparison import compare
        self.assertEqual(compare('он купил хлеб и молоко', 'он купил хлеб молоко'), (1, 5))
        self.assertEqual(compare('он купил хлеб и молоко', 'она купила хлеб и молоко'), (2, 5))

    def test_digits_are_not_compared_so_a_number_never_causes_an_error(self):
        from voiceover.voice_comparison import compare
        self.assertEqual(compare('по ГОСТу 8486-86 на стр. 17', 'по ГОСТу 804 на стр. 7'), (0, 4))
        self.assertEqual(compare('в 1998 году', 'в 1998 году'), (0, 2))

    def test_a_real_misreading_next_to_numbers_is_still_counted(self):
        from voiceover.voice_comparison import compare
        # The real case that exposed an earlier, too forgiving rule: «стр. 17» came out as «стрер, 7 си цить».
        wrong, words = compare('материалы: доски по ГОСТу 8486-86, см. рис. 5 на стр. 17, а также др. мелочи',
                               'материалы доски по ГОСТу 804 с 86 см рис 5 на стрер 7 си цить а также дрер мелочи')
        self.assertEqual((wrong, words), (5, 12))

    def test_the_threshold_and_silence(self):
        from voiceover.voice_comparison import is_suspicious
        self.assertFalse(is_suspicious(1, 10, 'текст'))
        self.assertTrue(is_suspicious(2, 10, 'текст'))
        self.assertFalse(is_suspicious(3, 26, 'текст'), 'a few small slips in a long fragment are normal')
        self.assertTrue(is_suspicious(4, 40, 'текст'), 'four wrong words are reported however long the fragment is')
        self.assertFalse(is_suspicious(1, 4, 'текст'), 'a short fragment tolerates one wrong word')
        self.assertTrue(is_suspicious(2, 4, 'текст'))
        self.assertTrue(is_suspicious(0, 8, '   '), 'nothing heard at all')
        self.assertFalse(is_suspicious(0, 0, ''))


class Pronunciation(unittest.TestCase):
    STEM = {'match': 'подар', 'kind': 'stem', 'replace': 'падар', 'engines': ['qwen3-tts-12hz-0.6b-base']}

    def test_stem_rule_keeps_the_ending_and_the_capital_letter(self):
        from voiceover.pronunciation_rules import apply_rules
        self.assertEqual(apply_rules('Подарок, подарки и ПОДАРКОМ.', [self.STEM]), 'Падарок, падарки и ПАДАРКОМ.')

    def test_word_rule_matches_whole_words_only(self):
        from voiceover.pronunciation_rules import apply_rules
        rule = {'match': 'мука', 'replace': 'мука', 'kind': 'word'}
        self.assertEqual(apply_rules('мука и мукаму', [dict(rule, replace='мука́')]), 'мука́ и мукаму')

    def test_keep_case_false_writes_the_replacement_as_given(self):
        from voiceover.pronunciation_rules import apply_rules
        rule = {'match': 'подарок', 'replace': 'подаррок', 'keep_case': False}
        self.assertEqual(apply_rules('Подарок и подарок, но не подарки', [rule]), 'подаррок и подаррок, но не подарки')

    def test_the_shipped_rule_for_podarok_is_for_qwen_only(self):
        from voiceover.pronunciation_rules import apply_rules, for_engine, load_rules
        rules = load_rules()
        self.assertEqual(apply_rules('Подарок лежал на столе.', for_engine(rules, 'qwen3-tts-12hz-0.6b-base')), 'подаррок лежал на столе.')
        self.assertEqual(apply_rules('Подарок лежал на столе.', for_engine(rules, 'voxcpm2')), 'Подарок лежал на столе.')

    def test_a_stem_does_not_match_in_the_middle_of_a_word(self):
        from voiceover.pronunciation_rules import apply_rules
        self.assertEqual(apply_rules('Неподарок и подарок', [self.STEM]), 'Неподарок и падарок')

    def test_rules_are_chosen_by_engine(self):
        from voiceover.pronunciation_rules import for_engine
        anywhere = {'match': 'x', 'replace': 'y'}
        self.assertEqual(for_engine([self.STEM, anywhere], 'voxcpm2'), [anywhere])
        self.assertEqual(for_engine([self.STEM, anywhere], 'qwen3-tts-12hz-0.6b-base'), [self.STEM, anywhere])

    def test_the_shipped_file_is_valid_and_every_rule_names_a_known_engine(self):
        from voiceover.pronunciation_rules import load_rules
        from voiceover.voiceover_registry import ENGINES
        for rule in load_rules():
            self.assertTrue(set(rule.get('engines', [])) <= set(ENGINES), rule)
            self.assertEqual(rule['match'], rule['match'].lower())

    def test_a_damaged_rules_file_is_refused(self):
        from voiceover.pronunciation_rules import load_rules
        folder = Path(tempfile.mkdtemp(prefix='vf-rules-'))
        self.addCleanup(shutil.rmtree, folder, True)
        for text in ('{"version": 2, "rules": []}', '{"version": 1, "rules": [{"match": "", "replace": "x"}]}', '{"version": 1, "rules": [{"match": "a", "kind": "regex", "replace": "x"}]}'):
            (folder / 'rules.json').write_text(text, encoding='utf-8')
            with self.assertRaises(ValueError):
                load_rules(folder / 'rules.json')

    def test_recognition_is_compared_in_the_same_respelled_form(self):
        from voiceover.voice_comparison import compare
        spoken = 'падарок для тебя и ёлкин'
        heard = 'Подарок для тебя и Елкин'
        self.assertGreater(compare(spoken, heard, expand=True)[0], 0)
        self.assertEqual(compare(spoken, heard, expand=True, replacements=[['Елкин', 'ёлкин']], rules=[Pronunciation.STEM]), (0, 5))

    def test_suspect_words_are_the_source_words_recognition_lost(self):
        from voiceover.voice_comparison import suspect_words
        self.assertEqual(suspect_words('Все дети радовались, а ёж ушёл в лес.', 'Все дети радовались а еж ушел в лес'), [])
        self.assertEqual(suspect_words('Подарок, сказала она, нельзя прятать.', 'По дорог, сказала она, нельзя прятать.'), ['подарок'])
        self.assertEqual(suspect_words('я и он идут', 'идут'), [], 'words of one or two letters are not offered')


class BuiltinVoiceDescriptions(unittest.TestCase):
    def test_the_shipped_description_is_valid_and_every_voice_is_named_in_both_languages(self):
        from voiceover import default_voices as defaults
        config = defaults.load_config()
        catalog = json.loads((ROOT / 'App/strings/voiceover-strings.json').read_text(encoding='utf-8'))
        self.assertEqual(len(config['voices']), 8)
        self.assertEqual({voice['gender'] for voice in config['voices']}, {'female', 'male'})
        for voice in config['voices']:
            self.assertTrue(catalog[voice['name_key']]['ru'] and catalog[voice['name_key']]['en'], voice['slug'])
            self.assertRegex(defaults.voice_id(voice['slug']), r'^[0-9a-f]{32}$')
        self.assertEqual(len({defaults.voice_id(voice['slug']) for voice in config['voices']}), 8)

    def test_an_invalid_description_is_refused(self):
        from voiceover import default_voices as defaults
        folder = Path(tempfile.mkdtemp(prefix='vf-defaults-'))
        self.addCleanup(shutil.rmtree, folder, True)
        good = dict(slug='a', gender='female', name_key='k', description='d', seed=1)
        for bad in ({'version': 2, 'text': 't', 'voices': [good]}, {'version': 1, 'text': 't', 'voices': []}, {'version': 1, 'text': 't', 'takes': 99, 'voices': [good]},
                    {'version': 1, 'text': 't', 'voices': [dict(good, gender='other')]}, {'version': 1, 'text': 't', 'voices': [good, good]}):
            (folder / 'v.json').write_text(json.dumps(bad), encoding='utf-8')
            with self.assertRaises(ValueError):
                defaults.load_config(folder / 'v.json')

    def test_the_best_take_is_the_acceptable_one_with_the_calmest_pace(self):
        from voiceover.default_voices import choose
        take = dict(wrong=0, pitch=210.0, seconds=13.0, peak=0.7, rate=14.0)
        self.assertEqual(choose([dict(take, rate=19.0), dict(take, rate=14.5), dict(take, rate=11.0)], 'female'), 1)
        self.assertEqual(choose([dict(take, wrong=1, rate=14.0), dict(take, rate=17.0)], 'female'), 1, 'a take heard back wrongly is never chosen')
        self.assertIsNone(choose([dict(take, pitch=120.0)], 'female'), 'a low voice is not an acceptable woman')
        self.assertIsNone(choose([dict(take, pitch=210.0)], 'male'), 'a high voice is not an acceptable man')
        self.assertEqual(choose([dict(take, pitch=110.0)], 'male'), 0)
        self.assertIsNone(choose([dict(take, seconds=4.0), dict(take, peak=1.0), dict(take, seconds=40.0)], 'female'))

    def test_the_pitch_of_a_tone_is_measured(self):
        try:
            import numpy as np
        except ImportError:
            raise unittest.SkipTest('numpy is not installed for the test interpreter') from None
        from voiceover.default_voices import median_pitch
        rate = 16000
        t = np.arange(rate * 2) / rate
        voiced = (np.sin(2 * np.pi * 120 * t) + 0.5 * np.sin(2 * np.pi * 240 * t)).astype(np.float32)
        self.assertAlmostEqual(median_pitch(voiced, rate), 120.0, delta=4.0)
        self.assertEqual(median_pitch(np.zeros(rate, dtype=np.float32), rate), 0.0)


class CatalogParity(unittest.TestCase):
    """The installer must put the files exactly where the engine looks for them, with pinned hashes."""

    def setUp(self):
        self.catalog = {row['id']: row for row in json.loads((ROOT / 'App/config/component-catalog.json').read_text(encoding='utf-8'))['components']}
        self.manifest = {row['id']: row for row in json.loads((ROOT / 'App/config/voiceover-assets.json').read_text(encoding='utf-8'))['components']}

    def test_manifest_and_catalog_agree(self):
        # The catalog gets mirror URLs from `enrich`; the pins (name, size, hash) must be the manifest's.
        for ident, row in self.manifest.items():
            key = 'files' if row['kind'] == 'model' else 'downloads'
            pins = lambda rows: [(item.get('path') or item.get('filename'), item['bytes'], item['sha256']) for item in rows]
            self.assertEqual(pins(self.catalog[ident][key]), pins(row[key]))

    def test_voxcpm_model_files_land_where_the_engine_looks_and_are_pinned(self):
        from voiceover.voiceover_registry import ENGINES
        spec = ENGINES['voxcpm2']
        model = self.catalog[spec['component']]
        paths = {row['path'] for row in model['files']}
        for name in spec['model_files']:
            self.assertIn('Models/' + spec['model'] + '/' + name, paths)
        self.assertEqual(model['requires'], ['voiceover.runtime'])
        for row in model['files']:
            self.assertRegex(row['sha256'], r'^[0-9a-f]{64}$')
            self.assertIn(spec['upstream_revision'], row['urls'][0], 'every file is pinned to the revision the engine records')
        self.assertEqual(model['license'], spec['license'])

    def test_voxcpm_packages_are_unpacked_where_the_worker_puts_them_on_sys_path(self):
        from voiceover.voiceover_registry import ENGINES
        spec = ENGINES['voxcpm2']
        runtime = self.catalog['voiceover.runtime']
        step = runtime['steps'][0]
        self.assertEqual((step['op'], step['to']), ('unpack_wheels', 'Runtime/Video/' + spec['packages'][0]))
        self.assertIn('Runtime/Video/' + spec['package_check'], runtime['present'])
        self.assertEqual(runtime['requires'], ['video.video_runtime'])
        self.assertIn('voxcpm-2.0.3', ' '.join(item['filename'] for item in runtime['downloads']))
        for item in runtime['downloads']:
            self.assertRegex(item['sha256'], r'^[0-9a-f]{64}$')


class Batching(unittest.TestCase):
    ITEMS = [{'key': str(n), 'text': 'x' * length} for n, length in enumerate((30, 250, 90, 260, 120, 40, 200))]

    def test_batch_size_one_keeps_the_reading_order(self):
        from voiceover.qwen_tts_worker import make_batches
        self.assertEqual([[item['key']] for item in self.ITEMS], [[group[0]['key']] for group in make_batches(self.ITEMS, 1)])

    def test_batch_fits_the_free_video_memory(self):
        from voiceover.qwen_tts_worker import fit_batch
        self.assertEqual(fit_batch(8, 13000), 8)
        self.assertEqual(fit_batch(8, 5000), 6)
        self.assertEqual(fit_batch(8, 3000), 3)
        self.assertEqual(fit_batch(8, 1200), 1)
        self.assertEqual(fit_batch(8, 100), 1, 'never below one fragment')
        self.assertEqual(fit_batch(1, 13000), 1, 'a request for one is never raised')

    def test_batches_group_fragments_of_similar_length_and_lose_nothing(self):
        from voiceover.qwen_tts_worker import make_batches
        groups = make_batches(self.ITEMS, 3)
        self.assertEqual([len(group) for group in groups], [3, 3, 1])
        self.assertEqual(sorted(item['key'] for group in groups for item in group), sorted(item['key'] for item in self.ITEMS))
        longest_first = [len(item['text']) for group in groups for item in group]
        self.assertEqual(longest_first, sorted(longest_first, reverse=True))


class Timeline(unittest.TestCase):
    def test_chapter_times_include_the_pauses_between_fragments(self):
        items = [dict(chapter=0, pause='sentence'), dict(chapter=0, pause='chapter'), dict(chapter=1, pause='chapter')]
        rows, total = assemble.timeline(items, [{'title': 'A'}, {'title': 'B'}], [1.0, 2.0, 3.0])
        self.assertEqual(rows[0], dict(title='A', start=0.0, end=3.25))
        self.assertEqual(rows[1], dict(title='B', start=4.75, end=7.75))
        self.assertAlmostEqual(total, 7.75)


def tone(path, seconds=1.0, rate=24000):
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(int(rate * seconds))))


@unittest.skipUnless(FFMPEG.is_file(), 'the studio ffmpeg is not installed here')
class Assembly(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix='vf-voiceover-'))
        self.cache = self.temp / 'cache'
        self.cache.mkdir()
        self.items = []
        for index, (chapter, pause) in enumerate(((0, 'sentence'), (0, 'chapter'), (1, 'chapter'))):
            tone(self.cache / ('k%d.wav' % index), 1.0)
            self.items.append(dict(key='k%d' % index, chapter=chapter, pause=pause))
        self.chapters = [{'title': 'Первая глава'}, {'title': 'Вторая = глава'}]

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def run_format(self, fmt):
        job = self.temp / fmt
        job.mkdir()
        command, output, rows, total = assemble.prepare(job, self.cache, self.items, self.chapters, 'Книга', fmt, FFMPEG)
        done = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return output, rows, total

    def test_all_formats_are_created_with_the_planned_length(self):
        for fmt in ('wav', 'mp3', 'm4b'):
            output, rows, total = self.run_format(fmt)
            self.assertGreater(output.stat().st_size, 1000, fmt)
            self.assertAlmostEqual(total, 3.0 + 0.25 + 1.5, places=2)
            self.assertEqual(len(rows), 2)

    @unittest.skipUnless(FFPROBE.is_file(), 'ffprobe is not installed here')
    def test_m4b_carries_chapter_titles_and_the_real_length(self):
        output, rows, total = self.run_format('m4b')
        info = json.loads(subprocess.check_output([str(FFPROBE), '-v', 'error', '-show_chapters', '-show_format', '-of', 'json', str(output)]))
        self.assertEqual([item['tags']['title'] for item in info['chapters']], ['Первая глава', 'Вторая = глава'])
        self.assertAlmostEqual(float(info['format']['duration']), total, delta=0.3)


@unittest.skipUnless(FFMPEG.is_file(), 'the studio ffmpeg is not installed here')
class Manager(unittest.TestCase):
    def setUp(self):
        from voiceover.voiceover_manager import VoiceoverManager
        self.temp = Path(tempfile.mkdtemp(prefix='vf-voiceover-'))
        runtime, model = self.temp / 'Runtime', self.temp / 'model'
        (runtime / 'voice-packages/qwen_tts').mkdir(parents=True)
        (runtime / 'voice-packages/qwen_tts/__init__.py').write_text('', encoding='utf-8')
        model.mkdir()
        (model / 'config.json').write_text('{}', encoding='utf-8')
        (model / 'model.safetensors').write_bytes(b'0')
        env = {'VISUALFORGE_VOICEOVER_RUNTIME': str(runtime), 'VISUALFORGE_VOICEOVER_MODEL': str(model), 'VISUALFORGE_VOICEOVER_PYTHON': sys.executable,
               'VISUALFORGE_VOICEOVER_FFMPEG': str(FFMPEG), 'VISUALFORGE_VOICEOVER_WORKER': str(ROOT / 'tests/fake_voiceover_worker.py'),
               'VISUALFORGE_VOICEOVER_STATE_ROOT': str(self.temp / 'state'), 'VISUALFORGE_VOICEOVER_VERIFY_PYTHON': sys.executable,
               'VISUALFORGE_VOICEOVER_VERIFY_WORKER': str(ROOT / 'tests/fake_verify_worker.py'),
               'VISUALFORGE_VOICEOVER_SPEECH_MODELS': str(self.temp / 'Speech'), 'VISUALFORGE_VOICEOVER_VOXCPM_MODEL': str(self.temp / 'voxmodel'),
               'VISUALFORGE_VOICEOVER_DEFAULTS_WORKER': str(ROOT / 'tests/fake_defaults_worker.py')}
        whisper = self.temp / 'Speech/whisper-turbo'
        whisper.mkdir(parents=True)
        for name in ('config.json', 'preprocessor_config.json', 'tokenizer.json'):
            (whisper / name).write_text('{}', encoding='utf-8')
        (whisper / 'model.safetensors').write_bytes(b'0' * 1_000_100)
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        paths = SimpleNamespace(root=self.temp, models=self.temp / 'Models', settings=self.temp / 'UserData/Settings', logs=self.temp / 'UserData/Logs')
        paths.settings.mkdir(parents=True)
        self.manager = VoiceoverManager(paths)
        self.addCleanup(self.manager.close)
        self.addCleanup(shutil.rmtree, self.temp, True)
        sample = self.temp / 'sample.wav'
        tone(sample, 5.0, 44100)
        self.voice = self.manager.add_voice(dict(name='Тестовый', consent=True, audio='data:audio/wav;base64,' + base64.b64encode(sample.read_bytes()).decode()))
        self.book = self.manager.prepare(dict(text=BOOK, title='Тест'))

    def request(self, **extra):
        return dict(book_id=self.book['book_id'], voice_id=self.voice['id'], language='Russian', format='mp3', seed=7, **extra)

    def finish(self, ident, timeout=90):
        deadline = time.time() + timeout
        while time.time() < deadline:
            row = self.manager.status(ident)
            if row['state'] in ('done', 'error', 'cancelled'):
                return row
            time.sleep(0.1)
        self.fail('the job did not finish')

    def install_voxcpm(self):
        (self.temp / 'Runtime/voxcpm-packages/voxcpm').mkdir(parents=True)
        (self.temp / 'Runtime/voxcpm-packages/voxcpm/__init__.py').write_text('', encoding='utf-8')
        (self.temp / 'voxmodel').mkdir()
        for name in ('config.json', 'model.safetensors', 'audiovae.pth', 'tokenizer.json'):
            (self.temp / 'voxmodel' / name).write_bytes(b'0')

    def test_engines_are_listed_and_each_one_is_available_only_when_its_files_are(self):
        listing = {state['id']: state for state in self.manager.availability()['engines']}
        self.assertEqual(set(listing), {'qwen3-tts-12hz-0.6b-base', 'voxcpm2'})
        self.assertTrue(listing['qwen3-tts-12hz-0.6b-base']['available'])
        self.assertFalse(listing['voxcpm2']['available'])
        self.assertEqual((listing['voxcpm2']['sample_rate'], listing['voxcpm2']['license']), (48000, 'Apache-2.0'))
        with self.assertRaises(ValueError):
            self.manager.submit(self.request(engine='voxcpm2'))
        self.install_voxcpm()
        self.assertTrue(self.manager.engine_state('voxcpm2')['available'])

    def test_voxcpm_job_gets_its_packages_model_and_the_voice_transcript(self):
        self.install_voxcpm()
        sample = self.temp / 'sample2.wav'
        tone(sample, 5.0, 44100)
        voice = self.manager.add_voice(dict(name='С расшифровкой', consent=True, transcript='  Это   образец голоса.  ',
                                            audio='data:audio/wav;base64,' + base64.b64encode(sample.read_bytes()).decode()))
        self.assertEqual([item['transcript'] for item in self.manager.voices()['voices'] if item['id'] == voice['id']], ['Это образец голоса.'])
        row = self.finish(self.manager.submit(dict(self.request(engine='voxcpm2'), voice_id=voice['id']))['id'])
        self.assertEqual((row['state'], row['engine']), ('done', 'voxcpm2'), row.get('error'))
        request = json.loads((self.manager.state / row['id'] / 'request.json').read_text(encoding='utf-8'))
        self.assertEqual([Path(item).name for item in request['packages']], ['voxcpm-packages', 'voice-packages'])
        self.assertEqual((Path(request['model']).name, request['transcript']), ('voxmodel', 'Это образец голоса.'))

    def test_fragment_keys_depend_on_the_engine(self):
        self.install_voxcpm()
        first = self.finish(self.manager.submit(self.request())['id'])
        second = self.finish(self.manager.submit(self.request(engine='voxcpm2'))['id'])
        self.assertEqual(second['reused'], 0, 'another engine never reuses the first one\'s fragments')
        self.assertEqual((first['state'], second['state']), ('done', 'done'))

    def wav_b64(self, name, seconds):
        path = self.temp / name
        tone(path, seconds, 44100)
        return 'data:audio/wav;base64,' + base64.b64encode(path.read_bytes()).decode()

    def test_several_recordings_are_joined_into_one_sample_with_a_gap(self):
        voice = self.manager.add_voice(dict(name='Из двух', consent=True, audios=[self.wav_b64('a.wav', 4.0), self.wav_b64('b.wav', 5.0)]))
        self.assertAlmostEqual(voice['duration'], 4.0 + 0.4 + 5.0, delta=0.15)
        meta = json.loads((self.manager.voices_dir / (voice['id'] + '.json')).read_text(encoding='utf-8'))
        self.assertEqual((meta['parts'], meta['transcript']), (2, ''))
        with wave.open(str(self.manager.voice_file(voice['id'])), 'rb') as handle:
            self.assertEqual((handle.getframerate(), handle.getnchannels()), (24000, 1))
        self.assertEqual(self.manager.voices()['voices'][0]['id'], voice['id'])

    def test_a_joined_sample_is_cut_at_the_limit(self):
        parts = [self.wav_b64('p%d.wav' % index, 25.0) for index in range(3)]
        voice = self.manager.add_voice(dict(name='Длинный', consent=True, audios=parts))
        self.assertAlmostEqual(voice['duration'], 45.0, delta=0.1)

    def test_several_recordings_refuse_a_transcript_and_a_crowd_of_files(self):
        pair = [self.wav_b64('a.wav', 4.0), self.wav_b64('b.wav', 4.0)]
        with self.assertRaises(ValueError):
            self.manager.add_voice(dict(name='X', consent=True, transcript='слова', audios=pair))
        with self.assertRaises(ValueError):
            self.manager.add_voice(dict(name='X', consent=True, audios=pair * 3))
        with self.assertRaises(ValueError):
            self.manager.add_voice(dict(name='X', consent=True, audios=[]))
        self.assertEqual(len(self.manager.voices()['voices']), 1, 'nothing was saved')

    def test_the_builtin_voices_are_missing_until_they_are_made_and_cannot_be_made_without_voxcpm(self):
        listing = self.manager.voices()
        self.assertEqual((listing['builtin'], listing['defaults']['ready'], listing['defaults']['wanted']), ([], 0, 8))
        self.assertFalse(listing['defaults']['can_build'])
        with self.assertRaises(ValueError):
            self.manager.build_defaults()
        self.install_voxcpm()
        self.assertTrue(self.manager.voices()['defaults']['can_build'])

    def build(self):
        self.manager.build_defaults()
        deadline = time.time() + 60
        while self.manager.defaults_state()['state'] == 'running' and time.time() < deadline:
            time.sleep(0.1)
        return self.manager.defaults_state()

    def test_building_the_builtin_voices_gives_usable_undeletable_voices(self):
        from voiceover import default_voices as defaults
        self.install_voxcpm()
        state = self.build()
        self.assertEqual((state['state'], state['ready'], state['failed']), ('done', 8, []), state)
        listing = self.manager.voices()
        self.assertEqual([item['id'] for item in listing['builtin']], [defaults.voice_id(voice['slug']) for voice in defaults.load_config()['voices']])
        self.assertEqual(list(self.manager.state.glob('defaults-*')), [], 'the scratch folder of the candidate takes is removed afterwards')
        ident = listing['builtin'][0]['id']
        self.assertTrue(self.manager.voice_file(ident).is_file())
        with self.assertRaises(ValueError):
            self.manager.delete_voice(dict(id=ident))
        self.assertTrue(self.manager.voice_file(ident).is_file(), 'the refusal leaves the voice in place')
        row = self.finish(self.manager.submit(dict(self.request(), voice_id=ident))['id'])
        self.assertEqual(row['state'], 'done', row.get('error'))
        self.assertIn('·', row['voice_name'])
        self.assertEqual(len(self.manager.voices()['voices']), 1, 'the built-in voices are not listed among the saved ones')

    def test_a_second_build_makes_only_the_voices_that_are_missing(self):
        from voiceover import default_voices as defaults
        self.install_voxcpm()
        self.build()
        gone = defaults.voice_id('man-deep')
        (self.manager.builtin_dir / (gone + '.wav')).unlink()
        self.assertEqual(self.manager.defaults_state()['ready'], 7)
        seen = []
        real = self.manager._execute

        def spy(row, command, env, directory, log_path, on_line=lambda line: None):
            seen.append(json.loads((directory / 'request.json').read_text(encoding='utf-8')))
            return real(row, command, env, directory, log_path, on_line)
        with mock.patch.object(self.manager, '_execute', spy):
            state = self.build()
        self.assertEqual((state['ready'], [item['slug'] for item in seen[0]['voices']]), (8, ['man-deep']))
        self.assertEqual(Path(seen[0]['model']).name, 'voxmodel')
        self.assertEqual([Path(item).name for item in seen[0]['packages']], ['voxcpm-packages', 'voice-packages'])
        self.assertEqual(Path(seen[0]['whisper']).name, 'whisper-turbo')

    def test_a_voice_without_an_acceptable_take_is_reported_not_hidden(self):
        self.install_voxcpm()
        with mock.patch.dict(os.environ, {'FAKE_DEFAULTS_SKIP': 'woman-low'}):
            state = self.build()
        self.assertEqual((state['state'], state['ready'], state['failed']), ('done', 7, ['woman-low']))

    def test_a_failed_build_reports_the_error_and_can_be_repeated(self):
        self.install_voxcpm()
        with mock.patch.dict(os.environ, {'FAKE_DEFAULTS_FAIL': '1'}):
            state = self.build()
        self.assertEqual(state['state'], 'error')
        self.assertIn('fake voice creation failure', state['error'])
        self.assertEqual(self.build()['state'], 'done')

    def test_a_build_blocks_voiceover_jobs_and_can_be_stopped(self):
        self.install_voxcpm()
        with mock.patch.dict(os.environ, {'FAKE_DEFAULTS_SLEEP': '30'}):
            self.manager.build_defaults()
            self.assertTrue(self.manager.busy())
            with self.assertRaises(ValueError):
                self.manager.submit(self.request())
            with self.assertRaises(ValueError):
                self.manager.build_defaults()
            deadline = time.time() + 20
            while not self.manager.owned and time.time() < deadline:
                time.sleep(0.05)
            self.manager.cancel_defaults()
            while self.manager.defaults_state()['state'] == 'running' and time.time() < deadline:
                time.sleep(0.1)
        self.assertEqual(self.manager.defaults_state()['state'], 'cancelled')
        self.assertFalse(self.manager.busy())
        self.assertEqual(self.manager.defaults_state()['ready'], 0)

    def test_a_voice_transcript_must_be_text_of_reasonable_length(self):
        with self.assertRaises(ValueError):
            self.manager.add_voice(dict(name='X', consent=True, transcript='я' * 1001, audio='AAAA'))

    def spoken_texts(self, row):
        return ' '.join(item['text'] for item in json.loads((self.manager.state / row['id'] / 'fragments.json').read_text(encoding='utf-8')))

    def test_numbers_are_written_as_words_before_synthesis_unless_switched_off(self):
        book = self.manager.prepare(dict(text='В 1998 году было 5 яблок, т.е. мало.', title='Числа'))
        self.assertEqual(book['normalization']['places'], 1)
        self.assertEqual(book['normalization']['examples'][0]['after'], 'В тысяча девятьсот девяносто восьмом году было пять яблок, то есть мало.')
        job = dict(self.request(), book_id=book['book_id'])
        on = self.finish(self.manager.submit(job)['id'])
        self.assertEqual(self.spoken_texts(on), 'В тысяча девятьсот девяносто восьмом году было пять яблок, то есть мало.')
        off = self.finish(self.manager.submit(dict(job, normalize=False))['id'])
        self.assertEqual(self.spoken_texts(off), 'В 1998 году было 5 яблок, т.е. мало.')
        english = self.finish(self.manager.submit(dict(job, language='English'))['id'])
        self.assertEqual(self.spoken_texts(english), 'В 1998 году было 5 яблок, т.е. мало.', 'the rules are Russian; other languages keep their digits')

    def written_and_spoken(self, row):
        items = json.loads((self.manager.state / row['id'] / 'fragments.json').read_text(encoding='utf-8'))
        return ' '.join(item['text'] for item in items), ' '.join(item['speak'] for item in items)

    def test_builtin_rules_change_what_the_engine_is_given_but_not_what_is_written(self):
        self.install_voxcpm()
        book = self.manager.prepare(dict(text='Глава 1\n\nПодарок лежал на столе. Это был лучший подарок.', title='Дары'))
        job = dict(self.request(), book_id=book['book_id'])
        with mock.patch('voiceover.pronunciation_rules.load_rules', return_value=[Pronunciation.STEM]):
            qwen = self.finish(self.manager.submit(job)['id'])
            vox = self.finish(self.manager.submit(dict(job, engine='voxcpm2'))['id'])
            off = self.finish(self.manager.submit(dict(job, dictionary=False, seed=8))['id'])
            english = self.finish(self.manager.submit(dict(job, language='English', seed=9))['id'])
        written, spoken = self.written_and_spoken(qwen)
        self.assertEqual((written, spoken), ('Подарок лежал на столе. Это был лучший подарок.', 'Падарок лежал на столе. Это был лучший падарок.'))
        request = json.loads((self.manager.state / qwen['id'] / 'request.json').read_text(encoding='utf-8'))
        self.assertIn('Падарок', request['fragments'][0]['text'], 'the worker is given the respelled text')
        verify = json.loads((self.manager.state / qwen['id'] / 'verify-request.json').read_text(encoding='utf-8'))
        self.assertIn('Подарок', verify['fragments'][0]['text'], 'the check compares with the written text')
        self.assertEqual(verify['rules'], [Pronunciation.STEM])
        for other in (vox, off, english):
            written, spoken = self.written_and_spoken(other)
            self.assertEqual(written, spoken)
        self.assertEqual([self.written_and_spoken(item)[1] for item in (qwen,)], ['Падарок лежал на столе. Это был лучший падарок.'])

    def test_a_damaged_rules_file_does_not_stop_the_job(self):
        with mock.patch('voiceover.pronunciation_rules.load_rules', side_effect=ValueError('broken')):
            row = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual(row['state'], 'done', row.get('error'))

    def test_flagged_fragments_carry_the_words_to_offer_for_the_dictionary(self):
        with mock.patch.dict(os.environ, {'FAKE_VERIFY_ALWAYS_BAD': '1'}):
            row = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual(row['flagged'][0]['suspects'], ['абзац'])

    def test_the_users_dictionary_acts_before_normalization(self):
        book = self.manager.prepare(dict(text='Смотри стр. 17.', title='Словарь'))
        row = self.finish(self.manager.submit(dict(self.request(), book_id=book['book_id'], replacements=[['стр. 17', 'страницу номер семнадцать']]))['id'])
        self.assertEqual(self.spoken_texts(row), 'Смотри страницу номер семнадцать.')

    def test_clean_text_passes_verification_without_repairs(self):
        row = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual(row['state'], 'done', row.get('error'))
        self.assertEqual((row['verification']['state'], row['verification']['repaired'], row['flagged']), ('done', 0, []))
        self.assertEqual(row['verification']['model'], 'whisper-turbo')
        self.assertEqual(self.manager.availability()['verification'], dict(available=True, model='whisper-turbo'))

    def test_a_wrong_fragment_is_synthesized_again_and_accepted(self):
        with mock.patch.dict(os.environ, {'FAKE_VERIFY_BAD_ROUNDS': '1'}):
            row = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual(row['state'], 'done', row.get('error'))
        self.assertEqual((row['verification']['repaired'], row['flagged']), (1, []))
        request = json.loads((self.manager.state / row['id'] / 'request.json').read_text(encoding='utf-8'))
        self.assertEqual((request['seed_shift'], len(request['fragments'])), (1, 1), 'only the wrong fragment is synthesized again, with other seeds')
        self.assertIn('Второй абзац', request['fragments'][0]['text'])

    def test_a_fragment_that_stays_wrong_is_reported_with_its_audio(self):
        with mock.patch.dict(os.environ, {'FAKE_VERIFY_ALWAYS_BAD': '1'}):
            row = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual(row['state'], 'done', row.get('error'))
        self.assertEqual(row['verification']['repaired'], 2, 'two repair rounds, then the report')
        self.assertEqual(len(row['flagged']), 1)
        report = row['flagged'][0]
        self.assertEqual((report['chapter'], report['heard'], report['wrong']), ('Глава 1', 'шум', 5))
        self.assertTrue(self.manager.fragment_file(row['id'], report['key']).is_file())
        other = next(path.stem for path in self.manager.cache.glob('*.wav') if path.stem != report['key'])
        for bad_key in (other, 'z' * 32, '../x'):
            with self.assertRaises(ValueError):
                self.manager.fragment_file(row['id'], bad_key)

    def verified_fragments(self, row):
        path = self.manager.state / row['id'] / 'verify-request.json'
        return len(json.loads(path.read_text(encoding='utf-8'))['fragments']) if path.is_file() else 0

    def test_a_repeated_job_does_not_listen_to_checked_fragments_again(self):
        first = self.finish(self.manager.submit(self.request())['id'])
        second = self.finish(self.manager.submit(dict(self.request(), format='wav'))['id'])
        self.assertEqual((first['verification']['cached'], self.verified_fragments(first)), (0, first['total']))
        self.assertEqual((second['state'], second['verification']['state'], second['verification']['cached'], self.verified_fragments(second)), ('done', 'done', second['total'], 0))

    def test_a_fragment_repairs_could_not_fix_stays_reported_without_another_repair_on_a_rerun(self):
        with mock.patch.dict(os.environ, {'FAKE_VERIFY_ALWAYS_BAD': '1'}):
            first = self.finish(self.manager.submit(self.request())['id'])
            second = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual((first['verification']['repaired'], len(first['flagged'])), (2, 1))
        self.assertEqual((second['verification']['repaired'], len(second['flagged']), self.verified_fragments(second)), (0, 1, 0))
        self.assertEqual(second['flagged'][0]['key'], first['flagged'][0]['key'])
        request = json.loads((self.manager.state / second['id'] / 'request.json').read_text(encoding='utf-8'))
        self.assertEqual(request['seed_shift'], 0, 'no repair round was started')

    def test_a_regenerated_fragment_is_checked_again_even_though_an_old_verdict_exists(self):
        first = self.finish(self.manager.submit(self.request())['id'])
        key = json.loads((self.manager.state / first['id'] / 'fragments.json').read_text(encoding='utf-8'))[0]['key']
        (self.manager.cache / (key + '.wav')).unlink()  # a crash lost this fragment; the next job synthesizes it again
        second = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual((second['state'], self.verified_fragments(second)), ('done', 1))

    def test_a_failing_checker_does_not_fail_the_job(self):
        with mock.patch.dict(os.environ, {'FAKE_VERIFY_FAIL': '1'}):
            row = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual(row['state'], 'done', row.get('error'))
        self.assertEqual(row['verification']['state'], 'skipped')
        self.assertIn('fake recognition failure', row['verification']['reason'])

    def test_checking_can_be_switched_off_or_may_lack_a_model(self):
        off = self.finish(self.manager.submit(self.request(verify=False))['id'])
        self.assertEqual(off['verification'], {'state': 'off'})
        shutil.rmtree(self.temp / 'Speech/whisper-turbo')
        self.assertFalse(self.manager.availability()['verification']['available'])
        lacking = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual((lacking['state'], lacking['verification']['state']), ('done', 'skipped'))

    def test_stop_during_verification_cancels_the_job(self):
        with mock.patch.dict(os.environ, {'FAKE_VERIFY_SLEEP': '30'}):
            started = self.manager.submit(self.request())
            deadline = time.time() + 30
            while 'Проверка' not in self.manager.status(started['id'])['phase'] and time.time() < deadline:
                time.sleep(0.1)
            self.manager.cancel(dict(id=started['id'], token=started['token']))
            began = time.time()
            row = self.finish(started['id'])
        self.assertEqual(row['state'], 'cancelled')
        self.assertLess(time.time() - began, 10)

    def test_batch_size_reaches_the_worker_request(self):
        self.assertEqual(self.manager.batch, 8)
        self.manager.batch = 4
        row = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual(json.loads((self.manager.state / row['id'] / 'request.json').read_text(encoding='utf-8'))['batch'], 4)

    def test_prepare_reports_chapters_and_an_estimate(self):
        self.assertEqual([row['title'] for row in self.book['chapters']], ['Глава 1', 'Глава 2'])
        self.assertGreater(self.book['fragments'], 2)
        self.assertGreater(self.book['estimate_sec'], 0)

    def test_voice_needs_consent_a_name_and_real_audio(self):
        for bad in (dict(name='X', consent=False, audio='AAAA'), dict(name='', consent=True, audio='AAAA'),
                    dict(name='X', consent=True, audio=base64.b64encode(b'not audio at all').decode())):
            with self.assertRaises(ValueError):
                self.manager.add_voice(bad)
        self.assertEqual(len(self.manager.voices()['voices']), 1)
        self.assertEqual(self.manager.voices()['voices'][0]['name'], 'Тестовый')

    def test_job_runs_publishes_and_a_second_job_reuses_the_cache(self):
        row = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual(row['state'], 'done', row.get('error'))
        output = Path(row['saved_path'])
        self.assertTrue(output.is_file() and output.suffix == '.mp3')
        self.assertTrue(output.with_suffix('.json').is_file())
        self.assertEqual(row['reused'], 0)
        self.assertEqual(len(row['chapters']), 2)
        again = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual(again['state'], 'done', again.get('error'))
        self.assertEqual(again['reused'], again['total'])
        self.assertEqual(len(self.manager.entries()['entries']), 2)

    def test_stop_keeps_finished_fragments_and_the_next_job_resumes(self):
        with mock.patch.dict(os.environ, {'FAKE_VOICEOVER_SLEEP': '0.4'}):
            started = self.manager.submit(self.request())
            deadline = time.time() + 30
            while self.manager.status(started['id']).get('done', 0) < 2 and time.time() < deadline:
                time.sleep(0.1)
            self.manager.cancel(dict(id=started['id'], token=started['token']))
            stopped = self.finish(started['id'])
        self.assertEqual(stopped['state'], 'cancelled')
        self.assertTrue(any(self.manager.cache.glob('*.wav')))
        resumed = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual(resumed['state'], 'done', resumed.get('error'))
        self.assertGreater(resumed['reused'], 0)

    def test_worker_failure_is_reported_with_its_message(self):
        with mock.patch.dict(os.environ, {'FAKE_VOICEOVER_FAIL': '1'}):
            row = self.finish(self.manager.submit(self.request())['id'])
        self.assertEqual(row['state'], 'error')
        self.assertIn('fake worker failure', row['error'])

    def test_one_job_at_a_time_and_cancel_needs_the_token(self):
        with mock.patch.dict(os.environ, {'FAKE_VOICEOVER_SLEEP': '0.3'}):
            started = self.manager.submit(self.request())
            with self.assertRaises(ValueError):
                self.manager.submit(self.request())
            with self.assertRaises(ValueError):
                self.manager.cancel(dict(id=started['id'], token='wrong'))
            self.manager.cancel(dict(id=started['id'], token=started['token']))
            self.finish(started['id'])

    def test_voice_in_use_cannot_be_deleted_and_delete_removes_files(self):
        with mock.patch.dict(os.environ, {'FAKE_VOICEOVER_SLEEP': '0.3'}):
            started = self.manager.submit(self.request())
            with self.assertRaises(ValueError):
                self.manager.delete_voice(dict(id=self.voice['id']))
            self.manager.cancel(dict(id=started['id'], token=started['token']))
            self.finish(started['id'])
        row = self.finish(self.manager.submit(self.request())['id'])
        output = Path(row['saved_path'])
        self.manager.delete(dict(id=row['id']))
        self.assertFalse(output.exists() or output.with_suffix('.json').exists())
        self.assertEqual(self.manager.delete_voice(dict(id=self.voice['id'])), {'deleted': True})
        self.assertEqual(self.manager.voices()['voices'], [])

    def test_unknown_book_or_voice_is_refused_before_anything_starts(self):
        for bad in (dict(self.request(), book_id='c' * 32), dict(self.request(), voice_id='c' * 32)):
            with self.assertRaises(ValueError):
                self.manager.submit(bad)
        self.assertFalse(self.manager.busy())

    def test_missing_model_files_make_the_engine_unavailable(self):
        (self.temp / 'model/model.safetensors').unlink()
        self.assertFalse(self.manager.availability()['available'])
        with self.assertRaises(ValueError):
            self.manager.submit(self.request())

    def test_clear_cache_frees_fragments(self):
        self.finish(self.manager.submit(self.request())['id'])
        self.assertGreater(self.manager.clear_cache()['freed_bytes'], 0)
        self.assertFalse(self.manager.cache.exists())


if __name__ == '__main__':
    unittest.main()
