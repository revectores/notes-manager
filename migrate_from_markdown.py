#!/usr/bin/env python3
"""One-shot migration off the Markdown knowledge base (August 2026).

Until now a note was Markdown in ~/…/CloudDocs/workspace/knowledge/notes/,
pandoc turned it into a flat notes-html/*.html build directory, and
assets/search-index.json held the search index — including, for some notes, a
filing that had been corrected by hand after generation and existed nowhere
else.

This moves the generated HTML into the store this app now reads directly,
and folds that index-only filing back into the file it describes, so each note
becomes self-describing and the index can be rebuilt from the notes alone:

  - every note gets <meta name="classification"> matching its index crumb,
  - <meta name="category"> is dropped — it came from Markdown front matter,
    and the papers it distinguished moved to paper-notes in August 2026,
  - images and any other files are copied across untouched.

Nothing is converted here: the build output was already in sync with every
Markdown source when this ran, so pandoc is not needed and never will be again.

Usage:
    python3 migrate_from_markdown.py --dry-run
    python3 migrate_from_markdown.py
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE = BASE_DIR / "notes-html"
DEFAULT_INDEX = BASE_DIR / "assets/search-index.json"
DEFAULT_STORE = (
    Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/database/knowledge-manager/notes"
)

_CLASSIFICATION_RE = re.compile(r'<meta name="classification" content="([^"]*)"\s*/?>')
_CATEGORY_RE = re.compile(r'[ \t]*<meta name="category" content="[^"]*"\s*/?>\n?')
_HEAD_RE = re.compile(r"<head>", re.IGNORECASE)


def crumbs_by_file(index_path: Path) -> dict[str, list[str]]:
    """Map each note filename to the crumb the search index files it under."""
    entries = json.loads(index_path.read_text(encoding="utf-8"))
    crumbs: dict[str, list[str]] = {}
    for entry in entries:
        crumb = entry.get("crumb", [])
        paths = entry["langs"].values() if entry.get("langs") else [entry["path"]]
        for p in paths:
            crumbs[Path(p).name] = crumb
    return crumbs


def rewrite_meta(text: str, crumb: list[str]) -> tuple[str, str]:
    """Set the classification meta tag to crumb and drop the category one.

    Returns (new text, what changed) for reporting."""
    text = _CATEGORY_RE.sub("", text)
    wanted = "/".join(crumb)
    m = _CLASSIFICATION_RE.search(text)
    tag = f'<meta name="classification" content="{html.escape(wanted)}" />'

    if m is None:
        return _HEAD_RE.sub(f"<head>\n  {tag}", text, count=1), f"filed under {wanted!r}"
    current = html.unescape(m.group(1))
    if current == wanted:
        return text, ""
    return text[: m.start()] + tag + text[m.end() :], f"refiled {current!r} -> {wanted!r}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Generated notes-html directory")
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="Existing search-index.json")
    parser.add_argument("--store", default=str(DEFAULT_STORE), help="Destination note store")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing anything")
    args = parser.parse_args()

    source, index_path, store = Path(args.source), Path(args.index), Path(args.store)
    if not source.is_dir():
        sys.exit(f"Source not found: {source}")
    if not index_path.is_file():
        sys.exit(f"Search index not found: {index_path}")

    crumbs = crumbs_by_file(index_path)
    notes = sorted(source.glob("*.html"))
    others = sorted(p for p in source.rglob("*") if p.is_file() and p.parent != source)
    loose = sorted(p for p in source.glob("*") if p.is_file() and p.suffix != ".html")

    print(f"{len(notes)} note(s), {len(loose)} loose file(s), {len(others)} file(s) in subdirectories")
    print(f"Store: {store}")

    if not args.dry_run:
        store.mkdir(parents=True, exist_ok=True)

    unindexed, changes = [], []
    for note in notes:
        text = note.read_text(encoding="utf-8")
        crumb = crumbs.get(note.name)
        if crumb is None:
            unindexed.append(note.name)
            new_text, change = text, ""
        else:
            new_text, change = rewrite_meta(text, crumb)
        if change:
            changes.append((note.name, change))
        if not args.dry_run:
            (store / note.name).write_text(new_text, encoding="utf-8")

    if not args.dry_run:
        for path in loose + others:
            dest = store / path.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)

    print(f"\nMetadata rewritten in {len(changes)} note(s):")
    for name, change in changes:
        print(f"  {name}: {change}")
    if unindexed:
        print(f"\n{len(unindexed)} note(s) absent from the index, copied with their existing filing:")
        for name in unindexed:
            print(f"  {name}")
    print("\nDry run — nothing written." if args.dry_run else f"\nWrote {len(notes)} note(s) to {store}")


if __name__ == "__main__":
    main()
