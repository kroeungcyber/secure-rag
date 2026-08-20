# srag/agent/tools.py
from __future__ import annotations
import shlex
import subprocess
from datetime import datetime, timezone
from typing import Callable
import httpx
import ollama

from srag.audit import log_event
from srag.store.db import hybrid_search_chunks, log_command

BLOCKED_PATTERNS = ["rm -rf", "dd if=", "mkfs", "shutdown", "reboot", ":(){:|:&};:"]

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_kb",
            "description": "Search the IT knowledge base for relevant information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "top_k": {"type": "integer", "description": "Number of results to return", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information using Tavily. "
                "Use for topics not in the local KB or when you need up-to-date facts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Propose a shell command. Requires user confirmation before execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run"},
                },
                "required": ["command"],
            },
        },
    },
]


def search_kb(query_text: str, query_embedding: list[float], db_path: str, top_k: int,
              embed_model: str | None = None,
              visible_doc_ids: set[str] | None = None) -> list[dict]:
    results = hybrid_search_chunks(db_path, query_embedding, query_text, top_k,
                                    embed_model=embed_model, visible_doc_ids=visible_doc_ids)
    return [
        {
            "id": chunk.id,
            "content": chunk.content,
            "source": chunk.doc_id,
            "metadata": chunk.metadata,
            "score": score,
        }
        for chunk, score in results
    ]


def web_search(query: str, api_key: str) -> str:
    """Search the web via Tavily API. Returns formatted results."""
    if not api_key:
        return (
            "[Web search unavailable: no Tavily API key configured. "
            "Set tavily_api_key in ~/.srag/config.toml or TAVILY_API_KEY env var.]"
        )

    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 5,
                "include_answer": True,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"[Web search error: {e}]"

    lines = []
    answer = data.get("answer", "")
    if answer:
        lines.append(f"Summary: {answer}\n")

    results = data.get("results", [])
    if results:
        lines.append("Top results:")
        for r in results:
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            content = r.get("content", "")
            lines.append(f"- {title}\n  {url}\n  {content}")

    return "\n".join(lines) or "[No results found.]"


def cite_sources(chunks: list[dict]) -> str:
    seen: dict[str, str] = {}
    for c in chunks:
        sid = c["source"]
        if sid not in seen:
            meta = c.get("metadata", {})
            path = meta.get("source_path", sid)
            heading = meta.get("heading")
            seen[sid] = f"{path} — {heading}" if heading else path
    return "\n".join(f"- [{sid}] {label}" for sid, label in seen.items())


def suggest_followups(answer: str, model: str) -> list[str]:
    resp = ollama.chat(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Generate exactly 3 short follow-up questions based on this IT support answer. "
                    "Return only the questions, one per line, no numbering or bullets."
                ),
            },
            {"role": "user", "content": answer},
        ],
    )
    lines = [ln.strip() for ln in resp["message"]["content"].strip().split("\n") if ln.strip()]
    return lines[:3]


def run_command(
    command: str,
    confirm_fn: Callable[[str], bool],
    db_path: str,
    query_id: str,
    timeout: int = 30,
) -> str:
    from srag.program import commands_enabled

    if not commands_enabled():
        return "[Commands are disabled in PROGRAM.md. Set commands_enabled: true to enable.]"

    _assert_not_blocked(command)

    if not confirm_fn(command):
        return "[Command skipped by user]"

    result = subprocess.run(
        shlex.split(command),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = (result.stdout + result.stderr).strip()

    log_command(
        db_path, command, output, result.returncode,
        datetime.now(timezone.utc).isoformat(), query_id
    )
    log_event(db_path, "command", command=command, query_id=query_id,
              exit_code=result.returncode)
    return output or f"[exit code {result.returncode}]"


def _assert_not_blocked(command: str) -> None:
    for pattern in BLOCKED_PATTERNS:
        if pattern in command:
            raise ValueError(f"Command blocked for safety: contains '{pattern}'")
