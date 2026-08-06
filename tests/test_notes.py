# tests/test_notes.py
from srag.store.db import (
    save_query_context, get_latest_query_context, save_note,
)


def test_note_saves_without_context(db_path):
    """No prior query — note saves with query_context_id=NULL."""
    ctx = get_latest_query_context(db_path)
    assert ctx is None

    result = save_note(db_path, "Restarted memcached, fixed the issue")
    assert result is None  # No query_context to link to

    # Verify the note was persisted anyway
    import pysqlite3.dbapi2 as sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT body, query_context_id, chunk_ids FROM incident_notes"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "Restarted memcached, fixed the issue"
    assert row[1] is None
    assert row[2] is None


def test_note_links_to_last_query(db_path):
    """Insert a query_context row first; note saves with correct link and copied chunk_ids."""
    # Simulate a prior query context
    save_query_context(
        db_path,
        query_id="abc12345",
        question="nginx 502 error troubleshooting",
        chunk_ids=["42", "57", "99"],
    )

    result = save_note(db_path, "Fixed by restarting nginx, not memcached")
    assert result is not None
    assert result["id"] == "abc12345"
    assert result["chunk_ids"] == ["42", "57", "99"]

    # Verify the note row is correct
    import pysqlite3.dbapi2 as sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT body, query_context_id, chunk_ids FROM incident_notes "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row[0] == "Fixed by restarting nginx, not memcached"
    assert row[1] == "abc12345"
    assert row[2] == '["42", "57", "99"]'


def test_query_writes_context_row(db_path, mocker):
    """Mock run_agent with a collector; assert query_context row is written."""
    from srag.cli import query
    from srag.config import Config
    from srag.store.db import get_latest_query_context

    cfg = Config(model="llama3.1:8b", embed_model="nomic-embed-text",
                 db_path=db_path, top_k=5, trusted=False)
    mocker.patch("srag.cli._cfg", return_value=cfg)

    # Simulate a run_agent generator that echoes tokens and collects chunks
    def fake_run_agent(question, cfg, confirm_fn,
                       collected_chunks=None, query_id_holder=None):
        if query_id_holder is not None:
            query_id_holder.append("fake-qid")
        if collected_chunks is not None:
            collected_chunks.extend(["42", "57"])
        yield "Answer: check the logs.\n"
        yield "\n\n---\n**Sources:**\n- [abc] /notes/runbook.md\n"

    mocker.patch("srag.agent.loop.run_agent", side_effect=fake_run_agent)

    # Run the query command — must suppress console output
    mocker.patch("srag.cli.console.print")
    mocker.patch("srag.cli.console.input", return_value="n")

    # Need to pass Typer arguments explicitly when calling the function directly
    query(question="how do I debug nginx?", k=0, no_run=True)

    # Verify the query_context row was written
    ctx = get_latest_query_context(db_path)
    assert ctx is not None
    assert ctx["id"] == "fake-qid"
    assert ctx["question"] == "how do I debug nginx?"
    assert ctx["chunk_ids"] == ["42", "57"]
