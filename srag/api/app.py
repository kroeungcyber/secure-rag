# srag/api/app.py
from __future__ import annotations
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from srag.api.auth import make_session_token, require_auth
from srag.audit import log_event
from srag.config import load_config, save_config
from srag.store.db import init_db, list_documents, delete_document


@asynccontextmanager
async def lifespan(app):
    # apply_env=False: save_config below must persist only the on-disk
    # config plus the new keys, never a transient SRAG_MODEL/SRAG_EMBED_MODEL
    # env override from load_config.
    cfg = load_config(apply_env=False)
    changed = False
    if not cfg.api_key:
        cfg.api_key = secrets.token_urlsafe(32)
        changed = True
    if not cfg.session_secret:
        cfg.session_secret = secrets.token_urlsafe(32)
        changed = True
    if changed:
        save_config(cfg)
        if not os.environ.get("SRAG_SILENT"):
            print(f"\n=== srag API key ===\n{cfg.api_key}\n=====================\n")
    init_db(cfg.db_path)
    yield


# docs_url/openapi_url disabled: the API schema and route map are not public.
app = FastAPI(title="secure-rag Web UI", docs_url=None, openapi_url=None, lifespan=lifespan)

_STATIC = Path(__file__).parent / "web" / "static"
_TEMPLATES = Path(__file__).parent / "web" / "templates"

app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


class QueryRequest(BaseModel):
    question: str
    top_k: int = 0


class IngestRequest(BaseModel):
    path: Optional[str] = None
    url: Optional[str] = None


class CommandRequest(BaseModel):
    command: str
    query_id: str
    token: str


class LoginRequest(BaseModel):
    key: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def chat_page():
    return (_TEMPLATES / "chat.html").read_text()


@app.get("/docs-ui", response_class=HTMLResponse)
def docs_page():
    return (_TEMPLATES / "docs.html").read_text()


@app.get("/history", response_class=HTMLResponse)
def history_page():
    return (_TEMPLATES / "history.html").read_text()


@app.get("/notes", response_class=HTMLResponse)
def notes_page():
    return (_TEMPLATES / "notes.html").read_text()


@app.post("/api/query", dependencies=[Depends(require_auth)])
def api_query(req: QueryRequest):
    import uuid
    from srag.agent.loop import run_agent
    from srag.store.db import create_pending_command

    cfg = load_config()
    if req.top_k > 0:
        cfg.top_k = req.top_k

    qid_holder: list = []

    def confirm(cmd: str) -> bool:
        # The web chat never auto-executes a proposed command. Instead we
        # record it as pending so /api/command/confirm can later verify a
        # confirmation request actually matches something the agent itself
        # proposed for this query, rather than blindly running whatever
        # command a POST body claims.
        query_id = qid_holder[0] if qid_holder else ""
        pending_token = uuid.uuid4().hex
        create_pending_command(cfg.db_path, pending_token, cmd, query_id)
        return False

    def stream():
        collected_chunks: list = []
        try:
            for token in run_agent(req.question, cfg, confirm_fn=confirm,
                                   collected_chunks=collected_chunks,
                                   query_id_holder=qid_holder):
                yield f"data: {json.dumps({'token': token})}\n\n"
        finally:
            log_event(cfg.db_path, "query",
                      query_id=qid_holder[0] if qid_holder else "",
                      question=req.question,
                      chunk_ids=collected_chunks,
                      source_count=len(collected_chunks))
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/ingest", dependencies=[Depends(require_auth)])
def api_ingest(req: IngestRequest):
    from srag.cli import _ingest_file, _ingest_url

    cfg = load_config()
    if req.url:
        _ingest_url(req.url, cfg)
        log_event(cfg.db_path, "ingest", source=req.url, kind="url")
        return {"status": "ok", "source": req.url}
    if req.path:
        p = Path(req.path)
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"Path not found: {req.path}")
        _ingest_file(p, cfg)
        log_event(cfg.db_path, "ingest", source=req.path, kind="file")
        return {"status": "ok", "source": req.path}
    raise HTTPException(status_code=400, detail="Provide path or url")


@app.get("/api/documents", dependencies=[Depends(require_auth)])
def api_list_documents():
    cfg = load_config()
    docs = list_documents(cfg.db_path)
    return [
        {
            "id": d.id, "title": d.title, "source_path": d.source_path,
            "file_type": d.file_type, "chunk_count": d.chunk_count,
            "ingested_at": d.ingested_at,
        }
        for d in docs
    ]


@app.delete("/api/documents/{doc_id}", dependencies=[Depends(require_auth)])
def api_delete_document(doc_id: str):
    cfg = load_config()
    delete_document(cfg.db_path, doc_id)
    log_event(cfg.db_path, "document.delete", doc_id=doc_id)
    return {"status": "ok"}


@app.get("/api/models", dependencies=[Depends(require_auth)])
def api_models():
    import ollama
    try:
        resp = ollama.list()
        return {"models": [m["name"] for m in resp.get("models", [])]}
    except Exception as e:
        return {"models": [], "error": str(e)}


@app.post("/api/command/confirm", dependencies=[Depends(require_auth)])
def api_run_command(req: CommandRequest):
    from srag.agent.tools import run_command
    from srag.store.db import consume_pending_command

    cfg = load_config()
    if not consume_pending_command(cfg.db_path, req.token, req.command, req.query_id):
        raise HTTPException(
            status_code=403,
            detail="No matching pending command confirmation (unknown token, "
                   "already used, expired, or command/query_id mismatch).",
        )
    try:
        output = run_command(req.command, confirm_fn=lambda cmd: True,
                             db_path=cfg.db_path, query_id=req.query_id)
        return {"output": output}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/notes", dependencies=[Depends(require_auth)])
def api_list_notes():
    from srag.store.db import list_notes

    cfg = load_config()
    return list_notes(cfg.db_path)


@app.post("/api/login")
def api_login(req: LoginRequest, response: Response):
    import secrets as _secrets
    from srag.config import load_config

    cfg = load_config()
    ok = bool(cfg.api_key) and _secrets.compare_digest(req.key, cfg.api_key)
    log_event(cfg.db_path, "auth.login", success=ok)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid API key")
    token = make_session_token(cfg.api_key, cfg.session_secret)
    response.set_cookie(
        "srag_session", token, httponly=True, samesite="lax",
        max_age=2592000, path="/",
    )
    return {"status": "ok"}


@app.post("/api/logout")
def api_logout(response: Response):
    response.delete_cookie("srag_session", path="/")
    return {"status": "ok"}
