#!/usr/bin/env python3
"""Shared assets/search-index.json sync helpers for *_to_html.py converters."""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path

from update_chat_index import INDEX_PATH, NOTES_HTML_DIR as DEFAULT_OUTPUT, extract_title_body

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
    """Update assets/search-index.json entries for newly converted HTML files."""
    if out_dir != DEFAULT_OUTPUT or not converted_paths:
        return 0, 0

    backup = INDEX_PATH.with_suffix(".json.bak")
    shutil.copy2(INDEX_PATH, backup)

    with open(INDEX_PATH, encoding="utf-8") as f:
        index: list[dict] = json.load(f)
    by_path = {e["path"]: e for e in index}

    updated = added = 0
    for html_path in converted_paths:
        target_path = f"/notes-html/{html_path.name}"
        title, body = extract_title_body(html_path)
        crumb = (crumb_map or {}).get(html_path, default_crumb if default_crumb is not None else ["chats"])
        entry = by_path.get(target_path)
        if entry is not None:
            entry["title"] = title
            entry["body"] = body
            entry["crumb"] = crumb
            updated += 1
        else:
            new_entry = {"title": title, "path": target_path, "body": body, "crumb": crumb}
            index.append(new_entry)
            by_path[target_path] = new_entry
            added += 1

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    return updated, added


def remove_from_search_index(removed_paths: list[Path], out_dir: Path) -> int:
    """Remove assets/search-index.json entries for HTML files that no longer exist."""
    if out_dir != DEFAULT_OUTPUT or not removed_paths:
        return 0

    backup = INDEX_PATH.with_suffix(".json.bak")
    shutil.copy2(INDEX_PATH, backup)

    with open(INDEX_PATH, encoding="utf-8") as f:
        index: list[dict] = json.load(f)

    targets = {f"/notes-html/{p.name}" for p in removed_paths}
    new_index = [e for e in index if e["path"] not in targets]
    removed = len(index) - len(new_index)

    if removed:
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
