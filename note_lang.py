#!/usr/bin/env python3
"""Shared helpers for multi-language notes.

A multi-language note is a group of sibling files in the store that share a
base name and differ only by a trailing "-<lang>" suffix, e.g.
"transformer-en.html" and "transformer-cn.html". They are served separately
but treated as ONE search-index entry (via its "langs" map, see
note_index.build_index) and get a language switcher on the page (see
server.py's inject_toc).
"""

from __future__ import annotations

import re

LANG_LABELS: dict[str, str] = {
    "en": "EN",
    "cn": "中文",
    "zh": "中文",
    "jp": "日本語",
    "ja": "日本語",
    "kr": "한국어",
    "ko": "한국어",
    "fr": "Français",
    "de": "Deutsch",
    "es": "Español",
    "ru": "Русский",
}

# Preference order used to pick the "default" language of a multi-language
# note (its title/body/path in the search index, and the variant opened from
# search results / the directory tree).
LANG_PRIORITY: list[str] = ["en", "zh", "cn", "ja", "jp", "ko", "kr", "fr", "de", "es", "ru"]

_LANG_SUFFIX_RE = re.compile(r"^(?P<base>.+)-(?P<lang>" + "|".join(LANG_LABELS) + r")$")


def split_lang_suffix(stem: str) -> tuple[str, str | None]:
    """Split "name-en" into ("name", "en"); ("name", None) if no recognized suffix."""
    m = _LANG_SUFFIX_RE.match(stem)
    if m:
        return m.group("base"), m.group("lang")
    return stem, None


def lang_label(lang: str) -> str:
    return LANG_LABELS.get(lang, lang.upper())


def pick_default_lang(langs: dict[str, str]) -> str:
    """Pick the preferred language code among a note's available languages."""
    for lang in LANG_PRIORITY:
        if lang in langs:
            return lang
    return sorted(langs)[0]
