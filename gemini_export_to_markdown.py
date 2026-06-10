#!/usr/bin/env python3
"""Convert a Gemini Apps Activity export (Google Takeout) to note-style Markdown.

Usage:
    python3 gemini_export_to_markdown.py <takeout_dir> [--output <chats_dir>]

Reads "My Activity/Gemini Apps/MyActivity.html" from a Google Takeout export
and writes one DATE-TITLE.md file per activity entry to the output directory
(matching the chat-export markdown format used in chats/).

Unlike ChatGPT's export, Gemini Apps Activity has no conversation threading —
each entry is a standalone record:

  - "Prompted ..." / "Added chat from link: ..." -> a single prompt/response
    pair, written with `## Prompt:` / `## Response (Gemini):` sections.
  - "Created Gemini Canvas titled ..." -> a generated artifact (code, quiz,
    report, ...), written with a `## Response (Gemini Canvas):` section.

"Used Gemini Apps" / "Cleared previous feedback" entries carry no content
and are skipped.
"""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from gpt_export_to_notes import slugify

_ICLOUD_KB = (
    Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/workspace/knowledge"
)
DEFAULT_CHATS = _ICLOUD_KB / "chats"
DEFAULT_EXPORT = Path.home() / "Downloads/Takeout"

_SOURCE_LABEL = "Gemini Apps Activity (Google Takeout)"
_MARKER = f"**Source:** {_SOURCE_LABEL}"


# ── activity-entry parsing ──────────────────────────────────────────────────

_CONTENT_RE = re.compile(
    r'<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">(.*?)'
    r'</div><div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1 mdl-typography--text-right">',
    re.DOTALL,
)
_TS_RE = re.compile(r"[A-Za-z]{3} \d{1,2}, \d{4}, \d{1,2}:\d{2}:\d{2}\s*[AP]M\s+[A-Z]{2,5}")
_TS_PARSE_RE = re.compile(r"([A-Za-z]{3} \d{1,2}, \d{4}, \d{1,2}:\d{2}:\d{2})\s*([AP]M)")
_PROMPT_PREFIX_RE = re.compile(r"^(?:Prompted|Added chat from link:)\s*")
_CANVAS_PREFIX_RE = re.compile(r"^Created Gemini Canvas titled\s*")
_BR_RE = re.compile(r"<br\s*/?>")
_TRAILING_BR_RE = re.compile(r"(?:<br\s*/?>\s*)+$")
_LEADING_BR_RE = re.compile(r"^(?:<br\s*/?>\s*)+")


def _norm(s: str) -> str:
    """Collapse Google's non-breaking/narrow-no-break spaces to plain spaces."""
    return s.replace("\xa0", " ").replace(" ", " ")


def _parse_ts(ts_str: str) -> datetime:
    m = _TS_PARSE_RE.search(_norm(ts_str))
    return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%b %d, %Y, %I:%M:%S %p")


def ts_display(dt: datetime) -> str:
    return dt.strftime("%Y/%m/%d %H:%M:%S")


def ts_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def parse_entries(activity_html: str) -> list[dict]:
    """Extract qa/canvas entries from MyActivity.html, in document order."""
    entries: list[dict] = []

    for raw in _CONTENT_RE.findall(activity_html):
        c = _norm(raw)

        if c.startswith("Prompted") or c.startswith("Added chat from link:"):
            m = _TS_RE.search(c)
            if not m:
                continue

            prompt_html = _PROMPT_PREFIX_RE.sub("", c[: m.start()], count=1)
            prompt_html = _LEADING_BR_RE.sub("", prompt_html)
            prompt_html = _TRAILING_BR_RE.sub("", prompt_html)
            prompt_text = html.unescape(_BR_RE.sub("\n", prompt_html)).strip()

            response_html = c[m.end():]
            response_html = _LEADING_BR_RE.sub("", response_html)
            response_html = _TRAILING_BR_RE.sub("", response_html)

            entries.append(
                {
                    "kind": "qa",
                    "ts": _parse_ts(m.group(0)),
                    "prompt": prompt_text or "(no text)",
                    "prompt_html": prompt_html,
                    "response_html": response_html,
                }
            )

        elif c.startswith("Created Gemini Canvas titled"):
            body = _CANVAS_PREFIX_RE.sub("", c, count=1)
            ms = list(_TS_RE.finditer(body))
            if not ms:
                continue

            before_ts = _TRAILING_BR_RE.sub("", body[: ms[-1].start()])
            title_part, sep, rest = before_ts.partition("<br>")
            if sep:
                title = html.unescape(title_part).strip()
                content_html = rest
            else:
                title = ""
                content_html = before_ts

            content = html.unescape(_BR_RE.sub("\n", content_html)).strip()

            entries.append(
                {
                    "kind": "canvas",
                    "ts": _parse_ts(ms[-1].group(0)),
                    "title": title,
                    "content": content,
                }
            )

        # "Used Gemini Apps" / "Cleared previous feedback" / etc -> skip

    return entries


# ── HTML -> Markdown for prompt/response bodies ─────────────────────────────


