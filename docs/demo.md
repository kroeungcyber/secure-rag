# Demo — NGO knowledge base (end-to-end)

A reproducible walkthrough of `secure-rag` on NGO-domain sample content, run
with the real local models (`llama3.2:3b` + `nomic-embed-text` via Ollama).

> Verified 2026-08-19 on this checkout. The sample corpus is **fictional demo
> content** in [`samples/ngo-demo/`](../samples/ngo-demo/) — no real
> organisational data.

---

## 1. Prerequisites

```bash
ollama pull llama3.2:3b
ollama pull nomic-embed-text
cd secure-rag && pip install -e .
```

## 2. Ingest the NGO corpus

```bash
srag add samples/ngo-demo/
srag list
```

Four documents, 25 chunks:

| Document | Intended role |
|---|---|
| `it-sop.md` | admin |
| `data-protection-policy.md` | staff, admin |
| `field-worker-handbook.md` | field |
| `grant-faq.md` | staff |

## 3. Ask a question (admin, sees everything)

```bash
srag query "How quickly must a data breach be reported to the data protection officer?"
```

**Observed (verified):** the answer is *grounded and correct* — "within 24
hours" — with the source cited as
`data-protection-policy.md → Data Protection & Confidentiality Policy > Breach reporting`.

## 4. Scope retrieval by role

An admin assigns role tags (via the Admin panel → **Document access**, or the
`/api/documents/{id}/roles` endpoint):

| Document | `roles` tag |
|---|---|
| `it-sop.md` | `admin` |
| `data-protection-policy.md` | `staff,admin` |
| `field-worker-handbook.md` | `field` |
| `grant-faq.md` | `staff` |

A **field** worker's visible set is then only the field handbook. The hybrid
search filters to that set *before* ranking — the IT SOP is unreachable.

## 5. Findings (honest assessment)

- ✅ **Retrieval is correct and scoped.** In-scope questions return grounded
  answers with the right citation; out-of-role documents are excluded.
- ⚠️ **Small-model hallucination.** `llama3.2:3b` invents plausible-sounding
  answers when a question is open-ended or falls outside the retrieved context
  (e.g. fabricating a password policy or recommending tools such as
  "Survey123"). It even proposed shell commands.
- ✅ **The guardrail held.** Those proposed commands were **not** executed —
  `PROGRAM.md` has `commands_enabled: false`, so the agent returned
  "Commands are disabled" instead of running anything.

**Mitigations — progress (the "keep up with AI" backlog):**

1. ✅ **Strengthened the system prompt** with explicit grounding rules ("answer
   only from retrieved context; never invent; say so if you have no source").
2. ✅ **Deterministic grounding guard** in the agent loop: when the KB was
   searched and nothing relevant was found (no relevant chunk and no real web
   result), the agent refuses with "I could not find this in the knowledge base"
   instead of fabricating. Covered by unit tests.
3. ✅ **Retrieval-relevance gate.** The agent measures cosine similarity between
   the query embedding and the retrieved chunks and refuses to answer when
   nothing clears the threshold (calibrated on `nomic-embed-text`: relevant
   ≈0.84–0.91, irrelevant ≈0.49–0.61, threshold 0.70). This closes the
   "out-of-scope question still returns an irrelevant nearest-neighbour chunk"
   failure mode.
4. ✅ **Resolved — answer faithfulness via model choice.** Verified on real
   hardware: `llama3.2:3b` (2 GB) is non-deterministic and can answer "not
   found" or hallucinate "72 hours" even with the correct "24 hours" chunk in
   context, while `gemma4` (9.6 GB) grounds its answer — it returned both the
   24-hour (policy) and 4-hour (incident response) timelines with the correct
   citations. The faithfulness gap is a *model-capability* issue, fixed by a
   stronger model at the cost of a larger/slower deployment.
5. ⏳ Formalise the model trade-off in
   [`llm-quantization-bench`](../llm-quantization-bench): quantify the
   latency/quality/memory curve between `llama3.2:3b` and `gemma4` so a field
   office can pick the point that fits its hardware. (Negative result logged:
   lowering temperature to 0 made `llama3.2:3b` deterministically answer "not
   found", so it was reverted.)
6. ✅ `run_command` stays denied-by-default regardless of model behaviour.

---

## 6. Keeping up with models & threats (Aug 2026)

The "72 hours vs 24 hours" failure above maps directly onto an active research
thread: the answer was *correct* in generic GDPR terms but **not faithful** to
the retrieved chunk. That is precisely ["Correctness is not Faithfulness in
Retrieval-Augmented Generation"](https://research.uni-hannover.de/en/publications/correctness-is-not-faithfulness-in-retrieval-augmented-generation/),
and faithfulness is an open, benchmarked problem — see ["Benchmarking LLM
Faithfulness in RAG with Evolving Leaderboards"](https://aclanthology.org/2025.emnlp-industry.54/).

Directions to keep the tool current (living backlog, not a one-shot fix):

- **Model choice is the faithfulness fix.** `gemma4` was verified faithful on
  the demo corpus (see §5.4); the remaining work is quantifying the
  latency/quality/memory trade-off against current local options — e.g. the
  [Hugging Face local-LLM roundup](https://huggingface.co/blog/daya-shankar/open-source-llm-models-to-run-locally)
  and [SitePoint's 2026 comparison](https://www.sitepoint.com/best-local-llm-models-2026/) —
  via [`llm-quantization-bench`](../llm-quantization-bench).
- **Add a faithfulness check** — verify the answer is entailed by the retrieved
  chunks (NLI-based or citation-grounded); see ["On Faithful Citations in
  Retrieval-Augmented Generation"](https://cse.hkust.edu.hk/pg/defenses/F25/yliumh-16-12-2025.html).
- **Keep `run_command` denied-by-default** regardless of model behaviour.

---

This walkthrough doubles as portfolio/admissions evidence: it shows the system
working on real local models **and** shows the engineering honesty of measuring
where it fails and planning the fix.
