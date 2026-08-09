from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from note_index import URL_PREFIX, build_index, snapshot
from note_lang import LANG_PRIORITY, lang_label

BASE_DIR   = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"

# The notes themselves are HTML files in a store outside this repo, synced by
# iCloud like every other app's data. They are the source of truth: nothing is
# generated from them and nothing generates them.
_DEFAULT_STORE = (
    Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/database/knowledge-manager/notes"
)
NOTES_DIR = Path(os.environ.get("KNOWLEDGE_HTML_PATH") or _DEFAULT_STORE).expanduser()

HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("PORT", "8024"))
# The store syncs from other devices, so poll it for edits made elsewhere.
WATCH_INTERVAL = float(os.environ.get("KNOWLEDGE_WATCH_INTERVAL", "5"))
AUTO_WATCH = os.environ.get("KNOWLEDGE_AUTO_WATCH", "1") != "0"


# ── Search engine ──────────────────────────────────────────────────────────────────

INDEX: list[dict] = []
_TREE: dict = {}
_BY_PATH: dict[str, dict] = {}
_MTIMES: dict[str, float] = {}
_BACKLINKS: dict[str, list[str]] = {}
_SNAPSHOT: dict[str, float] = {}
_WRITE_LOCK = threading.Lock()


def build_by_path(entries: list[dict]) -> dict[str, dict]:
    """Map every served path (including each language variant) to its index entry."""
    by_path: dict[str, dict] = {}
    for entry in entries:
        langs = entry.get("langs")
        if langs:
            for p in langs.values():
                by_path[p] = entry
        else:
            by_path[entry["path"]] = entry
    return by_path


def build_backlinks(entries: list[dict]) -> dict[str, list[str]]:
    """Map each note path to the list of paths that link to it."""
    bl: dict[str, list[str]] = {}
    for entry in entries:
        src = entry["path"]
        for target in entry.get("outlinks", []):
            if target != src:
                bl.setdefault(target, []).append(src)
    return bl


def _entry_mtime(entry: dict, snap: dict[str, float]) -> float:
    """Newest mtime among an entry's stored file(s), used to sort the browse
    view (no search query) by last-updated."""
    paths = entry["langs"].values() if entry.get("langs") else [entry["path"]]
    return max((snap.get(Path(p).name, 0.0) for p in paths), default=0.0)


def reload_index() -> None:
    """Re-read every note in the store. Cheap enough (a few hundred files) that
    any edit anywhere just rebuilds the lot."""
    global INDEX, _TREE, _BY_PATH, _MTIMES, _BACKLINKS, _SNAPSHOT
    with _WRITE_LOCK:
        snap = snapshot(NOTES_DIR)
        entries = build_index(NOTES_DIR)
        INDEX = entries
        _TREE = build_tree(entries)
        _BY_PATH = build_by_path(entries)
        _MTIMES = {e["path"]: _entry_mtime(e, snap) for e in entries}
        _BACKLINKS = build_backlinks(entries)
        _SNAPSHOT = snap


def watch_store() -> None:
    """Poll the store and reindex when a note is added, edited or removed —
    by hand here, or by iCloud carrying an edit over from another device."""
    while True:
        time.sleep(WATCH_INTERVAL)
        try:
            if snapshot(NOTES_DIR) != _SNAPSHOT:
                reload_index()
                print(f"笔记有变动，已重新索引 {len(INDEX)} 篇", flush=True)
        except OSError as e:
            print(f"扫描笔记目录失败: {e}", flush=True)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[\w一-鿿぀-ヿ＀-￯]+", text.lower())


def _score(entry: dict, tokens: list[str], phrase: str = "") -> int:
    title_low = entry["title"].lower()
    body_low  = entry.get("body", "").lower()
    s = 0
    for t in tokens:
        if t in title_low: s += 10
        if t in body_low:  s += 1
    if phrase:
        if phrase in title_low: s += 1000
        elif phrase in body_low: s += 500
    return s


def _snippet(body: str, tokens: list[str], length: int = 160) -> str:
    if not tokens:
        return body[:length]
    low = body.lower()
    pos = len(body)
    for t in tokens:
        i = low.find(t)
        if i != -1 and i < pos:
            pos = i
    start  = max(0, pos - 40)
    prefix = "…" if start > 0 else ""
    chunk  = body[start : start + length]
    suffix = "…" if start + length < len(body) else ""
    return prefix + chunk + suffix


