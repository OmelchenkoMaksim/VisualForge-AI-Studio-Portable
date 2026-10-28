"""Stable source messages for server protocols and persisted data, shared with the browser catalogs.

The selected UI language is applied at the presentation boundary. Keeping source messages
independent of a request's language avoids changing stored job state or another window's locale.
"""
from functools import lru_cache
import json
from pathlib import Path
import re

CATALOGS = Path(__file__).resolve().parents[1] / 'strings'
KEY = re.compile(r'[a-z]+\.[a-z0-9_.]+\Z')
PARAMETER = re.compile(r'\{(\d+)\}')


@lru_cache(maxsize=16)
def _section(name):
    return json.loads((CATALOGS / (name + '.json')).read_text(encoding='utf-8'))


def source(key, *values):
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise ValueError('Invalid UI string key')
    text = _section(key.split('.')[0])[key]['ru']
    if not values:
        return text
    # Substitute once: braces inside a user's filename or text are ordinary data.
    return PARAMETER.sub(lambda match: str(values[int(match[1])]) if int(match[1]) < len(values) else match[0], text)
