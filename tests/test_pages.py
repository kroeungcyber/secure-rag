# tests/test_pages.py
"""Smoke tests for the HTML pages (login/admin/onboarding/chat/docs gating)."""
from fastapi.testclient import TestClient

import srag.config as cfg_mod
from srag.store.db import init_db
from srag.api.app import app


def _temp_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SRAG_SILENT", "1")
    cfg_mod.CONFIG_DIR = tmp_path / ".srag"
    cfg_mod.CONFIG_FILE = cfg_mod.CONFIG_DIR / "config.toml"
    cfg_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    db = str(tmp_path / "srag.sqlite")
    init_db(db)
    cfg_mod.save_config(cfg_mod.Config(
        api_key="testkey123", session_secret="testsecret456", db_path=db,
    ))


def test_chat_page_renders(tmp_path, monkeypatch):
    _temp_config(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "srag" in r.text


def test_login_page_renders(tmp_path, monkeypatch):
    _temp_config(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.get("/login")
        assert r.status_code == 200
        assert "Sign in" in r.text
        assert "Claim invite" in r.text


def test_onboarding_page_renders(tmp_path, monkeypatch):
    _temp_config(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.get("/onboarding")
        assert r.status_code == 200
        assert "Welcome" in r.text


def test_admin_page_requires_admin(tmp_path, monkeypatch):
    _temp_config(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/admin").status_code == 401
        r = client.get("/admin", headers={"X-API-Key": "testkey123"})
        assert r.status_code == 200
        assert "Document access" in r.text


def test_docs_history_notes_pages_require_admin(tmp_path, monkeypatch):
    _temp_config(tmp_path, monkeypatch)
    with TestClient(app) as client:
        for path in ("/docs-ui", "/history", "/notes"):
            assert client.get(path).status_code == 401, path
            assert client.get(path, headers={"X-API-Key": "testkey123"}).status_code == 200, path
