"""Build template contexts from item snapshots.

`public_item` is the ONLY thing the runner sees before answering: no correct flags, why-texts, answers,
references, hidden tests, rubrics or model answers. Choice options are exposed in presented order with
presented indexes only (original indexes would leak the key when authors list the correct option first).
"""

from __future__ import annotations

import re
import sqlite3

from .graders.sql import run_query
from .taxonomy import load_taxonomy

TYPE_LABELS = {
    "single": "Multiple choice", "multi": "Multiple select", "numeric": "Value", "open": "Open text",
    "audio": "Spoken answer", "sql": "SQL", "code": "Code",
}
SCHEMA_PREVIEW_ROWS = 5
RUN_PREVIEW_ROWS = 50


def subtopic_name(tag: str) -> str:
    tax = load_taxonomy()
    return tax.subtopic(tag).name if tax.has_tag(tag) else tag


def order_of(item: dict, perm: list[int] | None) -> list[int]:
    n = len(item.get("options") or [])
    return list(perm) if perm and sorted(perm) == list(range(n)) else list(range(n))


def schema_preview(setup: str) -> list[dict]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(setup)
        out = []
        for (name, sql) in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY rowid"):
            cur = conn.execute(f'SELECT * FROM "{name}" LIMIT {SCHEMA_PREVIEW_ROWS}')
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            out.append({"name": name, "ddl": sql, "columns": cols, "rows": rows, "count": count})
        return out
    except sqlite3.Error as exc:
        return [{"name": "error", "ddl": str(exc), "columns": [], "rows": [], "count": 0}]
    finally:
        conn.close()


def public_item(item: dict, perm: list[int] | None) -> dict:
    t = item["type"]
    ctx = {
        "id": item["id"], "version": item["version"], "type": t, "type_label": TYPE_LABELS.get(t, t),
        "stem": item["stem"], "subtopic": item["subtopic"], "subtopic_name": subtopic_name(item["subtopic"]),
        "time_s": item.get("time_s"),
    }
    if t in ("single", "multi"):
        opts = item["options"]
        ctx["options"] = [{"index": i, "text": opts[orig]["text"]} for i, orig in enumerate(order_of(item, perm))]
        ctx["n_correct"] = sum(1 for o in opts if o.get("correct"))
    elif t == "numeric":
        ctx["unit"] = (item.get("answer") or {}).get("unit") or ""
    elif t == "open":
        ctx["max_chars"] = int(item.get("max_chars") or 2000)
    elif t == "audio":
        ctx["prep_s"] = int(item.get("prep_s") or 30)
        ctx["max_s"] = int(item.get("max_s") or 120)
    elif t == "sql":
        ctx["tables"] = schema_preview(item["sql"]["setup"])
    elif t == "code":
        c = item["code"]
        ctx["function"] = c["function"]
        ctx["starter"] = c.get("starter") or ""
        ctx["examples"] = [{"args": e.get("args", []), "kwargs": e.get("kwargs", {}), "expected": e.get("expected")}
                           for e in c.get("examples") or []]
    return ctx


def map_selected(item: dict, perm: list[int] | None, presented: list) -> list[int]:
    """Presented option indexes -> original option indexes (invalid entries dropped)."""
    order = order_of(item, perm)
    out = []
    for p in presented or []:
        try:
            p = int(p)
        except (TypeError, ValueError):
            continue
        if 0 <= p < len(order) and order[p] not in out:
            out.append(order[p])
    return sorted(out)


def verdict(resp: dict | None) -> str:
    if resp is None:
        return "unanswered"
    if resp["skipped"]:
        return "skipped"
    if resp["correct"] is None:
        return "ungraded"
    if resp["correct"]:
        return "correct"
    if resp["score"] and resp["score"] > 0:
        return "partial"
    return "incorrect"


