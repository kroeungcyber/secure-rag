# srag/ingestion/chunker.py
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CHUNK_SIZE = 512   # approximate tokens
CHUNK_OVERLAP = 64

MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdx"}

_PAGE_RE = re.compile(r"^\[Page (\d+)\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")
_FENCE_RE = re.compile(r"^(```|~~~)")


@dataclass
class RawChunk:
    content: str
    chunk_index: int
    page_number: Optional[int] = None
    section_heading: Optional[str] = None


def chunk_text(text: str, source_path: str) -> list[RawChunk]:
    sections = _split_into_sections(text, markdown=_is_markdown(source_path))
    chunks: list[RawChunk] = []
    idx = 0
    for section in sections:
        for content in _chunk_section(section["text"]):
            chunks.append(RawChunk(
                content=content,
                chunk_index=idx,
                page_number=section.get("page"),
                section_heading=section.get("heading"),
            ))
            idx += 1
    return chunks


def _is_markdown(source_path: str) -> bool:
    if source_path.startswith(("http://", "https://")):
        return True
    return Path(source_path).suffix.lower() in MARKDOWN_SUFFIXES


def _approx_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3)


def _split_into_sections(text: str, markdown: bool = True) -> list[dict]:
    """Split text on ATX headings (# .. ######), tracking heading depth so
    nested headings produce a full breadcrumb (e.g. "Docker > Networking")
    instead of just the innermost title. Heading detection is skipped inside
    fenced code blocks (``` or ~~~) so shell comments / example configs
    embedded in a note don't get mistaken for section breaks, and is skipped
    entirely for non-markdown sources where a leading '#' is just a comment
    character (.sh, .yaml, .conf, ...).
    """
    sections: list[dict] = []
    heading_stack: list[tuple[int, str]] = []
    current_text = ""
    current_page: Optional[int] = None
    in_fence = False
    fence_marker = ""

    def breadcrumb() -> Optional[str]:
        return " > ".join(title for _, title in heading_stack) if heading_stack else None

    def flush():
        if current_text.strip():
            sections.append({"text": current_text, "heading": breadcrumb(), "page": current_page})

    for line in text.split("\n"):
        stripped = line.strip()
        fence_m = _FENCE_RE.match(stripped)
        if fence_m:
            if not in_fence:
                in_fence = True
                fence_marker = fence_m.group(1)
            elif stripped.startswith(fence_marker):
                in_fence = False
            current_text += line + "\n"
            continue

        if in_fence:
            current_text += line + "\n"
            continue

        page_m = _PAGE_RE.match(line)
        heading_m = _HEADING_RE.match(line) if markdown else None

        if page_m:
            flush()
            current_text = ""
            current_page = int(page_m.group(1))
        elif heading_m:
            flush()
            current_text = line + "\n"
            level = len(heading_m.group(1))
            title = heading_m.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
        else:
            current_text += line + "\n"

    flush()
    return sections or [{"text": text, "heading": None, "page": None}]


def _chunk_section(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if _approx_tokens(text) <= CHUNK_SIZE:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    raw_chunks: list[str] = []
    current = ""

    for para in paragraphs:
        candidate = (current + "\n\n" + para).strip() if current else para
        if _approx_tokens(candidate) <= CHUNK_SIZE:
            current = candidate
        else:
            if current:
                raw_chunks.append(current)
            current = para

    if current:
        raw_chunks.append(current)

    # Apply overlap: prepend tail of previous chunk
    overlapped: list[str] = []
    for i, chunk in enumerate(raw_chunks):
        if i > 0:
            prev_words = raw_chunks[i - 1].split()
            tail = prev_words[-CHUNK_OVERLAP:] if len(prev_words) > CHUNK_OVERLAP else prev_words
            chunk = " ".join(tail) + "\n\n" + chunk
        overlapped.append(chunk)

    return overlapped
