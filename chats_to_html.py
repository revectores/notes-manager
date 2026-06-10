#!/usr/bin/env python3
"""Convert chat-export Markdown files (chats/*.md) to HTML via pandoc.

Usage:
    python3 chats_to_html.py [chats_dir] [--output notes_html_dir]

Mirrors assets/convert-notes.sh: runs each .md through pandoc with the
math extensions and MathJax config, strips pandoc's auto title-block
header, sets <title> from the file's "# Title" heading, and tags the
page with <meta name="classification" content="chats">.

After conversion, also updates assets/search-index.json for the files
just converted (and pings the running server's /api/reload, if any), so
the search index stays in sync without a separate manual step.
"""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
from pathlib import Path

from search_index import DEFAULT_OUTPUT, reload_server, update_search_index

_ICLOUD_KB = (
    Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/workspace/knowledge"
)
DEFAULT_CHATS = _ICLOUD_KB / "chats"
MATHJAX_CONFIG = Path(__file__).resolve().parent / "assets/mathjax-config.html"

_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)
_TITLE_BLOCK_RE = re.compile(r'<header id="title-block-header">.*?</header>\s*', re.DOTALL)
_TITLE_TAG_RE = re.compile(r"<title>.*?</title>")
_BODY_RE = re.compile(r"(<body>\n)(.*)(\n</body>)", re.DOTALL)
_COLGROUP_RE = re.compile(r"<colgroup>.*?</colgroup>\n", re.DOTALL)
_SECTION_RE = re.compile(r'<h2[^>]*>(Prompt:|Response(?: \([^<]*\))?:|References)</h2>\n', re.DOTALL)
_RESPONSE_MODEL_RE = re.compile(r"Response(?: \((.+)\))?:")

# CSS for the prompt/response message boxes, matching gpt_export_to_notes.py's build_html.
_MSG_CSS = """\
<style>
  .msg-user, .msg-assistant { margin: 1.2rem 0; padding: 0.75rem 1rem; border-radius: 6px; }
  .msg-user { background: #f0f4f8; border-left: 3px solid #0066cc; }
  .msg-assistant { background: #f8fafc; border-left: 3px solid #1a7f37; }
  .msg-label { font-size: 0.78rem; font-weight: 600; margin-bottom: 0.4rem;
    text-transform: uppercase; letter-spacing: 0.05em; }
  .msg-user .msg-label { color: #0066cc; }
  .msg-assistant .msg-label { color: #1a7f37; }
  .msg-body > *:first-child { margin-top: 0; }
  .msg-body > *:last-child { margin-bottom: 0; }
  .chat-meta { font-size: 0.82rem; color: #999; margin-bottom: 1.5rem; }
</style>
"""


def _restyle_body(body: str) -> str:
    """Wrap Prompt:/Response:/References sections in styled divs."""
    parts = _SECTION_RE.split(body)
    preamble = parts[0].replace(
        "<p><strong>Created:</strong>",
        '<p class="chat-meta"><strong>Created:</strong>',
        1,
    )
    out = [preamble.rstrip("\n")]
    for i in range(1, len(parts), 2):
        marker = parts[i]
        content = parts[i + 1].rstrip("\n") if i + 1 < len(parts) else ""
        if marker == "Prompt:":
            out.append(
                '<div class="msg-user">\n<div class="msg-label">You</div>\n'
                f'<div class="msg-body">\n{content}\n</div>\n</div>'
            )
        elif marker == "References":
            out.append(f'<div class="references">\n{content}\n</div>')
        else:
            m = _RESPONSE_MODEL_RE.match(marker)
            label = html.escape(m.group(1)) if m and m.group(1) else "ChatGPT"
            out.append(
                f'<div class="msg-assistant">\n<div class="msg-label">{label}</div>\n'
                f'<div class="msg-body">\n{content}\n</div>\n</div>'
            )
    return "\n".join(out)


def convert_file(md_path: Path, out_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    m = _TITLE_RE.search(text)
    title = m.group(1).strip() if m else md_path.stem

    result = subprocess.run(
        [
            "pandoc", str(md_path),
            "-f", "markdown+tex_math_single_backslash+tex_math_dollars",
            "-o", "-",
            "--standalone",
            "--css", "/assets/style.css",
            "--mathjax",
            "-H", str(MATHJAX_CONFIG),
            "--metadata", f"title={md_path.stem}",
        ],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )

    out_html = _TITLE_BLOCK_RE.sub("", result.stdout)
    out_html = _COLGROUP_RE.sub("", out_html)
    title_tag = f"<title>{html.escape(title)}</title>"
    out_html = _TITLE_TAG_RE.sub(lambda _m: title_tag, out_html, count=1)
    out_html = out_html.replace(
        "<head>", '<head>\n  <meta name="classification" content="chats" />', 1
    )
    out_html = out_html.replace("</head>", _MSG_CSS + "</head>", 1)

    body_m = _BODY_RE.search(out_html)
    if body_m:
        restyled = _restyle_body(body_m.group(2))
        out_html = (
            out_html[: body_m.start()]
            + body_m.group(1) + restyled + body_m.group(3)
            + out_html[body_m.end() :]
        )

    out_path.write_text(out_html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "chats_dir",
        nargs="?",
        default=str(DEFAULT_CHATS),
        help="Directory of chat-export .md files",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=str(DEFAULT_OUTPUT),
        help="Output directory for .html files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files without converting",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Skip updating assets/search-index.json and reloading the server",
    )
    args = parser.parse_args()

    chats_dir = Path(args.chats_dir)
    out_dir = Path(args.output)
    if not chats_dir.is_dir():
        sys.exit(f"Chats directory not found: {chats_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    md_files = sorted(chats_dir.glob("*.md"))
    print(f"Found {len(md_files)} markdown file(s)")
    print(f"Output directory: {out_dir}")

    converted = errors = 0
    converted_paths: list[Path] = []
    for md_path in md_files:
        out_path = out_dir / f"{md_path.stem}.html"
        if args.dry_run:
            print(f"  {md_path.name} -> {out_path.name}")
            continue
        try:
            convert_file(md_path, out_path)
            converted += 1
            converted_paths.append(out_path)
        except subprocess.CalledProcessError as e:
            errors += 1
            print(f"  ERROR {md_path.name}: {e.stderr.strip()[:200]}")

    print(f"\nConverted: {converted}  Errors: {errors}  Total: {len(md_files)}")

    if not args.dry_run and not args.no_index:
        updated, added = update_search_index(converted_paths, out_dir)
        if updated or added:
            print(f"Search index: updated {updated}, added {added}")
            print("Server reloaded" if reload_server() else "Server not running, skipped reload")


if __name__ == "__main__":
    main()
