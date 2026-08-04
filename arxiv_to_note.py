#!/usr/bin/env python3
"""
Introduce an arXiv paper as a durable knowledge note.

Given an arXiv id (or URL), fetch the paper's metadata from the arXiv API, then ask
an OpenAI-compatible model -- with extended thinking and web search -- to research
the paper and write an introductory Markdown note. The note is written under the
notes tree using the paper's title as the filename. Once the note has been
generated, a second (non-search) model call inspects the note's content together
with the existing notes tree and chooses the directory the note belongs in.

The English note is written as "<title>-en.md", and a Simplified Chinese
translation is written alongside it as "<title>-cn.md", so the two are grouped
as one multi-language note (see note_lang.py).

Usage:
    python3 arxiv_to_note.py 2401.12345 --prompt-only
    OPENAI_API_KEY=... python3 arxiv_to_note.py https://arxiv.org/abs/2401.12345
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from rewrite_chat_to_note import (
    infer_notes_root,
    path_with_suffix,
    slugify_note_filename,
    write_text,
)


DEFAULT_MODEL = "gpt-5.5"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_MAX_OUTPUT_TOKENS = 32000
ARXIV_API_URL = "http://export.arxiv.org/api/query"
# Front matter marking a note as paper-centric for notes_to_html.py's
# <meta name="category" content="paper"> tagging (see search_index.py).
PAPER_FRONT_MATTER = "---\ncategory: paper\n---\n\n"
ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


@dataclass
class ArxivPaper:
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    primary_category: str
    categories: list[str]
    published: str
    updated: str
    abs_url: str
    pdf_url: str
    comment: str | None = None


def normalize_arxiv_id(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r"^arxiv:", "", raw, flags=re.IGNORECASE)

    match = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", raw)
    if match:
        return match.group(1)

    match = re.search(r"([a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)", raw)
    if match:
        return match.group(1)

    raise ValueError(f"Could not parse an arXiv id from: {raw}")


def fetch_arxiv_metadata(arxiv_id: str) -> ArxivPaper:
    query = urllib.parse.urlencode({"id_list": arxiv_id})
    url = f"{ARXIV_API_URL}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "arxiv_to_note/1.0"})

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw_xml = response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to fetch arXiv metadata for {arxiv_id}: {exc}") from exc

    return parse_arxiv_entry(raw_xml, arxiv_id)


def parse_arxiv_entry(raw_xml: bytes, arxiv_id: str) -> ArxivPaper:
    root = ET.fromstring(raw_xml)
    entry = root.find("atom:entry", ARXIV_NS)
    if entry is None:
        raise RuntimeError(f"No arXiv entry found for id {arxiv_id}")

    title = " ".join(entry.findtext("atom:title", default="", namespaces=ARXIV_NS).split())
    abstract = " ".join(entry.findtext("atom:summary", default="", namespaces=ARXIV_NS).split())
    authors = [
        author.findtext("atom:name", default="", namespaces=ARXIV_NS).strip()
        for author in entry.findall("atom:author", ARXIV_NS)
    ]
    categories = [c.attrib.get("term", "") for c in entry.findall("atom:category", ARXIV_NS)]

    primary = entry.find("arxiv:primary_category", ARXIV_NS)
    primary_category = (
        primary.attrib.get("term", "") if primary is not None
        else (categories[0] if categories else "")
    )

    comment = entry.findtext("arxiv:comment", default=None, namespaces=ARXIV_NS)

    return ArxivPaper(
        arxiv_id=arxiv_id,
        title=title,
        authors=[a for a in authors if a],
        abstract=abstract,
        primary_category=primary_category,
        categories=categories,
        published=entry.findtext("atom:published", default="", namespaces=ARXIV_NS).strip(),
        updated=entry.findtext("atom:updated", default="", namespaces=ARXIV_NS).strip(),
        abs_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        comment=comment.strip() if comment else None,
    )


def build_source_material(paper: ArxivPaper) -> str:
    lines = [
        "# arXiv Metadata",
        "",
        f"- arXiv ID: {paper.arxiv_id}",
        f"- Title: {paper.title}",
        f"- Authors: {', '.join(paper.authors)}",
        f"- Primary category: {paper.primary_category}",
        f"- Categories: {', '.join(paper.categories)}",
        f"- Published: {paper.published}",
        f"- Updated: {paper.updated}",
        f"- Abstract page: {paper.abs_url}",
        f"- PDF: {paper.pdf_url}",
    ]
    if paper.comment:
        lines.append(f"- Comments: {paper.comment}")
    lines += ["", "## Abstract", "", paper.abstract]
    return "\n".join(lines)


def build_research_prompt(paper: ArxivPaper, note_title: str | None = None) -> str:
    title = note_title or paper.title
    references_instruction = (
        "Cite sources as numbered citations. "
        "Inline citations must be superscript intra-page links in exactly this form: "
        '<sup><a href="#ref1">[1]</a></sup>. '
        "Do not add a References heading. End the note with an `<hr/>` on its own line, "
        "then one reference per line (each its own paragraph, separated by a blank line) "
        "using matching anchors and Markdown links in exactly this form: "
        '<a id="ref1"></a>[1] [Source Name](https://example.com). '
        f"Always include the arXiv abstract page as reference [1]: "
        f'<a id="ref1"></a>[1] [{paper.title}]({paper.abs_url}). '
        "Add further references for any official code repositories, blog posts, or follow-up "
        "discussion you use, and cite specific factual claims rather than only listing sources."
    )

    return f"""You are researching an arXiv paper to write a durable knowledge note.

