# Repository Guidelines

## What this is

`knowledge-manager` serves a personal knowledge base of HTML notes as a searchable, browsable site. **The notes are HTML and nothing else.** There is no Markdown, no pandoc, no conversion pipeline and no build step: the store is read, indexed and served.

Everything in this repo is the reader — `server.py` (stdlib `ThreadingHTTPServer`), `note_index.py` (builds the search index by reading the store), `note_lang.py` (multi-language grouping), `index.html` and `assets/`.

It was called `notes-manager` until August 2026, and its GitHub remote still is — `revectores/notes-manager.git`, left alone the same way paper-notes kept `paper-manager.git`. The name was taken at the time by a browser-authored wiki on port 8054; that app was removed, this one kept port 8024, and 8054 is now unassigned. Anything still saying `notes-manager` or `NOTES_*` is stale: the env vars are `KNOWLEDGE_HTML_PATH`, `KNOWLEDGE_WATCH_INTERVAL` and `KNOWLEDGE_AUTO_WATCH`.

## The note store

The notes live outside this repo, in iCloud like every other app's data:

```
~/Library/Mobile Documents/com~apple~CloudDocs/database/knowledge-manager/notes/
```

`KNOWLEDGE_HTML_PATH` overrides it. The store is flat: one `<name>.html` per note at the top level, with images and any other files beside them at whatever relative path the notes reference. Only top-level `*.html` files are indexed; everything else is served as a static file.

A note is added by putting an HTML file in the store, and edited by editing it. Nothing regenerates it and nothing regenerates from it.

## A note describes itself

The store is the only source of truth, so everything the index needs is read out of each file:

| Read from | Becomes |
|---|---|
| `<title>` | the entry's title |
| `<body>`, tags stripped | the searchable body and result snippets |
| `<meta name="classification" content="a/b/c">` | the crumb, i.e. where the directory tree files it |
| `href="/notes-html/<name>.html"` | outlinks, which invert into the backlinks shown on a page |
| a `-<lang>` filename suffix | grouping into one multi-language entry (see `note_lang.py`) |

**A note is re-filed by editing its own `classification` meta tag** — there is no directory to move it between, and no index to edit. A note with no classification tag sits at the root of the tree.

## Serving and the index

Building the whole index means reading every file, which for a few hundred notes takes under 0.1s. That is cheap enough that there is no persisted index and no incremental update path: any change rebuilds the lot, so the index can never drift from the notes. `reload_index()` does the rebuild; a background thread compares a stat-only snapshot of the store every `KNOWLEDGE_WATCH_INTERVAL` seconds (default 5, `KNOWLEDGE_AUTO_WATCH=0` disables) and rebuilds when it differs, which is how an edit made on another device and carried over by iCloud shows up.

Routes: `/` and `/index.html` for the vanilla-JS search UI, `GET /api/search?q=&cls=&limit=` for token and phrase search, `GET /api/tree` for the category tree, `GET /api/reload` to force a rebuild, `GET /notes-html/*` for a stored note (with TOC, back-link, crumb, language switcher and backlinks injected) or any file beside it, and `DELETE /api/notes/<filename>` to delete a note from the store.

The `/notes-html/` prefix is named after the build directory it replaced, and it stays that way because every stored note links to its siblings with it.

The crumb and the table of contents share one fixed rail (`#kb-rail`) in the left gutter, and the injected script appends the contents *into* the rail rather than onto `<body>`. That is what keeps a deep path like `engineering / computer-science / tools / shell / tmux` from covering 目录 — the two stack in normal flow, so the crumb's height is whatever it needs. The rail is emitted even for an unfiled note, because it is what positions the contents. Below 1440px there is no gutter: the contents is dropped and the crumb becomes a single truncated badge in the corner.

## It looks like paper-notes

Both apps are places you read long-form notes, so they share one visual language: the palette, the Georgia / Noto Serif SC face and the measured column are copied from `../paper-notes/app.css` rather than reinvented. **`:root` in `assets/style.css` is the canonical copy here — `index.html` repeats it and `_TOC_INJECT` inherits it, so a palette change means editing both files, and ideally paper-notes too.** Everything supports `prefers-color-scheme: dark`, which is exactly why nothing in the injected CSS names a colour of its own.