def reveal(item: dict, perm: list[int] | None, resp: dict | None, audio_url: str | None = None) -> dict:
    t = item["type"]
    answer = (resp or {}).get("response") or {}
    detail = (resp or {}).get("detail") or {}
    ctx = {
        "type": t, "verdict": verdict(resp), "explanation": item.get("explanation"), "refs": item.get("refs") or [],
        "score": (resp or {}).get("score"), "points": (resp or {}).get("points"),
        "max_points": (resp or {}).get("max_points"), "grader": (resp or {}).get("grader"),
        "grader_model": (resp or {}).get("grader_model"), "response_id": (resp or {}).get("response_id"),
        "answer": answer, "detail": detail, "time_ms": (resp or {}).get("time_to_submit_ms"),
        "item_id": item["id"], "item_version": item["version"],
    }
    if t in ("single", "multi"):
        chosen = set(answer.get("selected") or [])
        ctx["options"] = [
            {"text": item["options"][orig]["text"], "correct": bool(item["options"][orig].get("correct")),
             "why": item["options"][orig].get("why"), "misconception": item["options"][orig].get("misconception"),
             "chosen": orig in chosen}
            for orig in order_of(item, perm)
        ]
    elif t == "numeric":
        a = item["answer"]
        ctx["expected"] = a["value"]
        ctx["tolerance"] = a["tolerance"]
        ctx["tolerance_type"] = a.get("tolerance_type", "abs")
        ctx["unit"] = a.get("unit") or ""
        ctx["work"] = item.get("work")
    elif t in ("open", "audio"):
        ctx["rubric"] = item.get("rubric") or []
        ctx["model_answer"] = item.get("model_answer")
        ctx["criteria"] = detail.get("criteria") or []
        ctx["overall_feedback"] = detail.get("overall_feedback")
        ctx["transcript"] = detail.get("transcript")
        ctx["needs_self_grade"] = resp is not None and not resp["skipped"] and resp["correct"] is None
        ctx["audio_url"] = audio_url if answer.get("audio") else None
        ctx["fallback"] = answer.get("fallback")
        ctx["grading_status"] = detail.get("status")
        ctx["grading_reason"] = detail.get("reason")
    elif t == "sql":
        s = item["sql"]
        ctx["reference"] = s["reference"]
        exp = run_query(s["setup"], s["reference"], max_rows=RUN_PREVIEW_ROWS)
        ctx["expected_result"] = {"columns": exp.columns, "rows": exp.rows, "error": exp.error}
        if answer.get("sql") and resp and not resp["skipped"]:
            got = run_query(s["setup"], answer["sql"], max_rows=RUN_PREVIEW_ROWS)
            ctx["your_result"] = {"columns": got.columns, "rows": got.rows, "error": got.error}
    elif t == "code":
        ctx["passed"] = detail.get("passed")
        ctx["total"] = detail.get("total")
        ctx["code_error"] = detail.get("error")
        ctx["case_errors"] = sorted({c["error"].split(":")[0] for c in detail.get("cases") or [] if c.get("error")})
    return ctx


def answer_summary(item: dict, resp: dict | None, perm: list[int] | None) -> str:
    """One-line plain-text answer for feedback context / exports."""
    if not resp:
        return ""
    a = resp.get("response") or {}
    t = item["type"]
    if resp.get("skipped"):
        return "(skipped)"
    if t in ("single", "multi"):
        return " | ".join(item["options"][i]["text"] for i in a.get("selected") or [] if i < len(item["options"]))
    if t == "numeric":
        return str(a.get("value", ""))
    if t == "sql":
        return a.get("sql", "")
    if t == "code":
        return a.get("code", "")
    if t in ("open", "audio"):
        return a.get("text") or (resp.get("detail") or {}).get("transcript") or ("(audio)" if a.get("audio") else "")
    return ""


_SAFE_EXT = re.compile(r"^[a-z0-9]{2,5}$")


def audio_ext(mime: str | None, filename: str | None) -> str:
    m = (mime or "").split(";")[0].strip().lower()
    table = {"audio/webm": "webm", "video/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "video/mp4": "m4a",
             "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/aac": "aac", "audio/flac": "flac"}
    if m in table:
        return table[m]
    ext = (filename or "").rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    return ext if _SAFE_EXT.match(ext) else "webm"


AUDIO_MIME = {"webm": "audio/webm", "ogg": "audio/ogg", "m4a": "audio/mp4", "mp3": "audio/mpeg", "wav": "audio/wav",
              "aac": "audio/aac", "flac": "audio/flac"}