Use web search to read the paper itself (via the abstract/PDF links below) and any
relevant supplementary material, official code repositories, blog posts, or
follow-up discussion needed to accurately explain it. Think carefully before writing.

Output only the final Markdown note. Do not include analysis, preface, or code fences.

Goal:
- Write an introductory note for the paper titled: {title}
- Explain the motivation, problem setting, method, and key results clearly enough
  for someone who has not read the paper.
- Preserve technical details, definitions, formulas, numbers, implementation facts,
  caveats, and conclusions found in the paper.
- Organize the note with clear Markdown headings, compact paragraphs, tables where
  useful, and concise bullet lists.
- Do not invent facts beyond the paper and the sources you find. If a conclusion is
  an inference, phrase it as an inference.
- Preserve identifiers, hyperparameters, dataset names, model names, and equations
  in backticks or LaTeX as appropriate.
- {references_instruction}

Recommended structure:
- One-paragraph overview (what the paper is and why it matters)
- Background / motivation
- Method / system formulation
- Experiments / results
- Implications / limitations
- `<hr/>` followed by the numbered references (no heading)

Source material:

{build_source_material(paper)}
"""


def build_translation_prompt(note_markdown: str) -> str:
    return f"""Translate the following Markdown knowledge note into Simplified Chinese.

Output only the translated Markdown. Do not include analysis, preface, or code fences.

Rules:
- Preserve the Markdown structure exactly: heading levels, tables (including
  alignment markers), lists, blockquotes, and paragraph breaks.
- Keep all inline code spans, LaTeX (`\\[...\\]` and inline math), HTML tags
  (`<sup>`, `<a>`, `<hr/>`), citation markers (e.g. `<sup><a href="#ref1">[1]</a></sup>`),
  and the anchors/links in the references section completely unchanged.
- Do not translate the references section after the `<hr/>`; copy it verbatim.
- Translate headings, prose, and table cell descriptions into natural, accurate
  Simplified Chinese technical writing. Keep model names, dataset names,
  hyperparameters, numbers, and equations as they are.
- Where it aids clarity, you may keep an English technical term in parentheses
  alongside its Chinese translation on first use.

Note to translate:

{note_markdown}
"""


def lang_paths(base_path: Path) -> tuple[Path, Path]:
    """Derive the "-en" and "-cn" sibling paths for a base note path."""
    return (
        base_path.with_name(f"{base_path.stem}-en{base_path.suffix}"),
        base_path.with_name(f"{base_path.stem}-cn{base_path.suffix}"),
    )


def next_available_base(base_path: Path) -> Path:
    """Find a base path whose "-en"/"-cn" siblings don't already exist."""
    candidate = base_path
    index = 2
    while any(p.exists() for p in lang_paths(candidate)):
        candidate = path_with_suffix(base_path, f"-{index}")
        index += 1
    return candidate


