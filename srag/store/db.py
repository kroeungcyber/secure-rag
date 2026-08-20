# srag/store/db.py
from __future__ import annotations
import hashlib
import json
import math
import re
import pysqlite3.dbapi2 as sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import sqlite_vec

from srag.store.models import Document, Chunk, User, Invite


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _serialize(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def _deserialize(b: bytes) -> list[float]:
    return list(struct.unpack(f"{len(b) // 4}f", b))


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
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'staff',
            status        TEXT NOT NULL DEFAULT 'active',
            created_at    TEXT NOT NULL,
            last_login    TEXT
        );
        CREATE TABLE IF NOT EXISTS invites (
            token       TEXT PRIMARY KEY,
            role        TEXT NOT NULL,
            created_by  TEXT,
            created_at  TEXT NOT NULL,
            expires_at  TEXT NOT NULL,
            claimed_by  TEXT
        );
        CREATE TABLE IF NOT EXISTS onboarding_steps (
            user_id      TEXT NOT NULL,
            step_key     TEXT NOT NULL,
            completed_at TEXT,
            PRIMARY KEY (user_id, step_key)
        );
    """)
    _ensure_column(conn, "documents", "embed_model", "TEXT")
    _ensure_column(conn, "documents", "roles", "TEXT DEFAULT ''")
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
        INSERT INTO documents (id, source_path, title, file_type, ingested_at, chunk_count, mtime, embed_model, roles)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, file_type=excluded.file_type,
            ingested_at=excluded.ingested_at, chunk_count=excluded.chunk_count,
            mtime=excluded.mtime, embed_model=excluded.embed_model
    """, (doc.id, doc.source_path, doc.title, doc.file_type,
          doc.ingested_at, doc.chunk_count, doc.mtime, doc.embed_model, doc.roles))
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
                   embed_model: Optional[str] = None,
                   visible_doc_ids: Optional[set[str]] = None) -> list[tuple[Chunk, float]]:
    """Vector search. When embed_model is given, chunks belonging to a
    document embedded with a *different* model are excluded — mixing
    vector spaces from different embedding models produces meaningless
    nearest-neighbor rankings. Legacy rows with no recorded embed_model
    (embedded before this check existed) are kept rather than dropped.
    When visible_doc_ids is given, chunks from documents outside that set
    are excluded (role-based retrieval scoping). Overfetches so filtering
    doesn't starve the result set below top_k.
    """
    conn = _connect(db_path)
    fetch_k = top_k * 5 if (embed_model or visible_doc_ids is not None) else top_k
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
        if visible_doc_ids is not None and r[1] not in visible_doc_ids:
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


