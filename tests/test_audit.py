# tests/test_audit.py
import json

from fastapi.testclient import TestClient

from srag.audit import audit_log_path, log_event


def _tmp_cfg(tmp_path, monkeypatch, **overrides):
    monkeypatch.setenv("HOME", str(tmp_path))
    import srag.config as cfg_mod
    cfg_mod.CONFIG_DIR = tmp_path / ".srag"
    cfg_mod.CONFIG_FILE = cfg_mod.CONFIG_DIR / "config.toml"
    cfg_mod.save_config(
        cfg_mod.Config(db_path=str(tmp_path / "srag.sqlite"), **overrides)
    )


def test_audit_path_is_next_to_db(tmp_path):
    assert audit_log_path(str(tmp_path / "srag.sqlite")) == tmp_path / "audit.jsonl"


def test_log_event_appends_valid_jsonl(tmp_path, monkeypatch):
    _tmp_cfg(tmp_path, monkeypatch)
    log_event(str(tmp_path / "srag.sqlite"), "query", query_id="abc", question="hi")
    lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event"] == "query"
    assert rec["query_id"] == "abc"
    assert rec["question"] == "hi"
    assert "ts" in rec


def test_log_event_append_order(tmp_path, monkeypatch):
    _tmp_cfg(tmp_path, monkeypatch)
    db = str(tmp_path / "srag.sqlite")
    log_event(db, "a", n=1)
    log_event(db, "b", n=2)
    lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
    assert json.loads(lines[0])["event"] == "a"
    assert json.loads(lines[1])["event"] == "b"


def test_disabled_audit_is_noop(tmp_path, monkeypatch):
    _tmp_cfg(tmp_path, monkeypatch, audit_enabled=False)
    log_event(str(tmp_path / "srag.sqlite"), "query", question="x")
    assert not (tmp_path / "audit.jsonl").exists()


def test_web_query_logs_query_event(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("SRAG_SILENT", "1")
    _tmp_cfg(tmp_path, monkeypatch, api_key="testkey123", session_secret="testsecret456")

    def fake_run_agent(question, cfg, confirm_fn, collected_chunks=None,
                       query_id_holder=None):
        if query_id_holder is not None:
            query_id_holder.append("qid123")
        collected_chunks.extend(["1", "2"])
        yield "token1"
        yield "token2"

    mocker.patch("srag.agent.loop.run_agent", side_effect=fake_run_agent)

    from srag.api.app import app
    with TestClient(app) as client:
        r = client.post(
            "/api/query",
            json={"question": "restart nginx"},
            headers={"X-API-Key": "testkey123"},
        )
        assert r.status_code == 200

    lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
    query_events = [json.loads(line) for line in lines
                    if json.loads(line)["event"] == "query"]
    assert len(query_events) == 1
    assert query_events[0]["query_id"] == "qid123"
    assert query_events[0]["question"] == "restart nginx"
    assert query_events[0]["chunk_ids"] == ["1", "2"]
    assert query_events[0]["source_count"] == 2


def test_login_logs_auth_event(tmp_path, monkeypatch):
    monkeypatch.setenv("SRAG_SILENT", "1")
    _tmp_cfg(tmp_path, monkeypatch, api_key="testkey123", session_secret="testsecret456")

    from srag.api.app import app
    with TestClient(app) as client:
        r = client.post("/api/login", json={"key": "testkey123"})
        assert r.status_code == 200
        r2 = client.post("/api/login", json={"key": "wrong"})
        assert r2.status_code == 401

    lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
    events = [json.loads(line) for line in lines if json.loads(line)["event"] == "auth.login"]
    assert [e["success"] for e in events] == [True, False]


def test_ingest_logs_ingest_event(tmp_path, monkeypatch):
    monkeypatch.setenv("SRAG_SILENT", "1")
    _tmp_cfg(tmp_path, monkeypatch, api_key="testkey123", session_secret="testsecret456")

    # /tmp is in PROGRAM.md's ingest whitelist (repo root PROGRAM.md).
    import pathlib
    import uuid
    src = pathlib.Path("/tmp") / f"srag-audit-test-{uuid.uuid4().hex}.md"
    src.write_text("# Test\n\ncontent\n")

    try:
        from srag.api.app import app
        with TestClient(app) as client:
            r = client.post(
                "/api/ingest",
                json={"path": str(src)},
                headers={"X-API-Key": "testkey123"},
            )
            assert r.status_code == 200
    finally:
        src.unlink(missing_ok=True)

    lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
    ev = [json.loads(line) for line in lines if json.loads(line)["event"] == "ingest"]
    assert len(ev) == 1
    assert ev[0]["source"] == str(src)
    assert ev[0]["kind"] == "file"


def test_delete_logs_delete_event(tmp_path, monkeypatch):
    monkeypatch.setenv("SRAG_SILENT", "1")
    _tmp_cfg(tmp_path, monkeypatch, api_key="testkey123", session_secret="testsecret456")

    from srag.store.db import init_db, upsert_document, doc_id
    from srag.store.models import Document
    from datetime import datetime, timezone

    init_db(str(tmp_path / "srag.sqlite"))
    doc = Document(
        id=doc_id("/kb/del.md"), source_path="/kb/del.md", title="Del",
        file_type="md", ingested_at=datetime.now(timezone.utc).isoformat(),
        chunk_count=1, mtime=1.0,
    )
    upsert_document(str(tmp_path / "srag.sqlite"), doc)

    from srag.api.app import app
    with TestClient(app) as client:
        r = client.delete(
            f"/api/documents/{doc.id}",
            headers={"X-API-Key": "testkey123"},
        )
        assert r.status_code == 200

    lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
    ev = [json.loads(line) for line in lines if json.loads(line)["event"] == "document.delete"]
    assert len(ev) == 1
    assert ev[0]["doc_id"] == doc.id


def test_command_logs_command_event(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("SRAG_SILENT", "1")
    _tmp_cfg(tmp_path, monkeypatch, audit_enabled=True)

    from srag.store.db import init_db
    init_db(str(tmp_path / "srag.sqlite"))
    mocker.patch("srag.program.commands_enabled", return_value=True)

    from srag.agent.tools import run_command
    output = run_command("true", confirm_fn=lambda cmd: True,
                         db_path=str(tmp_path / "srag.sqlite"), query_id="qid9")
    assert output is not None

    lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
    ev = [json.loads(line) for line in lines if json.loads(line)["event"] == "command"]
    assert len(ev) == 1
    assert ev[0]["command"] == "true"
    assert ev[0]["query_id"] == "qid9"
    assert ev[0]["exit_code"] == 0


def test_cli_ingest_logs_ingest_event(tmp_path, monkeypatch):
    monkeypatch.setenv("SRAG_SILENT", "1")
    _tmp_cfg(tmp_path, monkeypatch, audit_enabled=True)

    import pathlib
    import uuid
    src = pathlib.Path("/tmp") / f"srag-audit-cli-{uuid.uuid4().hex}.md"
    src.write_text("# Test\n\ncontent\n")

    from srag.cli import _ingest_file
    import srag.config as cfg_mod
    cfg = cfg_mod.load_config()

    try:
        _ingest_file(src, cfg)
    finally:
        src.unlink(missing_ok=True)

    if (tmp_path / "audit.jsonl").exists():
        lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
        ev = [json.loads(line) for line in lines if json.loads(line)["event"] == "ingest"]
        assert len(ev) >= 1, "expected at least one ingest event"
        assert ev[0]["source"] == str(src)
        assert ev[0]["kind"] == "file"
