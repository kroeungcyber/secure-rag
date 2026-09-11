#!/usr/bin/env python3
"""Re-score a saved eval run against the CURRENT ground truth, without re-running
the models. Uses the full answer text saved by run_eval.py (the `answer` field).

Usage:
  python3 eval/rescore.py [results.json] [ground-truth.json]
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from eval.run_eval import _norm, REFUSAL_MARKERS  # noqa: E402


def re_score_answer(item: dict, answer_text: str, citation_correct: bool) -> dict:
    a = _norm(answer_text)
    not_found = bool(item.get("not_found"))
    must = [_norm(x) for x in item.get("must_contain", [])]
    must_not = [_norm(x) for x in item.get("must_not_contain", [])]

    refused = any(m in a for m in REFUSAL_MARKERS)
    if not_found:
        answer_correct = 1.0 if refused else 0.0
    else:
        hits = sum(1 for m in must if m in a)
        answer_correct = hits / len(must) if must else 1.0
    hallucinated = any(m in a for m in must_not)
    faithful = bool(answer_correct == 1.0 and citation_correct and not hallucinated)
    return {
        "answer_correct": answer_correct,
        "hallucinated": hallucinated,
        "faithful": faithful,
    }


def main() -> int:
    results_path = Path(sys.argv[1]) if len(sys.argv) > 1 else sorted(
        (REPO / "eval" / "results").glob("run-*.json"))[-1]
    gt_path = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / "eval" / "ground-truth.json"

    gt = json.loads(gt_path.read_text())
    by_id = {i["id"]: i for i in gt["items"]}
    results = json.loads(results_path.read_text())

    faiths = []
    for run in results["runs"]:
        n = len(run["items"])
        faithful = 0
        changed = 0
        for it in run["items"]:
            gi = by_id[it["id"]]
            old = it.get("faithful")
            sc = re_score_answer(gi, it.get("answer") or it.get("answer_preview", ""),
                                 it.get("citation_correct", True))
            if sc["faithful"] != old:
                changed += 1
            faithful += 1 if sc["faithful"] else 0
        pct = round(100 * faithful / n, 1)
        faiths.append(pct)
        print(f"rep {run['rep']}: faithfulness {pct}% ({faithful}/{n})  [{changed} changed by rescore]")

    if len(faiths) > 1:
        print(f"\nMEAN faithfulness: {statistics.mean(faiths):.1f}%  (reps: {faiths})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
