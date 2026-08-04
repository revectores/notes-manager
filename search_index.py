#!/usr/bin/env python3
"""Shared assets/search-index.json sync helpers for *_to_html.py converters."""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path

from note_lang import pick_default_lang, split_lang_suffix
from update_chat_index import (
    INDEX_PATH,
    NOTES_HTML_DIR as DEFAULT_OUTPUT,
    extract_category,
    extract_outlinks,
    extract_title_body,
)

__all__ = [
    "DEFAULT_OUTPUT",
    "INDEX_PATH",
    "update_search_index",
    "remove_from_search_index",
    "reload_server",
]


def update_search_index(
    converted_paths: list[Path],
    out_dir: Path,
    crumb_map: dict[Path, list[str]] | None = None,
    default_crumb: list[str] | None = None,
) -> tuple[int, int]:
    """Update assets/search-index.json entries for newly converted HTML files.

    Files whose stem ends in a recognized "-<lang>" suffix (see note_lang.py)
    are grouped with their siblings into a single entry with a "langs" map,
    e.g. "transformer-en.html" and "transformer-cn.html" become one entry
    {"langs": {"en": "/notes-html/transformer-en.html", "cn": "..."}}.
    """
    if out_dir != DEFAULT_OUTPUT or not converted_paths:
        return 0, 0

    backup = INDEX_PATH.with_suffix(".json.bak")
    shutil.copy2(INDEX_PATH, backup)

    with open(INDEX_PATH, encoding="utf-8") as f:
        index: list[dict] = json.load(f)

    by_path: dict[str, dict] = {}
    by_base: dict[str, dict] = {}
    for entry in index:
        langs = entry.get("langs")
        if langs:
            for lang_path in langs.values():
                by_path[lang_path] = entry
            base, _ = split_lang_suffix(Path(entry["path"]).stem)
            by_base[base] = entry
        else:
            by_path[entry["path"]] = entry

    # Pre-clear outlinks for every entry being updated so stale links are
    # removed and multi-lang variants can accumulate cleanly below.
    cleared: set[int] = set()
    for hp in converted_paths:
        e = by_path.get(f"/notes-html/{hp.name}")
        if e is not None and id(e) not in cleared:
            e.pop("outlinks", None)
            cleared.add(id(e))

    def _merge_outlinks(entry: dict, outlinks: list[str]) -> None:
        if not outlinks:
            return
        existing = entry.get("outlinks") or []
        entry["outlinks"] = list(dict.fromkeys(existing + outlinks))

    updated = added = 0
    for html_path in converted_paths:
        base, lang = split_lang_suffix(html_path.stem)
        target_path = f"/notes-html/{html_path.name}"
        title, body = extract_title_body(html_path)
        category = extract_category(html_path)
        crumb = (crumb_map or {}).get(html_path, default_crumb if default_crumb is not None else ["chats"])

        outlinks = extract_outlinks(html_path)

        entry = by_path.get(target_path)
        if entry is not None:
            entry["crumb"] = crumb
            if category is not None:
                entry["category"] = category
            _merge_outlinks(entry, outlinks)
            if lang is None:
                entry["title"] = title
                entry["body"] = body
            else:
                if lang == pick_default_lang(entry.get("langs", {lang: target_path})):
                    entry["title"] = title
                    entry["body"] = body
                    entry["path"] = target_path
            updated += 1
            continue

        if lang is not None and base in by_base:
            entry = by_base[base]
            entry.setdefault("langs", {})[lang] = target_path
            entry["crumb"] = crumb
            if category is not None:
                entry["category"] = category
            _merge_outlinks(entry, outlinks)
            by_path[target_path] = entry
            if lang == pick_default_lang(entry["langs"]):
                entry["title"] = title
                entry["body"] = body
                entry["path"] = target_path
            updated += 1
            continue

        new_entry = {"title": title, "path": target_path, "body": body, "crumb": crumb}
        if category is not None:
            new_entry["category"] = category
        if outlinks:
            new_entry["outlinks"] = outlinks
        if lang is not None:
            new_entry["langs"] = {lang: target_path}
            by_base[base] = new_entry
        index.append(new_entry)
        by_path[target_path] = new_entry
        added += 1

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    return updated, added


def remove_from_search_index(removed_paths: list[Path], out_dir: Path) -> int:
    """Remove assets/search-index.json entries for HTML files that no longer exist.

    For a multi-language entry, only the matching language is dropped from its
    "langs" map (re-pointing "title"/"body"/"path" to the next-preferred
    language if the default one was removed); the whole entry is removed only
    once its last language variant is gone.
    """
    if out_dir != DEFAULT_OUTPUT or not removed_paths:
        return 0

    backup = INDEX_PATH.with_suffix(".json.bak")
    shutil.copy2(INDEX_PATH, backup)

    with open(INDEX_PATH, encoding="utf-8") as f:
        index: list[dict] = json.load(f)

    targets = {f"/notes-html/{p.name}" for p in removed_paths}
    new_index: list[dict] = []
    removed = 0
    changed = False

    for entry in index:
        langs = entry.get("langs")
        if langs:
            remaining = {l: p for l, p in langs.items() if p not in targets}
            if not remaining:
                removed += 1
                changed = True
                continue
            if len(remaining) != len(langs):
                entry["langs"] = remaining
                if entry["path"] in targets:
                    default_lang = pick_default_lang(remaining)
                    default_path = remaining[default_lang]
                    title, body = extract_title_body(out_dir / Path(default_path).name)
                    entry["title"], entry["body"], entry["path"] = title, body, default_path
                changed = True
            new_index.append(entry)
        elif entry["path"] in targets:
            removed += 1
            changed = True
        else:
            new_index.append(entry)

    if changed:
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(new_index, f, ensure_ascii=False, indent=2)

    return removed


def reload_server() -> bool:
    """Best-effort ping to the running server's /api/reload. Returns True on success."""
    port = os.environ.get("PORT", "8024")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/reload", timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False
