from __future__ import annotations

import json
import os
import re
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
_ICLOUD_KB = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/workspace/knowledge"
KNOWLEDGE_BASE = Path(os.environ.get("KNOWLEDGE_BASE_PATH", str(_ICLOUD_KB)))
NOTES_DIR  = KNOWLEDGE_BASE / "notes-manager"
ASSETS_DIR = KNOWLEDGE_BASE / "assets"
INDEX_PATH = ASSETS_DIR / "search-index.json"

HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("PORT", "8024"))


# ── 搜索引擎 ──────────────────────────────────────────────────────────────────

INDEX: list[dict] = []
_TREE: dict = {}


def load_index() -> None:
    global INDEX, _TREE
    with open(INDEX_PATH, encoding="utf-8") as f:
        INDEX = json.load(f)
    _TREE = build_tree(INDEX)
    print(f"已加载 {len(INDEX)} 条索引")


def tokenize(text: str) -> list[str]:
    return re.findall(r"[\w一-鿿぀-ヿ＀-￯]+", text.lower())


def _score(entry: dict, tokens: list[str]) -> int:
    title_low = entry["title"].lower()
    body_low  = entry.get("body", "").lower()
    s = 0
    for t in tokens:
        if t in title_low: s += 10
        if t in body_low:  s += 1
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
    scored = [(s, e) for e in pool if (s := _score(e, tokens)) > 0]
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
</style>
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
</script>
"""


def inject_toc(html: str) -> str:
    return html.replace("</body>", _TOC_INJECT + "</body>", 1)


# ── 编辑器页面构建 ────────────────────────────────────────────────────────────

_EDITOR_CSS = """
html{scroll-padding-top:3.2rem}
body{padding-top:3.2rem!important;position:relative}
#kb-bar{position:fixed;top:0;left:0;right:0;z-index:1000;height:3rem;
  background:#fff;border-bottom:1px solid #ddd;
  display:flex;align-items:center;gap:.4rem;padding:0 .8rem}
#kb-bar-title{font-size:.85rem;color:#555;flex:1;overflow:hidden;
  white-space:nowrap;text-overflow:ellipsis}
#kb-save{padding:.3rem .85rem;background:#0066cc;color:#fff;border:none;
  border-radius:4px;font-size:.82rem;cursor:pointer;font-family:inherit}
#kb-save:hover{background:#0052a3}
#kb-save:disabled{background:#aaa;cursor:default}
#kb-cancel{padding:.3rem .85rem;background:#fff;color:#555;
  border:1px solid #ddd;border-radius:4px;font-size:.82rem;
  cursor:pointer;text-decoration:none}
#kb-cancel:hover{background:#f5f5f5}
#kb-status{font-size:.75rem;white-space:nowrap}
#kb-fmt{position:absolute;display:none;z-index:2000;
  background:#fff;border:1px solid #ddd;border-radius:5px;
  box-shadow:0 2px 8px rgba(0,0,0,.12);padding:3px 5px;
  align-items:center;gap:2px;white-space:nowrap}
#kb-fmt select{font-size:.78rem;padding:2px 4px;border:1px solid #ddd;
  border-radius:3px;cursor:pointer;font-family:inherit;background:#fff}
#kb-fmt button{font-size:.8rem;border:none;background:none;border-radius:3px;
  padding:2px 6px;cursor:pointer;color:#333;font-family:inherit;line-height:1.4}
