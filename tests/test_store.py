# tests/test_store.py
from srag.store.db import (
    upsert_document, get_document, delete_document,
    insert_chunks, search_chunks, search_chunks_fts, hybrid_search_chunks,
    list_documents, log_command, doc_id,
    stale_embed_model_count, create_pending_command, consume_pending_command,
)
from srag.store.models import Document, Chunk
from datetime import datetime, timezone

def make_doc(path="/notes/test.md", embed_model=""):
    return Document(
        id=doc_id(path), source_path=path, title="Test",
        file_type="md", ingested_at=datetime.now(timezone.utc).isoformat(),
        chunk_count=1, mtime=1000.0, embed_model=embed_model
    )

def test_init_creates_tables(db_path):
    import pysqlite3.dbapi2 as sqlite3
    import sqlite_vec
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' OR type='shadow'"
    ).fetchall()}
    conn.close()
    assert "documents" in tables
    assert "chunks" in tables
    assert "command_history" in tables
    assert "query_context" in tables
    assert "incident_notes" in tables

def test_upsert_and_get_document(db_path):
    doc = make_doc()
    upsert_document(db_path, doc)
    fetched = get_document(db_path, "/notes/test.md")
    assert fetched is not None
    assert fetched.title == "Test"

def test_get_document_returns_none_if_missing(db_path):
    assert get_document(db_path, "/nonexistent.md") is None

def test_insert_and_search_chunks(db_path):
    doc = make_doc()
    upsert_document(db_path, doc)
    chunk = Chunk(id=None, doc_id=doc.id, content="restart the nginx service", chunk_index=0)
    embedding = [0.1] * 768
    insert_chunks(db_path, [chunk], [embedding])
    results = search_chunks(db_path, [0.1] * 768, top_k=1)
    assert len(results) == 1
    assert results[0][0].content == "restart the nginx service"

def test_delete_document_removes_chunks(db_path):
    doc = make_doc()
    upsert_document(db_path, doc)
    chunk = Chunk(id=None, doc_id=doc.id, content="hello", chunk_index=0)
    insert_chunks(db_path, [chunk], [[0.1] * 768])
    delete_document(db_path, doc.id)
    assert get_document(db_path, "/notes/test.md") is None
    results = search_chunks(db_path, [0.1] * 768, top_k=5)
    assert results == []
    assert search_chunks_fts(db_path, "hello", top_k=5) == []

def test_search_chunks_fts_matches_keyword(db_path):
    doc = make_doc()
    upsert_document(db_path, doc)
    chunk = Chunk(id=None, doc_id=doc.id, content="restart the nginx service", chunk_index=0)
    insert_chunks(db_path, [chunk], [[0.1] * 768])

    results = search_chunks_fts(db_path, "nginx", top_k=5)

    assert len(results) == 1
    assert results[0].content == "restart the nginx service"

def test_search_chunks_fts_no_match_returns_empty(db_path):
    doc = make_doc()
    upsert_document(db_path, doc)
    chunk = Chunk(id=None, doc_id=doc.id, content="restart the nginx service", chunk_index=0)
    insert_chunks(db_path, [chunk], [[0.1] * 768])

    assert search_chunks_fts(db_path, "kubernetes", top_k=5) == []

def test_search_chunks_fts_handles_special_characters_without_error(db_path):
    doc = make_doc()
    upsert_document(db_path, doc)
    chunk = Chunk(id=None, doc_id=doc.id, content="error: connection refused (port 443)", chunk_index=0)
    insert_chunks(db_path, [chunk], [[0.1] * 768])

    # FTS5 query syntax chars (":", "(", ")", "-") in raw user text must not
    # raise a syntax error -- they're user input, not a trusted FTS query.
    results = search_chunks_fts(db_path, 'why is port 443 giving "connection refused"?', top_k=5)

    assert len(results) == 1

def test_hybrid_search_surfaces_keyword_match_missed_by_vector_alone(db_path):
    doc = make_doc()
    upsert_document(db_path, doc)
    distractor1 = Chunk(id=None, doc_id=doc.id, content="unrelated topic one", chunk_index=0)
    distractor2 = Chunk(id=None, doc_id=doc.id, content="unrelated topic two", chunk_index=1)
    target = Chunk(id=None, doc_id=doc.id, content="nginx error 502 bad gateway", chunk_index=2)
    insert_chunks(
        db_path,
        [distractor1, distractor2, target],
        [[0.1] * 768, [0.11] * 768, [0.9] * 768],
    )

    # Query embedding is close to the two distractors and far from the
    # target -- a pure vector top_k=2 search returns only the distractors.
    vector_only = search_chunks(db_path, [0.1] * 768, top_k=2)
    assert "nginx error 502 bad gateway" not in [c.content for c, _ in vector_only]

    # Hybrid search's lexical branch matches "nginx"/"502" in target, and
    # RRF fusion is enough to pull it into the top 2 despite its distant
    # vector embedding.
    hybrid = hybrid_search_chunks(db_path, [0.1] * 768, "nginx 502", top_k=2)
    assert "nginx error 502 bad gateway" in [c.content for c, _ in hybrid]

