#!/usr/bin/env python3
"""Watch notes/**/*.md and notes/**/*.html and keep notes-html/ in sync.

Usage:
    python3 notes_watch.py [--notes-dir DIR]
                            [--output notes_html_dir] [--interval seconds]

Polls notes/**/*.md and notes/**/*.html every --interval seconds
(default 5). For each file that is new, modified, or removed since the
last scan, re-runs the matching conversion (notes_to_html.convert_note
for .md, notes_to_html.copy_handcrafted for hand-crafted .html), then
updates assets/search-index.json and pings the running server's
/api/reload.

Every notes/**/*.html file is watched and copied as hand-crafted. A
.md file with a same-name .html sibling is ignored (the .html wins).

On startup, also catches up any source file whose notes-html/*.html
output is missing or older than the source, to cover edits made while
the watcher wasn't running.

Runs until interrupted (Ctrl-C).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from notes_to_html import DEFAULT_NOTES, convert_note, copy_handcrafted
from search_index import (
    DEFAULT_OUTPUT,
    reload_server,
    remove_from_search_index,
    update_search_index,
)

State = dict[Path, float]


@dataclass
class Source:
    name: str
    root: Path
    pattern: str
    skip_if_sibling: str | None  # e.g. ".html" -> skip if a same-stem .html exists
    crumb_fn: Callable[[Path], list[str]]
    convert: Callable[[Path, Path, list[str]], None]


@dataclass
class SyncResult:
    converted_paths: list[Path] = field(default_factory=list)
    crumb_map: dict[Path, list[str]] = field(default_factory=dict)
    removed_paths: list[Path] = field(default_factory=list)


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _notes_convert(md_path: Path, out_path: Path, crumb: list[str]) -> None:
    convert_note(md_path, out_path, "/".join(crumb))


def _notes_html_convert(html_path: Path, out_path: Path, crumb: list[str]) -> None:
    copy_handcrafted(html_path, out_path, "/".join(crumb))


def make_sources(notes_dir: Path) -> list[Source]:
    sources = []
    if notes_dir.is_dir():
        notes_crumb_fn = lambda p, root=notes_dir: list(p.relative_to(root).parent.parts)
        sources.append(
            Source(
                name="notes",
                root=notes_dir,
                pattern="**/*.md",
                skip_if_sibling=".html",
                crumb_fn=notes_crumb_fn,
                convert=_notes_convert,
            )
        )
        sources.append(
            Source(
                name="notes-html",
                root=notes_dir,
                pattern="**/*.html",
                skip_if_sibling=None,
                crumb_fn=notes_crumb_fn,
                convert=_notes_html_convert,
            )
        )
    return sources


def scan(source: Source) -> State:
    """Snapshot mtime for every file matching source.pattern."""
    state: State = {}
    for path in source.root.glob(source.pattern):
        if source.skip_if_sibling and path.with_suffix(source.skip_if_sibling).is_file():
            continue
        state[path] = path.stat().st_mtime
    return state


def sync(old: State, new: State, source: Source, out_dir: Path, results: SyncResult) -> None:
    """Reconvert changed/new files and remove output for deleted ones."""
    for path, mtime in new.items():
        if old.get(path) == mtime:
            continue
        rel = path.relative_to(source.root)
        crumb = source.crumb_fn(path)
        out_path = out_dir / f"{path.stem}.html"
        try:
            source.convert(path, out_path, crumb)
            results.converted_paths.append(out_path)
            results.crumb_map[out_path] = crumb
            _log(f"updated: {source.name}/{rel} -> {out_path.name}")
        except subprocess.CalledProcessError as e:
            _log(f"ERROR converting {source.name}/{rel}: {e.stderr.strip()[:200]}")

    current_stems = {p.stem for p in new}
    for path in old:
        if path in new or path.stem in current_stems:
            continue
        rel = path.relative_to(source.root)
        out_path = out_dir / f"{path.stem}.html"
        if out_path.is_file():
            out_path.unlink()
            results.removed_paths.append(out_path)
            _log(f"removed: {source.name}/{rel} -> {out_path.name}")


def finalize(results: SyncResult, out_dir: Path) -> None:
    if not results.converted_paths and not results.removed_paths:
        return
    # A file moved between sources (e.g. .md -> .html with the same stem)
    # within one poll cycle can show up in both lists; keep it as converted.
    removed_paths = [p for p in results.removed_paths if p not in results.converted_paths]
    updated, added = update_search_index(results.converted_paths, out_dir, crumb_map=results.crumb_map)
    removed = remove_from_search_index(removed_paths, out_dir)
    if updated or added or removed:
        _log(f"Search index: updated {updated}, added {added}, removed {removed}")
    _log("Server reloaded" if reload_server() else "Server not running, skipped reload")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--notes-dir",
        default=str(DEFAULT_NOTES),
        help="Directory of notes/**/*.md and notes/**/*.html files",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=str(DEFAULT_OUTPUT),
        help="Output directory for .html files",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds (default: 5)",
    )
    args = parser.parse_args()

    notes_dir = Path(args.notes_dir)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    sources = make_sources(notes_dir)
    if not sources:
        sys.exit(f"Notes dir not found: {notes_dir}")

    states: dict[str, State] = {}
    startup = SyncResult()
    for source in sources:
        state = scan(source)
        states[source.name] = state
        _log(f"Watching {source.name}: {source.root} ({len(state)} files)")

        stale = {
            p
            for p, mtime in state.items()
            if not (out_dir / f"{p.stem}.html").is_file()
            or (out_dir / f"{p.stem}.html").stat().st_mtime < mtime
        }
        if stale:
            _log(f"Catching up {len(stale)} stale file(s) in {source.name}")
            sync({p: fp for p, fp in state.items() if p not in stale}, state, source, out_dir, startup)

    finalize(startup, out_dir)
    _log(f"Polling every {args.interval:g}s")

    try:
        while True:
            time.sleep(args.interval)
            results = SyncResult()
            for source in sources:
                try:
                    new_state = scan(source)
                except OSError as e:
                    _log(f"ERROR scanning {source.name} ({source.root}): {e}")
                    continue
                if new_state != states[source.name]:
                    sync(states[source.name], new_state, source, out_dir, results)
                    states[source.name] = new_state
            finalize(results, out_dir)
    except KeyboardInterrupt:
        _log("Stopped")


if __name__ == "__main__":
    main()