#kb-fmt button:hover{background:#f0f0f0}
#kb-fmt button.on{background:#e3eeff;color:#0055cc}
.kb-sep{width:1px;height:16px;background:#e0e0e0;margin:0 2px;flex-shrink:0}
#kb-content{outline:none}
#kb-content:focus{outline:none}
.kb-cur{outline:2px solid #3b82f6!important;outline-offset:2px;border-radius:2px}
#kb-content p:hover:not(.kb-cur),
#kb-content h1:hover:not(.kb-cur),#kb-content h2:hover:not(.kb-cur),
#kb-content h3:hover:not(.kb-cur),#kb-content h4:hover:not(.kb-cur),
#kb-content h5:hover:not(.kb-cur),#kb-content h6:hover:not(.kb-cur),
#kb-content li:hover:not(.kb-cur),#kb-content blockquote:hover:not(.kb-cur),
#kb-content td:hover:not(.kb-cur),#kb-content th:hover:not(.kb-cur),
#kb-content pre:hover:not(.kb-cur){outline:1px dashed #bcd;outline-offset:2px;
  border-radius:2px;cursor:text}
"""

_EDITOR_JS = r"""(function(){
const FILENAME=document.querySelector('meta[name="kb-filename"]').content;
const content=document.getElementById('kb-content');
const fmt=document.getElementById('kb-fmt');
const saveBtn=document.getElementById('kb-save');
const statusEl=document.getElementById('kb-status');
const fmtBlock=document.getElementById('fmt-block');
const BLOCKS=new Set(['P','H1','H2','H3','H4','H5','H6','LI','BLOCKQUOTE','TD','TH','PRE','DT','DD']);
let cur=null,dirty=false;

function getBlock(){
  const sel=window.getSelection();
  if(!sel||!sel.rangeCount)return null;
  let n=sel.getRangeAt(0).commonAncestorContainer;
  if(n.nodeType===3)n=n.parentElement;
  while(n&&n!==content){if(BLOCKS.has(n.tagName))return n;n=n.parentElement;}
  return null;
}

function setCur(b){
  if(b===cur)return;
  if(cur)cur.classList.remove('kb-cur');
  cur=b;
  if(cur){cur.classList.add('kb-cur');updateFmt();showFmt();}
  else fmt.style.display='none';
}

document.addEventListener('selectionchange',()=>setCur(getBlock()));

function updateFmt(){
  if(!cur)return;
  const tag=cur.tagName.toLowerCase();
  fmtBlock.value=fmtBlock.querySelector('[value="'+tag+'"]')?tag:'p';
  fmt.querySelectorAll('[data-cmd]').forEach(btn=>{
    try{btn.classList.toggle('on',document.queryCommandState(btn.dataset.cmd));}catch(e){}
  });
}
content.addEventListener('keyup',()=>{if(cur)updateFmt();});
content.addEventListener('mouseup',()=>{if(cur)updateFmt();});

function showFmt(){
  if(!cur)return;
  fmt.style.display='flex';
  const rect=cur.getBoundingClientRect();
  const fH=fmt.offsetHeight||34;
  let top=rect.top+window.scrollY-fH-6;
  if(top<52)top=rect.bottom+window.scrollY+6;
  let left=Math.max(8,rect.left+window.scrollX);
  left=Math.min(left,document.documentElement.clientWidth-320);
  fmt.style.top=top+'px';
  fmt.style.left=left+'px';
}
window.addEventListener('scroll',()=>{if(cur)showFmt();},{passive:true});

fmtBlock.addEventListener('mousedown',e=>e.preventDefault());
fmtBlock.addEventListener('change',()=>{
  if(!cur)return;
  const newTag=fmtBlock.value;
  if(newTag===cur.tagName.toLowerCase())return;
  const el=document.createElement(newTag);
  el.innerHTML=cur.innerHTML;
  cur.parentNode.replaceChild(el,cur);
  const r=document.createRange();
  r.selectNodeContents(el);r.collapse(false);
  const s=window.getSelection();s.removeAllRanges();s.addRange(r);
  el.classList.add('kb-cur');cur=el;
  markDirty();
});

fmt.querySelectorAll('[data-cmd]').forEach(btn=>{
  btn.addEventListener('mousedown',e=>{
    e.preventDefault();
    document.execCommand(btn.dataset.cmd,false,null);
    if(cur)updateFmt();
    markDirty();
  });
});

function doLink(){
  const sel=window.getSelection();
  const node=sel&&sel.anchorNode?sel.anchorNode.parentElement:null;
  const a=node?node.closest('a'):null;
  if(a){const url=prompt('链接地址',a.href);if(url!==null){a.href=url;markDirty();}}
  else{const url=prompt('链接地址');if(url){document.execCommand('createLink',false,url);markDirty();}}
}
document.getElementById('fmt-link').addEventListener('mousedown',e=>{e.preventDefault();doLink();});
document.getElementById('fmt-unlink').addEventListener('mousedown',e=>{
  e.preventDefault();document.execCommand('unlink');markDirty();
});

document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key==='s'){e.preventDefault();doSave();}
  if((e.metaKey||e.ctrlKey)&&e.key==='k'){e.preventDefault();doLink();}
});

content.addEventListener('input',markDirty);
function markDirty(){dirty=true;statusEl.textContent='未保存';statusEl.style.color='#e08000';}

async function doSave(){
  saveBtn.disabled=true;saveBtn.textContent='保存中…';
  const clone=content.cloneNode(true);
  clone.querySelectorAll('.kb-cur').forEach(el=>el.classList.remove('kb-cur'));
  try{
    const res=await fetch('/api/notes/'+encodeURIComponent(FILENAME),{
      method:'PUT',headers:{'Content-Type':'text/html; charset=utf-8'},body:clone.innerHTML
    });
    const d=await res.json();
    if(d.ok){dirty=false;statusEl.textContent='已保存';statusEl.style.color='#1a7f37';}
    else{statusEl.textContent='失败：'+(d.error||'');statusEl.style.color='#cc3333';}
  }catch(err){statusEl.textContent='网络错误';statusEl.style.color='#cc3333';}
  saveBtn.disabled=false;saveBtn.textContent='保存';
}

