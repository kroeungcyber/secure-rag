# tests/test_integration.py
"""Integration tests that require a live Ollama server with the project's models.

Skipped automatically when Ollama is unreachable or the configured models are
absent, so CI and machines without a local model still pass. These exercise a
REAL model round-trip with the exact models the project is configured to use
(from ~/.srag/config.toml) — the one thing mocked unit tests can't prove.
Chat is bounded (non-streaming, 120s) rather than running the full multi-call
agent loop.

Run explicitly with a live Ollama:
    pytest tests/test_integration.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

# Read at call time (not import time) so the env override works and tests
# that mutate HOME / config globals don't interfere.
def _ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")


def _model_tags() -> list[str]:
    try:
        resp = httpx.get(f"{_ollama_host()}/api/tags", timeout=2)
        return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return []


def _cfg_models() -> tuple[str, str]:
    """Read the real ~/.srag/config.toml directly (immune to test monkeypatching)."""
    import tomli

    p = Path.home() / ".srag" / "config.toml"
    if p.exists():
        data = tomli.loads(p.read_text())
        return data.get("model", "llama3.1:8b"), data.get("embed_model", "nomic-embed-text")
    return "llama3.1:8b", "nomic-embed-text"


def _available() -> tuple[str, str] | None:
    """Return (chat_model, embed_model) if both configured models exist, else None."""
    try:
        tags = _model_tags()
        if not tags:
            return None
        chat, embed = _cfg_models()
        if chat in tags and embed in tags:
            return chat, embed
        return None
    except Exception:
        return None


_skip_reason = "Ollama unreachable or project models (chat/embed) not present"


@pytest.mark.skipif(_available() is None, reason=_skip_reason)
def test_real_chat_roundtrip_with_project_model():
    chat, _ = _available()
    resp = httpx.post(
        f"{_ollama_host()}/api/chat",
        json={
            "model": chat,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "stream": False,
            "options": {"num_predict": 8},
        },
        timeout=120,
    )
    assert resp.status_code == 200, resp.text[:200]
    content = resp.json().get("message", {}).get("content", "")
    assert content.strip(), "model returned empty completion"


@pytest.mark.skipif(_available() is None, reason=_skip_reason)
def test_real_embedding_with_project_embed_model():
    _, embed = _available()
    resp = httpx.post(
        f"{_ollama_host()}/api/embeddings",
        json={"model": embed, "prompt": "test"},
        timeout=30,
    )
    assert resp.status_code == 200, resp.text[:200]
    emb = resp.json().get("embedding", [])
    assert len(emb) > 0, "embedding returned empty"
