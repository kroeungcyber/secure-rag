# secure-rag — Hallucination Guard (retro-spec)

Date: 2026-08-19
Status: **Approved by user (review in chat, 2026-08-19)** (retro-spec: documents
work already shipped, written to restore the spec↔code anchor)

## One-liner

Deterministic guards against small-model hallucination: a grounding prompt, a
refusal guard, and a retrieval-relevance gate.

## Problem

`llama3.2:3b` hallucinates. On out-of-scope questions it fabricates answers
(including fake web URLs); on in-scope questions it can ignore the retrieved
chunk — e.g. answering "72 hours" from pretrained GDPR knowledge instead of the
retrieved "24 hours".

## Goal

Refuse rather than fabricate when the KB has no relevant context, while keeping
grounded answers flowing when it does.

## Non-goals

- A full NLI/faithfulness checker (deferred — the model comparison below shows
  the residual gap is *model capability*, fixed by a stronger model).
- Changing the default chat model (still `llama3.2:3b`).

## Key decisions (verify each)

1. **Grounding prompt rewrite** — "answer only from retrieved context; never
   invent facts/names/commands/figures; if nothing relevant, say so."
2. **Refusal guard** — when the KB was searched and nothing relevant was found
   (and web returned no real result), emit exactly "I could not find this in the
   knowledge base." The earlier "preserve the model's own 'I don't know'"
   escape hatch was **removed** because it let partial hallucinations through.
3. **Retrieval-relevance gate = cosine similarity, threshold 0.70**, calibrated
   on `nomic-embed-text` (measured: relevant ≈0.84–0.91, irrelevant ≈0.49–0.61).
   Cosine (scale-invariant) is used rather than the raw vec0 L2 distance, which
   is not comparable across models.
4. **Negative result, reverted:** setting generation `temperature=0` made the 3B
   model *deterministically* answer "not found" even on in-scope questions.
5. **Model comparison finding:** `gemma4` answers faithfully where `llama3.2:3b`
   hallucinates → the residual faithfulness gap is model capability, not
   architecture.

## Already implemented (files)

- `srag/agent/prompts.py` — grounding prompt.
- `srag/agent/loop.py` — refusal guard + relevance gate (`RELEVANCE_THRESHOLD`).
- `srag/store/db.py` — `max_cosine_similarity()`.
- `tests/test_agent.py`, `tests/test_store.py`.
- `docs/demo.md` — findings, negative results, and the model comparison.