def _html_to_markdown(fragment: str) -> str:
    fragment = fragment.strip()
    if not fragment:
        return ""
    result = subprocess.run(
        ["pandoc", "-f", "html+tex_math_dollars", "-t", "markdown", "--wrap=none"],
        input=fragment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


# ── titles ───────────────────────────────────────────────────────────────────


def _entry_title(text: str, max_len: int = 70) -> str:
    first_line = text.strip().splitlines()[0] if text.strip() else "Untitled"
    if len(first_line) > max_len:
        return first_line[:max_len].rstrip() + "…"
    return first_line


_IMAGE_TITLE_RE = re.compile(r"^\[Image of (.+?)\]")
_IMAGE_CONTENT_RE = re.compile(r"^http://googleusercontent\.com/image_content/(\d+)")
_FENCE_RE = re.compile(r"^```(\w*)")


def _canvas_title(entry: dict) -> str:
    if entry["title"]:
        return entry["title"]
    content = entry["content"]
    if m := _IMAGE_TITLE_RE.match(content):
        return f"Image of {m.group(1)}"
    if m := _IMAGE_CONTENT_RE.match(content):
        return f"Canvas image {m.group(1)}"
    if m := _FENCE_RE.match(content):
        return f"Canvas {m.group(1) or 'code'}"
    return _entry_title(content, max_len=60)


# ── markdown template ─────────────────────────────────────────────────────────


def build_markdown(entry: dict, exported: str) -> tuple[str, str]:
    """Returns (title, markdown_content)."""
    if entry["kind"] == "qa":
        title = _entry_title(entry["prompt"])
        prompt_md = _html_to_markdown(entry["prompt_html"]) or entry["prompt"]
        body = _html_to_markdown(entry["response_html"])
        section = "## Response (Gemini):"
        messages = 2
        main = ["## Prompt:", "", prompt_md, "", section, "", body, ""]
    else:
        title = _canvas_title(entry)
        messages = 1
        main = ["## Response (Gemini Canvas):", "", entry["content"], ""]

    # Escape titles that look like raw markup (e.g. a pasted HTML document)
    # so pandoc doesn't treat the heading as containing real HTML elements.
    heading = html.escape(title, quote=False) if title.lstrip().startswith("<") else title

    lines: list[str] = [f"# {heading}", ""]
    lines += [
        f"**Created:** {ts_display(entry['ts'])}  ",
        f"**Exported:** {exported}  ",
        f"{_MARKER}  ",
        f"**Messages:** {messages}",
        "",
    ]
    lines += main
    return title, "\n".join(lines)


# ── per-entry processing ────────────────────────────────────────────────────


def process_entry(
    entry: dict, out_dir: Path, exported: str, seen_paths: set[Path]
) -> str | None:
    title, content = build_markdown(entry, exported)

    date = ts_date(entry["ts"])
    slug = slugify(title) or "untitled"
    base_stem = f"{date}-{slug}"
    ideal = out_dir / f"{base_stem}.md"

    out_path = ideal
    if ideal in seen_paths:
        i = 2
        while out_path in seen_paths:
            out_path = out_dir / f"{base_stem}-{i}.md"
            i += 1
    elif ideal.exists() and _MARKER not in ideal.read_text(encoding="utf-8", errors="ignore"):
        # Slot taken by a hand-written note -> find next free/own slot.
        i = 2
        while True:
            cand = out_dir / f"{base_stem}-{i}.md"
            if cand in seen_paths:
                i += 1
                continue
            if not cand.exists() or _MARKER in cand.read_text(encoding="utf-8", errors="ignore"):
                out_path = cand
                break
            i += 1

    seen_paths.add(out_path)
    out_path.write_text(content, encoding="utf-8")
    return out_path.name


# ── main ──────────────────────────────────────────────────────────────────────


def find_activity_html(export_dir: Path) -> Path:
    for cand in (
        export_dir / "MyActivity.html",
        export_dir / "My Activity" / "Gemini Apps" / "MyActivity.html",
    ):
        if cand.is_file():
            return cand
    sys.exit(f"MyActivity.html not found under {export_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "export_dir",
        nargs="?",
        default=str(DEFAULT_EXPORT),
        help="Path to the Google Takeout export directory",
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
    activity_html = find_activity_html(export_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading {activity_html}")
    entries = parse_entries(activity_html.read_text(encoding="utf-8"))
    qa = sum(1 for e in entries if e["kind"] == "qa")
    canvas = sum(1 for e in entries if e["kind"] == "canvas")
    print(f"Found {len(entries)} entries ({qa} prompt/response, {canvas} canvas)")
    print(f"Output directory: {out_dir}")

    exported = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    seen_paths: set[Path] = set()
    written = errors = 0

    for entry in entries:
        if args.dry_run:
            title = (
                _entry_title(entry["prompt"])
                if entry["kind"] == "qa"
                else _canvas_title(entry)
            )
            print(f"  [{entry['kind']}] {ts_date(entry['ts'])}-{slugify(title)}.md")
            continue
        try:
            name = process_entry(entry, out_dir, exported, seen_paths)
            written += 1
        except subprocess.CalledProcessError as e:
            errors += 1
            print(f"  ERROR: {e.stderr.strip()[:200]}")

    print(f"\nDone: {written} notes written, {errors} errors, {len(entries)} total entries")


if __name__ == "__main__":
    main()
