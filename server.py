from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from note_lang import LANG_PRIORITY, lang_label, pick_default_lang
from update_chat_index import extract_title_body

BASE_DIR = Path(__file__).resolve().parent
NOTES_DIR  = BASE_DIR / "notes-html"
ASSETS_DIR = BASE_DIR / "assets"
INDEX_PATH = ASSETS_DIR / "search-index.json"

HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("PORT", "8024"))


# ── Search engine ──────────────────────────────────────────────────────────────────

INDEX: list[dict] = []
_TREE: dict = {}
_BY_PATH: dict[str, dict] = {}
_MTIMES: dict[str, float] = {}
_BACKLINKS: dict[str, list[str]] = {}


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


def ensure_notes_html() -> None:
    """notes-html/ and assets/search-index.json are gitignored build
    artifacts. On a fresh checkout (neither exists yet), regenerate them
    from notes/ via the converter before serving."""
    if NOTES_DIR.is_dir() and any(NOTES_DIR.iterdir()):
        return
    print("notes-html/ not found — regenerating from notes/ (this may take a while)...")
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX_PATH.is_file():
        INDEX_PATH.write_text("[]", encoding="utf-8")
    subprocess.run([sys.executable, str(BASE_DIR / "notes_to_html.py")], cwd=BASE_DIR, check=True)


def _entry_mtime(entry: dict) -> float:
    """Newest mtime among an entry's generated notes-html/*.html file(s),
    used to sort the browse view (no search query) by last-updated."""
    paths = entry["langs"].values() if entry.get("langs") else [entry["path"]]
    mtimes = []
    for p in paths:
        try:
            mtimes.append((NOTES_DIR / Path(p).name).stat().st_mtime)
        except OSError:
            pass
    return max(mtimes, default=0.0)


def load_index() -> None:
    global INDEX, _TREE, _BY_PATH, _MTIMES, _BACKLINKS
    with open(INDEX_PATH, encoding="utf-8") as f:
        INDEX = json.load(f)
    _TREE = build_tree(INDEX)
    _BY_PATH = build_by_path(INDEX)
    _MTIMES = {e["path"]: _entry_mtime(e) for e in INDEX}
    _BACKLINKS = build_backlinks(INDEX)
    print(f"已加载 {len(INDEX)} 条索引")


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
            "title":    e["title"],
            "path":     e["path"],
            "crumb":    e.get("crumb", []),
            "category": e.get("category"),
            "snippet":  _snippet(e.get("body", ""), tokens),
            "langs":    e.get("langs"),
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
#kb-back{position:fixed;top:.8rem;left:.9rem;font-size:.75rem;color:#bbb;
  text-decoration:none;z-index:200;background:rgba(255,255,255,.85);
  padding:2px 7px;border-radius:3px;border:1px solid #e5e5e5}
#kb-back:hover{color:#333}
#kb-backlinks{margin-top:2.5rem;padding-top:1rem;border-top:1px solid #eee;max-width:640px}
#kb-backlinks-title{font-size:.75rem;font-weight:600;color:#aaa;
  text-transform:uppercase;letter-spacing:.06em;margin-bottom:.4rem}
#kb-backlinks ul{list-style:none;padding:0;margin:0}
#kb-backlinks li{margin:.2rem 0}
#kb-backlinks a{font-size:.86rem;color:#0066cc;text-decoration:none}
#kb-backlinks a:hover{text-decoration:underline}
#kb-toc{position:fixed;top:2rem;right:calc(50% + 470px);width:240px;
  font-size:.78rem;line-height:1.55;max-height:calc(100vh - 4rem);
  overflow-y:auto;color:#888}
@media(max-width:1440px){#kb-toc{display:none}}
#kb-toc-title{font-weight:600;color:#555;margin-bottom:.5rem;font-size:.72rem;
  text-transform:uppercase;letter-spacing:.06em}
#kb-toc a{display:block;color:#aaa;text-decoration:none;padding:1px 0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#kb-toc a:hover{color:#333}
#kb-toc a.active{color:#0066cc;font-weight:500}
#kb-lang{position:fixed;top:.8rem;right:.9rem;font-size:.75rem;
  z-index:200;display:flex;gap:4px}
#kb-lang a{color:#bbb;text-decoration:none;background:rgba(255,255,255,.85);
  padding:2px 7px;border-radius:3px;border:1px solid #e5e5e5}
