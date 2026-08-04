#!/usr/bin/env python3
"""Update assets/search-index.json entries for chats/*.md -> notes-html/*.html.

For every chats/*.md file, re-extracts {title, body} from its converted
notes-html/<stem>.html and either updates the matching index entry in place
(preserving its existing "crumb") or appends a new entry with crumb=["chats"]
if no entry for that path exists yet.
"""

from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

_ICLOUD_KB = (
    Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/workspace/knowledge"
)
CHATS_DIR = _ICLOUD_KB / "chats"
NOTES_HTML_DIR = Path(__file__).resolve().parent / "notes-html"
INDEX_PATH = Path(__file__).resolve().parent / "assets/search-index.json"

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_CATEGORY_RE = re.compile(r'<meta name="category" content="([^"]*)"\s*/?>')
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_BODY_RE = re.compile(r"<body[^>]*>(.*)</body>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_OUTLINK_RE = re.compile(r'href="/notes-html/([^"#\s]+\.html)"')

BODY_LIMIT = 4000


def extract_title_body(html_path: Path) -> tuple[str, str]:
    text = html_path.read_text(encoding="utf-8")

    m = _TITLE_RE.search(text)
    title = html.unescape(m.group(1).strip()) if m else html_path.stem

    m = _BODY_RE.search(text)
    body_html = m.group(1) if m else text
    body_html = _SCRIPT_STYLE_RE.sub(" ", body_html)
    body_text = _TAG_RE.sub(" ", body_html)
    body_text = html.unescape(body_text)
    body_text = _WS_RE.sub(" ", body_text).strip()
    return title, body_text[:BODY_LIMIT]


def extract_outlinks(html_path: Path) -> list[str]:
    """Return deduplicated /notes-html/... paths that this note links to."""
    try:
        content = html_path.read_text(encoding="utf-8")
        seen: dict[str, None] = {}
        for m in _OUTLINK_RE.finditer(content):
            seen[f"/notes-html/{m.group(1)}"] = None
        return list(seen)
    except OSError:
        return []


def extract_category(html_path: Path) -> str | None:
    """Read <meta name="category" content="paper|note">, if notes_to_html.py added one.

    Returns None for chat-converted HTML, which has no such tag."""
    text = html_path.read_text(encoding="utf-8")
    m = _CATEGORY_RE.search(text)
    return html.unescape(m.group(1)) if m else None


def main() -> None:
    backup = INDEX_PATH.with_suffix(".json.bak")
    shutil.copy2(INDEX_PATH, backup)
    print(f"Backed up index to {backup}")

    with open(INDEX_PATH, encoding="utf-8") as f:
        index: list[dict] = json.load(f)

    by_path = {e["path"]: e for e in index}

    md_files = sorted(CHATS_DIR.glob("*.md"))
    print(f"Found {len(md_files)} chat markdown file(s)")

    updated = added = 0
    for md_path in md_files:
        html_path = NOTES_HTML_DIR / f"{md_path.stem}.html"
        if not html_path.is_file():
            print(f"  WARNING missing html: {html_path.name}")
            continue

        target_path = f"/notes-html/{md_path.stem}.html"
        title, body = extract_title_body(html_path)

        entry = by_path.get(target_path)
        if entry is not None:
            entry["title"] = title
            entry["body"] = body
            updated += 1
        else:
            new_entry = {
                "title": title,
                "path": target_path,
                "body": body,
                "crumb": ["chats"],
            }
            index.append(new_entry)
            by_path[target_path] = new_entry
            added += 1

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"Updated: {updated}  Added: {added}  Total entries: {len(index)}")


if __name__ == "__main__":
    main()
