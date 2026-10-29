"""Spoken forms of numbers and common abbreviations (Russian), applied to a text before synthesis. Standard library only.

The speech model reads digits and abbreviations unreliably («стр. 17» came out as «стрер, семь си цить»), so they are rewritten
into the words a person would say. The rules are conservative: only patterns with one clear reading are rewritten, everything else is
left to the model and to the user's pronunciation dictionary.

Known limits (also in docs/VisualForge-Voiceover-Guide.md): a plain number is spoken in the nominative/accusative («пять минут», «в пять минут»), so
«о 5 минутах» becomes «о пять минутах»; the numeral takes its gender from the next word's ending (one noun in a thousand is
wrong); only years with «год/г.», centuries with «век/в.» and numbers with an ordinal suffix («5-й») are declined; ranges («1941-1945 гг.»),
dates («12.05.2024») and mixed tokens («A4», «mp3») are left as they are.
"""
import re

# ---- cardinal numbers --------------------------------------------------------------------------------------------------
UNITS = {'m': ['ноль', 'один', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять'],
         'f': ['ноль', 'одна', 'две', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять'],
         'n': ['ноль', 'одно', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять']}
UNITS['fa'] = UNITS['f'][:1] + ['одну'] + UNITS['f'][2:]  # feminine accusative: «одну минуту»
TEENS = ['десять', 'одиннадцать', 'двенадцать', 'тринадцать', 'четырнадцать', 'пятнадцать', 'шестнадцать', 'семнадцать', 'восемнадцать', 'девятнадцать']
TENS = ['', '', 'двадцать', 'тридцать', 'сорок', 'пятьдесят', 'шестьдесят', 'семьдесят', 'восемьдесят', 'девяносто']
HUNDREDS = ['', 'сто', 'двести', 'триста', 'четыреста', 'пятьсот', 'шестьсот', 'семьсот', 'восемьсот', 'девятьсот']
SCALES = [('тысяча', 'тысячи', 'тысяч', 'f'), ('миллион', 'миллиона', 'миллионов', 'm'), ('миллиард', 'миллиарда', 'миллиардов', 'm'),
          ('триллион', 'триллиона', 'триллионов', 'm')]


def plural(count, one, few, many):
    """The noun form after a count: 1 рубль, 2 рубля, 5 рублей, 11 рублей, 21 рубль."""
    if 11 <= count % 100 <= 14:
        return many
    if count % 10 == 1:
        return one
    return few if 2 <= count % 10 <= 4 else many


def _triplet(value, gender):
    words = []
    if value >= 100:
        words.append(HUNDREDS[value // 100])
    value %= 100
    if value >= 20:
        words.append(TENS[value // 10])
        value %= 10
        if value:
            words.append(UNITS[gender][value])
    elif value >= 10:
        words.append(TEENS[value - 10])
    elif value:
        words.append(UNITS[gender][value])
    return words


def cardinal(number, gender='m'):
    """«сто двадцать пять», for 0 <= number < 10**15. `gender` only changes «один/одна/одно» and «два/две»."""
    if number == 0:
        return UNITS['m'][0]
    groups = []
    while number:
        groups.append(number % 1000)
        number //= 1000
    words = []
    for index in reversed(range(len(groups))):
        value = groups[index]
        if not value:
            continue
        if index == 0:
            words += _triplet(value, gender)
        else:
            one, few, many, scale_gender = SCALES[index - 1]
            if not (value == 1 and index == 1):  # «тысяча», not «одна тысяча», but «один миллион»
                words += _triplet(value, scale_gender)
            words.append(plural(value, one, few, many))
    return ' '.join(words)


# ---- ordinal numbers ---------------------------------------------------------------------------------------------------
ORD_UNITS = ['нулевой', 'первый', 'второй', 'третий', 'четвёртый', 'пятый', 'шестой', 'седьмой', 'восьмой', 'девятый']
ORD_TEENS = ['десятый', 'одиннадцатый', 'двенадцатый', 'тринадцатый', 'четырнадцатый', 'пятнадцатый', 'шестнадцатый', 'семнадцатый', 'восемнадцатый', 'девятнадцатый']
ORD_TENS = ['', '', 'двадцатый', 'тридцатый', 'сороковой', 'пятидесятый', 'шестидесятый', 'семидесятый', 'восьмидесятый', 'девяностый']
ORD_HUNDREDS = ['', 'сотый', 'двухсотый', 'трёхсотый', 'четырёхсотый', 'пятисотый', 'шестисотый', 'семисотый', 'восьмисотый', 'девятисотый']
ORD_THOUSANDS = ['', 'тысячный', 'двухтысячный', 'трёхтысячный', 'четырёхтысячный', 'пятитысячный', 'шеститысячный', 'семитысячный', 'восьмитысячный', 'девятитысячный']
# Adjective endings after the stem. «hard» is -ый/-ой, «soft» is only «третий».
HARD = {'nom': ('ый', 'ая', 'ое', 'ые'), 'gen': ('ого', 'ой', 'ого', 'ых'), 'dat': ('ому', 'ой', 'ому', 'ым'), 'acc': ('ый', 'ую', 'ое', 'ые'),
        'ins': ('ым', 'ой', 'ым', 'ыми'), 'prep': ('ом', 'ой', 'ом', 'ых')}
SOFT = {'nom': ('ий', 'ья', 'ье', 'ьи'), 'gen': ('ьего', 'ьей', 'ьего', 'ьих'), 'dat': ('ьему', 'ьей', 'ьему', 'ьим'), 'acc': ('ий', 'ью', 'ье', 'ьи'),
        'ins': ('ьим', 'ьей', 'ьим', 'ьими'), 'prep': ('ьем', 'ьей', 'ьем', 'ьих')}
GENDER_COLUMN = {'m': 0, 'f': 1, 'n': 2}


def _decline(word, case, gender, plural_form):
    """An ordinal given in the masculine nominative («восьмой») in the wanted case and gender."""
    soft = word.endswith('ий')
    stem = word[:-2]
    endings = (SOFT if soft else HARD)[case]
    ending = endings[3] if plural_form else endings[GENDER_COLUMN[gender]]
    if not soft and case in ('nom', 'acc') and gender == 'm' and not plural_form:
        ending = word[-2:]  # keep -ой where the stress is on the ending (второй, шестой, сороковой)
    return stem + ending


def ordinal(number, case='nom', gender='m', plural_form=False):
    """«тысяча девятьсот девяносто восьмой» (declined). None when the number has no simple ordinal (the caller keeps the digits)."""
    if not 0 < number < 10000:
        return None
    head, last = [], None
    if number % 100:
        tail = number % 100
        if number - tail:
            head = cardinal(number - tail).split()
        if tail < 10:
            last = ORD_UNITS[tail]
        elif tail < 20:
            last = ORD_TEENS[tail - 10]
        elif tail % 10:
            head += TENS[tail // 10].split()
            last = ORD_UNITS[tail % 10]
        else:
            last = ORD_TENS[tail // 10]
    elif number % 1000:
        if number // 1000:
            head = cardinal(number // 1000 * 1000).split()
        last = ORD_HUNDREDS[number % 1000 // 100]
    else:
        last = ORD_THOUSANDS[number // 1000]
    return ' '.join(head + [_decline(last, case, gender, plural_form)])


# ---- words that are numerals (for the comparison with what recognition heard) --------------------------------------------
_OBLIQUE = ('двух двум двумя трех трем тремя четырех четырем четырьмя пяти пятью шести шестью семи семью восьми восьмью девяти девятью десяти '
            'одиннадцати двенадцати тринадцати четырнадцати пятнадцати шестнадцати семнадцати восемнадцати девятнадцати двадцати тридцати '
            'сорока пятидесяти шестидесяти семидесяти восьмидесяти девяноста ста двухсот трехсот четырехсот пятисот шестисот семисот восьмисот '
            'девятисот тысячи тысяче тысячу тысячей тысячами тысячах миллиона миллиону миллионом миллионе миллионов одного одной одному одним '
            'одном одну целая целых десятая десятых сотая сотых тысячная тысячных ноль нуля').split()


def _vocabulary():
    words = set(_OBLIQUE)
    for table in (UNITS['m'], UNITS['f'], UNITS['n'], TEENS, TENS, HUNDREDS):
        words.update(word for word in table if word)
    for one, few, many, _ in SCALES:
        words.update((one, few, many))
    for table in (ORD_UNITS, ORD_TEENS, ORD_TENS, ORD_HUNDREDS, ORD_THOUSANDS):
        for word in table:
            if word:
                for case in HARD:
                    for gender in 'mfn':
                        for plural_form in (False, True):
                            words.add(_decline(word, case, gender, plural_form))
    return frozenset(word.replace('ё', 'е') for word in words)


NUMBER_WORDS = _vocabulary()


# ---- context --------------------------------------------------------------------------------------------------------------
PREPOSITION_CASE = {}
for _case, _words in (('prep', 'в во на о об при'), ('dat', 'к ко по'), ('gen', 'из от до с со без для у около после вместо'),
                      ('ins', 'за над под перед между'), ('acc', 'см смотри через про')):
    for _word in _words.split():
        PREPOSITION_CASE[_word] = _case


def _case_after(text, position):
    """The case a noun takes after the preposition just before `position` (nominative when there is none)."""
    before = re.search(r'(\w+)\W*$', text[:position])
    return PREPOSITION_CASE.get(before.group(1).lower(), 'nom') if before else 'nom'


def _gender_of_next_word(text, position, last_digit):
    """Form of «один/одна/одну/одно» (last digit 1) or «два/две» (last digit 2) from the ending of the next word.

    After 1 the noun is nominative or accusative singular («одна минута», «одну минуту», «одно окно»); after 2 it is genitive singular
    («две минуты», but «два дома», «два окна»). A heuristic: see the limits in the module docstring."""
    following = re.match(r'\s*([а-яёА-ЯЁ]+)', text[position:])
    if not following:
        return 'm'
    ending = following.group(1).lower()[-1]
    if last_digit == 2:
        return 'f' if ending in 'ыи' else 'm'
    return 'f' if ending in 'аяыи' else 'fa' if ending in 'уюю' else 'n' if ending in 'оеё' else 'm'


def _capitalize_like(original, replacement):
    return replacement[:1].upper() + replacement[1:] if original[:1].isupper() else replacement


# ---- abbreviations ----------------------------------------------------------------------------------------------------------
# (pattern, spoken form, may end a sentence). The dot of an abbreviation that ends a sentence is also the sentence's full stop, so
# it is kept when nothing but a capital letter, a quote, a dash or the end of the paragraph follows («и т.д. Потом» -> «и так далее. Потом»).
FIXED_ABBREVIATIONS = [
    (r'в\s?т\.\s?ч\.', 'в том числе', False), (r'и\s?т\.\s?д\.', 'и так далее', True), (r'и\s?т\.\s?п\.', 'и тому подобное', True),
    (r'т\.\s?е\.', 'то есть', False), (r'т\.\s?к\.', 'так как', False), (r'т\.\s?н\.', 'так называемый', False), (r'т\.\s?д\.', 'так далее', True),
    (r'т\.\s?п\.', 'тому подобное', True), (r'и\s?др\.', 'и другие', True), (r'и\s?пр\.', 'и прочее', True), (r'и\s?проч\.', 'и прочее', True),
    (r'напр\.', 'например', False), (r'англ\.', 'английский', False), (r'проф\.', 'профессор', False), (r'акад\.', 'академик', False),
    (r'доц\.', 'доцент', False), (r'им\.', 'имени', False), (r'ок\.', 'около', False), (r'др\.(?=\s+[а-яё])', 'другие', False),
]
SENTENCE_END = re.compile(r'(?:\Z|\n|\s+[А-ЯЁA-Z«"(—])')


def _stop(text, end):
    """'.' when the sentence ends right after `end` (see FIXED_ABBREVIATIONS), else nothing."""
    return '.' if SENTENCE_END.match(text, end) else ''
# noun abbreviations: nominative, genitive, dative/prepositional, instrumental, accusative
NOUN_ABBREVIATIONS = {
    'стр': ('страница', 'страницы', 'странице', 'страницей', 'страницу'), 'гл': ('глава', 'главы', 'главе', 'главой', 'главу'),
    'табл': ('таблица', 'таблицы', 'таблице', 'таблицей', 'таблицу'), 'ул': ('улица', 'улицы', 'улице', 'улицей', 'улицу'),
    'рис': ('рисунок', 'рисунка', 'рисунке', 'рисунком', 'рисунок'),
}
NOUN_FORM = {'nom': 0, 'gen': 1, 'dat': 2, 'prep': 2, 'ins': 3, 'acc': 4}


def _abbreviations(text):
    for pattern, replacement, may_end in FIXED_ABBREVIATIONS:
        text = re.sub(r'(?<!\w)' + pattern, lambda m, r=replacement, e=may_end: _capitalize_like(m.group(), r) + (_stop(m.string, m.end()) if e else ''),
                      text, flags=re.IGNORECASE)

    def noun(match):
        forms = NOUN_ABBREVIATIONS[match.group(1).lower()]
        return _capitalize_like(match.group(1), forms[NOUN_FORM[_case_after(text, match.start())]])
    text = re.sub(r'(?<!\w)(' + '|'.join(NOUN_ABBREVIATIONS) + r')\.(?=\s*[\dIVX«"(]|\s+[А-ЯЁ])', noun, text, flags=re.IGNORECASE)
    # «см.» means «смотри» unless a number comes right before it («5 см.» is centimetres).
    text = re.sub(r'(?<!\w)(?<!\d )(?<!\d)см\.(?=\s)', lambda m: _capitalize_like(m.group(), 'смотри'), text, flags=re.IGNORECASE)
    return re.sub(r'№\s?(?=\d)', 'номер ', text)


# ---- numbers ----------------------------------------------------------------------------------------------------------------
UNIT_WORDS = {'кг': ('килограмм', 'килограмма', 'килограммов'), 'км': ('километр', 'километра', 'километров'),
              'см': ('сантиметр', 'сантиметра', 'сантиметров'), 'мм': ('миллиметр', 'миллиметра', 'миллиметров'),
              'руб': ('рубль', 'рубля', 'рублей'), 'коп': ('копейка', 'копейки', 'копеек'), 'млн': ('миллион', 'миллиона', 'миллионов'),
              'млрд': ('миллиард', 'миллиарда', 'миллиардов')}
FRACTION_NAMES = {1: ('десятая', 'десятых'), 2: ('сотая', 'сотых'), 3: ('тысячная', 'тысячных')}
YEAR_NOUN = {'nom': 'год', 'gen': 'года', 'dat': 'году', 'prep': 'году', 'ins': 'годом', 'acc': 'год'}
YEAR_FORM_CASE = {'год': 'nom', 'года': 'gen', 'году': 'prep', 'годом': 'ins', 'годе': 'prep'}
CENTURY_NOUN = {'nom': 'век', 'gen': 'века', 'dat': 'веку', 'prep': 'веке', 'ins': 'веком', 'acc': 'век'}
CENTURY_FORM_CASE = {'век': 'nom', 'века': 'gen', 'веку': 'dat', 'веке': 'prep', 'веком': 'ins'}
ROMAN = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100}
SUFFIX = {'й': ('nom', 'm', False), 'ый': ('nom', 'm', False), 'ой': ('gen', 'f', False), 'я': ('nom', 'f', False), 'ая': ('nom', 'f', False),
          'е': ('nom', 'n', False), 'ое': ('nom', 'n', False), 'го': ('gen', 'm', False), 'ого': ('gen', 'm', False),
          'му': ('dat', 'm', False), 'ому': ('dat', 'm', False), 'м': ('ins', 'm', False), 'ом': ('prep', 'm', False), 'ым': ('ins', 'm', False), 'ю': ('acc', 'f', False), 'ую': ('acc', 'f', False),
          'х': ('gen', 'm', True), 'ых': ('gen', 'm', True), 'ми': ('ins', 'm', True), 'ыми': ('ins', 'm', True), 'ые': ('nom', 'm', True)}


def _roman(value):
    total = 0
    for index, char in enumerate(value):
        number = ROMAN[char]
        total += -number if index + 1 < len(value) and ROMAN[value[index + 1]] > number else number
    return total


def _spoken_decimal(whole, fraction):
    value = int(fraction)
    one, many = FRACTION_NAMES[len(fraction)]
    whole_form = plural(whole, 'целая', 'целых', 'целых')
    fraction_form = one if value % 10 == 1 and value % 100 != 11 else many
    return '%s %s %s %s' % (cardinal(whole, 'f'), whole_form, cardinal(value, 'f'), fraction_form)


def _numbers(text):
    # A hyphen right before a number that follows a space or the start is a minus sign («-5 °C»); «1941-1945» is not touched.
    text = re.sub(r'(?<![\w.,])[-−](?=\d)', 'минус ', text)

    def time(match):
        hours, minutes = int(match.group(1)), int(match.group(2))
        if hours > 23 or minutes > 59:
            return match.group()
        tail = 'ноль ноль' if minutes == 0 else cardinal(minutes) if minutes >= 10 else 'ноль ' + cardinal(minutes)
        return cardinal(hours) + ' ' + tail
    text = re.sub(r'(?<![\d:.,])(\d{1,2}):(\d{2})(?![\d:])', time, text)

    # decimals first: «3,5 кг» and «3,5%» must not be taken for two integers
    def decimal_unit(match):
        words = _spoken_decimal(int(match.group(1)), match.group(2))
        suffix = match.group(3)
        return words + (' процента' if suffix == '%' else ' ' + UNIT_WORDS[suffix.rstrip('.')][1])
    text = re.sub(r'(?<![\w.,])(\d+),(\d{1,3})\s?(%|кг|км|см|мм)(?![\w])', decimal_unit, text)
    text = re.sub(r'(?<![\w.,])(\d+),(\d{1,3})(?![\w]|,\d)', lambda m: _spoken_decimal(int(m.group(1)), m.group(2)), text)

    text = re.sub(r'(?<![\w.,])(\d{1,12})\s?%', lambda m: cardinal(int(m.group(1))) + ' ' + plural(int(m.group(1)), 'процент', 'процента', 'процентов'), text)
    text = re.sub(r'(?<![\w.,])(\d{1,12})\s?°\s?[CС](?!\w)', lambda m: cardinal(int(m.group(1))) + ' ' + plural(int(m.group(1)), 'градус', 'градуса', 'градусов') + ' Цельсия', text)
    text = re.sub(r'(?<![\w.,])(\d{1,12})\s?тыс\.?(?!\w)', lambda m: cardinal(int(m.group(1)), 'f') + ' ' + plural(int(m.group(1)), 'тысяча', 'тысячи', 'тысяч'), text)

    def unit(match):
        forms = UNIT_WORDS[match.group(2)]
        return cardinal(int(match.group(1))) + ' ' + plural(int(match.group(1)), *forms)
    text = re.sub(r'(?<![\w.,])(\d{1,12})\s?(кг|км|см|мм|руб|коп|млн|млрд)\.?(?!\w)', unit, text)

    def year(match):
        number = int(match.group(1))
        noun = match.group(2).lower().rstrip('.')
        if not 100 <= number <= 2999:
            return match.group()
        preposition = _case_after(text, match.start())
        if noun == 'г':
            case = preposition  # the abbreviation carries no case of its own: «в 1998 г.», «с 1998 г.», «1998 г.»
        elif noun == 'году':
            case = 'dat' if preposition == 'dat' else 'prep'  # «к 1998 году» but «в 1998 году»
        else:
            case = YEAR_FORM_CASE[noun]
        words = ordinal(number, case, 'm')
        return match.group() if words is None else words + ' ' + YEAR_NOUN[case] + (_stop(text, match.end()) if noun == 'г' else '')
    text = re.sub(r'(?<![\w.,])(\d{3,4})\s?(году|годе|года|годом|год|г\.)(?!\w)', year, text)

    def century(match):
        value = _roman(match.group(1))
        noun = match.group(2).lower().rstrip('.')
        case = CENTURY_FORM_CASE.get(noun) or _case_after(text, match.start())
        words = ordinal(value, case, 'm')
        return match.group() if words is None else words + ' ' + CENTURY_NOUN[case] + (_stop(text, match.end()) if noun == 'в' else '')
    text = re.sub(r'(?<![\w])([IVXLC]{1,6})\s?(веке|веку|века|веком|век|в\.)(?!\w)', century, text)

    def suffixed(match):
        case, gender, plural_form = SUFFIX[match.group(2)]
        if match.group(2) in ('м', 'ом', 'ым'):
            case = 'prep' if match.group(2) == 'ом' or _case_after(text, match.start()) == 'prep' else 'ins'
        words = ordinal(int(match.group(1)), case, gender, plural_form)
        return match.group() if words is None else words
    text = re.sub(r'(?<![\w.,])(\d{1,4})-(' + '|'.join(sorted(SUFFIX, key=len, reverse=True)) + r')(?!\w)', suffixed, text)

    def plain(match):
        digits = match.group(2)
        if len(digits) > 1 and digits.startswith('0'):
            return match.group(1) + ' '.join(cardinal(int(char)) for char in digits)
        number = int(digits)
        gender = _gender_of_next_word(text, match.end(), number % 10) if number % 10 in (1, 2) and number % 100 not in (11, 12) else 'm'
        return match.group(1) + cardinal(number, gender)
    return re.sub(r'(?<![\w.,/:])()(\d{1,15})(?![\w]|[.,]\d|:\d)', plain, text)


def normalize(text):
    """The text with numbers and common abbreviations written as words."""
    return _numbers(_abbreviations(text))


def changes(text, limit=6):
    """(places changed, [{'before', 'after'}, ...]): sentences that normalization rewrites, for the preview before a job starts."""
    from .text_prep import sentences
    found, total = [], 0
    for sentence in (piece for paragraph in re.split(r'\n+', text) for piece in sentences(paragraph)):
        spoken = normalize(sentence)
        if spoken != sentence:
            total += 1
            if len(found) < limit:
                found.append({'before': sentence[:240], 'after': spoken[:300]})
    return total, found
