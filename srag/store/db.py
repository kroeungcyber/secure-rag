# srag/store/db.py
from __future__ import annotations
import hashlib
import json
import re
import pysqlite3.dbapi2 as sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import sqlite_vec

from srag.store.models import Document, Chunk


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _serialize(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id          TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            title       TEXT,
            file_type   TEXT,
            ingested_at TEXT,
            chunk_count INTEGER,
            mtime       REAL,
            embed_model TEXT
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id      TEXT REFERENCES documents(id) ON DELETE CASCADE,
            content     TEXT NOT NULL,
            chunk_index INTEGER,
            metadata    TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(
            chunk_id  INTEGER PRIMARY KEY,
            embedding FLOAT[768]
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            content,
            content='chunks',
            content_rowid='id'
        );
        CREATE TABLE IF NOT EXISTS command_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            command   TEXT NOT NULL,
            output    TEXT,
            exit_code INTEGER,
            ran_at    TEXT,
            query_id  TEXT
        );
        CREATE TABLE IF NOT EXISTS query_context (
            id        TEXT PRIMARY KEY,
            question  TEXT NOT NULL,
            chunk_ids TEXT NOT NULL,
            asked_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS incident_notes (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            body             TEXT NOT NULL,
            query_context_id TEXT,
            chunk_ids        TEXT,
            created_at       TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending_commands (
            token      TEXT PRIMARY KEY,
            command    TEXT NOT NULL,
            query_id   TEXT,
            created_at TEXT NOT NULL,
            consumed   INTEGER NOT NULL DEFAULT 0
        );
    """)
    _ensure_column(conn, "documents", "embed_model", "TEXT")
    conn.commit()
    conn.close()


def doc_id(source_path: str) -> str:
    return hashlib.sha256(source_path.encode()).hexdigest()[:16]


def get_document(db_path: str, source_path: str) -> Optional[Document]:
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT id, source_path, title, file_type, ingested_at, chunk_count, mtime, embed_model "
        "FROM documents WHERE id = ?",
        (doc_id(source_path),)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return Document(*row)


def upsert_document(db_path: str, doc: Document) -> None:
    conn = _connect(db_path)
    conn.execute("""
        INSERT INTO documents (id, source_path, title, file_type, ingested_at, chunk_count, mtime, embed_model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, file_type=excluded.file_type,
            ingested_at=excluded.ingested_at, chunk_count=excluded.chunk_count,
            mtime=excluded.mtime, embed_model=excluded.embed_model
    """, (doc.id, doc.source_path, doc.title, doc.file_type,
          doc.ingested_at, doc.chunk_count, doc.mtime, doc.embed_model))
    conn.commit()
    conn.close()


def delete_document(db_path: str, doc_id_: str) -> None:
    conn = _connect(db_path)
    chunk_ids = [r[0] for r in conn.execute(
        "SELECT id FROM chunks WHERE doc_id = ?", (doc_id_,)
    ).fetchall()]
    for cid in chunk_ids:
        conn.execute("DELETE FROM chunk_embeddings WHERE chunk_id = ?", (cid,))
        # chunks_fts is an external-content FTS5 table (content='chunks'):
        # it stores no data of its own and isn't kept in sync automatically,
        # so every chunk insert/delete must mirror into it explicitly.
        conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id_,))
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id_,))
    conn.commit()
    conn.close()


def insert_chunks(db_path: str, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
    conn = _connect(db_path)
    for chunk, embedding in zip(chunks, embeddings):
        cursor = conn.execute(
            "INSERT INTO chunks (doc_id, content, chunk_index, metadata) VALUES (?, ?, ?, ?)",
            (chunk.doc_id, chunk.content, chunk.chunk_index, chunk.metadata_json())
        )
        chunk_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, _serialize(embedding))
        )
        conn.execute(
            "INSERT INTO chunks_fts (rowid, content) VALUES (?, ?)",
            (chunk_id, chunk.content)
        )
    conn.commit()
    conn.close()


def search_chunks(db_path: str, query_embedding: list[float], top_k: int = 5,
                   embed_model: Optional[str] = None) -> list[tuple[Chunk, float]]:
    """Vector search. When embed_model is given, chunks belonging to a
    document embedded with a *different* model are excluded — mixing
    vector spaces from different embedding models produces meaningless
    nearest-neighbor rankings. Legacy rows with no recorded embed_model
    (embedded before this check existed) are kept rather than dropped.
    Overfetches so filtering doesn't starve the result set below top_k.
    """
    conn = _connect(db_path)
    fetch_k = top_k * 5 if embed_model else top_k
    rows = conn.execute("""
        SELECT c.id, c.doc_id, c.content, c.chunk_index, c.metadata, ce.distance, d.embed_model
        FROM chunk_embeddings ce
        JOIN chunks c ON c.id = ce.chunk_id
        LEFT JOIN documents d ON d.id = c.doc_id
        WHERE ce.embedding MATCH ?
          AND k = ?
        ORDER BY ce.distance
    """, (_serialize(query_embedding), fetch_k)).fetchall()
    conn.close()

    results: list[tuple[Chunk, float]] = []
    for r in rows:
        chunk_embed_model = r[6]
        if embed_model and chunk_embed_model and chunk_embed_model != embed_model:
            continue
        results.append((
            Chunk(id=r[0], doc_id=r[1], content=r[2], chunk_index=r[3],
                  metadata=json.loads(r[4] or "{}")),
            r[5],
        ))
        if len(results) >= top_k:
            break
    return results


_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")


def _fts_match_query(text: str) -> Optional[str]:
    """Build a safe FTS5 MATCH expression from free-form user text. Each
    token is individually double-quoted so FTS5 query-syntax characters in
    the raw text (-, :, *, quotes, parens ...) can't break the query or be
    misread as operators; tokens are OR'd together since this is meant as
    a keyword-recall signal, not a strict phrase match. Returns None if the
    text contains no matchable tokens.
    """
    tokens = _FTS_TOKEN_RE.findall(text)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


def search_chunks_fts(db_path: str, query_text: str, top_k: int = 5) -> list[Chunk]:
    """Lexical (BM25) search over chunk content via FTS5. Complements vector
    search for exact terms -- error codes, flags, package/service names --
    that embeddings tend to blur.
    """
    match_query = _fts_match_query(query_text)
    if match_query is None:
        return []
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT c.id, c.doc_id, c.content, c.chunk_index, c.metadata
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        WHERE chunks_fts MATCH ?
        ORDER BY bm25(chunks_fts)
        LIMIT ?
    """, (match_query, top_k)).fetchall()
    conn.close()
    return [
        Chunk(id=r[0], doc_id=r[1], content=r[2], chunk_index=r[3],
              metadata=json.loads(r[4] or "{}"))
        for r in rows
    ]


RRF_K = 60


def hybrid_search_chunks(db_path: str, query_embedding: list[float], query_text: str,
                          top_k: int = 5, embed_model: Optional[str] = None) -> list[tuple[Chunk, float]]:
    """Merge vector (semantic) and FTS5 (lexical/BM25) search via Reciprocal
    Rank Fusion. Vector-only retrieval blurs exact terms IT runbooks are
    full of (error codes, flags, package names); lexical-only retrieval
    misses paraphrases. RRF combines the two ranked lists by position
    rather than raw score, sidestepping the fact that L2 distance and BM25
    live on incompatible scales.
    """
    overfetch = max(top_k * 4, 20)
    vector_results = search_chunks(db_path, query_embedding, top_k=overfetch, embed_model=embed_model)
    fts_results = search_chunks_fts(db_path, query_text, top_k=overfetch)

    scores: dict[int, float] = {}
    chunks_by_id: dict[int, Chunk] = {}

    for rank, (chunk, _distance) in enumerate(vector_results, start=1):
        assert chunk.id is not None, "chunks read back from the DB always have an id"
        scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (RRF_K + rank)
        chunks_by_id[chunk.id] = chunk

    for rank, chunk in enumerate(fts_results, start=1):
        assert chunk.id is not None, "chunks read back from the DB always have an id"
        scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (RRF_K + rank)
        chunks_by_id.setdefault(chunk.id, chunk)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [(chunks_by_id[cid], score) for cid, score in ranked[:top_k]]


def stale_embed_model_count(db_path: str, embed_model: str) -> int:
    """Count documents embedded with a model other than the given one."""
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE embed_model IS NOT NULL AND embed_model != ?",
        (embed_model,)
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def list_documents(db_path: str) -> list[Document]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT id, source_path, title, file_type, ingested_at, chunk_count, mtime, embed_model "
        "FROM documents ORDER BY ingested_at DESC"
    ).fetchall()
    conn.close()
    return [Document(*r) for r in rows]


def log_command(db_path: str, command: str, output: str, exit_code: int,
                ran_at: str, query_id: str) -> None:
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO command_history (command, output, exit_code, ran_at, query_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (command, output, exit_code, ran_at, query_id)
    )
    conn.commit()
    conn.close()


PENDING_COMMAND_TTL_SECONDS = 15 * 60


def create_pending_command(db_path: str, token: str, command: str, query_id: str) -> None:
    """Record that the agent itself proposed `command` for `query_id`. The
    web UI's confirm endpoint may only execute a command that has a matching,
    unconsumed, unexpired row here — it must not execute arbitrary commands
    supplied directly in the confirm request.
    """
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO pending_commands (token, command, query_id, created_at, consumed) "
        "VALUES (?, ?, ?, ?, 0)",
        (token, command, query_id, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()


def consume_pending_command(db_path: str, token: str, command: str, query_id: str) -> bool:
    """Validate and single-use-consume a pending command confirmation.
    Returns True only if `token` exists, is unconsumed, unexpired, and its
    stored command/query_id exactly match what's being confirmed.
    """
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT command, query_id, created_at FROM pending_commands "
        "WHERE token = ? AND consumed = 0",
        (token,)
    ).fetchone()
    if row is None:
        conn.close()
        return False

    stored_command, stored_query_id, created_at = row
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(created_at)).total_seconds()
    valid = (
        stored_command == command
        and stored_query_id == query_id
        and age <= PENDING_COMMAND_TTL_SECONDS
    )
    if valid:
        conn.execute("UPDATE pending_commands SET consumed = 1 WHERE token = ?", (token,))
        conn.commit()
    conn.close()
    return valid


