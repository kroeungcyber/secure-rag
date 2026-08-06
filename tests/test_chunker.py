# tests/test_chunker.py
from srag.ingestion.chunker import chunk_text

SHORT_TEXT = "This is a short note about restarting nginx."

LONG_TEXT = "\n\n".join([f"Paragraph {i}: " + "word " * 80 for i in range(10)])

MARKDOWN_TEXT = """# Section One

First paragraph about DNS configuration.

# Section Two

Second paragraph about firewall rules and how to configure them properly.
"""

PDF_TEXT = "[Page 1]\nContent on page one.\n\n[Page 2]\nContent on page two."

NESTED_MARKDOWN = """# Docker

Intro paragraph about Docker.

## Networking

Notes about container networking.

### Common Errors

Bridge network conflicts.

## Volumes

Notes about bind mounts.
"""

FENCED_CODE_MARKDOWN = """# Nginx Troubleshooting

Restart the service like this:

```bash
# restart nginx
systemctl restart nginx
```

That's the fix.
"""

DEEP_HEADING_MARKDOWN = """###### Tiny Heading

Some content under a level six heading.
"""

SHELL_SCRIPT = """#!/bin/bash
# restart nginx
systemctl restart nginx
# reload config
nginx -s reload
"""

def test_short_text_produces_single_chunk():
    chunks = chunk_text(SHORT_TEXT, "/notes/test.md")
    assert len(chunks) == 1
    assert chunks[0].content == SHORT_TEXT

def test_long_text_produces_multiple_chunks():
    chunks = chunk_text(LONG_TEXT, "/notes/long.md")
    assert len(chunks) > 1

def test_chunks_have_sequential_indices():
    chunks = chunk_text(LONG_TEXT, "/notes/long.md")
    for i, c in enumerate(chunks):
        assert c.chunk_index == i

def test_markdown_headings_captured_in_metadata():
    chunks = chunk_text(MARKDOWN_TEXT, "/notes/config.md")
    headings = [c.section_heading for c in chunks if c.section_heading]
    assert "Section One" in headings
    assert "Section Two" in headings

def test_pdf_page_numbers_captured():
    chunks = chunk_text(PDF_TEXT, "/docs/manual.pdf")
    pages = [c.page_number for c in chunks if c.page_number is not None]
    assert 1 in pages
    assert 2 in pages

def test_overlap_content_present_in_consecutive_chunks():
    chunks = chunk_text(LONG_TEXT, "/notes/overlap.md")
    if len(chunks) >= 2:
        last_words_of_first = chunks[0].content.split()[-10:]
        start_of_second = chunks[1].content
        assert any(w in start_of_second for w in last_words_of_first)

def test_nested_headings_produce_full_breadcrumb():
    chunks = chunk_text(NESTED_MARKDOWN, "/notes/docker.md")
    headings = [c.section_heading for c in chunks]
    assert "Docker" in headings
    assert "Docker > Networking" in headings
    assert "Docker > Networking > Common Errors" in headings
    assert "Docker > Volumes" in headings

def test_sibling_heading_resets_nested_breadcrumb():
    chunks = chunk_text(NESTED_MARKDOWN, "/notes/docker.md")
    headings = [c.section_heading for c in chunks]
    # "Volumes" is a sibling of "Networking", not nested under it
    assert "Docker > Networking > Volumes" not in headings

def test_h6_heading_recognized():
    chunks = chunk_text(DEEP_HEADING_MARKDOWN, "/notes/deep.md")
    assert any(c.section_heading == "Tiny Heading" for c in chunks)

def test_hash_inside_fenced_code_block_is_not_a_heading():
    chunks = chunk_text(FENCED_CODE_MARKDOWN, "/notes/nginx.md")
    headings = [c.section_heading for c in chunks]
    assert headings == ["Nginx Troubleshooting"]
    assert any("# restart nginx" in c.content for c in chunks)

def test_non_markdown_source_does_not_split_on_hash_comments():
    chunks = chunk_text(SHELL_SCRIPT, "/notes/restart.sh")
    assert len(chunks) == 1
    assert chunks[0].section_heading is None