def do_search(query: str, limit: int = 50, pool: list[dict] | None = None) -> tuple[list[dict], int]:
    if pool is None:
        pool = INDEX
    tokens = tokenize(query)
    if not tokens:
        return [], 0
    phrase = query.lower().strip() if len(tokens) > 1 else ""
    scored = [(s, e) for e in pool if (s := _score(e, tokens, phrase)) > 0]
    scored.sort(key=lambda x: -x[0])
    results = [
        {
            "title":   e["title"],
            "path":    e["path"],
            "crumb":   e.get("crumb", []),
            "snippet": _snippet(e.get("body", ""), tokens),
            "langs":   e.get("langs"),
        }
        for _, e in scored[:limit]
    ]
    return results, len(scored)


# ── Directory tree ────────────────────────────────────────────────────────────────────

def build_tree(entries: list[dict]) -> dict:
    """Build a category tree from index entries (counts only, no note lists)."""
    root: dict = {"name": "", "path": "", "count": 0, "children": {}}

    for entry in entries:
        crumb = entry.get("crumb", [])
        node = root
        node["count"] += 1
        for segment in crumb:
            if segment not in node["children"]:
                parent = node["path"]
                node["children"][segment] = {
                    "name":     segment,
                    "path":     (parent + "/" + segment).lstrip("/"),
                    "count":    0,
                    "children": {},
                }
            node = node["children"][segment]
            node["count"] += 1

    def _sort(node: dict) -> dict:
        node["children"] = sorted(
            [_sort(c) for c in node["children"].values()],
            key=lambda x: x["name"],
        )
        return node

    return _sort(root)


# ── TOC injection ──────────────────────────────────────────────────────────────────

_TOC_INJECT = """
<style>
/* Injected into a stored note, which has already loaded /assets/style.css, so
   the palette variables are in scope — including under a dark colour scheme,
   which is the whole reason nothing here names a colour of its own. */
#kb-back{display:block;font-size:.78rem;color:var(--muted);
  text-decoration:none;border:none;margin-bottom:1.4rem}
#kb-back:hover{color:var(--accent)}
#kb-backlinks{margin-top:2.5rem;padding-top:1rem;border-top:1px solid var(--border)}
#kb-backlinks-title{font-size:.68rem;font-weight:700;color:var(--muted);
  text-transform:uppercase;letter-spacing:.08em;margin-bottom:.5rem}
#kb-backlinks ul{list-style:none;padding:0;margin:0}
#kb-backlinks li{margin:.2rem 0}
#kb-backlinks a{font-size:.86rem;color:var(--accent);
  text-decoration:none;border-bottom:1px dotted var(--accent)}
#kb-backlinks a:hover{border-bottom-style:solid}
/* The crumb and the table of contents share one fixed rail in the left
   gutter and stack inside it, so a deep classification path pushes the
   contents down instead of covering it. The rail's offset is the note
   column's own half-width plus the gap, both declared in style.css. */
#kb-rail{position:fixed;top:1.5rem;width:var(--rail);
  right:calc(50% + var(--column) / 2 + var(--rail-gap));
  max-height:calc(100vh - 3rem);overflow-y:auto;z-index:150}
#kb-crumb{font-size:.72rem;line-height:1.5;color:var(--muted);
  margin-bottom:1.4rem;word-break:break-word}
#kb-toc{font-size:.76rem;line-height:1.5}
#kb-toc-title{font-size:.68rem;font-weight:700;color:var(--muted);
  text-transform:uppercase;letter-spacing:.08em;margin-bottom:.5rem}
#kb-toc a{display:block;color:var(--muted);text-decoration:none;
  border:none;padding:.1rem 0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#kb-toc a:hover{color:var(--fg)}
#kb-toc a.active{color:var(--accent);font-weight:700}
#kb-lang{position:fixed;top:1.5rem;right:1.5rem;z-index:200;
  display:flex;gap:.3rem;font-size:.75rem}
#kb-lang a{color:var(--muted);text-decoration:none;background:var(--card-bg);
  border:1px solid var(--border);border-radius:6px;padding:.2rem .6rem}
#kb-lang a:hover{color:var(--accent);border-color:var(--accent)}
#kb-lang a.active{color:#fff;background:var(--accent);border-color:var(--accent)}
/* 46rem of column, 13rem of rail and 2.5rem between them need ~1264px of
   viewport. Below that there is no gutter to hold the rail, so it collapses
   into one strip along the top: the way back and where you are, side by side,
   with the contents dropped. The note gets extra head room to sit under. */
@media(max-width:1264px){
  body{padding-top:4.2rem}
  #kb-rail{top:1.5rem;left:1.5rem;right:auto;width:auto;
    max-width:calc(100vw - 9rem);max-height:none;overflow:visible;
    display:flex;align-items:center;gap:.4rem}
  #kb-toc{display:none}
  #kb-back,#kb-crumb{margin:0;flex-shrink:0;background:var(--card-bg);
    border:1px solid var(--border);border-radius:6px;padding:.2rem .6rem}
  #kb-crumb{min-width:0;flex-shrink:1;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
}
</style>
<script>
document.querySelectorAll('a:not(#kb-back)').forEach(function(a){
  var h=a.getAttribute('href')||'';
  if(!h.startsWith('#')&&!h.startsWith('/notes-html/'))a.target='_blank';
});
</script>
<script>
(function(){
  var hs=Array.from(document.querySelectorAll('h2,h3,h4,h5'));
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
  (document.getElementById('kb-rail')||document.body).appendChild(nav);
  var io=new IntersectionObserver(function(es){
    es.forEach(function(e){
      var a=nav.querySelector('a[href="#'+e.target.id+'"]');
      if(a)a.classList.toggle('active',e.isIntersecting);
    });
  },{rootMargin:'-10% 0px -75% 0px'});
  hs.forEach(function(h){io.observe(h);});
})();
</script>
"""


