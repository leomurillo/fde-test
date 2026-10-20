"""Scoring for single, multi and numeric items.

Every grader returns a GradeResult with:
  correct  bool  dichotomous outcome (what IRT models consume)
  score    float partial credit in [0, 1]
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class GradeResult:
    correct: bool | None          # None = pending (e.g. awaiting AI or self grading)
    score: float | None
    detail: dict = field(default_factory=dict)


def grade_single(selected: list[int], correct_indexes: list[int]) -> GradeResult:
    ok = len(selected) == 1 and selected[0] in correct_indexes
    return GradeResult(ok, 1.0 if ok else 0.0, {"selected": selected, "correct": correct_indexes})


def grade_multi(selected: list[int], correct_indexes: list[int], n_options: int) -> GradeResult:
    """Exact set match is `correct`. Partial score = (hits - false picks) / n_correct, floored at 0."""
    sel, cor = set(selected), set(correct_indexes)
    hits = len(sel & cor)
    false_picks = len(sel - cor)
    score = max(0.0, (hits - false_picks) / len(cor)) if cor else 0.0
    return GradeResult(sel == cor, round(score, 4),
                       {"selected": sorted(sel), "correct": sorted(cor), "hits": hits, "false_picks": false_picks})


def parse_number(text: str) -> float | None:
    cleaned = (text or "").strip().replace(",", "").replace("_", "").replace("$", "").replace("%", "")
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def grade_numeric(raw: str, value: float, tolerance: float, tolerance_type: str = "abs") -> GradeResult:
    got = parse_number(raw)
    if got is None:
        return GradeResult(False, 0.0, {"raw": raw, "parsed": None, "error": "not a number"})
    allowed = abs(value) * tolerance if tolerance_type == "rel" else tolerance
    ok = abs(got - value) <= allowed + 1e-12
    return GradeResult(ok, 1.0 if ok else 0.0, {"raw": raw, "parsed": got, "expected": value, "allowed_error": allowed})
