# Real NGO Question Intake (spec S1 / S5)

This template turns real field questions into the `ground-truth.json` eval set.
The whole point of the checkpoint is that the number reflects **real** questions
from **real** users about **their own** documents — not the fictional demo
corpus.

## What to collect from each user

Ask a real field/staff worker: *"What is one question you'd actually type into
this tool at work?"* Then, together, verify the correct answer against the
document(s) it lives in. Capture:

| Field | Why it matters |
|-------|----------------|
| `role` | which RBAC role the user has (`admin` / `staff` / `field`) |
| `question` | the exact wording they'd type |
| `must_contain` | the 1–4 fact phrases the answer must include (timeline, number, term) |
| `source` | the document basename the answer must cite |
| `must_not_contain` | a known-wrong answer they've seen the tool give (hallucination trap) |
| `not_found` | `true` if the correct behaviour is "not in our documents" |

## Rules for a usable ground-truth entry

1. **Every fact must be traceable** to a specific line in a source document.
2. **`must_contain` = stable phrases** — numbers, timelines, fixed terms
   ("24 hours", "3 years", "full name"). Avoid prose the model might paraphrase.
3. **One hallucination trap is gold** — if a user already caught the tool
   giving a wrong figure, record it as `must_not_contain`.
4. **Include at least 1–2 `not_found` questions** per user (things genuinely
   absent from the corpus) — they test the grounding guard.

## Fill-in form (copy one block per question)

```json
{
  "id": "user1-q01",
  "role": "staff",
  "question": "",
  "source": "",
  "must_contain": [""],
  "must_not_contain": [""],
  "not_found": false
}
```

> Real questions replace/append the fallback items in
> [`ground-truth.json`](./ground-truth.json). Keep `id` unique and stable so
> before/after runs are comparable.

## Minimum bar (from SCOPE.md S1)

≥ 15 real questions, every fact traceable to a source document, spanning at
least two roles so the RBAC scoping is exercised too.
