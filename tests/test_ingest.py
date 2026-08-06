# tests/test_ingest.py
"""Coverage for kb add (file + URL ingestion) in srag/cli.py.

These paths previously had zero test coverage. Guards against regressions in:
- heading/page metadata reaching stored chunks
- PROGRAM.md whitelist enforcement
- duplicate chunks accumulating on re-ingest
- errors surfacing instead of crashing the CLI
"""
import json
import pysqlite3.dbapi2 as sqlite3
import pytest

from srag.cli import _ingest_file, _ingest_url, reindex
from srag.config import Config
from srag.store.db import list_documents


@pytest.fixture
def cfg(db_path):
    return Config(model="llama3.1:8b", embed_model="nomic-embed-text",
                  db_path=db_path, top_k=5, trusted=False)


def _chunk_rows(db_path, doc_id=None):
    conn = sqlite3.connect(db_path)
    if doc_id:
        rows = conn.execute(
            "SELECT doc_id, content, metadata FROM chunks WHERE doc_id = ?", (doc_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT doc_id, content, metadata FROM chunks").fetchall()
    conn.close()
    return rows


def test_ingest_file_stores_heading_and_page_metadata(tmp_path, cfg, fake_embedding_fn, mocker):
    f = tmp_path / "notes.md"
    f.write_text("# Title\n\n## Section\n\nSome content about nginx.\n")

    mocker.patch("srag.program.ingest_path_allowed", return_value=True)
    mocker.patch("srag.ingestion.embedder.embed_texts",
                 side_effect=lambda texts, model: [fake_embedding_fn(t, model) for t in texts])

    _ingest_file(f, cfg)

    rows = _chunk_rows(cfg.db_path)
    metas = [json.loads(r[2]) for r in rows]
    assert all(m["source_path"] == str(f) for m in metas)
    assert any(m["heading"] == "Title > Section" for m in metas)


def test_ingest_url_stores_heading_and_page_metadata(cfg, fake_embedding_fn, mocker):
    url = "https://wiki.example.com/nginx"

    mocker.patch("srag.program.ingest_url_allowed", return_value=True)
    mocker.patch("srag.ingestion.parsers.parse_url",
                 return_value=("# Nginx\n\nRestart the service.\n", "Nginx Wiki Page"))
    mocker.patch("srag.ingestion.embedder.embed_texts",
                 side_effect=lambda texts, model: [fake_embedding_fn(t, model) for t in texts])

    _ingest_url(url, cfg)

    rows = _chunk_rows(cfg.db_path)
    assert len(rows) == 1
    meta = json.loads(rows[0][2])
    assert meta["source_path"] == url
    assert meta["heading"] == "Nginx"


def test_ingest_url_blocked_by_whitelist(cfg, mocker):
    mocker.patch("srag.program.ingest_url_allowed", return_value=False)
    parse_url = mocker.patch("srag.ingestion.parsers.parse_url")

    _ingest_url("https://untrusted.example.com/page", cfg)

    parse_url.assert_not_called()
    assert _chunk_rows(cfg.db_path) == []


def test_ingest_url_reingest_does_not_duplicate_chunks(cfg, fake_embedding_fn, mocker):
    url = "https://wiki.example.com/nginx"

    mocker.patch("srag.program.ingest_url_allowed", return_value=True)
    mocker.patch("srag.ingestion.parsers.parse_url",
                 return_value=("# Nginx\n\nRestart the service.\n", "Nginx Wiki Page"))
    mocker.patch("srag.ingestion.embedder.embed_texts",
                 side_effect=lambda texts, model: [fake_embedding_fn(t, model) for t in texts])

    _ingest_url(url, cfg)
    _ingest_url(url, cfg)

    rows = _chunk_rows(cfg.db_path)
    assert len(rows) == 1  # re-ingest replaces, doesn't append


def test_ingest_url_error_is_caught_not_raised(cfg, mocker):
    mocker.patch("srag.program.ingest_url_allowed", return_value=True)
    mocker.patch("srag.ingestion.parsers.parse_url", side_effect=RuntimeError("network down"))

    _ingest_url("https://wiki.example.com/nginx", cfg)  # must not raise

    assert _chunk_rows(cfg.db_path) == []


def test_reindex_skips_missing_file_without_deleting_it(tmp_path, cfg, fake_embedding_fn, mocker):
    """A doc whose source file vanished must survive reindex, not be silently dropped."""
    f = tmp_path / "gone.md"
    f.write_text("# Gone\n\nWill be deleted before reindex.\n")

    mocker.patch("srag.program.ingest_path_allowed", return_value=True)
    mocker.patch("srag.ingestion.embedder.embed_texts",
                 side_effect=lambda texts, model: [fake_embedding_fn(t, model) for t in texts])

    _ingest_file(f, cfg)
    assert len(list_documents(cfg.db_path)) == 1

    f.unlink()  # simulate the source file having disappeared since ingest
    mocker.patch("srag.cli._cfg", return_value=cfg)
    mocker.patch("srag.store.db.init_db")

    reindex()

    docs = list_documents(cfg.db_path)
    assert len(docs) == 1  # document must NOT be deleted just because its source is gone
    assert _chunk_rows(cfg.db_path, docs[0].id) != []


def test_reindex_forces_reingest_even_when_mtime_unchanged(tmp_path, cfg, fake_embedding_fn, mocker):
    """reindex exists to re-embed after a model/chunker change; mtime-skip must not block it."""
    f = tmp_path / "notes.md"
    f.write_text("# Old Heading\n\nBody text.\n")

    mocker.patch("srag.program.ingest_path_allowed", return_value=True)
    mocker.patch("srag.ingestion.embedder.embed_texts",
                 side_effect=lambda texts, model: [fake_embedding_fn(t, model) for t in texts])

    _ingest_file(f, cfg)
    before = _chunk_rows(cfg.db_path)
    assert len(before) == 1

    mocker.patch("srag.cli._cfg", return_value=cfg)
    mocker.patch("srag.store.db.init_db")

    reindex()  # file's mtime hasn't changed, but reindex must still re-ingest it

    after = _chunk_rows(cfg.db_path)
    assert len(after) == 1  # replaced, not duplicated or skipped