def save_query_context(db_path: str, query_id: str, question: str,
                       chunk_ids: list[str]) -> None:
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO query_context (id, question, chunk_ids, asked_at) "
        "VALUES (?, ?, ?, ?)",
        (query_id, question, json.dumps(chunk_ids),
         datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()


def get_latest_query_context(db_path: str) -> dict | None:
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT id, question, chunk_ids, asked_at FROM query_context "
        "ORDER BY asked_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "question": row[1],
        "chunk_ids": json.loads(row[2]),
        "asked_at": row[3],
    }


def save_note(db_path: str, body: str) -> dict | None:
    ctx = get_latest_query_context(db_path)
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO incident_notes (body, query_context_id, chunk_ids, created_at) "
        "VALUES (?, ?, ?, ?)",
        (
            body,
            ctx["id"] if ctx else None,
            json.dumps(ctx["chunk_ids"]) if ctx else None,
            datetime.now(timezone.utc).isoformat(),
        )
    )
    conn.commit()
    conn.close()
    return ctx


def list_notes(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT n.id, n.body, n.query_context_id, n.chunk_ids, n.created_at, "
        "q.question "
        "FROM incident_notes n "
        "LEFT JOIN query_context q ON q.id = n.query_context_id "
        "ORDER BY n.created_at DESC"
    ).fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "body": r[1],
            "query_context_id": r[2],
            "chunk_ids": json.loads(r[3]) if r[3] else None,
            "created_at": r[4],
            "linked_question": r[5],
        }
        for r in rows
    ]
