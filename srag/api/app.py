# srag/api/app.py
from __future__ import annotations
import json
import os
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from srag.api.auth import (
    hash_password, make_user_session_token,
    require_auth, require_role, verify_password,
)
from srag.audit import log_event
from srag.config import load_config, save_config
from srag.store.db import (
    init_db, list_documents, delete_document,
    create_user, get_user_by_username, list_users, update_user, record_login,
    create_invite, get_invite, consume_invite, list_invites,
    set_document_roles, visible_document_ids,
    list_onboarding_steps, complete_onboarding_step,
)
from srag.store.models import User, Invite

USERNAME_RE = re.compile(r"[A-Za-z0-9._-]{2,32}")

ROLES = ("admin", "staff", "field")

ONBOARDING_STEPS = [
    {"key": "welcome", "title": "Welcome",
     "hint": "Ask questions about your organisation's documents in plain language."},
    {"key": "ask", "title": "Ask your first question",
     "hint": "Try asking about a procedure you use every day."},
    {"key": "help", "title": "Know who to contact",
     "hint": "If an answer looks wrong or missing, contact IT support."},
]


@asynccontextmanager
async def lifespan(app):
    # PROGRAM.md is the single source of truth: sync models/top_k/trusted into
    # config.toml before the key bootstrap reads it.
    from srag.program import reload_config_from_program
    reload_config_from_program()
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
    key: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


class ClaimRequest(BaseModel):
    token: str
    username: str
    password: str


class InviteRequest(BaseModel):
    role: str = "staff"


class UserUpdateRequest(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = None


class RolesRequest(BaseModel):
    roles: list[str] = []


class StepRequest(BaseModel):
    step: str


# ── Pages ────────────────────────────────────────────────────────────

def _page(name: str) -> str:
    return (_TEMPLATES / name).read_text()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def chat_page():
    return _page("chat.html")


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return _page("login.html")


@app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_role("admin"))])
def admin_page():
    return _page("admin.html")


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding_page():
    return _page("onboarding.html")


@app.get("/docs-ui", response_class=HTMLResponse, dependencies=[Depends(require_role("admin"))])
def docs_page():
    return _page("docs.html")


@app.get("/history", response_class=HTMLResponse, dependencies=[Depends(require_role("admin"))])
def history_page():
    return _page("history.html")


@app.get("/notes", response_class=HTMLResponse, dependencies=[Depends(require_role("admin"))])
def notes_page():
    return _page("notes.html")


# ── Auth ─────────────────────────────────────────────────────────────

@app.post("/api/login")
def api_login(req: LoginRequest, response: Response):
    cfg = load_config()

    if req.username:
        user = get_user_by_username(cfg.db_path, req.username)
        ok = bool(
            user and user.is_active()
            and verify_password(req.password or "", user.password_hash)
        )
        log_event(cfg.db_path, "auth.login", success=ok, user=req.username)
        if not ok:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        assert user is not None  # ok=True implies a user was found and verified
        record_login(cfg.db_path, user.id)
        token = make_user_session_token(user.id, user.role, cfg.session_secret)
        response.set_cookie(
            "srag_session", token, httponly=True, samesite="lax",
            max_age=2592000, path="/",
        )
        return {"status": "ok", "role": user.role, "username": user.username}

    ok = bool(cfg.api_key) and bool(req.key) and secrets.compare_digest(req.key or "", cfg.api_key)
    log_event(cfg.db_path, "auth.login", success=ok)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid API key")
    token = make_user_session_token("bootstrap", "admin", cfg.session_secret)
    response.set_cookie(
        "srag_session", token, httponly=True, samesite="lax",
        max_age=2592000, path="/",
    )
    return {"status": "ok", "role": "admin", "username": "admin"}


@app.post("/api/claim")
def api_claim(req: ClaimRequest):
    cfg = load_config()
    invite = get_invite(cfg.db_path, req.token)
    now = datetime.now(timezone.utc).isoformat()
    if invite is None or not invite.is_claimable(now):
        raise HTTPException(status_code=400, detail="Invite is invalid, used, or expired")
    if not USERNAME_RE.fullmatch(req.username):
        raise HTTPException(
            status_code=400,
            detail="Username may contain only letters, digits, dot, underscore, hyphen (2-32 chars)",
        )
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if get_user_by_username(cfg.db_path, req.username) is not None:
        raise HTTPException(status_code=409, detail="Username already taken")

    user = User(
        id=uuid.uuid4().hex, username=req.username,
        password_hash=hash_password(req.password), role=invite.role,
        status="active", created_at=now,
    )
    create_user(cfg.db_path, user)
    consume_invite(cfg.db_path, req.token, req.username)
    log_event(cfg.db_path, "auth.claim", user=req.username, role=invite.role)
    return {"status": "ok", "role": invite.role}


@app.post("/api/logout")
def api_logout(response: Response):
    response.delete_cookie("srag_session", path="/")
    return {"status": "ok"}


@app.get("/api/me")
def api_me(user: dict = Depends(require_auth)):
    return {"id": user["id"], "username": user["username"], "role": user["role"]}


# ── Query ────────────────────────────────────────────────────────────

