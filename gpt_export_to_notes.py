#!/usr/bin/env python3
"""Convert GPT export conversations to notes-manager HTML files.

Usage:
    python3 gpt_export_to_notes.py <export_dir> [--output <notes_dir>]

Reads all conversations-*.json from the export directory and writes
DATE-TITLE.html files to the notes-manager directory.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import markdown as md_lib
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.extra import ExtraExtension
from markdown.extensions.toc import TocExtension

# ── defaults ─────────────────────────────────────────────────────────────────

DEFAULT_EXPORT = Path(
    "/Users/rex/Downloads/"
    "faef4f3e12610d0812fb80dd167661d890b464660493f90ce277edf2f2c478d8"
    "-2026-06-06-10-27-53-ebbdc857cf204843a58c9f3f5f0688dc"
)
_ICLOUD_KB = (
    Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/workspace/knowledge"
)
DEFAULT_NOTES = _ICLOUD_KB / "notes-manager"

# ── timestamp helpers ─────────────────────────────────────────────────────────


def _dt(ts: float | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()


def ts_date(ts: float | None) -> str:
    dt = _dt(ts)
    return dt.strftime("%Y-%m-%d") if dt else ""


def ts_display(ts: float | None) -> str:
    dt = _dt(ts)
    return dt.strftime("%Y/%m/%d %H:%M:%S") if dt else ""


# ── filename slug ─────────────────────────────────────────────────────────────

_UNSAFE = re.compile(r'[\\/:*?"<>|]')


def slugify(title: str, max_len: int = 80) -> str:
    """Produce a filesystem-safe slug from a title."""
    slug = _UNSAFE.sub("-", title)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    return slug[:max_len]


# ── markdown conversion ───────────────────────────────────────────────────────

_MD_EXTENSIONS = [
    ExtraExtension(),           # tables, fenced code, footnotes …
    CodeHiliteExtension(guess_lang=False, noclasses=True),
    TocExtension(permalink=False),
]


def _fix_citations(text: str) -> str:
    """Normalise ChatGPT inline citation markers.

    Converts  【3†source text here】  →  <sup>[3]</sup>
    and plain 【3†source】 as well.
    """
    return re.sub(r"【(\d+)†[^】]*】", r"<sup>[\1]</sup>", text)


def md_to_html(text: str) -> str:
    text = _fix_citations(text)
    converter = md_lib.Markdown(extensions=_MD_EXTENSIONS)
    return converter.convert(text)


# ── message extraction ────────────────────────────────────────────────────────

_SKIP_ROLES = {"system", "tool"}
_SKIP_CONTENT_TYPES = {"thoughts", "reasoning_recap"}

_MODEL_LABELS: dict[str, str] = {
    "gpt-4o": "GPT-4o",
    "gpt-4o-mini": "GPT-4o mini",
    "gpt-4": "GPT-4",
    "gpt-4-turbo": "GPT-4 Turbo",
    "gpt-3.5-turbo": "GPT-3.5",
    "o1": "o1",
    "o1-mini": "o1 mini",
    "o1-preview": "o1 preview",
    "o3": "o3",
    "o3-mini": "o3 mini",
    "o4-mini": "o4 mini",
    "gpt-5": "GPT-5",
    "gpt-5-1": "GPT-5.1",
}


def _model_label(slug: str | None) -> str:
    if not slug:
        return "ChatGPT"
    for key, label in _MODEL_LABELS.items():
        if key in slug:
            return label
    return f"ChatGPT ({slug})"


def _parts_to_text(parts: list) -> str:
    """Join text parts; represent image attachments as placeholders."""
    chunks = []
    for p in parts:
        if isinstance(p, str):
            chunks.append(p)
        elif isinstance(p, dict):
            ct = p.get("content_type", "")
            if ct == "image_asset_pointer":
                chunks.append("*[image attachment]*")
            elif ct == "audio_asset_pointer":
                chunks.append("*[audio attachment]*")
            elif "text" in p:
                chunks.append(p["text"])
    return "\n".join(chunks)


def extract_messages(mapping: dict, current_node: str) -> list[dict]:
    """Walk from current_node to root via parent links, then reverse."""
    chain: list[dict] = []
    node_id: str | None = current_node

    while node_id and node_id in mapping:
        node = mapping[node_id]
        msg = node.get("message")
        if msg:
            role = msg.get("author", {}).get("role", "")
            ct = msg.get("content", {}).get("content_type", "text")
            parts = msg.get("content", {}).get("parts", [])
            text = _parts_to_text(parts).strip()

            if role not in _SKIP_ROLES and ct not in _SKIP_CONTENT_TYPES and text:
                model_slug = msg.get("metadata", {}).get("model_slug") or ""
                chain.append(
                    {
                        "role": role,
                        "text": text,
                        "model": model_slug,
                        "create_time": msg.get("create_time"),
                    }
                )
        node_id = node.get("parent")

    return list(reversed(chain))


# ── HTML template ─────────────────────────────────────────────────────────────

_FOOTER_CSS = """\
<style>
#kb-back{position:fixed;top:.8rem;left:.9rem;font-size:.75rem;color:#bbb;
  text-decoration:none;z-index:200;background:rgba(255,255,255,.85);
  padding:2px 7px;border-radius:3px;border:1px solid #e5e5e5}
