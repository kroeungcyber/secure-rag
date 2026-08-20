# tests/test_agent.py
from srag.agent.loop import (
    run_agent, _format_chunk, _web_result_is_real,
)
from srag.config import Config

def _cfg(db_path):
    cfg = Config()
    cfg.db_path = db_path
    cfg.trusted = False
    return cfg

def test_agent_returns_answer_when_no_tool_calls(mocker, db_path):
    mock_chat = mocker.patch("srag.agent.loop.ollama.chat")
    mock_chat.return_value = {
        "message": {
            "content": "Restart nginx with: sudo systemctl restart nginx",
            "tool_calls": [],
        }
    }
    mocker.patch("srag.agent.loop.suggest_followups", return_value=["Q1?", "Q2?", "Q3?"])

    tokens = list(run_agent("how do I restart nginx?", _cfg(db_path),
                             confirm_fn=lambda cmd: False))
    full = "".join(tokens)
    assert "nginx" in full.lower()

def test_agent_calls_search_kb_tool(mocker, db_path):
    from srag.store.db import upsert_document, insert_chunks, doc_id
    from srag.store.models import Document, Chunk
    from datetime import datetime, timezone

    doc = Document(
        id=doc_id("/kb/nginx.md"), source_path="/kb/nginx.md", title="Nginx",
        file_type="md", ingested_at=datetime.now(timezone.utc).isoformat(),
        chunk_count=1, mtime=1.0
    )
    upsert_document(db_path, doc)
    insert_chunks(db_path, [Chunk(None, doc.id, "nginx restart command", 0)], [[0.1]*768])

    call_count = 0

    def fake_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "search_kb", "arguments": {"query": "restart nginx"}}}],
                }
            }
        return {"message": {"content": "Use systemctl restart nginx", "tool_calls": []}}

    mocker.patch("srag.agent.loop.ollama.chat", side_effect=fake_chat)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1]*768)
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])

    list(run_agent("restart nginx", _cfg(db_path), confirm_fn=lambda cmd: False))
    assert call_count == 2

def test_agent_stops_after_max_iterations(mocker, db_path):
    call_count = 0

    def always_calls_tool(**kwargs):
        nonlocal call_count
        call_count += 1
        return {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "search_kb", "arguments": {"query": "q"}}}],
            }
        }

    mocker.patch("srag.agent.loop.ollama.chat", side_effect=always_calls_tool)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1]*768)
    mocker.patch("srag.agent.loop.web_search", return_value="web")  # empty DB now auto-falls back; stub HTTP
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])

    list(run_agent("keep searching forever", _cfg(db_path), confirm_fn=lambda cmd: False))
    assert call_count <= 6  # max 5 iterations + 1 forced final answer

def test_agent_auto_web_fallback_on_empty_kb(mocker, db_path):
    call_count = 0

    def fake_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "search_kb", "arguments": {"query": "brand new issue"}}}],
                }
            }
        return {"message": {"content": "Answer from the web", "tool_calls": []}}

    mock_chat = mocker.patch("srag.agent.loop.ollama.chat", side_effect=fake_chat)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1] * 768)
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])
    mock_web = mocker.patch("srag.agent.loop.web_search", return_value="Summary: answer from web")

    list(run_agent("brand new issue", _cfg(db_path), confirm_fn=lambda cmd: False))

    assert mock_web.call_count == 1
    second_msgs = mock_chat.call_args_list[1].kwargs["messages"]
    tool_msgs = [m.get("content", "") for m in second_msgs if m.get("role") == "tool"]
    assert any("[Auto web search" in c for c in tool_msgs)

def test_agent_web_fallback_fires_once_per_query(mocker, db_path):
    call_count = 0

    def fake_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "search_kb", "arguments": {"query": "q"}}}],
                }
            }
        return {"message": {"content": "final", "tool_calls": []}}

    mocker.patch("srag.agent.loop.ollama.chat", side_effect=fake_chat)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1] * 768)
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])
    mock_web = mocker.patch("srag.agent.loop.web_search", return_value="web")

    list(run_agent("q", _cfg(db_path), confirm_fn=lambda cmd: False))
    assert mock_web.call_count == 1