def build_notes_tree(notes_root: Path) -> str:
    """Render the existing notes tree as an indented directory listing.

    Paths are relative to ``notes_root``, one directory per line, indented to
    show nesting. Hidden and underscore-prefixed directories (scratch/asset
    folders, not topic categories) are omitted.
    """

    def walk(dir_path: Path, depth: int) -> list[str]:
        try:
            entries = sorted(
                p for p in dir_path.iterdir()
                if p.is_dir() and not p.name.startswith((".", "_"))
            )
        except OSError:
            return []
        lines: list[str] = []
        for entry in entries:
            lines.append("  " * depth + entry.name + "/")
            lines.extend(walk(entry, depth + 1))
        return lines

    if not notes_root.exists():
        return ""
    return "\n".join(walk(notes_root, 0))


def build_subdir_prompt(title: str, note_markdown: str, notes_tree: str) -> str:
    return f"""You are organizing a personal knowledge base of Markdown notes about
engineering and natural-science topics, arranged in a hierarchical directory tree
under a single notes root.

Below is the current directory tree (one directory per line, indented to show
nesting; all paths are relative to the notes root).

Choose where the following new note belongs. Prefer an existing directory if one
fits well. If nothing fits well, propose a new directory path that extends an
existing parent directory from the tree, following the same naming conventions
(lowercase, hyphenated, English directory names).

Respond with only the relative directory path, using "/" as the separator (e.g.
`engineering/computer-science/artificial-intelligence/large-language-model/llm-memory`).
No explanation, no code fences, no leading or trailing slash.

Current directory tree:

{notes_tree}

New note title: {title}

New note content:

{note_markdown}
"""


def parse_subdir_response(raw: str) -> Path:
    stripped = raw.strip().strip("`")
    line = stripped.splitlines()[0].strip().strip("/") if stripped else ""
    parts = [p for p in re.split(r"[\\/]+", line) if p and p not in (".", "..")]
    return Path(*parts) if parts else Path(".")


def infer_note_subdir_llm(
    title: str,
    note_markdown: str,
    notes_root: Path,
    model: str,
    base_url: str,
    api_key: str,
    reasoning_effort: str,
    max_output_tokens: int,
) -> Path:
    notes_tree = build_notes_tree(notes_root)
    prompt = build_subdir_prompt(title, note_markdown, notes_tree)
    response = call_responses_api(
        prompt,
        model,
        base_url,
        api_key,
        reasoning_effort,
        max_output_tokens,
        use_web_search=False,
        developer_message=(
            "You organize a personal knowledge base of Markdown notes into a "
            "hierarchical directory tree by topic."
        ),
    )
    return parse_subdir_response(response)


def extract_output_text(data: dict) -> str:
    text = data.get("output_text")
    if text:
        return text.strip()

    chunks: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text":
                chunks.append(part.get("text", ""))

    if chunks:
        return "\n".join(chunks).strip()

    if data.get("status") == "incomplete":
        reason = data.get("incomplete_details", {}).get("reason", "unknown")
        raise RuntimeError(
            f"API response is incomplete (reason: {reason}). "
            "Try increasing --max-output-tokens."
        )

    raise RuntimeError(f"Unexpected API response shape: {data}")


