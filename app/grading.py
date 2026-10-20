"""Grade a stored response against an item snapshot. Deterministic graders for single/multi/numeric/sql/code;
Gemini rubric grading for open/audio with a self-grade fallback that never blocks the test."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .gemini import GeminiClient, GeminiError, RubricGrade, clamp_grade, grade_audio, grade_text
from .graders.basic import grade_multi, grade_numeric, grade_single
from .graders.code import grade_code
from .graders.sql import grade_sql

log = logging.getLogger("fde.grading")

RUBRIC_TYPES = ("open", "audio")


@dataclass
class Outcome:
    correct: bool | None
    score: float | None
    max_points: float | None
    points: float | None
    grader: str                       # auto | sql | code | gemini | self | none
    grader_model: str | None = None
    detail: dict = field(default_factory=dict)

    @property
    def correct_int(self) -> int | None:
        return None if self.correct is None else int(bool(self.correct))


def correct_indexes(item: dict) -> list[int]:
    return [i for i, o in enumerate(item.get("options") or []) if o.get("correct")]


def rubric_max(item: dict) -> float:
    return float(sum(r["points"] for r in item.get("rubric") or []))


def skipped_outcome(item: dict) -> Outcome:
    max_pts = rubric_max(item) if item["type"] in RUBRIC_TYPES else 1.0
    return Outcome(False, 0.0, max_pts, 0.0, "none", detail={"skipped": True})


def grade_deterministic(item: dict, answer: dict) -> Outcome:
    t = item["type"]
    if t == "single":
        g = grade_single(list(answer.get("selected") or []), correct_indexes(item))
        grader = "auto"
    elif t == "multi":
        g = grade_multi(list(answer.get("selected") or []), correct_indexes(item), len(item["options"]))
        grader = "auto"
    elif t == "numeric":
        a = item["answer"]
        g = grade_numeric(str(answer.get("value", "")), float(a["value"]), float(a["tolerance"]),
                          a.get("tolerance_type", "abs"))
        grader = "auto"
    elif t == "sql":
        s = item["sql"]
        g = grade_sql(s["setup"], s["reference"], str(answer.get("sql", "")), bool(s.get("order_matters", False)),
                      int(s.get("float_round", 2)))
        grader = "sql"
    elif t == "code":
        c = item["code"]
        g = grade_code(str(answer.get("code", "")), c["function"], c.get("tests") or [], float(c.get("timeout_s", 5)))
        grader = "code"
    else:
        raise ValueError(f"no deterministic grader for type {t}")
    return Outcome(g.correct, g.score, 1.0, g.score, grader, detail=g.detail)


def outcome_from_rubric(grade: RubricGrade) -> Outcome:
    detail = {"criteria": grade.criteria, "overall_feedback": grade.overall_feedback}
    if grade.transcript is not None:
        detail["transcript"] = grade.transcript
    return Outcome(grade.correct, grade.score, grade.max_points, grade.points, "gemini", grade.model, detail)


def pending_outcome(item: dict, reason: str, extra: dict | None = None) -> Outcome:
    """Gemini unavailable/failed: correct=NULL, user can self-grade on reveal."""
    return Outcome(None, None, rubric_max(item), None, "none",
                   detail={"status": "ungraded", "reason": reason, **(extra or {})})


def grade_rubric(item: dict, answer: dict, client: GeminiClient | None, audio: bytes | None = None,
                 mime: str | None = None) -> Outcome:
    text = str(answer.get("text") or "")
    uses_text = item["type"] == "open" or answer.get("fallback") == "text"
    if (uses_text and not text.strip()) or (not uses_text and not audio):
        return Outcome(False, 0.0, rubric_max(item), 0.0, "auto", detail={"status": "empty answer"})
    if client is None:
        return pending_outcome(item, "no Gemini API key configured")
    try:
        if item["type"] == "audio" and audio is not None and answer.get("fallback") != "text":
            g = grade_audio(client, item, audio, mime or "audio/webm")
        else:
            g = grade_text(client, item, text)
    except GeminiError as exc:
        log.warning("Gemini grading failed: %s", exc)
        return pending_outcome(item, f"Gemini error: {exc}")
    except Exception as exc:  # never block the test on the grader
        log.exception("unexpected grading failure")
        return pending_outcome(item, f"grading failed: {type(exc).__name__}")
    return outcome_from_rubric(g)


def self_grade(item: dict, points: list[float], previous_detail: dict | None = None) -> Outcome:
    judge = {"criteria": [{"criterion": r["criterion"], "points": p, "justification": "self-graded"}
                          for r, p in zip(item.get("rubric") or [], points)],
             "overall_feedback": ""}
    g = clamp_grade(item, judge)
    detail = dict(previous_detail or {})
    detail.update({"criteria": g.criteria, "status": "self-graded"})
    return Outcome(g.correct, g.score, g.max_points, g.points, "self", None, detail)
