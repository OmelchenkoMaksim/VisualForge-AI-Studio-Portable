"""Conservative checks for common unsolicited instrumentation/style in prepared captions.

These bounded lexical anchors are not a general translation or semantic-proof system. Unknown terms still need
scoped model testing; known additions must not slip through merely because Gemma produced valid JSON.
"""
import re


# Source aliases include common non-English requests; English output names stay stable for audit/correction.
FEATURES = {
    'vocals': {
        'female': (r'\bfemale\b|\bwoman[\x27\u2019]?s?\s+voice\b', r'женск|женщ|девуш|\bfemale\b|\bwoman\b|f[eé]min|mujer|weiblich|\bfrau\b|femmin|女性|女声|여성'),
        'male': (r'\bmale\b|\bman[\x27\u2019]?s?\s+voice\b', r'мужск|мужчин|\bmale\b|\bman\b|masculin|hombre|m[aä]nnlich|\bmann\b|男性|男声|남성'),
        'choir': (r'\b(?:choir|choral)\b', r'\bхор(?:\b|ов)|choir|choral|coro|chor\b|合唱|コーラス|합창'),
        'operatic': (r'\b(?:operatic|opera)\b', r'оперн|опер[аыу]|operatic|opera|opéra|歌剧|オペラ|오페라'),
        'whisper': (r'\bwhisper(?:ed|ing)?\b', r'ш[её]п|whisper|susurr|chuchot|flüst|囁|耳语|속삭'),
    },
    'instruments': {
        'piano': (r'\b(?:piano|keyboard|keys)\b', r'пианин|фортепиан|клавиш|piano|klavier|钢琴|鋼琴|ピアノ|피아노'),
        'guitar': (r'\bguitars?\b', r'гитар|guitar|guitarr|guitare|gitar|吉他|ギター|기타'),
        'strings': (r'\b(?:strings|violin|cello|viola)\b', r'струн|скрип|виолон|альт|strings|violin|cello|viola|cordes|弦|提琴|ヴァイオリン|바이올린'),
        'orchestra': (r'\borchestra(?:l)?\b', r'оркестр|orchestr|orquest|オーケストラ|管弦|오케스트라'),
        'drums': (r'\b(?:drums?|percussion)\b', r'барабан|ударн|перкус|drum|percus|bater[ií]|schlagzeug|鼓|ドラム|드럼'),
        'bass': (r'\bbass\b', r'бас|bass|bajo|低音|ベース|베이스'),
        'synthesizer': (r'\b(?:synths?|synthesizers?)\b', r'синтез|synth|电子|電子|シンセ|신시'),
        'flute': (r'\bflutes?\b', r'флейт|flute|flöte|flauta|笛|フルート|플루트'),
        'saxophone': (r'\bsax(?:ophone)?\b', r'сакс|sax|サックス|萨克斯|색소폰'),
        'trumpet': (r'\btrumpet\b', r'труб|trumpet|trompet|喇叭|トランペット|트럼펫'),
        'harp': (r'\bharp\b', r'арф|harp|arpa|竖琴|ハープ|하프'),
        'accordion': (r'\baccordion\b', r'аккордеон|баян|гармон|accordion|accordéon|akkordeon|acorde[oó]n|手风琴|アコーディオン'),
    },
    'styles': {
        'folk': (r'\bfolk\b', r'фолк|народн|folk|folcl|民谣|民謡|フォーク|포크'),
        'pop': (r'\bpop\b', r'\bпоп|эстрад|\bpop|流行|ポップ|팝'),
        'rock': (r'\brock\b', r'\bрок|\brock|摇滚|ロック|록'),
        'jazz': (r'\bjazz\b', r'джаз|jazz|爵士|ジャズ|재즈'),
        'classical': (r'\bclassical\b', r'классич|classi|klassik|cl[aá]sic|古典|クラシック|클래식'),
        'ballad': (r'\bballad\b', r'баллад|ballad|balad|バラード|발라드'),
        'blues': (r'\bblues\b', r'блюз|blues|ブルース|블루스'),
        'country': (r'\bcountry\b', r'кантри|country|カントリー|컨트리'),
        'hip-hop': (r'\b(?:hip[ -]?hop|rap)\b', r'хип.?хоп|\bрэп|hip.?hop|\brap\b|嘻哈|ヒップホップ|힙합'),
        'electronic': (r'\b(?:electronic|techno|house|EDM)\b', r'электрон|техно|хаус|electro|techno|\bhouse\b|\bedm\b|电子|電子|テクノ|일렉'),
        'ambient': (r'\bambient\b(?!\s+(?:sounds?|soundscapes?|noise|environment|recording)\b)', r'эмбиент|ambient|アンビエント|앰비언트'),
    },
    'production': {
        'acoustic': (r'\bacoustic\b', r'акустич|acoustic|ac[uú]stic|akust|acoustique|アコースティック|어쿠스틱'),
        'lo-fi': (r'\blo[ -]?fi\b', r'лоу.?фай|ло.?фи|lo.?fi|ローファイ|로파이'),
        'reverb': (r'\b(?:reverb|reverberation)\b', r'реверб|эхо|reverb|echo|混响|残響|리버브'),
        'distortion': (r'\b(?:distortion|distorted)\b', r'дистор|перегруз|искаж|distort|verzerr|失真|歪み|디스토션'),
    },
}


def checked_features(request, caption):
    # Negative clauses name what should be absent, not additional instrumentation.
    positive = re.sub(r'\b(?:no|without|avoid|exclude|free of)\b[^.;,\n]*', '', caption, flags=re.I)
    # Real correction used "non-operatic", a faithful exclusion that must not be mistaken for requested opera.
    positive = re.sub(r'\bnon[- ]+[a-z]+\b', '', positive, flags=re.I)
    intent = {}
    unrequested, excluded = [], []
    for category, features in FEATURES.items():
        accepted = []
        for name, (output_pattern, source_pattern) in features.items():
            if not re.search(output_pattern, positive, re.I):
                continue
            anchors = list(re.finditer(source_pattern, request, re.I))
            if not anchors:
                unrequested.append(name)
                continue
            # An explicit exclusion must not become a positive musical feature during translation.
            allowed = [match for match in anchors if not re.search(
                r'\b(?:no|without|avoid|exclude|sin|sans|ohne|senza|без|нет|никаких)\b[\w\s-]{0,32}$',
                request[:match.start()], re.I)]
            if not allowed:
                excluded.append(name)
                continue
            accepted.append(dict(name=name, source_anchors=[match.group() for match in allowed]))
        intent[category] = accepted
    if unrequested or excluded:
        # Reporting only the first addition made real Gemma remove just piano, retaining invented guitar/folk/pop.
        # The single allowed correction must see all violations together, rather than needing a rewrite per word.
        raise ValueError('Omit ALL unrequested/excluded features: ' + ', '.join(unrequested + excluded) +
                         '. Leave unspecified choices open; do not substitute other instruments, genres or effects')
    return intent