def _lang_sort_key(lang: str) -> tuple[int, str]:
    return (LANG_PRIORITY.index(lang) if lang in LANG_PRIORITY else len(LANG_PRIORITY), lang)


def render_lang_switcher(langs: dict[str, str], current_path: str) -> str:
    links = []
    for lang in sorted(langs, key=_lang_sort_key):
        target = langs[lang]
        cls = ' class="active"' if target == current_path else ""
        links.append(f'<a href="{html.escape(target)}"{cls}>{html.escape(lang_label(lang))}</a>')
    return f'<div id="kb-lang">{"".join(links)}</div>\n'


def render_rail(crumb: list[str]) -> str:
    """The fixed left rail: the way back, where the note is filed, and the
    table of contents the injected script appends below them.

    The back link lives in the rail rather than floating in the corner on its
    own, because near the breakpoint the corner is where the rail itself is.
    Always emitted, even unfiled and even for a note too short to have
    contents, because the rail is what positions the contents.
    """
    parts = ['<a id="kb-back" href="/">← 知识库</a>']
    if crumb:
        path = html.escape(" / ".join(crumb))
        parts.append(f'<div id="kb-crumb" title="{path}">{path}</div>')
    return f'<div id="kb-rail">{"".join(parts)}</div>\n'


def render_backlinks_html(srcs: list[str]) -> str:
    items = []
    for src in srcs:
        entry = _BY_PATH.get(src)
        if not entry:
            continue
        items.append(
            f'<li><a href="{html.escape(src)}">{html.escape(entry["title"])}</a></li>'
        )
    if not items:
        return ""
    return (
        '<div id="kb-backlinks">'
        '<div id="kb-backlinks-title">引用此页</div>'
        f'<ul>{"".join(items)}</ul>'
        '</div>\n'
    )


def inject_toc(
    page_html: str,
    langs: dict[str, str] | None = None,
    current_path: str = "",
    crumb: list[str] | None = None,
    backlinks: list[str] | None = None,
) -> str:
    extra = render_rail(crumb or []) + _TOC_INJECT
    if langs and len(langs) > 1:
        extra = render_lang_switcher(langs, current_path) + extra
    if backlinks:
        extra = render_backlinks_html(backlinks) + extra
    return page_html.replace("</body>", extra + "</body>", 1)


# ── HTTP server ───────────────────────────────────────────────────────────────

_MIME: dict[str, str] = {
    ".html":  "text/html; charset=utf-8",
    ".css":   "text/css; charset=utf-8",
    ".js":    "application/javascript; charset=utf-8",
    ".json":  "application/json; charset=utf-8",
    ".png":   "image/png",
    ".jpg":   "image/jpeg",
    ".jpeg":  "image/jpeg",
    ".gif":   "image/gif",
    ".svg":   "image/svg+xml",
    ".ico":   "image/x-icon",
    ".pdf":   "application/pdf",
    ".woff2": "font/woff2",
    ".woff":  "font/woff",
}


