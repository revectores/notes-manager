from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
NOTES_DIR  = BASE_DIR / "notes-html"
ASSETS_DIR = BASE_DIR / "assets"
INDEX_PATH = ASSETS_DIR / "search-index.json"

HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("PORT", "8024"))


# ── 搜索引擎 ──────────────────────────────────────────────────────────────────

INDEX: list[dict] = []
_TREE: dict = {}


def ensure_notes_html() -> None:
    """notes-html/ and assets/search-index.json are gitignored build
    artifacts. On a fresh checkout (neither exists yet), regenerate them
    from notes/ and chats/ via the converters before serving."""
    if NOTES_DIR.is_dir() and any(NOTES_DIR.iterdir()):
        return
    print("notes-html/ not found — regenerating from notes/ and chats/ (this may take a while)...")
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX_PATH.is_file():
        INDEX_PATH.write_text("[]", encoding="utf-8")
    for script in ("notes_to_html.py", "chats_to_html.py"):
        subprocess.run([sys.executable, str(BASE_DIR / script)], cwd=BASE_DIR, check=True)


def load_index() -> None:
    global INDEX, _TREE
    with open(INDEX_PATH, encoding="utf-8") as f:
        INDEX = json.load(f)
    _TREE = build_tree(INDEX)
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
            "title":   e["title"],
            "path":    e["path"],
            "crumb":   e.get("crumb", []),
            "snippet": _snippet(e.get("body", ""), tokens),
        }
        for _, e in scored[:limit]
    ]
    return results, len(scored)


# ── 目录树 ────────────────────────────────────────────────────────────────────

def build_tree(entries: list[dict]) -> dict:
    """从索引条目构建分类树（只含计数，不含笔记列表）。"""
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


# ── TOC 注入 ──────────────────────────────────────────────────────────────────

_TOC_INJECT = """
<style>
#kb-back{position:fixed;top:.8rem;left:.9rem;font-size:.75rem;color:#bbb;
  text-decoration:none;z-index:200;background:rgba(255,255,255,.85);
  padding:2px 7px;border-radius:3px;border:1px solid #e5e5e5}
#kb-back:hover{color:#333}
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
</style>
<a id="kb-back" href="/">← 知识库</a>
<script>
document.querySelectorAll('a:not(#kb-back)').forEach(function(a){
  var h=a.getAttribute('href')||'';
  if(!h.startsWith('#'))a.target='_blank';
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


def inject_toc(html: str) -> str:
    return html.replace("</body>", _TOC_INJECT + "</body>", 1)


# ── HTTP 服务器 ───────────────────────────────────────────────────────────────

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

        global INDEX, _TREE
        note_path = f"/notes-html/{filename}"
        INDEX = [e for e in INDEX if e.get("path") != note_path]
        _TREE = build_tree(INDEX)
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(INDEX, f, ensure_ascii=False, indent=2)

        self.send_json({"ok": True, "count": len(INDEX)})

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path   = urllib.parse.unquote(parsed.path)
        qs     = urllib.parse.parse_qs(parsed.query)

        # ── 首页 ──────────────────────────────────────────────────────────────
        if path in ("/", "/index.html"):
            self.send_file(BASE_DIR / "index.html")

        # ── 搜索 API ──────────────────────────────────────────────────────────
        elif path == "/api/search":
            q     = qs.get("q",     [""])[0].strip()
            cls   = qs.get("cls",   [""])[0].strip()
            limit = int(qs.get("limit", ["50"])[0])

            pool = INDEX
            if cls:
                pool = [e for e in INDEX if "/".join(e.get("crumb", [])).startswith(cls)]

            if q:
                results, total = do_search(q, limit, pool)
            else:
                results = [
                    {"title": e["title"], "path": e["path"],
                     "crumb": e.get("crumb", []), "snippet": ""}
                    for e in pool[:limit]
                ]
                total = len(pool)

            self.send_json({"results": results, "total": total, "count": len(INDEX)})

        # ── 目录树 API ────────────────────────────────────────────────────────
        elif path == "/api/tree":
            self.send_json(_TREE)

        # ── 重载索引 ──────────────────────────────────────────────────────────
        elif path == "/api/reload":
            load_index()
            self.send_json({"ok": True, "count": len(INDEX)})

        # ── 笔记文件（注入 TOC）───────────────────────────────────────────────
        elif path.startswith("/notes-html/"):
            filepath = NOTES_DIR / path[len("/notes-html/"):]
            if not filepath.is_file():
                self.send_response(HTTPStatus.NOT_FOUND)
                self.end_headers()
                return
            if filepath.suffix == ".html":
                html = inject_toc(filepath.read_text(encoding="utf-8"))
                self.send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self.send_file(filepath)

        # ── 静态资源 ──────────────────────────────────────────────────────────
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