#kb-back:hover{color:#333}
#kb-edit{position:fixed;top:.8rem;left:5.5rem;font-size:.75rem;color:#bbb;
  text-decoration:none;z-index:200;background:rgba(255,255,255,.85);
  padding:2px 7px;border-radius:3px;border:1px solid #e5e5e5}
#kb-edit:hover{color:#333}
#kb-toc{position:fixed;top:2rem;right:calc(50% + 470px);width:190px;
  font-size:.78rem;line-height:1.55;max-height:calc(100vh - 4rem);
  overflow-y:auto;color:#888}
@media(max-width:1340px){#kb-toc{display:none}}
#kb-toc-title{font-weight:600;color:#555;margin-bottom:.5rem;font-size:.72rem;
  text-transform:uppercase;letter-spacing:.06em}
#kb-toc a{display:block;color:#aaa;text-decoration:none;padding:1px 0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#kb-toc a:hover{color:#333}
#kb-toc a.active{color:#0066cc;font-weight:500}
</style>"""

_FOOTER_JS = """\
<a id="kb-back" href="/">← 知识库</a>
<a id="kb-edit" href="">✎</a>
<script>
document.getElementById('kb-edit').href='/edit/'+encodeURIComponent(location.pathname.split('/').pop());
document.querySelectorAll('a:not(#kb-back):not(#kb-edit)').forEach(function(a){
  var h=a.getAttribute('href')||'';
  if(!h.startsWith('#'))a.target='_blank';
});
</script>
<script>
(function(){
  var hs=Array.from(document.querySelectorAll('h2,h3,h4'));
  if(hs.length<2)return;
  hs.forEach(function(h,i){if(!h.id)h.id='toc'+i;});
  var nav=document.createElement('nav');
  nav.id='kb-toc';
  nav.innerHTML='<div id="kb-toc-title">目录</div>'+hs.map(function(h){
    var lvl=parseInt(h.tagName[1]);
    var indent=(lvl-2)*10;
    return '<a href="#'+h.id+'" style="padding-left:'+indent+'px" title="'+
      h.textContent.trim()+'">'+h.textContent.trim()+'</a>';
  }).join('');
  document.body.appendChild(nav);
  var io=new IntersectionObserver(function(es){
    es.forEach(function(e){
      var a=nav.querySelector('a[href="#'+e.target.id+'"]');
      if(a)a.classList.toggle('active',e.isIntersecting);
    });
  },{rootMargin:'-10% 0px -75% 0px'});
  hs.forEach(function(h){io.observe(h);});
})();
</script>"""


def build_html(
    conv_id: str,
    title: str,
    create_ts: float | None,
    update_ts: float | None,
    messages: list[dict],
    exported: str,
) -> str:
    chat_url = f"https://chatgpt.com/c/{conv_id}"
    esc_title = html.escape(title)

    msg_parts: list[str] = []
    for msg in messages:
        role = msg["role"]
        if role == "user":
            label = "You"
            cls = "msg-user"
        else:
            label = html.escape(_model_label(msg.get("model")))
            cls = "msg-assistant"
        body = md_to_html(msg["text"])
        msg_parts.append(
            f'<div class="{cls}">'
            f'<div class="msg-label">{label}</div>'
            f'<div class="msg-body">{body}</div>'
            f"</div>"
        )

    msgs_html = "\n".join(msg_parts)

    return f"""\
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="" xml:lang="">
<head>
 <meta name="classification" content="chats">
 <meta charset="utf-8" />
 <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes" />
 <title>{esc_title}</title>
 <link rel="stylesheet" href="/assets/style.css" />
 <style>
  .msg-user, .msg-assistant {{ margin: 1.2rem 0; padding: 0.75rem 1rem; border-radius: 6px; }}
  .msg-user {{ background: #f0f4f8; border-left: 3px solid #0066cc; }}
  .msg-assistant {{ background: #f8fafc; border-left: 3px solid #1a7f37; }}
  .msg-label {{ font-size: 0.78rem; font-weight: 600; margin-bottom: 0.4rem;
    text-transform: uppercase; letter-spacing: 0.05em; }}
  .msg-user .msg-label {{ color: #0066cc; }}
  .msg-assistant .msg-label {{ color: #1a7f37; }}
  .msg-body > *:first-child {{ margin-top: 0; }}
  .msg-body > *:last-child {{ margin-bottom: 0; }}
  .chat-meta {{ font-size: 0.82rem; color: #999; margin-bottom: 1.5rem; }}
 </style>
</head>
<body>
<h1>{esc_title}</h1>
<p class="chat-meta">
 <strong>Created:</strong> {ts_display(create_ts)}<br />
 <strong>Updated:</strong> {ts_display(update_ts)}<br />
 <strong>Exported:</strong> {exported}<br />
 <strong>Link:</strong> <a href="{chat_url}">{chat_url}</a><br />
 <strong>Messages:</strong> {len(messages)}
</p>
{msgs_html}
{_FOOTER_CSS}
{_FOOTER_JS}
</body>
</html>
"""


# ── per-conversation processing ───────────────────────────────────────────────


def scan_existing_chat_notes(notes_dir: Path) -> dict[str, Path]:
    """Build a map of conv_id → existing file path by scanning chat notes."""
    conv_id_re = re.compile(r"chatgpt\.com/c/([0-9a-f-]{36})")
    existing: dict[str, Path] = {}
    for p in notes_dir.glob("*.html"):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            m = conv_id_re.search(text)
            if m:
                existing[m.group(1)] = p
        except OSError:
            pass
    return existing


def process_conversation(
    conv: dict,
    notes_dir: Path,
    exported: str,
    existing_map: dict[str, Path],
) -> str | None:
    """Convert one conversation dict to an HTML file. Returns filename or None."""
    title = (conv.get("title") or "Untitled").strip()
    conv_id = conv.get("id") or conv.get("conversation_id", "")
    create_ts: float | None = conv.get("create_time")
    update_ts: float | None = conv.get("update_time")
    current_node: str | None = conv.get("current_node")
    mapping: dict = conv.get("mapping") or {}

    if not current_node or not mapping:
        return None

    messages = extract_messages(mapping, current_node)
    if not messages:
        return None

    # Reuse the existing file path for this conv_id if already written before.
    if conv_id and conv_id in existing_map:
        out_path = existing_map[conv_id]
    else:
        date = ts_date(create_ts) or "0000-00-00"
        slug = slugify(title)
        base_stem = f"{date}-{slug}"
        out_path = notes_dir / f"{base_stem}.html"

        # Resolve collision: target file belongs to a different conversation.
        if out_path.exists() and conv_id:
            existing_text = out_path.read_text(encoding="utf-8", errors="ignore")
            # Overwrite if it's a stale chat export (has chat CSS but no ChatGPT URL).
            is_old_chat_export = (
                "msg-user" in existing_text and "chatgpt.com/c/" not in existing_text
            )
            if not is_old_chat_export:
                # Hand-written note → find next available suffix slot.
                i = 2
                while out_path.exists():
                    out_path = notes_dir / f"{base_stem}-{i}.html"
                    i += 1

        if conv_id:
            existing_map[conv_id] = out_path

    content = build_html(conv_id, title, create_ts, update_ts, messages, exported)
    out_path.write_text(content, encoding="utf-8")
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
        default=str(DEFAULT_NOTES),
        help="Notes output directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print filenames without writing",
    )
    args = parser.parse_args()

    export_dir = Path(args.export_dir)
    notes_dir = Path(args.output)

    if not export_dir.is_dir():
        sys.exit(f"Export directory not found: {export_dir}")
    notes_dir.mkdir(parents=True, exist_ok=True)

    json_files = sorted(export_dir.glob("conversations-*.json"))
    if not json_files:
        sys.exit("No conversations-*.json files found in export directory.")

    print(f"Found {len(json_files)} conversation file(s)")
    print(f"Output directory: {notes_dir}")

    exported = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    print("Scanning existing chat notes …", end=" ", flush=True)
    existing_map = scan_existing_chat_notes(notes_dir)
    print(f"{len(existing_map)} found")

    total = converted = skipped = 0

    for json_path in json_files:
        print(f"  Processing {json_path.name} …", end=" ", flush=True)
        with open(json_path, encoding="utf-8") as f:
            conversations: list[dict] = json.load(f)

        file_count = 0
        for conv in conversations:
            total += 1
            filename = None if args.dry_run else process_conversation(
                conv, notes_dir, exported, existing_map
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