def test_agent_no_web_fallback_when_disabled(mocker, db_path):
    call_count = 0

    def fake_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "search_kb", "arguments": {"query": "q"}}}],
                }
            }
        return {"message": {"content": "final", "tool_calls": []}}

    mocker.patch("srag.agent.loop.ollama.chat", side_effect=fake_chat)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1] * 768)
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])
    mock_web = mocker.patch("srag.agent.loop.web_search", return_value="web")

    cfg = _cfg(db_path)
    cfg.web_fallback = False
    list(run_agent("q", cfg, confirm_fn=lambda cmd: False))
    assert mock_web.call_count == 0


def test_agent_no_web_fallback_when_kb_has_results(mocker, db_path):
    from srag.store.db import upsert_document, insert_chunks, doc_id
    from srag.store.models import Document, Chunk
    from datetime import datetime, timezone

    doc = Document(
        id=doc_id("/kb/nginx.md"), source_path="/kb/nginx.md", title="Nginx",
        file_type="md", ingested_at=datetime.now(timezone.utc).isoformat(),
        chunk_count=1, mtime=1.0
    )
    upsert_document(db_path, doc)
    insert_chunks(db_path, [Chunk(None, doc.id, "nginx restart command", 0)], [[0.1] * 768])

    call_count = 0

    def fake_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "search_kb", "arguments": {"query": "restart nginx"}}}],
                }
            }
        return {"message": {"content": "Use systemctl restart nginx", "tool_calls": []}}

    mocker.patch("srag.agent.loop.ollama.chat", side_effect=fake_chat)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1] * 768)
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])
    mock_web = mocker.patch("srag.agent.loop.web_search", return_value="web")

    list(run_agent("restart nginx", _cfg(db_path), confirm_fn=lambda cmd: False))
    assert mock_web.call_count == 0

def test_format_chunk_includes_heading_when_present():
    chunk = {"source": "docabc", "content": "restart nginx", "metadata": {"heading": "Docker > Networking"}}
    formatted = _format_chunk(chunk)
    assert formatted == "[docabc] — Docker > Networking\nrestart nginx"

def test_format_chunk_omits_heading_when_absent():
    chunk = {"source": "docabc", "content": "restart nginx", "metadata": {}}
    formatted = _format_chunk(chunk)
    assert formatted == "[docabc]\nrestart nginx"


def test_search_kb_top_k_string_coerced_to_int(mocker, db_path):
    """Model tool args are untrusted: a JSON string top_k must not crash the loop."""
    call_count = 0

    def fake_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "search_kb",
                            "arguments": {"query": "q", "top_k": "10"},
                        },
                    }],
                }
            }
        return {"message": {"content": "done", "tool_calls": []}}

    mocker.patch("srag.agent.loop.ollama.chat", side_effect=fake_chat)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1] * 768)
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])
    mock_search = mocker.patch("srag.agent.loop.search_kb", return_value=[])

    list(run_agent("q", _cfg(db_path), confirm_fn=lambda cmd: False))
    top_k = mock_search.call_args[0][3]
    assert isinstance(top_k, int)
    assert top_k == 10


def test_search_kb_top_k_garbage_falls_back_to_default(mocker, db_path):
    call_count = 0

    def fake_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "search_kb",
                            "arguments": {"query": "q", "top_k": "abc"},
                        },
                    }],
                }
            }
        return {"message": {"content": "done", "tool_calls": []}}

    mocker.patch("srag.agent.loop.ollama.chat", side_effect=fake_chat)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1] * 768)
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])
    mock_search = mocker.patch("srag.agent.loop.search_kb", return_value=[])

    cfg = _cfg(db_path)
    list(run_agent("q", cfg, confirm_fn=lambda cmd: False))
    top_k = mock_search.call_args[0][3]
    assert top_k == cfg.top_k


def test_search_kb_top_k_clamped(mocker, db_path):
    call_count = 0

    def fake_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {"name": "search_kb",
                                     "arguments": {"query": "q", "top_k": "-5"}},
                    }],
                }
            }
        return {"message": {"content": "done", "tool_calls": []}}

    mocker.patch("srag.agent.loop.ollama.chat", side_effect=fake_chat)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1] * 768)
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])
    mock_search = mocker.patch("srag.agent.loop.search_kb", return_value=[])

    list(run_agent("q", _cfg(db_path), confirm_fn=lambda cmd: False))
    assert mock_search.call_args[0][3] == 1  # clamped to min 1


