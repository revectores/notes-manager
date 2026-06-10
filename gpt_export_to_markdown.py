#!/usr/bin/env python3
"""Convert GPT export conversations to note-style Markdown files.

Usage:
    python3 gpt_export_to_markdown.py <export_dir> [--output <chats_dir>]

Reads all conversations-*.json from the export directory and writes
DATE-TITLE.md files (matching the chat-export markdown format used in
chats/, with `**Link:**` metadata and `## Prompt:` / `## Response:`
sections) to the output directory.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from gpt_export_to_notes import (
    DEFAULT_EXPORT,
    _BRACKET_CITE_RE,
    _model_label,
    extract_messages,
    slugify,
    ts_date,
    ts_display,
)

_ICLOUD_KB = (
    Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/workspace/knowledge"
)
DEFAULT_CHATS = _ICLOUD_KB / "chats"


# ── markdown template ─────────────────────────────────────────────────────────


def build_markdown(
    conv_id: str,
    title: str,
    create_ts: float | None,
    update_ts: float | None,
    messages: list[dict],
    exported: str,
    sources: list[dict] | None = None,
) -> str:
    chat_url = f"https://chatgpt.com/c/{conv_id}"

    lines: list[str] = [f"# {title}", ""]
    lines += [
        f"**Created:** {ts_display(create_ts)}  ",
        f"**Updated:** {ts_display(update_ts)}  ",
        f"**Exported:** {exported}  ",
        f"**Link:** [{chat_url}]({chat_url})  ",
        f"**Messages:** {len(messages)}",
        "",
    ]

    for msg in messages:
        text = _BRACKET_CITE_RE.sub(r"<sup>[\1]</sup>", msg["text"]).strip()
        if msg["role"] == "user":
            label = "## Prompt:"
        else:
            label = f"## Response ({_model_label(msg.get('model'))}):"
        lines += [label, "", text, ""]

    if sources:
        lines.append("## References")
        lines.append("")
        for s in sources:
            n = s["num"]
            label = s["title"] or s["url"]
            anchor = f'<a id="ref{n}"></a>'
            if s["url"]:
                lines.append(f"{anchor}[{n}] [{label}]({s['url']})")
            else:
                lines.append(f"{anchor}[{n}] {label}")
            lines.append("")

    return "\n".join(lines)


# ── per-conversation processing ───────────────────────────────────────────────

_HAND_WRITTEN = re.compile(r"chatgpt\.com/c/|^## Prompt:", re.M)


def _is_hand_written(text: str) -> bool:
    """True if this file is a genuine hand-written note (no GPT link/sections)."""
    return not _HAND_WRITTEN.search(text)


_CONV_ID_RE = re.compile(r"chatgpt\.com/c/([0-9a-f-]{36})")


def _delete_stale_dupes(out_dir: Path, conv_id: str, keep: Path) -> None:
    """Delete any other Markdown file in out_dir that contains the same conv_id."""
    for p in out_dir.glob("*.md"):
        if p == keep:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = _CONV_ID_RE.search(text)
        if m and m.group(1) == conv_id:
            p.unlink()


def process_conversation(
    conv: dict,
    out_dir: Path,
    exported: str,
    seen_paths: set[Path],
) -> str | None:
    """Convert one conversation dict to a Markdown file. Returns filename or None.

    Overwrites old bad-format exports unconditionally.
    Only creates a -N suffix when the target slot is a genuine hand-written note
    or was already claimed by a different conversation in this run.
    """
    title = (conv.get("title") or "Untitled").strip()
    conv_id = conv.get("id") or conv.get("conversation_id", "")
    create_ts: float | None = conv.get("create_time")
    update_ts: float | None = conv.get("update_time")
    current_node: str | None = conv.get("current_node")
    mapping: dict = conv.get("mapping") or {}

    if not current_node or not mapping:
        return None

    messages, sources = extract_messages(mapping, current_node)
    if not messages:
        return None

    date = ts_date(create_ts) or "0000-00-00"
    slug = slugify(title)
    base_stem = f"{date}-{slug}"
    ideal = out_dir / f"{base_stem}.md"

    out_path = ideal
    if ideal in seen_paths:
        # Another conversation in this run already claimed the ideal slot.
        i = 2
        while out_path in seen_paths:
            out_path = out_dir / f"{base_stem}-{i}.md"
            i += 1
    elif ideal.exists():
        text = ideal.read_text(encoding="utf-8", errors="ignore")
        if _is_hand_written(text):
            # Genuine hand-written note — find the suffix slot that either
            # already belongs to this conversation or is the next free one.
            i = 2
            while True:
                cand = out_dir / f"{base_stem}-{i}.md"
                if cand in seen_paths:
                    i += 1
                    continue
                if not cand.exists():
                    out_path = cand
                    break
                cand_text = cand.read_text(encoding="utf-8", errors="ignore")
                if conv_id and f"chatgpt.com/c/{conv_id}" in cand_text:
                    out_path = cand  # reclaim our own previous slot
                    break
                if not _is_hand_written(cand_text):
                    # Old bad-format or prior GPT export — overwrite this slot.
                    out_path = cand
                    break
                i += 1
        # else: old bad-format or prior GPT export → overwrite ideal (out_path = ideal)

    seen_paths.add(out_path)
    content = build_markdown(conv_id, title, create_ts, update_ts, messages, exported, sources)
    out_path.write_text(content, encoding="utf-8")

    # Delete any stale duplicate files for this conversation (same conv_id, different path).
    if conv_id:
        _delete_stale_dupes(out_dir, conv_id, out_path)

    return out_path.name


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "export_dir",
        nargs="?",
        default=str(DEFAULT_EXPORT),
        help="Path to the GPT export directory",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=str(DEFAULT_CHATS),
        help="Markdown output directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print filenames without writing",
    )
    args = parser.parse_args()

    export_dir = Path(args.export_dir)
    out_dir = Path(args.output)

    if not export_dir.is_dir():
        sys.exit(f"Export directory not found: {export_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    json_files = sorted(export_dir.glob("conversations-*.json"))
    if not json_files:
        sys.exit("No conversations-*.json files found in export directory.")

    print(f"Found {len(json_files)} conversation file(s)")
    print(f"Output directory: {out_dir}")

    exported = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    seen_paths: set[Path] = set()
    total = converted = skipped = 0

    for json_path in json_files:
        print(f"  Processing {json_path.name} …", end=" ", flush=True)
        with open(json_path, encoding="utf-8") as f:
            conversations: list[dict] = json.load(f)

        file_count = 0
        for conv in conversations:
            total += 1
            filename = None if args.dry_run else process_conversation(
                conv, out_dir, exported, seen_paths
            )
            if filename:
                converted += 1
                file_count += 1
            else:
                skipped += 1

        print(f"{file_count} notes written")

    print(f"\nDone: {converted} notes written, {skipped} skipped (empty/no messages)")
    print(f"Total conversations: {total}")


if __name__ == "__main__":
    main()
