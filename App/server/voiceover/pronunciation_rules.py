"""Built-in pronunciation rules: a different spelling of a word that makes an engine read it correctly. Standard library only.

The rules live in `App/config/voiceover-pronunciation.json`, each one measured (see docs/VisualForge-Voiceover-Guide.md): a word an engine garbles («Подарок»
read as «По дорог…» by Qwen3-TTS) is written the way that engine reads it. A rule names the engines it is for, because a respelling
that rescues one engine can hurt another. Matching ignores case and keeps it: «Подарок» becomes «Падарок», «подарки» becomes «падарки».
A `stem` rule matches the beginning of a word and keeps its ending, a `word` rule matches the whole word.

The user's own dictionary («word=how to read it») is separate and acts on the text as written, before the numbers are spoken.
"""
import json
from pathlib import Path
import re

RULES_FILE = Path(__file__).resolve().parents[2] / 'config/voiceover-pronunciation.json'


def load_rules(path=RULES_FILE):
    """The rules of the file, checked; a damaged file raises ValueError instead of silently switching the dictionary off."""
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    rules = data.get('rules')
    if data.get('version') != 1 or not isinstance(rules, list):
        raise ValueError('voiceover-pronunciation.json: unsupported format')
    for rule in rules:
        kind = rule.get('kind', 'word')
        if kind not in ('word', 'stem') or not isinstance(rule.get('match'), str) or not rule['match'] or not isinstance(rule.get('replace'), str):
            raise ValueError('voiceover-pronunciation.json: invalid rule %r' % (rule,))
        if not (isinstance(rule.get('engines', []), list) and all(isinstance(item, str) for item in rule.get('engines', []))):
            raise ValueError('voiceover-pronunciation.json: invalid engines in %r' % (rule,))
    return rules


def for_engine(rules, engine):
    """The rules that apply to one engine (a rule without `engines` applies to all of them)."""
    return [rule for rule in rules if not rule.get('engines') or engine in rule['engines']]


def _pattern(rule):
    ending = r'' if rule.get('kind') == 'stem' else r'(?!\w)'
    return re.compile(r'(?<!\w)' + re.escape(rule['match']) + ending, re.IGNORECASE)


def _like(matched, replacement):
    if len(matched) > 1 and matched.isupper():
        return replacement.upper()
    return replacement[:1].upper() + replacement[1:] if matched[:1].isupper() else replacement


def apply_rules(text, rules):
    for rule in rules:
        # `keep_case: false` writes the replacement as given: measured for «Подарок», Qwen3-TTS reads the lower-case
        # respelling at the start of a sentence more often than the capitalized one, and the engine does not need the capital.
        keep = rule.get('keep_case', True)
        text = _pattern(rule).sub(lambda match, r=rule['replace'], k=keep: _like(match.group(), r) if k else r, text)
    return text
