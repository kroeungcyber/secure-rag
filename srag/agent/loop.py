# srag/agent/loop.py
from __future__ import annotations
import json
import os
import uuid
from typing import Callable, Generator
import ollama

from srag.agent.prompts import SYSTEM_PROMPT
from srag.agent.tools import (
    TOOL_DEFINITIONS, search_kb, web_search, cite_sources, suggest_followups, run_command
)
from srag.ingestion.embedder import embed_query
from srag.config import Config
from srag.store.db import max_cosine_similarity

MAX_ITERATIONS = 5

# Minimum cosine similarity between the query embedding and a retrieved chunk
# for that chunk to count as *relevant* context. Calibrated on nomic-embed-text
# (measured ~0.84-0.92 for relevant pairs, ~0.52-0.61 for irrelevant ones).
RELEVANCE_THRESHOLD = 0.70


def _format_chunk(c: dict) -> str:
    heading = c.get("metadata", {}).get("heading")
    header = f"[{c['source']}]" + (f" — {heading}" if heading else "")
    return f"{header}\n{c['content']}"


def _load_dotenv() -> None:
    """Load TAVILY_API_KEY from ~/srag/.env into os.environ if present."""
    from pathlib import Path
    env_file = Path.home() / ".srag" / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = val


def _resolve_api_key(cfg: Config) -> str:
    """Priority: config.toml > .env > shell env var."""
    _load_dotenv()
    return cfg.tavily_api_key or os.environ.get("TAVILY_API_KEY", "")


def _web_result_is_real(result: str) -> bool:
    """web_search returns bracketed status strings for unavailable/error/empty
    results. Anything else is treated as a real retrieved result."""
    return bool(result) and not result.startswith("[")


def run_agent(
    question: str,
    cfg: Config,
    confirm_fn: Callable[[str], bool],
    collected_chunks: list | None = None,
    query_id_holder: list | None = None,
    visible_doc_ids: set[str] | None = None,
) -> Generator[str, None, None]:
    """Yield streamed response tokens including citations and follow-ups.
    visible_doc_ids (when given) scopes KB retrieval to the caller's allowed
    documents — role-based access control. None means 'see everything'."""
    query_id = uuid.uuid4().hex[:8]
    if query_id_holder is not None:
        query_id_holder.append(query_id)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    context_chunks: list[dict] = []
    web_fallback_used = False
    searched = False
    web_had_results = False
    relevant_context_found = False

    api_key = _resolve_api_key(cfg)

    for _ in range(MAX_ITERATIONS):
        response = ollama.chat(
            model=cfg.model,
            messages=messages,
            tools=TOOL_DEFINITIONS,
        )
        msg = response["message"]
        messages.append(msg)
        tool_calls = msg.get("tool_calls") or []

        # Enforce rule 1 deterministically: if the model produced a final
        # answer without ever searching the KB, force a search with the
        # original question instead of accepting a groundless answer.
        if not tool_calls and not searched:
            tool_calls = [{
                "function": {
                    "name": "search_kb",
                    "arguments": json.dumps({"query": question, "top_k": cfg.top_k}),
                }
            }]

        if not tool_calls:
            answer = msg["content"]
            # Grounding guard: the KB was searched and nothing *relevant* was
            # found (no relevant chunk and no real web result) — refuse to
            # fabricate an answer, whatever the model produced.
            if searched and not relevant_context_found and not web_had_results:
                yield "I could not find this in the knowledge base."
                return
            yield answer
            if context_chunks:
                yield f"\n\n---\n**Sources:**\n{cite_sources(context_chunks)}"
            followups = suggest_followups(answer, cfg.model)
            if followups:
                yield "\n\n**Follow-up questions:**\n" + "".join(f"- {q}\n" for q in followups)
            return

        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args)

            if name == "search_kb":
                searched = True
                q_emb = embed_query(args["query"], cfg.embed_model)
                try:
                    top_k = int(args.get("top_k", cfg.top_k))
                except (TypeError, ValueError):
                    top_k = cfg.top_k
                top_k = max(1, min(top_k, 100))
                chunks = search_kb(args["query"], q_emb, cfg.db_path,
                                   top_k,
                                   embed_model=cfg.embed_model,
                                   visible_doc_ids=visible_doc_ids)
                context_chunks.extend(chunks)
                if chunks:
                    sim = max_cosine_similarity(
                        cfg.db_path, q_emb, [c["id"] for c in chunks]
                    )
                    if sim >= RELEVANCE_THRESHOLD:
                        relevant_context_found = True
                    else:
                        # The model may have reformulated the query poorly.
                        # The chunks are still relevant if they relate to the
                        # user's ORIGINAL question — grounding is about the
                        # user's intent, not the model's paraphrase.
                        q0_emb = embed_query(question, cfg.embed_model)
                        sim0 = max_cosine_similarity(
                            cfg.db_path, q0_emb, [c["id"] for c in chunks]
                        )
                        if sim0 >= RELEVANCE_THRESHOLD:
                            relevant_context_found = True
                if collected_chunks is not None:
                    collected_chunks.extend(str(c["id"]) for c in chunks)
                tool_result = "\n\n".join(
                    _format_chunk(c) for c in chunks
                ) or "No results found."
                messages.append({"role": "tool", "content": tool_result})
                if not chunks and cfg.web_fallback and not web_fallback_used:
                    web_fallback_used = True
                    result = web_search(args["query"], api_key)
                    if _web_result_is_real(result):
                        web_had_results = True
                    messages.append({"role": "tool", "content":
                                     "[Auto web search: KB returned no results for this query]\n" + result})

            elif name == "web_search":
                result = web_search(args["query"], api_key)
                if _web_result_is_real(result):
                    web_had_results = True
                messages.append({"role": "tool", "content": result})

            elif name == "run_command":
                output = run_command(args["command"], confirm_fn, cfg.db_path, query_id)
                messages.append({"role": "tool", "content": output})

    # Forced final answer after max iterations
    response = ollama.chat(
        model=cfg.model,
        messages=messages + [{"role": "user", "content": "Provide your final answer now."}],
    )
    yield response["message"]["content"]
