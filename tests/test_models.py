# tests/test_models.py
import json
from srag.store.models import Document, Chunk

def test_chunk_metadata_json_roundtrip():
    chunk = Chunk(id=None, doc_id="abc", content="hello", chunk_index=0,
                  metadata={"page": 2, "heading": "Setup"})
    serialized = chunk.metadata_json()
    assert json.loads(serialized) == {"page": 2, "heading": "Setup"}

def test_chunk_from_row():
    row = (1, "docabc", "some content", 3, '{"page": 1}')
    chunk = Chunk.from_row(row)
    assert chunk.id == 1
    assert chunk.doc_id == "docabc"
    assert chunk.metadata == {"page": 1}

def test_document_fields():
    doc = Document(
        id="abc123", source_path="/notes/runbook.md", title="Runbook",
        file_type="md", ingested_at="2026-06-08T00:00:00Z",
        chunk_count=4, mtime=1234567890.0
    )
    assert doc.file_type == "md"
    assert doc.chunk_count == 4