#kb-lang a:hover{color:#333}
#kb-lang a.active{color:#0066cc;font-weight:600;border-color:#cce0ff}
#kb-category{position:fixed;top:2.5rem;left:.9rem;font-size:.75rem;color:#888;
  z-index:200;background:rgba(255,255,255,.85);
  padding:2px 7px;border-radius:3px;border:1px solid #e5e5e5}
</style>
<a id="kb-back" href="/">← 知识库</a>
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
  document.body.appendChild(nav);
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


_CATEGORY_LABELS = {"paper": "📄 论文", "note": "📝 笔记"}


def render_category_badge(category: str | None) -> str:
    label = _CATEGORY_LABELS.get(category or "")
    if not label:
        return ""
    return f'<div id="kb-category">{label}</div>\n'


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
    category: str | None = None,
    backlinks: list[str] | None = None,
) -> str:
    extra = render_category_badge(category) + _TOC_INJECT
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

    def send_file(self, path: Path | str) -> None:
        path = Path(path)
        if not path.is_file():
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return
        self.send_bytes(path.read_bytes(), _mime(path))

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path   = urllib.parse.unquote(parsed.path)

        if not path.startswith("/api/notes/"):
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
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

        global INDEX, _TREE, _BY_PATH, _BACKLINKS
        note_path = f"/notes-html/{filename}"
        new_index: list[dict] = []
        for entry in INDEX:
            langs = entry.get("langs")
            if langs and note_path in langs.values():
                removed_lang = next(l for l, p in langs.items() if p == note_path)
                del langs[removed_lang]
                if not langs:
                    continue
                if entry["path"] == note_path:
                    default_lang = pick_default_lang(langs)
                    default_path = langs[default_lang]
                    title, body = extract_title_body(NOTES_DIR / Path(default_path).name)
                    entry["title"], entry["body"], entry["path"] = title, body, default_path
                new_index.append(entry)
            elif entry.get("path") == note_path:
                continue
            else:
                new_index.append(entry)

        INDEX = new_index
        _TREE = build_tree(INDEX)
        _BY_PATH = build_by_path(INDEX)
        _BACKLINKS = build_backlinks(INDEX)
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(INDEX, f, ensure_ascii=False, indent=2)

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
            q     = qs.get("q",     [""])[0].strip()
            cls   = qs.get("cls",   [""])[0].strip()
            cat   = qs.get("cat",   [""])[0].strip()
            limit = int(qs.get("limit", ["50"])[0])

            pool = INDEX
            if cls:
                pool = [e for e in pool if "/".join(e.get("crumb", [])).startswith(cls)]
            if cat:
                pool = [e for e in pool if e.get("category") == cat]

            if q:
                results, total = do_search(q, limit, pool)
            else:
                pool = sorted(pool, key=lambda e: _MTIMES.get(e["path"], 0.0), reverse=True)
                results = [
                    {"title": e["title"], "path": e["path"],
                     "crumb": e.get("crumb", []), "category": e.get("category"),
                     "snippet": "", "langs": e.get("langs")}
                    for e in pool[:limit]
                ]
                total = len(pool)

            self.send_json({"results": results, "total": total, "count": len(INDEX)})

        # ── Directory tree API ────────────────────────────────────────────────────────
        elif path == "/api/tree":
            self.send_json(_TREE)

        # ── Reload index ──────────────────────────────────────────────────────────
        elif path == "/api/reload":
            load_index()
            self.send_json({"ok": True, "count": len(INDEX)})

        # ── Note files (inject TOC) ───────────────────────────────────────────────
        elif path.startswith("/notes-html/"):
            filepath = NOTES_DIR / path[len("/notes-html/"):]
            if not filepath.is_file():
                self.send_response(HTTPStatus.NOT_FOUND)
                self.end_headers()
                return
            if filepath.suffix == ".html":
                entry = _BY_PATH.get(path)
                langs = entry.get("langs") if entry else None
                category = entry.get("category") if entry else None
                backlinks = _BACKLINKS.get(path)
                page_html = inject_toc(filepath.read_text(encoding="utf-8"), langs, path, category, backlinks)
                self.send_bytes(page_html.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self.send_file(filepath)

        # ── Static assets ──────────────────────────────────────────────────────────
        elif path.startswith("/assets/"):
            self.send_file(ASSETS_DIR / path[len("/assets/"):])

        else:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()


if __name__ == "__main__":
    ensure_notes_html()
    load_index()
    server = ThreadingHTTPServer((HOST, DEFAULT_PORT), Handler)
    print(f"笔记管理器运行在 http://{HOST}:{DEFAULT_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
