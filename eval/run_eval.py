#!/usr/bin/env python3
"""secure-rag end-to-end faithfulness eval harness.

Measures the approved checkpoint metric on this Mac's real local models:

    faithfulness % = share of ground-truth questions answered with (a) all
    required facts present, (b) the correct source cited, and (c) no forbidden
    fact. Hallucination is a hard fail.

It ingests the corpus into an ISOLATED db under eval/.eval-data/ (never touches
~/.srag) and drives srag.agent.loop.run_agent directly.

Usage:
  python3 eval/run_eval.py                          # default corpus + model
  python3 eval/run_eval.py --model gemma4 --reps 3  # faithfulness of gemma4
  python3 eval/run_eval.py --ground-truth path.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from srag.config import Config  # noqa: E402
from srag.program import get_models, get_query_settings  # noqa: E402
from srag.store.db import (  # noqa: E402
    init_db, upsert_document, insert_chunks, delete_document, doc_id,
    get_document, visible_document_ids,
)
from srag.store.models import Document, Chunk  # noqa: E402
from srag.ingestion.parsers import parse_file  # noqa: E402
from srag.ingestion.chunker import chunk_text  # noqa: E402
from srag.ingestion.embedder import embed_texts  # noqa: E402

# Disable follow-up suggestions in the loop: they add an extra non-deterministic
# model call that is irrelevant to answer faithfulness and inflates latency.
from srag.agent import loop as loop_mod  # noqa: E402
loop_mod.suggest_followups = lambda answer, model: []

# Role tags per demo doc (mirrors docs/demo.md §4). Real corpora should set
# these from the actual RBAC config, not a hard-coded map.
ROLE_MAP = {
    "it-sop.md": "admin",
    "data-protection-policy.md": "staff,admin",
    "field-worker-handbook.md": "field",
    "grant-faq.md": "staff",
}

REFUSAL_MARKERS = [
    "could not find",
    "not find this",
    "no relevant information",
    "no information in the knowledge base",
    "not found in the knowledge base",
    "cannot find",
]


def ingest_corpus(db_path: str, corpus_dir: Path, embed_model: str) -> int:
    """Ingest all files under corpus_dir into db_path. Returns chunk count."""
    init_db(db_path)
    total = 0
    repo_root = REPO.resolve()
    files = sorted(p for p in corpus_dir.rglob("*") if p.is_file())
    for f in files:
        src = str(f.resolve().relative_to(repo_root))
        text, title = parse_file(f)
        raw_chunks = chunk_text(text, src)
        embeddings = embed_texts([c.content for c in raw_chunks], embed_model)
        did = doc_id(src)
        if get_document(db_path, src):
            delete_document(db_path, did)
        doc = Document(
            id=did, source_path=src, title=title,
            file_type=f.suffix.lstrip("."),
            ingested_at=datetime.now(timezone.utc).isoformat(),
            chunk_count=len(raw_chunks), mtime=f.stat().st_mtime,
            embed_model=embed_model, roles=ROLE_MAP.get(f.name, ""),
        )
        upsert_document(db_path, doc)
        chunks = [
            Chunk(id=None, doc_id=did, content=rc.content, chunk_index=rc.chunk_index,
                  metadata={"page": rc.page_number, "heading": rc.section_heading,
                            "source_path": src})
            for rc in raw_chunks
        ]
        insert_chunks(db_path, chunks, embeddings)
        total += len(chunks)
    return total


def run_one(question: str, cfg: Config, role: str | None) -> tuple[str, float]:
    visible = None
    if role and role != "admin":
        visible = visible_document_ids(cfg.db_path, role)
    tokens: list[str] = []
    qid_holder: list = []
    t0 = time.perf_counter()
    for tok in loop_mod.run_agent(
        question, cfg, confirm_fn=lambda cmd: False,
        collected_chunks=[], query_id_holder=qid_holder,
        visible_doc_ids=visible,
    ):
        tokens.append(tok)
    dt = time.perf_counter() - t0
    return "".join(tokens), dt


def split_answer_sources(full_text: str) -> tuple[str, str]:
    answer, sources = full_text, ""
    if "\n**Sources:**" in full_text:
        answer, sources = full_text.split("\n**Sources:**", 1)
        if "\n**Follow-up questions:**" in sources:
            sources = sources.split("\n**Follow-up questions:**", 1)[0]
    return answer, sources


def _norm(s: str) -> str:
    """Normalize for substring matching: lowercase, hyphens→spaces (so
    'need-to-know' matches 'need to know' and 'full-disk encryption' matches
    'full disk encryption'), collapse whitespace. Applied to BOTH the answer
    and the fact, so exact tokens still match."""
    return " ".join(s.lower().replace("-", " ").split())


def score_item(item: dict, full_text: str) -> dict:
    answer, sources = split_answer_sources(full_text)
    a = _norm(answer)
    s = _norm(sources)
    not_found = bool(item.get("not_found"))
    must = [_norm(x) for x in item.get("must_contain", [])]
    must_not = [_norm(x) for x in item.get("must_not_contain", [])]

    refused = any(m in a for m in REFUSAL_MARKERS)

    if not_found:
        answer_correct = 1.0 if refused else 0.0
        citation_correct = True  # not applicable: correct behavior cites nothing
    else:
        hits = sum(1 for m in must if m in a)
        answer_correct = hits / len(must) if must else 1.0
        citation_correct = _norm(item["source"]) in s if item.get("source") else True

    hallucinated = any(m in a for m in must_not)
    faithful = bool(answer_correct == 1.0 and citation_correct and not hallucinated)

    return {
        "answer_correct": answer_correct,
        "citation_correct": bool(citation_correct),
        "hallucinated": hallucinated,
        "refused": refused,
        "faithful": faithful,
        "answer_preview": " ".join(answer.split())[:220],
        "sources_preview": " ".join(sources.split())[:220],
        "answer": answer,  # full answer, for re-scoring without a re-run
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ground-truth", default=str(REPO / "eval" / "ground-truth.json"))
    ap.add_argument("--corpus", default="samples/ngo-demo")
    ap.add_argument("--model", default=None, help="override chat model (default: PROGRAM.md)")
    ap.add_argument("--embed-model", default=None, help="override embed model")
    ap.add_argument("--reps", type=int, default=1, help="repeat each question N times (non-determinism)")
    ap.add_argument("--db-path", default=None, help="override isolated eval db path")
    ap.add_argument("--out", default=None, help="results JSON path (auto under eval/results/)")
    ap.add_argument("--keep-db", action="store_true", help="reuse existing eval db instead of re-ingesting")
    args = ap.parse_args()

    gt_path = Path(args.ground_truth)
    gt = json.loads(gt_path.read_text())
    items = gt["items"]

    cfg = Config()
    models = get_models()
    cfg.model = args.model or models.get("chat") or cfg.model
    cfg.embed_model = args.embed_model or models.get("embed") or cfg.embed_model
    settings = get_query_settings()
    if settings.get("top_k"):
        cfg.top_k = int(settings["top_k"])
    cfg.web_fallback = False  # isolate KB grounding; keep the measure local-first
    cfg.db_path = args.db_path or str(REPO / "eval" / ".eval-data" / "srag.sqlite")

    # Fresh isolated db unless --keep-db
    if not args.keep_db:
        dbp = Path(cfg.db_path)
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(dbp) + suffix)
            if p.exists():
                p.unlink()

    corpus_dir = REPO / args.corpus
    n_chunks = ingest_corpus(cfg.db_path, corpus_dir, cfg.embed_model)
    print(f"Ingested {n_chunks} chunks from {args.corpus} into {cfg.db_path}")
    print(f"Model: {cfg.model} | Embed: {cfg.embed_model} | top_k: {cfg.top_k} | reps: {args.reps}")
    print("Running eval (real local models, end-to-end)…\n")

    runs = []
    for rep in range(args.reps):
        results = []
        for item in items:
            role = item.get("role")
            t0 = time.perf_counter()
            full_text, _ = run_one(item["question"], cfg, role)
            latency_ms = (time.perf_counter() - t0) * 1000
            sc = score_item(item, full_text)
            sc["id"] = item["id"]
            sc["role"] = role
            sc["latency_ms"] = round(latency_ms, 1)
            results.append(sc)
            flag = "OK " if sc["faithful"] else "FAIL"
            hal = " HALLUCINATED" if sc["hallucinated"] else ""
            print(f"  [{flag}{hal}] {item['id']}  {latency_ms:7.1f}ms  {item['question'][:70]}")
        n = len(results)
        faithful = sum(1 for r in results if r["faithful"])
        answer_score = sum(r["answer_correct"] for r in results)
        hallucinated = sum(1 for r in results if r["hallucinated"])
        # Citation is only meaningful for answerable questions (not_found
        # questions correctly cite nothing, so counting them in the denominator
        # would understate the metric).
        answerable = [r for i, r in zip(items, results) if not i.get("not_found")]
        citation = sum(1 for r in answerable if r["citation_correct"])
        citation_denom = len(answerable)
        latencies = [r["latency_ms"] for r in results]
        runs.append({
            "rep": rep,
            "faithfulness_pct": round(100 * faithful / n, 1),
            "answer_fact_pct": round(100 * answer_score / n, 1),
            "answerable_n": citation_denom,
            "citation_pct": round(100 * citation / citation_denom, 1) if citation_denom else 0.0,
            "hallucination_pct": round(100 * hallucinated / n, 1),
            "latency_mean_ms": round(statistics.mean(latencies), 1),
            "latency_median_ms": round(statistics.median(latencies), 1),
            "latency_max_ms": round(max(latencies), 1),
            "items": results,
        })

    # Summarise across reps
    faiths = [r["faithfulness_pct"] for r in runs]
    mean_faith = statistics.mean(faiths)
    print("\n================ SUMMARY ================")
    if args.reps > 1:
        print(f"faithfulness across reps: {faiths}  → mean {mean_faith:.1f}%")
    r = runs[0]
    print(f"  faithfulness      : {mean_faith:.1f}%  (the checkpoint number)")
    print(f"  answer facts      : {r['answer_fact_pct']}%")
    print(f"  correct citation  : {r['citation_pct']}%  (of {r['answerable_n']} answerable)")
    print(f"  hallucination rate: {r['hallucination_pct']}%")
    print(f"  latency (mean/med/max): {r['latency_mean_ms']}/{r['latency_median_ms']}/{r['latency_max_ms']} ms")
    print("=========================================")

    out_path = Path(args.out) if args.out else (REPO / "eval" / "results" / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ground_truth": gt_path.name,
        "corpus": args.corpus,
        "model": cfg.model,
        "embed_model": cfg.embed_model,
        "top_k": cfg.top_k,
        "reps": args.reps,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "summary": runs[0] if args.reps == 1 else {"runs": runs},
        "runs": runs,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nResults → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
