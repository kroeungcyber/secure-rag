# tests/test_tools.py
import pytest
from srag.agent.tools import search_kb, cite_sources, suggest_followups, run_command

def test_search_kb_returns_chunks(db_path, mocker):
    from srag.store.db import upsert_document, insert_chunks, doc_id
    from srag.store.models import Document, Chunk
    from datetime import datetime, timezone

    doc = Document(
        id=doc_id("/test.md"), source_path="/test.md", title="Test",
        file_type="md", ingested_at=datetime.now(timezone.utc).isoformat(),
        chunk_count=1, mtime=1.0
    )
    upsert_document(db_path, doc)
    chunk = Chunk(id=None, doc_id=doc.id, content="restart nginx", chunk_index=0)
    insert_chunks(db_path, [chunk], [[0.1] * 768])

    results = search_kb("restart nginx", [0.1] * 768, db_path, top_k=1)
    assert len(results) == 1
    assert results[0]["content"] == "restart nginx"
    assert "source" in results[0]

def test_cite_sources_deduplicates(db_path):
    chunks = [
        {"source": "docabc", "content": "c1", "metadata": {"source_path": "/notes/a.md"}},
        {"source": "docabc", "content": "c2", "metadata": {"source_path": "/notes/a.md"}},
        {"source": "docxyz", "content": "c3", "metadata": {"source_path": "/notes/b.md"}},
    ]
    result = cite_sources(chunks)
    assert result.count("docabc") == 1
    assert "docxyz" in result

def test_cite_sources_includes_heading_when_present(db_path):
    chunks = [
        {"source": "docabc", "content": "c1",
         "metadata": {"source_path": "/notes/a.md", "heading": "Docker > Networking"}},
    ]
    result = cite_sources(chunks)
    assert "/notes/a.md — Docker > Networking" in result

def test_suggest_followups_returns_three(mocker):
    mock_chat = mocker.patch("srag.agent.tools.ollama.chat")
    mock_chat.return_value = {
        "message": {"content": "How do I restart nginx?\nHow do I check logs?\nHow do I reload config?"}
    }
    questions = suggest_followups("nginx is running", model="llama3.1:8b")
    assert len(questions) == 3

def test_run_command_blocks_dangerous_patterns(mocker):
    mocker.patch("srag.program.commands_enabled", return_value=True)
    with pytest.raises(ValueError, match="blocked"):
        run_command("rm -rf /", confirm_fn=lambda cmd: True, db_path=":memory:", query_id="q1")

def test_run_command_skips_when_confirm_returns_false(mocker):
    mocker.patch("srag.program.commands_enabled", return_value=True)
    mocker.patch("srag.agent.tools.subprocess.run")
    result = run_command("ls -la", confirm_fn=lambda cmd: False, db_path=":memory:", query_id="q1")
    assert result == "[Command skipped by user]"

def test_run_command_executes_when_confirmed(mocker, db_path):
    mocker.patch("srag.program.commands_enabled", return_value=True)
    mock_run = mocker.patch("srag.agent.tools.subprocess.run")
    mock_run.return_value.stdout = "file1\nfile2"
    mock_run.return_value.stderr = ""
    mock_run.return_value.returncode = 0

    result = run_command("ls -la", confirm_fn=lambda cmd: True, db_path=db_path, query_id="q1")
    assert "file1" in result

def test_run_command_blocked_when_commands_disabled(mocker):
    mocker.patch("srag.program.commands_enabled", return_value=False)
    result = run_command("ls", confirm_fn=lambda cmd: True, db_path=":memory:", query_id="q1")
    assert "PROGRAM.md" in result
