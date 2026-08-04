#!/usr/bin/env python3
"""
Rewrite an LLM chat export markdown file into a proper note markdown file.

The script parses common ChatGPT-style markdown exports, removes chat metadata,
builds a strict rewrite prompt, and optionally calls an OpenAI-compatible chat
completion API. If no API key is configured, use --prompt-only to write the
rewrite prompt instead.

Usage:
    python3 scripts/chat_export_to_note.py input.md output.md --prompt-only
    OPENAI_API_KEY=... python3 scripts/chat_export_to_note.py input.md output.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "gpt-5.5"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_NOTES_ROOT = Path(
    os.environ.get(
        "NOTES_ROOT",
        "/Users/rex/Library/Mobile Documents/com~apple~CloudDocs/workspace/knowledge/notes",
    )
)


@dataclass
class ChatExport:
    title: str
    prompts: list[str]
    responses: list[str]
    references: list[tuple[str, str]]
    raw_body: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def strip_front_matter_and_metadata(markdown: str) -> str:
    lines = markdown.splitlines()
    kept: list[str] = []
    metadata_keys = {
        "created",
        "updated",
        "exported",
        "link",
        "messages",
    }

    for line in lines:
        stripped = line.strip()
        normalized = stripped.strip("*").split(":", 1)[0].lower()
        if normalized in metadata_keys:
            continue
        kept.append(line)

    return "\n".join(kept).strip()


def extract_title(markdown: str, input_path: Path) -> str:
    match = re.search(r"^\s*#\s+(.+?)\s*$", markdown, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return input_path.stem


def extract_references(markdown: str) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []

    ref_section = re.search(r"(?ims)^##\s+References\s*$\n(?P<body>.*)$", markdown)
    if ref_section:
        body = ref_section.group("body")
    else:
        body = markdown

    html_ref_pattern = re.compile(
        r"""<a\s+id=["']ref\d+["']></a>\s*
            \[\d+\]\s*
            (?:\[(?P<md_name>[^\]]+)\]\((?P<md_url>[^)]+)\)
            |(?P<html_name>.*?)\s+(?P<html_url>https?://\S+))""",
        re.IGNORECASE | re.VERBOSE,
    )

    for match in html_ref_pattern.finditer(body):
        name = (match.group("md_name") or match.group("html_name") or "").strip()
        url = (match.group("md_url") or match.group("html_url") or "").strip()
        if name and url:
            references.append((clean_reference_name(name), clean_url(url)))

    md_links = re.findall(r"\[([^\]]+)\]\((https?://[^)]+)\)", body)
    for name, url in md_links:
        pair = (clean_reference_name(name), clean_url(url))
        if pair not in references:
            references.append(pair)

    bare_urls = re.findall(r"(?<!\()https?://[^\s)>]+", body)
    for url in bare_urls:
        clean = clean_url(url)
        if not any(existing_url == clean for _, existing_url in references):
            references.append((clean, clean))

    return references


def clean_reference_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip(" .")


def clean_url(url: str) -> str:
    return url.rstrip(".,;)")


def extract_blocks(markdown: str, heading_pattern: str) -> list[str]:
    boundary = r"(?:Prompt:|Response(?:\s*\([^)]+\))?:|References)"
    pattern = re.compile(
        rf"(?ims)^##\s+{heading_pattern}\s*$\n"
        rf"(?P<body>.*?)(?=^##\s+{boundary}\s*$|\Z)"
    )
    return [clean_block(match.group("body")) for match in pattern.finditer(markdown)]


def clean_block(block: str) -> str:
    block = re.sub(r"(?ims)^##\s+References\s*$.*\Z", "", block)
    return block.strip()


def parse_chat_export(markdown: str, input_path: Path) -> ChatExport:
    cleaned = strip_front_matter_and_metadata(markdown)
    title = extract_title(cleaned, input_path)
    prompts = extract_blocks(cleaned, r"Prompt:")
    responses = extract_blocks(cleaned, "Response(?:\\s*\\([^)]+\\))?:")

    if not prompts and not responses:
        body_without_refs = re.sub(r"(?ims)^##\s+References\s*$.*\Z", "", cleaned).strip()
        responses = [body_without_refs]

    return ChatExport(
        title=title,
        prompts=prompts,
        responses=responses,
        references=extract_references(cleaned),
        raw_body=cleaned,
    )