saveBtn.addEventListener('click',doSave);
window.addEventListener('beforeunload',e=>{if(dirty)e.preventDefault();});
})();"""


def _build_edit_page(filename: str, raw: str) -> str:
    import html as _html

    head_m      = re.search(r"<head[^>]*>([\s\S]*?)</head>", raw, re.IGNORECASE)
    head_content = head_m.group(1) if head_m else '<link rel="stylesheet" href="/assets/style.css">'

    body_open_m  = re.search(r"<body[^>]*>",    raw, re.IGNORECASE)
    body_close_m = re.search(r"</body\s*>", raw, re.IGNORECASE)
    body_inner = (
        raw[body_open_m.end():body_close_m.start()]
        if body_open_m and body_close_m else raw
    )

    esc = _html.escape(filename)
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f'  <meta name="kb-filename" content="{esc}">\n'
        + head_content + "\n"
        + f"  <title>编辑 — {esc}</title>\n"
        + "  <style>" + _EDITOR_CSS + "</style>\n"
        + "</head>\n<body>\n"
        + '<div id="kb-bar">\n'
        + f'  <span id="kb-bar-title">✎ {esc}</span>\n'
        + '  <span id="kb-status"></span>\n'
        + f'  <a id="kb-cancel" href="/notes-manager/{esc}">取消</a>\n'
        + '  <button id="kb-save">保存</button>\n'
        + "</div>\n"
        + '<div id="kb-fmt">\n'
        + '  <select id="fmt-block">\n'
        + '    <option value="p">段落</option>\n'
        + '    <option value="h2">H2</option>\n'
        + '    <option value="h3">H3</option>\n'
        + '    <option value="h4">H4</option>\n'
        + '    <option value="blockquote">引用</option>\n'
        + '    <option value="pre">代码块</option>\n'
        + '  </select>\n'
        + '  <span class="kb-sep"></span>\n'
        + '  <button data-cmd="bold" title="粗体 (⌘B)"><b>B</b></button>\n'
        + '  <button data-cmd="italic" title="斜体 (⌘I)"><i>I</i></button>\n'
        + '  <button data-cmd="removeFormat" title="清除格式">✕</button>\n'
        + '  <span class="kb-sep"></span>\n'
        + '  <button id="fmt-link" title="链接 (⌘K)">🔗</button>\n'
        + '  <button id="fmt-unlink" title="移除链接">⛓</button>\n'
        + "</div>\n"
        + '<div id="kb-content" contenteditable="true">\n'
        + body_inner + "\n"
        + "</div>\n"
        + "<script>\n" + _EDITOR_JS + "\n</script>\n"
        + "</body>\n</html>"
    )


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

    def do_PUT(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path   = urllib.parse.unquote(parsed.path)

        if not path.startswith("/api/notes/"):
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return

        filename = path[len("/api/notes/"):]
        if "/" in filename or "\\" in filename or filename.startswith("."):
            self.send_json({"ok": False, "error": "invalid filename"}, 400)
            return

        filepath = NOTES_DIR / filename
        if not filepath.is_file():
            self.send_json({"ok": False, "error": "not found"}, 404)
            return

        length = int(self.headers.get("Content-Length", 0))
        new_body = self.rfile.read(length).decode("utf-8")

        # Reconstruct full document: preserve original <head> and <body> tag,
        # replace only the body inner content.
        raw = filepath.read_text(encoding="utf-8")
        body_open_m = re.search(r"<body[^>]*>", raw, re.IGNORECASE)
        body_close_m = re.search(r"</body\s*>", raw, re.IGNORECASE)
        if body_open_m and body_close_m:
            new_html = raw[:body_open_m.end()] + "\n" + new_body + "\n" + raw[body_close_m.start():]
        else:
            new_html = new_body
        filepath.write_text(new_html, encoding="utf-8")

        import html as _html
        text = _html.unescape(re.sub(r"<[^>]+>", " ", new_body))
        text = " ".join(text.split())
        h1_m = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", new_body, re.IGNORECASE)
        new_title = (
            _html.unescape(re.sub(r"<[^>]+>", "", h1_m.group(1))).strip()
            if h1_m else None
        )
        note_path = f"/notes-manager/{filename}"
        for entry in INDEX:
            if entry.get("path") == note_path:
                entry["body"] = text[:4000]
                if new_title:
                    entry["title"] = new_title
                break
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(INDEX, f, ensure_ascii=False, indent=2)

        self.send_json({"ok": True})

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
        note_path = f"/notes-manager/{filename}"
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

        # ── 编辑页 ────────────────────────────────────────────────────────────
        elif path.startswith("/edit/"):
            filename = urllib.parse.unquote(path[len("/edit/"):])
            if "/" in filename or "\\" in filename or filename.startswith("."):
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.end_headers()
                return
            filepath = NOTES_DIR / filename
            if not filepath.is_file():
                self.send_response(HTTPStatus.NOT_FOUND)
                self.end_headers()
                return
            page = _build_edit_page(filename, filepath.read_text(encoding="utf-8"))
            self.send_bytes(page.encode("utf-8"), "text/html; charset=utf-8")

        # ── 笔记文件（注入 TOC）───────────────────────────────────────────────
        elif path.startswith("/notes-manager/"):
            filepath = NOTES_DIR / path[len("/notes-manager/"):]
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
    load_index()
    server = ThreadingHTTPServer((HOST, DEFAULT_PORT), Handler)
    print(f"笔记管理器运行在 http://{HOST}:{DEFAULT_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
