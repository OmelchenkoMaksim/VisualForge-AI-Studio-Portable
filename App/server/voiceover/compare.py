"""Compares the words of a source fragment with what speech recognition heard in its audio. Standard library only.

This is a check for *wrong or missing words*, not a pronunciation score: it ignores case, punctuation and the letter «ё».
Numbers are not compared at all, neither digits nor number words: recognition writes a spoken number as digits, as another number or as
words, so they say nothing reliable about the sound. The price is that a mispronounced number goes unnoticed.
"""
import difflib
import re

from .normalize import NUMBER_WORDS, normalize
from .pronunciation import apply_rules

THRESHOLD = 0.15         # more than 15 % wrong words makes a fragment suspicious
ABSOLUTE = 4             # ...and so do four wrong words, however long the fragment is
SHORT_WORDS = 4          # at most this many words: one wrong word is too coarse a signal, so the limit is higher
SHORT_THRESHOLD = 0.34


def _words(text):
    """(comparable form, written form) of the words that take part in the comparison."""
    pairs = []
    for written in re.findall(r'[a-zа-яё0-9]+', (text or '').lower()):
        comparable = written.replace('ё', 'е')
        # Number words are skipped as well as digits: the spoken text has «тысяча девятьсот девяносто восьмом» where recognition may
        # write «1998», and the two cannot be told apart reliably.
        if not comparable.isdigit() and comparable not in NUMBER_WORDS:
            pairs.append((comparable, written))
    return pairs


def tokens(text):
    return [comparable for comparable, _ in _words(text)]


def edit_distance(reference, heard):
    previous = list(range(len(heard) + 1))
    for i, word in enumerate(reference, 1):
        current = [i]
        for j, other in enumerate(heard, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (word != other)))
        previous = current
    return previous[-1]


def heard_form(heard, replacements=(), rules=()):
    """What recognition heard, written the way the source was written for the engine.

    The source went through the same steps before it was spoken: the numbers and abbreviations became words, the user's dictionary
    and the built-in rules respelled some words. Recognition writes «и т.д.» for a spoken «и так далее» and «подарок» for a spoken
    «падарок», so the heard text gets the same treatment. Russian only."""
    heard = normalize(heard)
    for old, new in replacements:
        heard = re.sub(re.escape(old), lambda match, text=new: text, heard, flags=re.IGNORECASE)
    return apply_rules(heard, rules)


def compare(reference, heard, expand=False, replacements=(), rules=()):
    """(wrong words, words in the source), numbers excluded on both sides. `expand` applies `heard_form` to the heard text."""
    if expand:
        heard = heard_form(heard, replacements, rules)
    ref = tokens(reference)
    return edit_distance(ref, tokens(heard)), len(ref)


def suspect_words(reference, heard, limit=6):
    """Words of the source that recognition did not give back, as written: candidates for the pronunciation dictionary."""
    ref, hyp = _words(reference), tokens(heard)
    found = []
    for tag, start, end, _, _ in difflib.SequenceMatcher(None, [pair[0] for pair in ref], hyp, autojunk=False).get_opcodes():
        if tag in ('replace', 'delete'):
            found.extend(written for _, written in ref[start:end] if len(written) > 2 and written not in found)
    return found[:limit]


def is_suspicious(wrong, words, heard):
    """Nothing heard at all is always suspicious; otherwise the number and the share of wrong words decide."""
    if words == 0:
        return False
    if not tokens(heard):
        return True
    if words <= SHORT_WORDS:
        return wrong / words > SHORT_THRESHOLD
    return wrong >= ABSOLUTE or wrong / words > THRESHOLD