def test_search_kb_top_k_absurd_clamped(mocker, db_path):
    call_count = 0

    def fake_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {"name": "search_kb",
                                     "arguments": {"query": "q", "top_k": "999999"}},
                    }],
                }
            }
        return {"message": {"content": "done", "tool_calls": []}}

    mocker.patch("srag.agent.loop.ollama.chat", side_effect=fake_chat)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1] * 768)
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])
    mock_search = mocker.patch("srag.agent.loop.search_kb", return_value=[])

    list(run_agent("q", _cfg(db_path), confirm_fn=lambda cmd: False))
    assert mock_search.call_args[0][3] == 100  # clamped to max 100


# ── Grounding guard ──────────────────────────────────────────────────

def test_web_result_is_real():
    assert _web_result_is_real("Summary: answer from web") is True
    assert _web_result_is_real("[Web search unavailable: no Tavily API key configured.]") is False
    assert _web_result_is_real("[No results found.]") is False
    assert _web_result_is_real("[Web search error: timeout]") is False
    assert _web_result_is_real("") is False


def test_agent_guards_against_fabrication_when_kb_empty(mocker, db_path):
    call_count = 0

    def fake_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "search_kb", "arguments": {"query": "password policy"}}}],
                }
            }
        return {
            "message": {
                "content": "The password policy requires 12 characters and changes every 60 days.",
                "tool_calls": [],
            }
        }

    mocker.patch("srag.agent.loop.ollama.chat", side_effect=fake_chat)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1] * 768)
    mocker.patch("srag.agent.loop.search_kb", return_value=[])
    mocker.patch("srag.agent.loop.web_search",
                 return_value="[Web search unavailable: no Tavily API key configured.]")
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])

    full = "".join(list(run_agent("What is the password policy?", _cfg(db_path),
                                  confirm_fn=lambda c: False)))
    assert "I could not find this in the knowledge base" in full
    assert "60 days" not in full


def test_agent_guard_overrides_model_unknown_when_no_context(mocker, db_path):
    call_count = 0

    def fake_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "search_kb", "arguments": {"query": "q"}}}],
                }
            }
        return {"message": {"content": "I don't know the answer to that.", "tool_calls": []}}

    mocker.patch("srag.agent.loop.ollama.chat", side_effect=fake_chat)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1] * 768)
    mocker.patch("srag.agent.loop.search_kb", return_value=[])
    mocker.patch("srag.agent.loop.web_search",
                 return_value="[Web search unavailable: no Tavily API key configured.]")
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])

    full = "".join(list(run_agent("q", _cfg(db_path), confirm_fn=lambda c: False)))
    assert "I could not find this in the knowledge base" in full
    assert "I don't know the answer" not in full


def test_agent_guard_does_not_fire_with_kb_context(mocker, db_path):
    call_count = 0

    def fake_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "search_kb", "arguments": {"query": "breach"}}}],
                }
            }
        return {"message": {"content": "Report the breach within 24 hours.", "tool_calls": []}}

    chunk = {"id": 1, "content": "Report the breach within 24 hours.", "source": "docA", "metadata": {}}
    mocker.patch("srag.agent.loop.ollama.chat", side_effect=fake_chat)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1] * 768)
    mocker.patch("srag.agent.loop.search_kb", return_value=[chunk])
    mocker.patch("srag.agent.loop.max_cosine_similarity", return_value=1.0)
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])

    full = "".join(list(run_agent("q", _cfg(db_path), confirm_fn=lambda c: False)))
    assert "Report the breach within 24 hours" in full


def test_agent_guard_fires_on_irrelevant_context(mocker, db_path):
    call_count = 0

    def fake_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "search_kb", "arguments": {"query": "password policy"}}}],
                }
            }
        return {"message": {"content": "The password policy requires 12 characters.", "tool_calls": []}}

    chunk = {"id": 1, "content": "Field visits have no internet.", "source": "docA", "metadata": {}}
    mocker.patch("srag.agent.loop.ollama.chat", side_effect=fake_chat)
    mocker.patch("srag.agent.loop.embed_query", return_value=[0.1] * 768)
    mocker.patch("srag.agent.loop.search_kb", return_value=[chunk])
    mocker.patch("srag.agent.loop.max_cosine_similarity", return_value=0.3)
    mocker.patch("srag.agent.loop.suggest_followups", return_value=[])

    full = "".join(list(run_agent("What is the password policy?", _cfg(db_path),
                                  confirm_fn=lambda c: False)))
    assert "I could not find this in the knowledge base" in full
    assert "12 characters" not in full