def call_responses_api(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    reasoning_effort: str,
    max_output_tokens: int,
    use_web_search: bool = True,
    developer_message: str = (
        "You research arXiv papers and write clean, citation-preserving Markdown notes."
    ),
) -> str:
    endpoint = base_url.rstrip("/") + "/responses"
    payload = {
        "model": model,
        "reasoning": {"effort": reasoning_effort},
        "max_output_tokens": max_output_tokens,
        "input": [
            {"role": "developer", "content": developer_message},
            {"role": "user", "content": prompt},
        ],
    }
    if use_web_search:
        payload["tools"] = [{"type": "web_search"}]

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API request failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"API request failed: {exc}") from exc

    return extract_output_text(data)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Research an arXiv paper with extended thinking and web search, and write an introductory note."
    )
    parser.add_argument(
        "arxiv_id",
        help="arXiv id or URL, e.g. 2401.12345 or https://arxiv.org/abs/2401.12345",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output note markdown file. If omitted, the filename is derived from the paper title and the "
        "containing directory is chosen by an LLM call after the note is generated.",
    )
    parser.add_argument("--title", help="Override the note title (and filename) instead of using the paper's arXiv title.")
    parser.add_argument(
        "--notes-root",
        type=Path,
        help="Root of the notes tree used for inferred output paths and directory-choice context. "
        "Defaults to NOTES_ROOT, the current notes tree, or the iCloud notes directory.",
    )
    parser.add_argument(
        "--print-output-path",
        action="store_true",
        help="Print the resolved output path to stderr.",
    )
    parser.add_argument(
        "--prompt-only",
        action="store_true",
        help="Write the research prompt to the output file instead of calling an API.",
    )
    parser.add_argument(
        "--no-cn",
        action="store_true",
        help="Skip generating the Simplified Chinese translation (\"-cn\" note).",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        help=f"Model name. Defaults to OPENAI_MODEL or {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL),
        help=f"OpenAI-compatible base URL. Defaults to OPENAI_BASE_URL or {DEFAULT_BASE_URL}.",
    )
    parser.add_argument(
        "--reasoning-effort",
        default=os.environ.get("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT),
        choices=["minimal", "low", "medium", "high"],
        help="Extended-thinking effort. Defaults to OPENAI_REASONING_EFFORT or %(default)s.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS)),
        help=f"Max output tokens, including reasoning and search. Defaults to {DEFAULT_MAX_OUTPUT_TOKENS}.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    arxiv_id = normalize_arxiv_id(args.arxiv_id)
    paper = fetch_arxiv_metadata(arxiv_id)

    notes_root = (args.notes_root.expanduser() if args.notes_root else None) or infer_notes_root(Path.cwd())
    title = args.title or paper.title
    filename = slugify_note_filename(title)
    prompt = build_research_prompt(paper, args.title)

    if args.prompt_only:
        base_path = next_available_base(args.output or notes_root / filename)
        en_path, cn_path = lang_paths(base_path)
        if args.print_output_path or args.output is None:
            print(f"Output path (EN): {en_path}", file=sys.stderr)
        write_text(en_path, prompt)
        print(f"Research prompt written: {en_path}", file=sys.stderr)
        if not args.no_cn:
            print("Skipping Chinese translation in --prompt-only mode (requires the English note).", file=sys.stderr)
        return 0

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print(
            "OPENAI_API_KEY is not set. Re-run with --prompt-only or set OPENAI_API_KEY.",
            file=sys.stderr,
        )
        return 2

    note_en = call_responses_api(
        prompt, args.model, args.base_url, api_key, args.reasoning_effort, args.max_output_tokens
    )

    if args.output:
        base_path = args.output
    else:
        subdir = infer_note_subdir_llm(
            title, note_en, notes_root, args.model, args.base_url, api_key,
            args.reasoning_effort, args.max_output_tokens,
        )
        base_path = notes_root / subdir / filename
    base_path = next_available_base(base_path)
    en_path, cn_path = lang_paths(base_path)

    if args.print_output_path or args.output is None:
        print(f"Output path (EN): {en_path}", file=sys.stderr)
        if not args.no_cn:
            print(f"Output path (CN): {cn_path}", file=sys.stderr)

    write_text(en_path, PAPER_FRONT_MATTER + note_en.lstrip())
    print(f"Note written: {en_path}", file=sys.stderr)

    if not args.no_cn:
        translation_prompt = build_translation_prompt(note_en)
        note_cn = call_responses_api(
            translation_prompt,
            args.model,
            args.base_url,
            api_key,
            args.reasoning_effort,
            args.max_output_tokens,
            use_web_search=False,
            developer_message=(
                "You translate technical Markdown knowledge notes into accurate, "
                "natural Simplified Chinese while preserving structure and precision."
            ),
        )
        write_text(cn_path, PAPER_FRONT_MATTER + note_cn.lstrip())
        print(f"Translated note written: {cn_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