@app.post("/api/query")
def api_query(req: QueryRequest, user: dict = Depends(require_auth)):
    from srag.agent.loop import run_agent
    from srag.store.db import create_pending_command

    cfg = load_config()
    if req.top_k > 0:
        cfg.top_k = req.top_k
    visible = visible_document_ids(cfg.db_path, user["role"])

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
                                   query_id_holder=qid_holder,
                                   visible_doc_ids=visible):
                yield f"data: {json.dumps({'token': token})}\n\n"
        finally:
            log_event(cfg.db_path, "query",
                      query_id=qid_holder[0] if qid_holder else "",
                      question=req.question,
                      chunk_ids=collected_chunks,
                      source_count=len(collected_chunks))
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── Documents ────────────────────────────────────────────────────────

@app.post("/api/ingest")
def api_ingest(req: IngestRequest, user: dict = Depends(require_role("admin"))):
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


@app.get("/api/documents")
def api_list_documents(user: dict = Depends(require_auth)):
    cfg = load_config()
    visible = visible_document_ids(cfg.db_path, user["role"])
    docs = list_documents(cfg.db_path, visible)
    return [
        {
            "id": d.id, "title": d.title, "source_path": d.source_path,
            "file_type": d.file_type, "chunk_count": d.chunk_count,
            "ingested_at": d.ingested_at, "roles": d.roles,
        }
        for d in docs
    ]


@app.delete("/api/documents/{doc_id}")
def api_delete_document(doc_id: str, user: dict = Depends(require_role("admin"))):
    cfg = load_config()
    delete_document(cfg.db_path, doc_id)
    log_event(cfg.db_path, "document.delete", doc_id=doc_id)
    return {"status": "ok"}


@app.post("/api/documents/{doc_id}/roles")
def api_set_document_roles(doc_id: str, req: RolesRequest, user: dict = Depends(require_role("admin"))):
    cfg = load_config()
    roles_str = ",".join(r.strip() for r in req.roles if r.strip())
    if not set_document_roles(cfg.db_path, doc_id, roles_str):
        raise HTTPException(status_code=404, detail="Document not found")
    log_event(cfg.db_path, "document.roles", doc_id=doc_id, roles=roles_str)
    return {"status": "ok", "roles": roles_str}


# ── Users & invites (admin) ──────────────────────────────────────────

@app.get("/api/users")
def api_list_users(user: dict = Depends(require_role("admin"))):
    cfg = load_config()
    return [
        {"id": u.id, "username": u.username, "role": u.role, "status": u.status,
         "created_at": u.created_at, "last_login": u.last_login}
        for u in list_users(cfg.db_path)
    ]


@app.patch("/api/users/{user_id}")
def api_update_user(user_id: str, req: UserUpdateRequest, user: dict = Depends(require_role("admin"))):
    cfg = load_config()
    if req.role is not None and req.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {ROLES}")
    if req.status is not None and req.status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="status must be active or disabled")
    if not update_user(cfg.db_path, user_id, role=req.role, status=req.status):
        raise HTTPException(status_code=404, detail="User not found")
    log_event(cfg.db_path, "user.update", user_id=user_id, role=req.role, status=req.status)
    return {"status": "ok"}


@app.post("/api/invites")
def api_create_invite(req: InviteRequest, user: dict = Depends(require_role("admin"))):
    if req.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {ROLES}")
    cfg = load_config()
    now = datetime.now(timezone.utc)
    invite = Invite(
        token=secrets.token_urlsafe(24), role=req.role,
        created_by=user["username"], created_at=now.isoformat(),
        expires_at=(now + timedelta(days=7)).isoformat(),
    )
    create_invite(cfg.db_path, invite)
    log_event(cfg.db_path, "invite.create", role=req.role, token=invite.token)
    return {"status": "ok", "token": invite.token, "role": invite.role}


@app.get("/api/invites")
def api_list_invites(user: dict = Depends(require_role("admin"))):
    cfg = load_config()
    return [
        {"token": i.token, "role": i.role, "created_by": i.created_by,
         "created_at": i.created_at, "expires_at": i.expires_at,
         "claimed_by": i.claimed_by}
        for i in list_invites(cfg.db_path)
    ]


# ── Onboarding ───────────────────────────────────────────────────────

@app.get("/api/onboarding")
def api_onboarding(user: dict = Depends(require_auth)):
    cfg = load_config()
    completed = {s["step_key"] for s in list_onboarding_steps(cfg.db_path, user["id"])}
    return {
        "steps": [
            {**step, "done": step["key"] in completed}
            for step in ONBOARDING_STEPS
        ],
        "role": user["role"],
    }


@app.post("/api/onboarding/{step}")
def api_complete_step(step: str, user: dict = Depends(require_auth)):
    cfg = load_config()
    if step not in {s["key"] for s in ONBOARDING_STEPS}:
        raise HTTPException(status_code=400, detail="Unknown onboarding step")
    complete_onboarding_step(cfg.db_path, user["id"], step)
    return {"status": "ok"}


# ── Models / command / notes ─────────────────────────────────────────

@app.get("/api/models")
def api_models(user: dict = Depends(require_auth)):
    import ollama
    try:
        resp = ollama.list()
        return {"models": [m["name"] for m in resp.get("models", [])]}
    except Exception as e:
        return {"models": [], "error": str(e)}


@app.post("/api/command/confirm")
def api_run_command(req: CommandRequest, user: dict = Depends(require_role("admin"))):
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


@app.get("/api/notes")
def api_list_notes(user: dict = Depends(require_role("admin"))):
    from srag.store.db import list_notes

    cfg = load_config()
    return list_notes(cfg.db_path)