def test_hybrid_search_respects_embed_model_filter_on_vector_branch(db_path):
    doc_a = make_doc("/a.md", embed_model="nomic-embed-text")
    doc_b = make_doc("/b.md", embed_model="embeddinggemma")
    upsert_document(db_path, doc_a)
    upsert_document(db_path, doc_b)
    insert_chunks(db_path, [Chunk(id=None, doc_id=doc_a.id, content="alpha chunk", chunk_index=0)], [[0.1] * 768])
    insert_chunks(db_path, [Chunk(id=None, doc_id=doc_b.id, content="beta chunk", chunk_index=0)], [[0.1] * 768])

    results = hybrid_search_chunks(db_path, [0.1] * 768, "chunk", top_k=5,
                                    embed_model="nomic-embed-text")

    contents = [c.content for c, _ in results]
    assert "alpha chunk" in contents
    # beta's vector hit is excluded by embed_model, but its FTS hit for
    # "chunk" still surfaces it -- lexical relevance isn't tied to the
    # embedding model, only vector nearest-neighbor ranking is.
    assert "beta chunk" in contents

def test_list_documents(db_path):
    upsert_document(db_path, make_doc("/a.md"))
    upsert_document(db_path, make_doc("/b.md"))
    docs = list_documents(db_path)
    assert len(docs) == 2

def test_search_chunks_excludes_chunks_from_different_embed_model(db_path):
    doc_a = make_doc("/a.md", embed_model="nomic-embed-text")
    doc_b = make_doc("/b.md", embed_model="embeddinggemma")
    upsert_document(db_path, doc_a)
    upsert_document(db_path, doc_b)
    insert_chunks(db_path, [Chunk(id=None, doc_id=doc_a.id, content="a", chunk_index=0)], [[0.1] * 768])
    insert_chunks(db_path, [Chunk(id=None, doc_id=doc_b.id, content="b", chunk_index=0)], [[0.1] * 768])

    results = search_chunks(db_path, [0.1] * 768, top_k=5, embed_model="nomic-embed-text")

    contents = [c.content for c, _ in results]
    assert "a" in contents
    assert "b" not in contents

def test_search_chunks_keeps_legacy_rows_with_no_recorded_embed_model(db_path):
    doc = make_doc("/legacy.md", embed_model=None)
    upsert_document(db_path, doc)
    insert_chunks(db_path, [Chunk(id=None, doc_id=doc.id, content="legacy", chunk_index=0)], [[0.1] * 768])

    results = search_chunks(db_path, [0.1] * 768, top_k=5, embed_model="nomic-embed-text")

    assert any(c.content == "legacy" for c, _ in results)

def test_stale_embed_model_count(db_path):
    upsert_document(db_path, make_doc("/a.md", embed_model="nomic-embed-text"))
    upsert_document(db_path, make_doc("/b.md", embed_model="embeddinggemma"))
    upsert_document(db_path, make_doc("/c.md", embed_model=None))

    assert stale_embed_model_count(db_path, "nomic-embed-text") == 1

def test_pending_command_confirm_roundtrip(db_path):
    create_pending_command(db_path, "tok1", "ls -la", "q1")
    assert consume_pending_command(db_path, "tok1", "ls -la", "q1") is True

def test_pending_command_is_single_use(db_path):
    create_pending_command(db_path, "tok1", "ls -la", "q1")
    assert consume_pending_command(db_path, "tok1", "ls -la", "q1") is True
    assert consume_pending_command(db_path, "tok1", "ls -la", "q1") is False

def test_pending_command_rejects_mismatched_command(db_path):
    create_pending_command(db_path, "tok1", "ls -la", "q1")
    assert consume_pending_command(db_path, "tok1", "rm -rf /", "q1") is False

def test_pending_command_rejects_unknown_token(db_path):
    assert consume_pending_command(db_path, "nope", "ls -la", "q1") is False

def test_pending_command_expires(db_path):
    import pysqlite3.dbapi2 as sqlite3
    from datetime import timedelta

    create_pending_command(db_path, "tok1", "ls -la", "q1")
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE pending_commands SET created_at = ? WHERE token = ?", (old_ts, "tok1"))
    conn.commit()
    conn.close()

    assert consume_pending_command(db_path, "tok1", "ls -la", "q1") is False

def test_log_command(db_path):
    log_command(db_path, "ls -la", "file1\nfile2", 0, "2026-06-08T00:00:00Z", "qid1")
    import pysqlite3.dbapi2 as sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT command, exit_code FROM command_history").fetchone()
    conn.close()
    assert row[0] == "ls -la"
    assert row[1] == 0