def merge_chat_exports(chats: list[ChatExport]) -> ChatExport:
    if not chats:
        raise ValueError("At least one chat export is required")

    title = chats[0].title
    prompts: list[str] = []
    responses: list[str] = []
    references: list[tuple[str, str]] = []
    seen_refs: set[tuple[str, str]] = set()
    raw_sections: list[str] = []

    for source_index, chat in enumerate(chats, start=1):
        raw_sections.append(f"# Source {source_index}: {chat.title}\n\n{chat.raw_body}")
        for prompt in chat.prompts:
            prompts.append(f"Source {source_index} ({chat.title})\n\n{prompt}")
        for response in chat.responses:
            responses.append(f"Source {source_index} ({chat.title})\n\n{response}")
        for reference in chat.references:
            if reference not in seen_refs:
                references.append(reference)
                seen_refs.add(reference)

    if len(chats) > 1:
        title = common_title(chats) or title

    return ChatExport(
        title=title,
        prompts=prompts,
        responses=responses,
        references=references,
        raw_body="\n\n".join(raw_sections),
    )


def common_title(chats: list[ChatExport]) -> str | None:
    normalized = [normalize_title(chat.title) for chat in chats]
    if not normalized:
        return None
    if all(title == normalized[0] for title in normalized):
        return normalized[0]

    # Prefer a compact common prefix when exports are split by question but share a topic token.
    token_sets = [set(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", title)) for title in normalized]
    common = set.intersection(*token_sets) if token_sets else set()
    for title in normalized:
        for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", title):
            if token in common and len(token) >= 3:
                return token
    return None

def build_source_material(chat: ChatExport) -> str:
    sections: list[str] = [f"# Source Chat Title\n\n{chat.title}"]

    for index, prompt in enumerate(chat.prompts, start=1):
        sections.append(f"## User Prompt {index}\n\n{prompt}")

    for index, response in enumerate(chat.responses, start=1):
        sections.append(f"## Assistant Response {index}\n\n{response}")

    if chat.references:
        refs = "\n".join(
            f"[{index}] {name}: {url}"
            for index, (name, url) in enumerate(chat.references, start=1)
        )
        sections.append(f"## Extracted References\n\n{refs}")

    return "\n\n".join(sections).strip()


def build_rewrite_prompt(chat: ChatExport, note_title: str | None = None) -> str:
    title = note_title or normalize_title(chat.title)
    references_instruction = (
        "If source references are present, preserve them as numbered citations. "
        "Inline citations must be superscript intra-page links in exactly this form: "
        '<sup><a href="#ref1">[1]</a></sup>. '
        "End the note with a `<hr/>` followed by matching anchors and Markdown links in exactly this form: "
        '<a id="ref1"></a>[1] [Source Name](https://example.com). '
        "Do not add a 'References' heading; the `<hr/>` separator is the only marker for this section. "
        "Renumber citations sequentially if needed, and cite specific factual claims rather than only listing sources."
    )

    return f"""You are rewriting an exported LLM chat into a durable knowledge note.

Output only the final Markdown note. Do not include analysis, preface, or code fences.

Goal:
- Convert the chat transcript into a proper note titled: {title}
- Remove question-answering style, chat metadata, timestamps, exported links, and conversational phrases.
- Merge duplicate or overlapping content.
- Preserve technical details, definitions, numbers, implementation facts, caveats, and conclusions.
- Organize the note with clear Markdown headings, compact paragraphs, tables where useful, and concise bullet lists.
- Keep the language and terminology style of the source. If the source is mostly Chinese, write the note in Chinese.
- Do not invent facts beyond the source material. If a conclusion is an inference, phrase it as an inference.
- Preserve code identifiers, command-line flags, file names, API calls, and data schemas in backticks.
- {references_instruction}

Recommended structure when applicable:
- One-paragraph overview
- Dataset / background
- Core concepts or system formulation
- Method or implementation details
- Experimental findings or conclusions
- Implications / limitations
- References

Source material:

{build_source_material(chat)}
"""


def normalize_title(title: str) -> str:
    title = re.sub(r"^(论文解读[:：]\s*)", "", title).strip()
    title = re.sub(r"\s+", " ", title)
    return title or "Untitled Note"



def slugify_note_filename(title: str) -> str:
    title = normalize_title(title)
    title = re.sub(r"[\\/:*?\"<>|]", "-", title)
    title = re.sub(r"\s+", " ", title).strip(" .-_")
    return (title or "Untitled Note") + ".md"


def infer_notes_root(input_path: Path) -> Path:
    env_root = os.environ.get("NOTES_ROOT")
    if env_root:
        return Path(env_root).expanduser()

    resolved_input = input_path.expanduser().resolve()
    for parent in [resolved_input.parent, *resolved_input.parents]:
        if parent.name == "chats":
            candidate = parent.parent / "notes"
            if candidate.exists():
                return candidate

    cwd = Path.cwd().resolve()
    if (cwd / "engineering").exists() or (cwd / "natural-science").exists():
        return cwd

    for parent in [cwd, *cwd.parents]:
        if parent.name == "notes":
            return parent

    return DEFAULT_NOTES_ROOT


def infer_note_subdir(chat: ChatExport) -> Path:
    haystack = "\n".join([chat.title, chat.raw_body]).lower()

    rules: list[tuple[tuple[str, ...], str]] = [
        (
            ("q4_k_m", "q4 k m", "gguf", "llama.cpp", "k-quants", "kv cache", "量化"),
            "engineering/computer-science/artificial-intelligence/large-language-model/llm-inference/llm-quantization",
        ),
        (
            ("atm-bench", "memory benchmark", "llm-memory-benchmark", "长期记忆", "记忆评测"),
            "engineering/computer-science/artificial-intelligence/large-language-model/llm-memory/llm-memory-benchmark",
        ),
        (
            ("llm memory", "agent memory", "memory system", "记忆系统"),
            "engineering/computer-science/artificial-intelligence/large-language-model/llm-memory",
        ),
        (
            ("benchmark", "基准", "评测"),
            "engineering/computer-science/artificial-intelligence/large-language-model/llm-benchmark",
        ),
        (
            ("fine-tuning", "finetuning", "微调", "lora", "qlora"),
            "engineering/computer-science/artificial-intelligence/large-language-model/llm-finetuning",
        ),
        (
            ("prompt", "提示词"),
            "engineering/computer-science/artificial-intelligence/large-language-model/prompt-engineering",
        ),
        (
            ("large language model", "llm", "大语言模型"),
            "engineering/computer-science/artificial-intelligence/large-language-model",
        ),
        (
            ("bmi", "body mass index", "肥胖", "体重", "medical", "医学"),
            "natural-science/medical-science",
        ),
        (
            ("python", "javascript", "programming", "编程"),
            "engineering/computer-science/programming",
        ),
    ]

    for keywords, subdir in rules:
        if any(keyword in haystack for keyword in keywords):
            return Path(subdir)

    return Path("_inbox")


def infer_output_path(input_path: Path, chat: ChatExport, title_override: str | None) -> Path:
    notes_root = infer_notes_root(input_path)
    title = title_override or chat.title
    return notes_root / infer_note_subdir(chat) / slugify_note_filename(title)


def path_with_suffix(path: Path, suffix: str) -> Path:
    return path.with_name(f"{path.stem}{suffix}{path.suffix}")


def next_available_path(path: Path) -> Path:
    if not path.exists():
        return path

    index = 2
    while True:
        candidate = path_with_suffix(path, f"-{index}")
        if not candidate.exists():
            return candidate
        index += 1


def collision_path(output_path: Path) -> Path:
    return next_available_path(path_with_suffix(output_path, "-2"))


def archive_target_path(source_path: Path) -> Path:
    if source_path.parent.name == "chats":
        archive_dir = source_path.parent.parent / "chats_merged"
    else:
        archive_dir = source_path.parent / "chats_merged"

    target = archive_dir / f"{source_path.name}.ar"

    if not target.exists():
        return target

    index = 2
    while True:
        if source_path.suffix.lower() == ".md":
            candidate = archive_dir / f"{source_path.stem}-{index}{source_path.suffix}.ar"
        else:
            candidate = archive_dir / f"{source_path.name}-{index}.ar"
        if not candidate.exists():
            return candidate
        index += 1


def archive_chat_sources(input_paths: list[Path]) -> list[tuple[Path, Path]]:
    archived: list[tuple[Path, Path]] = []
    for source_path in input_paths:
        target_path = archive_target_path(source_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.rename(target_path)
        archived.append((source_path, target_path))
    return archived


def call_chat_completion(prompt: str, model: str, base_url: str, api_key: str) -> str:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You rewrite chat exports into clean, citation-preserving Markdown notes.",
            },
            {"role": "user", "content": prompt},
        ],
    }

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
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API request failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"API request failed: {exc}") from exc

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected API response shape: {data}") from exc