Two things do not carry over, and both are structural rather than cosmetic:

- **The index keeps its two panes.** paper-notes lists one line per paper with nothing to browse by; this app is browsed by a classification tree, so the tree panel stays and only its dress changes.
- **The note stylesheet styles bare tags, not a `.paper-note` wrapper.** A note here *is* the document — pandoc output loaded whole — so `h2`, `table` and `code` are styled directly. Pandoc's own `<style>` sits above the link to this file and sets no colours, so these rules win.

The heading scale is deliberately flat and nothing is muted, because a note's heading levels are whatever its source used — the tmux note runs `h1`/`h3`/`h5`, so an `h5` is a real section here, not an aside. paper-notes greys out `h4`–`h6`; copying that would grey out half this corpus.

`llm-memory-benchmark.html` is the one note with furniture of its own — a 46-row comparison table with colour-coded cells. Those classes are kept and given dark-mode values; the table scrolls inside its `.table-wrapper`, so the page never scrolls sideways.

## Development

```bash
PORT=8024 python3 server.py
KNOWLEDGE_HTML_PATH=/tmp/some-notes PORT=8924 python3 server.py   # against a scratch store
```

Stdlib only, no pip dependencies, no build. There is no test suite; validate by starting the server and checking `/api/tree`, `/api/search?q=...` and a note page with `curl`. When changing multilingual behaviour, test a pair such as `hybrid-retrieval-en.html` / `hybrid-retrieval-cn.html`.

## The Markdown era, and what it left behind

Until August 2026 a note was Markdown in `~/…/CloudDocs/workspace/knowledge/notes/`, pandoc built it into a flat `notes-html/` directory in this repo, and `assets/search-index.json` held the index. `migrate_from_markdown.py` moved that build output into the store, and folded back the one thing the index held that the notes did not — 15 notes had been re-filed by hand after generation, so their `classification` said `chats` while the index knew better. Rebuilding the index from the migrated store reproduces the old one exactly: same 542 entries, crumbs, titles, languages, outlinks and bodies.

The Markdown tree still exists in iCloud and is still its own git repo; it is simply no longer read. `notes-html/` and `assets/search-index.json` are also still on disk, gitignored, as a way back. Deleting all three is safe once the store has proven itself.

Gone with the pipeline: `notes_to_html.py`, `notes_watch.py`, `search_index.py`, `update_chat_index.py`, the `launchd/` watcher and daily-summary agents, and the ingestion scripts that wrote Markdown (`arxiv_to_note.py`, `rewrite_chat_to_note.py`, `gpt_export_to_markdown.py`, `gemini_export_to_markdown.py`, `gpt_export_to_notes.py`, `daily_notes_summary.py`). Also gone is the `category` (paper/note) distinction: it was read from Markdown front matter, and the paper notes it marked moved to `../paper-notes/` in August 2026.

## Chat transcripts live in llm-chat-manager

The Markdown-era ingestion scripts turned exported ChatGPT conversations into notes, so the store carried a `chats` branch and, after hand re-filing, a scatter of transcripts under real topics. All of it was removed in August 2026: 17 notes that were conversations rather than notes — dialogue turns (`class="msg-user"` / `<p>You:</p>`), an `Exported:` header, a `chatgpt.com/c/` source link or the ChatGPT Exporter footer. llm-chat-manager (8065) holds every conversation you have had, so a transcript here was a second, worse copy.

What survived that pass is the line to hold: a note *written* from a chat answer is a note, and stays. What comes back through iCloud with `You:` and `ChatGPT:` in it does not belong here.

## Paper notes live in paper-notes

A note whose subject is one specific paper is not a knowledge-base note — it belongs on that paper's record in `../paper-notes/` (port 8035), beside the paper's metadata and PDF. Notes *about a topic* that happen to cite papers stay here.

## Coding Style

Python 3 stdlib-first, matching what is already here: 4-space indent, `Path` for filesystem paths, type hints on public helpers, concise docstrings for non-obvious behaviour. UI strings and server-side messages are Chinese, as they already are; code comments are English.
