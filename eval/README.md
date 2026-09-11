# secure-rag — End-to-End Faithfulness Eval

The objective instrument for the 2-week "real-world-tested" checkpoint
(see [`/SCOPE.md`](../../SCOPE.md)). It turns "does it actually work?" into one
measured number.

## The metric

```
faithfulness % = share of ground-truth questions answered with
    (a) all required facts present,
    (b) the correct source cited, and
    (c) no forbidden (hallucinated) fact.   ← hallucination = hard fail
```

Secondary numbers reported per run: answer-fact %, correct-citation %,
hallucination rate, and latency (mean/median/max).

## How to run

```bash
cd secure-rag

# Baseline (defaults to PROGRAM.md models: llama3.2:3b + nomic-embed-text)
python3 eval/run_eval.py

# Compare a stronger model (the known faithfulness fix)
python3 eval/run_eval.py --model gemma4

# Repeat each question 3× to expose non-determinism
python3 eval/run_eval.py --reps 3
```

Requirements: Ollama running with the chat + embed models pulled. The harness
ingests the corpus into an **isolated** db at `eval/.eval-data/` and never
touches `~/.srag`.

## Scoring rubric (objective, documented)

| Signal | How it's judged |
|--------|-----------------|
| Answer facts | Every `must_contain` substring present (case-insensitive) in the answer |
| Citation | `source` basename appears in the "**Sources:**" block |
| Hallucination | Any `must_not_contain` substring present in the answer → hard fail |
| Not-found | `not_found: true` → correct behaviour is the grounding refusal ("could not find…") |
| Role scope | `role: "field"` runs retrieval scoped to that role's visible documents |

**Known limitations (honest, by design):**
- Substring matching is approximate; facts are chosen to be stable phrases
  (timelines, numbers, fixed terms). It can false-negative on paraphrase.
- `llama3.2:3b` is non-deterministic; use `--reps` to see variance. A seed
  option in the agent loop is a candidate S4 fix.
- Follow-up suggestions are disabled during measurement (irrelevant to
  faithfulness, adds a model call). Web fallback is off so the number measures
  *local KB* grounding only.

## Ground truth

`ground-truth.json` is currently the **fallback** set, source-verified against
`samples/ngo-demo/`. Spec **S1** replaces/extends it with real NGO-user
questions about their own documents. Every fact must stay traceable to a source
document.
