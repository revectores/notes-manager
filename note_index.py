#!/usr/bin/env python3
"""Read the note store and build the search index from it.

A note is one standalone HTML file in the store directory, and the store is the
only source of truth — there is no Markdown behind it and no persisted index
beside it. Everything the index needs is read out of the file itself:

  - the title from <title>,
  - the body text from <body> with tags stripped, for search and snippets,
  - the filing from <meta name="classification" content="a/b/c">, which becomes
    the entry's crumb, so a note is re-filed by editing the note,
  - the links to other notes from href="/notes-html/...", which give backlinks.

Reading a store of a few hundred notes takes well under a second, so a change
anywhere just rebuilds the whole index rather than patching entries in place.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from note_lang import pick_default_lang, split_lang_suffix

# Every note links to its siblings as href="/notes-html/<name>.html", so this
# prefix is part of the stored content and outlives the build directory it was
# named after.
URL_PREFIX = "/notes-html/"

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_CLASSIFICATION_RE = re.compile(r'<meta name="classification" content="([^"]*)"\s*/?>')
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_BODY_RE = re.compile(r"<body[^>]*>(.*)</body>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_OUTLINK_RE = re.compile(r'href="' + URL_PREFIX + r'([^"#\s]+\.html)"')

BODY_LIMIT = 4000


def read_note(path: Path) -> tuple[str, str, list[str], list[str]]:
    """Return (title, body text, crumb, outlinks) for one stored note."""
    text = path.read_text(encoding="utf-8", errors="replace")

    m = _TITLE_RE.search(text)
    title = html.unescape(m.group(1).strip()) if m else path.stem

    m = _BODY_RE.search(text)
    body_html = m.group(1) if m else text
    body_html = _SCRIPT_STYLE_RE.sub(" ", body_html)
    body = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", body_html))).strip()

    m = _CLASSIFICATION_RE.search(text)
    crumb = [seg for seg in html.unescape(m.group(1)).split("/") if seg] if m else []

    outlinks = list(dict.fromkeys(URL_PREFIX + m.group(1) for m in _OUTLINK_RE.finditer(text)))

    return title, body[:BODY_LIMIT], crumb, outlinks


def snapshot(store: Path) -> dict[str, float]:
    """Cheap stat-only fingerprint of the store, used to detect edits."""
    try:
        return {p.name: p.stat().st_mtime for p in store.glob("*.html")}
    except OSError:
        return {}


def build_index(store: Path) -> list[dict]:
    """Build the full index from the store's top-level *.html files.

    Files whose stem ends in a recognized "-<lang>" suffix (see note_lang.py)
    are grouped into one entry with a "langs" map, e.g. "transformer-en.html"
    and "transformer-cn.html" become a single entry whose title, body and path
    come from the preferred language.
    """
    index: list[dict] = []
    by_base: dict[str, dict] = {}

    for path in sorted(store.glob("*.html")):
        base, lang = split_lang_suffix(path.stem)
        url = URL_PREFIX + path.name
        title, body, crumb, outlinks = read_note(path)

        entry = by_base.get(base) if lang is not None else None
        if entry is None:
            entry = {"title": title, "path": url, "body": body, "crumb": crumb}
            if outlinks:
                entry["outlinks"] = outlinks
            if lang is not None:
                entry["langs"] = {lang: url}
                by_base[base] = entry
            index.append(entry)
            continue

        entry["langs"][lang] = url
        if outlinks:
            entry["outlinks"] = list(dict.fromkeys(entry.get("outlinks", []) + outlinks))
        if lang == pick_default_lang(entry["langs"]):
            entry["title"], entry["body"], entry["path"], entry["crumb"] = title, body, url, crumb

    return index
