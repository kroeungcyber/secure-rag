# tests/test_users.py
"""Tests for multi-user onboarding, roles, invites, and retrieval scoping."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import srag.config as cfg_mod
from srag.api.app import app
from srag.api.auth import (
    hash_password, verify_password, make_user_session_token, resolve_session,
)
from srag.store.db import (
    init_db, create_user, get_user_by_id, get_user_by_username, list_users,
    update_user, record_login, create_invite, get_invite, consume_invite,
    list_invites, set_document_roles, get_document_roles, visible_document_ids,
    complete_onboarding_step, list_onboarding_steps, upsert_document,
    insert_chunks, search_chunks,
)
from srag.store.models import User, Invite, Document, Chunk


# ── Helpers ──────────────────────────────────────────────────────────

def _temp_config(tmp_path, monkeypatch, **kw):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SRAG_SILENT", "1")
    cfg_mod.CONFIG_DIR = tmp_path / ".srag"
    cfg_mod.CONFIG_FILE = cfg_mod.CONFIG_DIR / "config.toml"
    cfg_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    db = str(tmp_path / "srag.sqlite")
    init_db(db)
    values = dict(api_key="testkey123", session_secret="testsecret456", db_path=db)
    values.update(kw)
    cfg_mod.save_config(cfg_mod.Config(**values))
    return db


def _make_user(db, username="alice", role="staff", password="password123",
               status="active"):
    user = User(id=f"u_{username}", username=username,
                password_hash=hash_password(password), role=role,
                status=status, created_at=datetime.now(timezone.utc).isoformat())
    create_user(db, user)
    return user


def _make_doc(db, doc_id, roles=""):
    doc = Document(id=doc_id, source_path=f"/{doc_id}.md", title=doc_id,
                   file_type="md", ingested_at="", chunk_count=1, mtime=0.0,
                   roles=roles)
    upsert_document(db, doc)
    return doc


# ── Password hashing ─────────────────────────────────────────────────

def test_password_roundtrip():
    h = hash_password("correct horse battery")
    assert h.startswith("pbkdf2$")
    assert verify_password("correct horse battery", h) is True
    assert verify_password("wrong", h) is False


def test_verify_password_rejects_malformed():
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "bcrypt$abc$def") is False


# ── User sessions ────────────────────────────────────────────────────

def test_user_session_roundtrip():
    token = make_user_session_token("u1", "staff", "secret")
    claims = resolve_session(token, "secret", "apikey")
    assert claims == {"user_id": "u1", "role": "staff"}


def test_user_session_rejects_wrong_secret():
    token = make_user_session_token("u1", "staff", "secret")
    assert resolve_session(token, "WRONG", "apikey") is None


def test_user_session_rejects_tampered():
    token = make_user_session_token("u1", "staff", "secret")
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert resolve_session(tampered, "secret", "apikey") is None


def test_legacy_session_still_resolves():
    from srag.api.auth import make_session_token
    token = make_session_token("apikey", "secret")
    claims = resolve_session(token, "secret", "apikey")
    assert claims == {"user_id": "bootstrap", "role": "admin"}


# ── User CRUD ────────────────────────────────────────────────────────

def test_user_crud(db_path):
    _make_user(db_path, "alice", "staff")
    u = get_user_by_username(db_path, "alice")
    assert u is not None and u.role == "staff" and u.is_active()
    assert get_user_by_id(db_path, u.id).username == "alice"
    assert len(list_users(db_path)) == 1

    assert update_user(db_path, u.id, role="admin") is True
    assert get_user_by_id(db_path, u.id).role == "admin"

    record_login(db_path, u.id)
    assert get_user_by_id(db_path, u.id).last_login != ""

    assert update_user(db_path, "missing", role="admin") is False
    assert get_user_by_username(db_path, "nobody") is None


def test_update_user_status_disables_login(db_path):
    _make_user(db_path, "bob", "field")
    u = get_user_by_username(db_path, "bob")
    update_user(db_path, u.id, status="disabled")
    assert not get_user_by_id(db_path, u.id).is_active()


# ── Invites ──────────────────────────────────────────────────────────

def test_invite_crud_and_single_use(db_path):
    now = datetime.now(timezone.utc)
    inv = Invite(token="tok1", role="field", created_by="admin",
                 created_at=now.isoformat(),
                 expires_at=(now + timedelta(days=7)).isoformat())
    create_invite(db_path, inv)
    got = get_invite(db_path, "tok1")
    assert got is not None and got.role == "field"
    assert got.is_claimable(now.isoformat())

    assert consume_invite(db_path, "tok1", "alice") is True
    assert consume_invite(db_path, "tok1", "bob") is False  # single use
    assert get_invite(db_path, "tok1").claimed_by == "alice"
    assert get_invite(db_path, "tok1").is_claimable(now.isoformat()) is False
    assert len(list_invites(db_path)) == 1


def test_invite_expiry(db_path):
    now = datetime.now(timezone.utc)
    inv = Invite(token="tok2", role="staff", created_by="admin",
                 created_at=now.isoformat(),
                 expires_at=(now - timedelta(days=1)).isoformat())
    create_invite(db_path, inv)
    assert get_invite(db_path, "tok2").is_claimable(now.isoformat()) is False


# ── Document roles & retrieval scoping ───────────────────────────────

def test_set_get_document_roles(db_path):
    _make_doc(db_path, "docA", roles="")
    assert set_document_roles(db_path, "docA", "admin,staff") is True
    assert get_document_roles(db_path, "docA") == "admin,staff"
    assert set_document_roles(db_path, "missing", "admin") is False


def test_visible_document_ids(db_path):
    _make_doc(db_path, "public", roles="")
    _make_doc(db_path, "admin_only", roles="admin")
    _make_doc(db_path, "staff_field", roles="staff,field")

    assert visible_document_ids(db_path, "admin") is None
    assert visible_document_ids(db_path, "staff") == {"public", "staff_field"}
    assert visible_document_ids(db_path, "field") == {"public", "staff_field"}


def test_search_scoped_excludes_other_role(db_path):
    _make_doc(db_path, "public", roles="")
    _make_doc(db_path, "secret", roles="admin")
    insert_chunks(db_path,
                  [Chunk(id=None, doc_id="public", content="public content", chunk_index=0),
                   Chunk(id=None, doc_id="secret", content="secret content", chunk_index=0)],
                  [[0.1] * 768, [0.11] * 768])

    visible = visible_document_ids(db_path, "staff")
    results = search_chunks(db_path, [0.1] * 768, top_k=5, visible_doc_ids=visible)
    contents = [c.content for c, _ in results]
    assert "public content" in contents
    assert "secret content" not in contents


# ── Onboarding ───────────────────────────────────────────────────────

def test_onboarding_steps(db_path):
    complete_onboarding_step(db_path, "u1", "welcome")
    complete_onboarding_step(db_path, "u1", "ask")
    steps = list_onboarding_steps(db_path, "u1")
    assert {s["step_key"] for s in steps} == {"welcome", "ask"}
    assert all(s["completed_at"] for s in steps)


# ── API: claim → login → role enforcement ────────────────────────────

def test_claim_invite_then_login(tmp_path, monkeypatch):
    db = _temp_config(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    create_invite(db, Invite(token="inv-tok", role="field", created_by="admin",
                             created_at=now.isoformat(),
                             expires_at=(now + timedelta(days=7)).isoformat()))

    with TestClient(app) as client:
        r = client.post("/api/claim", json={"token": "inv-tok",
                                            "username": "fieldworker",
                                            "password": "longenough1"})
        assert r.status_code == 200
        assert r.json()["role"] == "field"

        # invite is now consumed
        assert get_invite(db, "inv-tok").claimed_by == "fieldworker"

        # login with the new credentials
        r2 = client.post("/api/login", json={"username": "fieldworker",
                                             "password": "longenough1"})
        assert r2.status_code == 200

        r3 = client.get("/api/me")
        assert r3.status_code == 200
        me = r3.json()
        assert me["username"] == "fieldworker"
        assert me["role"] == "field"
        assert me["id"]  # a non-empty generated id


def test_claim_rejects_used_or_missing_invite(tmp_path, monkeypatch):
    _temp_config(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.post("/api/claim", json={"token": "nope",
                                               "username": "x", "password": "longenough1"}).status_code == 400


def test_claim_rejects_short_password(tmp_path, monkeypatch):
    db = _temp_config(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    create_invite(db, Invite(token="tok", role="staff", created_by="admin",
                             created_at=now.isoformat(),
                             expires_at=(now + timedelta(days=7)).isoformat()))
    with TestClient(app) as client:
        r = client.post("/api/claim", json={"token": "tok", "username": "x", "password": "short"})
        assert r.status_code == 400


def test_staff_cannot_manage_users(tmp_path, monkeypatch):
    db = _temp_config(tmp_path, monkeypatch)
    _make_user(db, "staffuser", "staff")
    with TestClient(app) as client:
        client.post("/api/login", json={"username": "staffuser", "password": "password123"})
        assert client.get("/api/users").status_code == 403
        assert client.get("/api/invites").status_code == 403
        assert client.post("/api/invites", json={"role": "staff"}).status_code == 403
        # ingest is admin-only too
        assert client.post("/api/ingest", json={"path": "/tmp/x"}).status_code == 403


def test_admin_can_manage_users(tmp_path, monkeypatch):
    _temp_config(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/invites", json={"role": "field"},
                        headers={"X-API-Key": "testkey123"})
        assert r.status_code == 200
        token = r.json()["token"]
        assert client.get("/api/invites", headers={"X-API-Key": "testkey123"}).status_code == 200

        # claim it, then admin lists users
        client.post("/api/claim", json={"token": token, "username": "nw",
                                        "password": "longenough1"})
        users = client.get("/api/users", headers={"X-API-Key": "testkey123"}).json()
        assert any(u["username"] == "nw" and u["role"] == "field" for u in users)


def test_admin_can_update_user_role(tmp_path, monkeypatch):
    db = _temp_config(tmp_path, monkeypatch)
    _make_user(db, "promoteme", "staff")
    with TestClient(app) as client:
        r = client.patch("/api/users/u_promoteme", json={"role": "admin"},
                         headers={"X-API-Key": "testkey123"})
        assert r.status_code == 200
        assert get_user_by_username(db, "promoteme").role == "admin"


def test_query_scopes_retrieval_by_role(tmp_path, monkeypatch, mocker):
    db = _temp_config(tmp_path, monkeypatch)
    _make_user(db, "alice", "staff")
    _make_doc(db, "public", roles="")
    _make_doc(db, "secret", roles="admin")

    captured = {}

    def fake_run_agent(question, cfg, confirm_fn, collected_chunks=None,
                       query_id_holder=None, visible_doc_ids=None):
        captured["visible"] = visible_doc_ids
        yield "ok"

    mocker.patch("srag.agent.loop.run_agent", side_effect=fake_run_agent)

    with TestClient(app) as client:
        client.post("/api/login", json={"username": "alice", "password": "password123"})
        assert client.post("/api/query", json={"question": "hi"}).status_code == 200
    assert captured["visible"] == {"public"}


def test_admin_query_has_no_scoping(tmp_path, monkeypatch, mocker):
    db = _temp_config(tmp_path, monkeypatch)
    _make_doc(db, "public", roles="")
    _make_doc(db, "secret", roles="admin")

    captured = {}

    def fake_run_agent(question, cfg, confirm_fn, collected_chunks=None,
                       query_id_holder=None, visible_doc_ids=None):
        captured["visible"] = visible_doc_ids
        yield "ok"

    mocker.patch("srag.agent.loop.run_agent", side_effect=fake_run_agent)

    with TestClient(app) as client:
        r = client.post("/api/query", json={"question": "hi"},
                        headers={"X-API-Key": "testkey123"})
        assert r.status_code == 200
    assert captured["visible"] is None


def test_onboarding_api_flow(tmp_path, monkeypatch):
    db = _temp_config(tmp_path, monkeypatch)
    _make_user(db, "alice", "staff")
    with TestClient(app) as client:
        client.post("/api/login", json={"username": "alice", "password": "password123"})
        r = client.get("/api/onboarding")
        assert r.status_code == 200
        assert len(r.json()["steps"]) == 3
        assert all(not s["done"] for s in r.json()["steps"])

        assert client.post("/api/onboarding/welcome").status_code == 200
        r2 = client.get("/api/onboarding")
        done = {s["key"]: s["done"] for s in r2.json()["steps"]}
        assert done["welcome"] is True
        assert done["ask"] is False


def test_documents_list_is_scoped(tmp_path, monkeypatch):
    db = _temp_config(tmp_path, monkeypatch)
    _make_user(db, "alice", "staff")
    _make_doc(db, "public", roles="")
    _make_doc(db, "secret", roles="admin")
    with TestClient(app) as client:
        client.post("/api/login", json={"username": "alice", "password": "password123"})
        docs = client.get("/api/documents").json()
        assert {d["id"] for d in docs} == {"public"}


def test_admin_sets_document_roles_via_api(tmp_path, monkeypatch):
    db = _temp_config(tmp_path, monkeypatch)
    _make_doc(db, "docA", roles="")
    _make_user(db, "alice", "staff")
    with TestClient(app) as client:
        # admin sets a role tag
        r = client.post("/api/documents/docA/roles", json={"roles": ["staff"]},
                        headers={"X-API-Key": "testkey123"})
        assert r.status_code == 200
        assert r.json()["roles"] == "staff"
        assert get_document_roles(db, "docA") == "staff"

        # missing document → 404
        assert client.post("/api/documents/missing/roles", json={"roles": []},
                           headers={"X-API-Key": "testkey123"}).status_code == 404

        # non-admin is forbidden
        client.post("/api/login", json={"username": "alice", "password": "password123"})
        assert client.post("/api/documents/docA/roles",
                           json={"roles": []}).status_code == 403


def test_documents_list_exposes_roles_field(tmp_path, monkeypatch):
    db = _temp_config(tmp_path, monkeypatch)
    _make_doc(db, "docA", roles="staff")
    with TestClient(app) as client:
        docs = client.get("/api/documents", headers={"X-API-Key": "testkey123"}).json()
        assert docs[0]["roles"] == "staff"
