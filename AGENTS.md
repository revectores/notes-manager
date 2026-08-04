# Repository Guidelines

## Project Structure & Module Organization

`notes-manager` serves a personal Markdown knowledge base as a searchable, browsable HTML site. This repo contains both the HTTP server and the conversion pipeline that turns source notes into served HTML.

Top-level Python scripts are the main modules. `server.py` runs a stdlib `ThreadingHTTPServer`. `notes_to_html.py` converts durable notes into `notes-html/*.html`, and `search_index.py` maintains `assets/search-index.json`. Ingestion and authoring helpers include `gpt_export_to_markdown.py`, `gemini_export_to_markdown.py`, `gpt_export_to_notes.py`, `rewrite_chat_to_note.py`, `arxiv_to_note.py`, and `update_chat_index.py` (the latter also provides shared HTML-extraction helpers used by `search_index.py`).

Static UI assets live in `assets/`, generated pages live in the flat `notes-html/*.html` output directory, and macOS watcher files live in `launchd/`. Runtime logs are under `logs/`.

The source knowledge base is outside this repo at `~/Library/Mobile Documents/com~apple~CloudDocs/workspace/knowledge/`, hardcoded as `_ICLOUD_KB` in converters. Its source tree is `notes/` for durable discipline-organized notes, which is what gets rendered into `notes-html/`. `chats/` (raw LLM chat exports) is no longer rendered directly into notes-manager; ingestion helpers (`gpt_export_to_notes.py`, `rewrite_chat_to_note.py`) can still turn selected chats into durable notes under `notes/`. Do not assume these directories exist inside this checkout.

## Build, Test, and Development Commands

- `PORT=8024 python3 server.py` starts the local site. On a fresh checkout, `ensure_notes_html()` regenerates missing `notes-html/` and `assets/search-index.json` by invoking `notes_to_html.py`.
- `python3 notes_to_html.py --dry-run` previews durable-note conversion.
- `python3 notes_to_html.py` converts `notes/**/*.md`, copies hand-authored `notes/**/*.html`, and updates the search index.
- `python3 notes_watch.py --interval 5` polls notes for incremental rebuilds, removes generated pages for deleted sources, updates the index, and pings `/api/reload`.
- `./launchd/install.sh` installs the watcher as LaunchAgent `com.rex.notes-watch`.

Pandoc is required for Markdown conversion. OpenAI-backed tools require `OPENAI_API_KEY`; use `--prompt-only` where available to inspect prompts without calling an API.

## Conversion Pipeline

`notes_to_html.py` sets `<meta name="classification">` from the source path relative to `notes/`, tags pages as `paper` or `note` from leading YAML front matter, renumbers nested headings, and restyles trailing reference lists into `<div class="references">`. If a `.md` file has a same-stem `.html` sibling, skip the Markdown file; the hand-crafted HTML wins.

It shares `search_index.py`, which backs up `assets/search-index.json` to `.json.bak` and best-effort reloads the running server.

## Server Routes

Key routes are `/` and `/index.html` for the vanilla JS search UI, `GET /api/search?q=&cls=&cat=&limit=` for token and phrase search, `GET /api/tree` for category counts, `GET /api/reload` for index reloads, `GET /notes-html/*.html` for served notes with injected TOC/back-link/language controls, and `DELETE /api/notes/<filename>` for deleting generated HTML and updating the index.

## Multi-Language Notes

Sibling files with recognized `-<lang>` suffixes from `note_lang.py` (`en`, `zh`, `cn`, `ja`, `jp`, `ko`, `kr`, `fr`, `de`, `es`, `ru`) are grouped into one search-index entry with a `langs` map. `LANG_PRIORITY` chooses the default title, body, and path. `server.py` injects a language switcher when serving a variant.

## Coding Style & Naming Conventions

Use Python 3 stdlib-first patterns already present in the codebase. Keep scripts executable as standalone CLIs, with module-level constants such as `BASE_DIR`, `ASSETS_DIR`, and `INDEX_PATH`. Follow 4-space indentation, use type hints for public helpers, prefer `Path` for filesystem paths, and add concise docstrings for non-obvious behavior. Preserve UTF-8 handling and generated path stability.

## Testing Guidelines

There is no formal test suite yet. Validate with focused command-line checks: run converter `--dry-run` modes first, then run the affected converter and start `server.py`. Verify endpoints with `curl`, especially `/api/tree`, `/api/search?q=...`, and `/api/reload`. When changing multilingual behavior, test sibling files such as `topic-en.md` and `topic-cn.md`.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries, for example `Add notes/chats to HTML conversion pipeline with search index and watcher`. Keep commits focused on one behavior change and mention generated artifacts only when intentionally committed.

Pull requests should describe the user-visible change, list validation commands, and call out required local configuration such as `OPENAI_API_KEY`, pandoc, iCloud paths, or LaunchAgent changes. Include screenshots for UI changes to `index.html` or `assets/style.css`.

## Agent-Specific Instructions

Do not edit generated `notes-html/` files unless the task explicitly targets generated output. Prefer changing converters or shared index logic, then regenerate. Preserve unrelated local changes in this repository.