def search_chunks_fts(db_path: str, query_text: str, top_k: int = 5,
                      visible_doc_ids: Optional[set[str]] = None) -> list[Chunk]:
    """Lexical (BM25) search over chunk content via FTS5. Complements vector
    search for exact terms -- error codes, flags, package/service names --
    that embeddings tend to blur. When visible_doc_ids is given, chunks from
    documents outside that set are excluded (role-based scoping).
    """
    match_query = _fts_match_query(query_text)
    if match_query is None:
        return []
    fetch_k = top_k * 5 if visible_doc_ids is not None else top_k
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT c.id, c.doc_id, c.content, c.chunk_index, c.metadata
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        WHERE chunks_fts MATCH ?
        ORDER BY bm25(chunks_fts)
        LIMIT ?
    """, (match_query, fetch_k)).fetchall()
    conn.close()
    results: list[Chunk] = []
    for r in rows:
        if visible_doc_ids is not None and r[1] not in visible_doc_ids:
            continue
        results.append(
            Chunk(id=r[0], doc_id=r[1], content=r[2], chunk_index=r[3],
                  metadata=json.loads(r[4] or "{}"))
        )
        if len(results) >= top_k:
            break
    return results


RRF_K = 60


def hybrid_search_chunks(db_path: str, query_embedding: list[float], query_text: str,
                          top_k: int = 5, embed_model: Optional[str] = None,
                          visible_doc_ids: Optional[set[str]] = None) -> list[tuple[Chunk, float]]:
    """Merge vector (semantic) and FTS5 (lexical/BM25) search via Reciprocal
    Rank Fusion. Vector-only retrieval blurs exact terms IT runbooks are
    full of (error codes, flags, package names); lexical-only retrieval
    misses paraphrases. RRF combines the two ranked lists by position
    rather than raw score, sidestepping the fact that L2 distance and BM25
    live on incompatible scales. visible_doc_ids scopes both branches to the
    caller's allowed documents (role-based access).
    """
    overfetch = max(top_k * 4, 20)
    vector_results = search_chunks(db_path, query_embedding, top_k=overfetch,
                                    embed_model=embed_model, visible_doc_ids=visible_doc_ids)
    fts_results = search_chunks_fts(db_path, query_text, top_k=overfetch,
                                    visible_doc_ids=visible_doc_ids)

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


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else list(v)


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def max_cosine_similarity(db_path: str, query_embedding: list[float],
                          chunk_ids: list[int]) -> float:
    """Scale-invariant relevance signal (0..1) between a query embedding and the
    stored embeddings of the given chunks. Used as a retrieval-relevance gate:
    the raw vec0 L2 distance is not comparable across models, but cosine
    similarity of the (normalised) vectors is. Returns 0.0 if chunk_ids is
    empty or no embedding can be read."""
    if not chunk_ids:
        return 0.0
    conn = _connect(db_path)
    placeholders = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        f"SELECT embedding FROM chunk_embeddings WHERE chunk_id IN ({placeholders})",
        list(chunk_ids),
    ).fetchall()
    conn.close()
    q = _normalize(query_embedding)
    best = 0.0
    for (blob,) in rows:
        if blob is None:
            continue
        best = max(best, _cosine(q, _normalize(_deserialize(blob))))
    return best


def stale_embed_model_count(db_path: str, embed_model: str) -> int:
    """Count documents embedded with a model other than the given one."""
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE embed_model IS NOT NULL AND embed_model != ?",
        (embed_model,)
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def list_documents(db_path: str, visible_doc_ids: Optional[set[str]] = None) -> list[Document]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT id, source_path, title, file_type, ingested_at, chunk_count, mtime, embed_model, roles "
        "FROM documents ORDER BY ingested_at DESC"
    ).fetchall()
    conn.close()
    docs = [Document(*r) for r in rows]
    if visible_doc_ids is not None:
        docs = [d for d in docs if d.id in visible_doc_ids]
    return docs


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


# ── Document role scoping ─────────────────────────────────────────────

def set_document_roles(db_path: str, doc_id_: str, roles: str) -> bool:
    """Set the comma-separated role list for a document. Returns False if the
    document does not exist. An empty string means 'visible to all roles'."""
    conn = _connect(db_path)
    cursor = conn.execute(
        "UPDATE documents SET roles = ? WHERE id = ?", (roles, doc_id_)
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def get_document_roles(db_path: str, doc_id_: str) -> Optional[str]:
    conn = _connect(db_path)
    row = conn.execute("SELECT roles FROM documents WHERE id = ?", (doc_id_,)).fetchone()
    conn.close()
    return row[0] if row is not None else None


def visible_document_ids(db_path: str, role: str) -> Optional[set[str]]:
    """Return the set of document ids a role may retrieve. ``admin`` returns
    None (everything). Other roles see documents whose roles tag is empty
    (public) or explicitly lists that role."""
    if role == "admin":
        return None
    conn = _connect(db_path)
    rows = conn.execute("SELECT id, roles FROM documents").fetchall()
    conn.close()
    allowed: set[str] = set()
    for doc_id_, roles in rows:
        role_tags = [t.strip() for t in (roles or "").split(",") if t.strip()]
        if not role_tags or role in role_tags:
            allowed.add(doc_id_)
    return allowed


# ── Users ─────────────────────────────────────────────────────────────

def create_user(db_path: str, user: User) -> None:
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, status, created_at, last_login) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user.id, user.username, user.password_hash, user.role, user.status,
         user.created_at, user.last_login or None)
    )
    conn.commit()
    conn.close()


def get_user_by_id(db_path: str, user_id: str) -> Optional[User]:
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT id, username, password_hash, role, status, created_at, last_login "
        "FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return User(id=row[0], username=row[1], password_hash=row[2], role=row[3],
                status=row[4], created_at=row[5], last_login=row[6] or "")


def get_user_by_username(db_path: str, username: str) -> Optional[User]:
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT id, username, password_hash, role, status, created_at, last_login "
        "FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return User(id=row[0], username=row[1], password_hash=row[2], role=row[3],
                status=row[4], created_at=row[5], last_login=row[6] or "")


def list_users(db_path: str) -> list[User]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT id, username, password_hash, role, status, created_at, last_login "
        "FROM users ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [
        User(id=r[0], username=r[1], password_hash=r[2], role=r[3],
             status=r[4], created_at=r[5], last_login=r[6] or "")
        for r in rows
    ]


def update_user(db_path: str, user_id: str, role: Optional[str] = None,
                status: Optional[str] = None) -> bool:
    """Update a user's role and/or status. Returns False if the user is missing."""
    conn = _connect(db_path)
    sets: list[str] = []
    params: list = []
    if role is not None:
        sets.append("role = ?")
        params.append(role)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if not sets:
        conn.close()
        return False
    params.append(user_id)
    cursor = conn.execute(
        f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def record_login(db_path: str, user_id: str) -> None:
    conn = _connect(db_path)
    conn.execute(
        "UPDATE users SET last_login = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), user_id)
    )
    conn.commit()
    conn.close()


# ── Invites ───────────────────────────────────────────────────────────

def create_invite(db_path: str, invite: Invite) -> None:
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO invites (token, role, created_by, created_at, expires_at, claimed_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (invite.token, invite.role, invite.created_by, invite.created_at,
         invite.expires_at, invite.claimed_by or None)
    )
    conn.commit()
    conn.close()


def get_invite(db_path: str, token: str) -> Optional[Invite]:
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT token, role, created_by, created_at, expires_at, claimed_by "
        "FROM invites WHERE token = ?", (token,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return Invite(token=row[0], role=row[1], created_by=row[2] or "",
                  created_at=row[3], expires_at=row[4], claimed_by=row[5] or "")


def consume_invite(db_path: str, token: str, username: str) -> bool:
    """Mark an invite as claimed. Returns False if the invite is missing or
    already claimed. A unique-claim race is handled by the WHERE clause."""
    conn = _connect(db_path)
    cursor = conn.execute(
        "UPDATE invites SET claimed_by = ? WHERE token = ? AND claimed_by IS NULL",
        (username, token)
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def list_invites(db_path: str) -> list[Invite]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT token, role, created_by, created_at, expires_at, claimed_by "
        "FROM invites ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [
        Invite(token=r[0], role=r[1], created_by=r[2] or "",
               created_at=r[3], expires_at=r[4], claimed_by=r[5] or "")
        for r in rows
    ]


# ── Onboarding ────────────────────────────────────────────────────────

def complete_onboarding_step(db_path: str, user_id: str, step_key: str) -> None:
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO onboarding_steps (user_id, step_key, completed_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, step_key) DO UPDATE SET completed_at = excluded.completed_at",
        (user_id, step_key, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()


def list_onboarding_steps(db_path: str, user_id: str) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT step_key, completed_at FROM onboarding_steps WHERE user_id = ?",
        (user_id,)
    ).fetchall()
    conn.close()
    return [{"step_key": r[0], "completed_at": r[1]} for r in rows]
