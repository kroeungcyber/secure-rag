# srag/ingestion/parsers.py
from __future__ import annotations
import re
from pathlib import Path

TEXT_SUFFIXES = {".md", ".txt", ".rst", ".yaml", ".yml", ".json",
                 ".toml", ".conf", ".ini", ".cfg", ".env", ".sh",
                 ".bash", ".zsh", ".ps1"}


def parse_file(path: str | Path) -> tuple[str, str]:
    """Return (text, title). Raises ValueError for unsupported binary files."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8"), path.stem

    if suffix == ".pdf":
        return _parse_pdf(path), path.stem

    if suffix == ".docx":
        return _parse_docx(path), path.stem

    try:
        return path.read_text(encoding="utf-8"), path.stem
    except UnicodeDecodeError:
        raise ValueError(f"Unsupported or binary file: {path}")


def parse_url(url: str) -> tuple[str, str]:
    """Fetch a URL and convert to markdown. Returns (markdown_text, title)."""
    import httpx
    from markdownify import markdownify

    resp = httpx.get(url, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    title = _extract_title(resp.text) or url
    md = markdownify(resp.text, strip=["script", "style"])
    return md, title


def _parse_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        pages.append(f"[Page {i + 1}]\n{text}")
    return "\n\n".join(pages)


def _parse_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""