def looks_like_markdown_chat(path: Path) -> bool:
    return path.suffix.lower() in {".md", ".markdown"} and path.exists()


def resolve_input_and_output_paths(paths: list[Path], output: Path | None) -> tuple[list[Path], Path | None]:
    if output is not None:
        return paths, output

    if len(paths) == 1:
        return paths, None

    # Backward-compatible form: script input1.md input2.md output.md
    # Treat the last path as output only when it does not exist. Existing paths are all inputs.
    if not paths[-1].exists():
        return paths[:-1], paths[-1]

    return paths, None

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite an LLM chat export markdown file into a clean note markdown file."
    )
    parser.add_argument(
        "paths",
        type=Path,
        nargs="+",
        help="Input chat export markdown file(s), followed optionally by an output note path for backward-compatible single-output use.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output note markdown file. If omitted, infer a path under the notes root.",
    )
    parser.add_argument("--title", help="Override the generated note title.")
    parser.add_argument(
        "--notes-root",
        type=Path,
        help="Root of the notes tree used for inferred output paths. Defaults to NOTES_ROOT, a sibling notes directory, or the current notes tree.",
    )
    parser.add_argument(
        "--print-output-path",
        action="store_true",
        help="Print the resolved output path to stderr.",
    )
    parser.add_argument(
        "--prompt-only",
        action="store_true",
        help="Write the rewrite prompt to the output file instead of calling an API.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        help=f"Chat model name. Defaults to OPENAI_MODEL or {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL),
        help=f"OpenAI-compatible base URL. Defaults to OPENAI_BASE_URL or {DEFAULT_BASE_URL}.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    input_paths, output_path = resolve_input_and_output_paths(args.paths, args.output)
    chats = [parse_chat_export(read_text(input_path), input_path) for input_path in input_paths]
    chat = merge_chat_exports(chats)

    if output_path is None:
        if args.notes_root:
            os.environ["NOTES_ROOT"] = str(args.notes_root)
        output_path = infer_output_path(input_paths[0], chat, args.title)

    if args.print_output_path or output_path is None or args.output is None:
        print(f"Output path: {output_path}", file=sys.stderr)

    prompt = build_rewrite_prompt(chat, args.title)
    target_exists = output_path.exists()
    if args.prompt_only:
        if target_exists:
            new_note_path = collision_path(output_path)
            write_text(new_note_path, prompt)
            print(f"Existing output kept: {output_path}", file=sys.stderr)
            print(f"New rewrite prompt written: {new_note_path}", file=sys.stderr)
        else:
            write_text(output_path, prompt)
        return 0

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print(
            "OPENAI_API_KEY is not set. Re-run with --prompt-only or set OPENAI_API_KEY.",
            file=sys.stderr,
        )
        return 2

    note = call_chat_completion(prompt, args.model, args.base_url, api_key)
    if target_exists:
        new_note_path = collision_path(output_path)
        write_text(new_note_path, note)
        print(f"Existing output kept: {output_path}", file=sys.stderr)
        print(f"New note written: {new_note_path}", file=sys.stderr)
    else:
        write_text(output_path, note)

    for source_path, archived_path in archive_chat_sources(input_paths):
        print(f"Archived source chat: {source_path} -> {archived_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