def _mime(path: str | Path) -> str:
    return _MIME.get(Path(path).suffix.lower(), "application/octet-stream")


def resolve_in(root: Path, relative: str) -> Path | None:
    """Resolve a request path inside root, or None if it escapes it. The store
    sits outside this repo, so traversal has to be refused explicitly."""
    try:
        target = (root / relative).resolve()
        target.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return target


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args) -> None:
        pass

    def send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_missing(self) -> None:
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def send_file(self, path: Path | str | None) -> None:
        if path is None or not Path(path).is_file():
            self.send_missing()
            return
        path = Path(path)
        self.send_bytes(path.read_bytes(), _mime(path))

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path   = urllib.parse.unquote(parsed.path)

        if not path.startswith("/api/notes/"):
            self.send_missing()
            return

        filename = path[len("/api/notes/"):]
        # Safety: reject path traversal
        if "/" in filename or "\\" in filename or filename.startswith("."):
            self.send_json({"ok": False, "error": "invalid filename"}, 400)
            return

        filepath = NOTES_DIR / filename
        if not filepath.is_file():
            self.send_json({"ok": False, "error": "not found"}, 404)
            return

        filepath.unlink()
        reload_index()
        self.send_json({"ok": True, "count": len(INDEX)})

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path   = urllib.parse.unquote(parsed.path)
        qs     = urllib.parse.parse_qs(parsed.query)

        # ── Home page ──────────────────────────────────────────────────────────────
        if path in ("/", "/index.html"):
            self.send_file(BASE_DIR / "index.html")

        # ── Search API ──────────────────────────────────────────────────────────
        elif path == "/api/search":
            q     = qs.get("q",   [""])[0].strip()
            cls   = qs.get("cls", [""])[0].strip()
            limit = int(qs.get("limit", ["50"])[0])

            pool = INDEX
            if cls:
                pool = [e for e in pool if "/".join(e.get("crumb", [])).startswith(cls)]

            if q:
                results, total = do_search(q, limit, pool)
            else:
                pool = sorted(pool, key=lambda e: _MTIMES.get(e["path"], 0.0), reverse=True)
                results = [
                    {"title": e["title"], "path": e["path"],
                     "crumb": e.get("crumb", []), "snippet": "", "langs": e.get("langs")}
                    for e in pool[:limit]
                ]
                total = len(pool)

            self.send_json({"results": results, "total": total, "count": len(INDEX)})

        # ── Directory tree API ────────────────────────────────────────────────────────
        elif path == "/api/tree":
            self.send_json(_TREE)

        # ── Reload index ──────────────────────────────────────────────────────────
        elif path == "/api/reload":
            reload_index()
            self.send_json({"ok": True, "count": len(INDEX)})

        # ── Stored notes (inject TOC) ─────────────────────────────────────────────
        elif path.startswith(URL_PREFIX):
            filepath = resolve_in(NOTES_DIR, path[len(URL_PREFIX):])
            if filepath is None or not filepath.is_file():
                self.send_missing()
                return
            if filepath.suffix == ".html":
                entry = _BY_PATH.get(path)
                langs = entry.get("langs") if entry else None
                crumb = entry.get("crumb") if entry else None
                backlinks = _BACKLINKS.get(path)
                page_html = inject_toc(
                    filepath.read_text(encoding="utf-8"), langs, path, crumb, backlinks
                )
                self.send_bytes(page_html.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self.send_file(filepath)

        # ── Static assets ──────────────────────────────────────────────────────────
        elif path.startswith("/assets/"):
            self.send_file(resolve_in(ASSETS_DIR, path[len("/assets/"):]))

        else:
            self.send_missing()


if __name__ == "__main__":
    if not NOTES_DIR.is_dir():
        raise SystemExit(
            f"笔记目录不存在: {NOTES_DIR}\n"
            "请设置 KNOWLEDGE_HTML_PATH 指向存放笔记 HTML 的目录。"
        )
    reload_index()
    print(f"已从 {NOTES_DIR} 加载 {len(INDEX)} 篇笔记")
    if AUTO_WATCH:
        threading.Thread(target=watch_store, daemon=True).start()
    server = ThreadingHTTPServer((HOST, DEFAULT_PORT), Handler)
    print(f"知识库运行在 http://{HOST}:{DEFAULT_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
