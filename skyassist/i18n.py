"""Reviewed copy lookup. Every data/i18n/<lang>/*.json file is merged; no English fallback."""

import json
import string
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from types import MappingProxyType

from skyassist.models import I18nText

ROOT = Path(__file__).resolve().parent.parent / "data" / "i18n"
LRI, PDI = chr(0x2066), chr(0x2069)
ISOLATED = frozenset({"gate", "old", "flight", "code", "time", "margin", "lower", "upper", "wait", "seconds", "source",
                      "origin", "destination", "date", "flights"})
RTL = frozenset({"ar"})


@cache
def _load(lang: str) -> tuple[Mapping[str, str], str]:
    folder = ROOT / lang
    if not folder.is_dir():
        raise KeyError(lang)
    texts: dict[str, str] = {}
    reviews: set[str] = set()
    for path in sorted(folder.glob("*.json")):
        for key, value in json.loads(path.read_text(encoding="utf-8")).items():
            if key == "_review":
                reviews.add(value)
            elif key.startswith("_"):
                continue
            elif not isinstance(value, str) or key in texts:
                raise ValueError(f"{path.name}: text or key {key!r} is invalid or duplicated")
            else:
                texts[key] = value
    return MappingProxyType(texts), (reviews.pop() if len(reviews) == 1 else "unreviewed")


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def t(key: str, lang: str, /, **params: object) -> str:
    template = _load(lang)[0].get(key)
    if template is None or not _fields(template).issubset(params):
        raise KeyError(key)
    if lang in RTL:
        params = {name: f"{LRI}{value}{PDI}" if name in ISOLATED else value for name, value in params.items()}
    return template.format(**params)


def render(text: I18nText, lang: str) -> str:
    return t(text.key, lang, **text.params)


def has(key: str, lang: str) -> bool:
    return key in _load(lang)[0]


def review_status(lang: str) -> str:
    return _load(lang)[1]


def catalog(lang: str) -> Mapping[str, str]:
    return _load(lang)[0]


def languages() -> tuple[str, ...]:
    return tuple(sorted(p.name for p in ROOT.glob("*") if p.is_dir()))
