"""Queries and writes used by the routes: pool, history, sessions, responses, progress, feedback."""

from __future__ import annotations

import random
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass

from . import __version__
from .assemble import ASSEMBLER_VERSION, SECTION_ORDER, History, PoolItem, Request, assemble
from .db import dumps, loads, new_id, now_iso
from .taxonomy import load_taxonomy

MODES = ("realtime", "end")
FEEDBACK_CATEGORIES = {
    "wrong_key": "Wrong answer key",
    "ambiguous": "Ambiguous / more than one right answer",
    "too_easy": "Too easy",
    "too_hard": "Too hard",
    "typo": "Typo or wording",
    "explanation": "Explanation could be better",
    "ui": "App / UI problem",
    "other": "Other",
}

def snapshot(conn: sqlite3.Connection, item_id: str, version: int) -> dict:
    """The stored content of item_id@version: what the candidate saw and was graded on."""
    row = conn.execute("SELECT content_json FROM item_versions WHERE item_id = ? AND version = ?",
                       (item_id, int(version))).fetchone()
    if row is None:
        raise KeyError(f"{item_id}@{version}")
    return loads(row[0])


def current_items(conn: sqlite3.Connection, include_retired: bool = False) -> list[sqlite3.Row]:
    where = "" if include_retired else "WHERE i.retired = 0"
    return conn.execute(f"""
        SELECT i.item_id, i.current_version AS version, i.retired, v.type, v.subtopic, v.domain_id, v.tags_json,
               v.difficulty, v.level, v.time_s, v.content_json, v.content_hash
        FROM items i JOIN item_versions v ON v.item_id = i.item_id AND v.version = i.current_version
        {where} ORDER BY i.item_id""").fetchall()


def pool_items(conn: sqlite3.Connection) -> list[PoolItem]:
    out = []
    for r in current_items(conn):
        content = loads(r["content_json"])
        out.append(PoolItem(r["item_id"], r["version"], r["type"], r["subtopic"], tuple(loads(r["tags_json"], [])),
                            int(r["time_s"] or 60), len(content.get("options") or [])))
    return out


def item_history(conn: sqlite3.Connection) -> dict[str, History]:
    hist: dict[str, History] = {}
    scores: dict[str, list[float]] = defaultdict(list)
    for r in conn.execute("""SELECT item_id, correct, score, submitted_at FROM responses
                             WHERE superseded = 0 ORDER BY submitted_at"""):
        h = hist.setdefault(r["item_id"], History())
        h.n_answered += 1
        h.last_correct = None if r["correct"] is None else bool(r["correct"])
        h.last_at = r["submitted_at"]
        if r["score"] is not None:
            scores[r["item_id"]].append(r["score"])
    for item_id, s in scores.items():
        hist[item_id].mean_score = sum(s) / len(s)
    return hist


# ---------------------------------------------------------------- sessions

@dataclass
class NewSession:
    subtopics: list[str]
    length: int
    formats: list[str]
    priority: str
    mode: str
    preset: str | None = None
    time_limit_s: int | None = None
    seed: int | None = None
    client: dict | None = None


def preview(conn: sqlite3.Connection, ns: NewSession):
    req = Request(ns.subtopics, ns.length, ns.formats, ns.priority, ns.seed if ns.seed is not None else 0)
    return assemble(pool_items(conn), item_history(conn), load_taxonomy(), req)


def create_session(conn: sqlite3.Connection, ns: NewSession, installation_id: str, bank_fingerprint: str) -> tuple[str, object]:
    seed = ns.seed if ns.seed is not None else random.SystemRandom().randrange(1, 2**31)
    req = Request(ns.subtopics, ns.length, ns.formats, ns.priority, seed)
    result = assemble(pool_items(conn), item_history(conn), load_taxonomy(), req)
    if not result.picked:
        raise ValueError(result.note or "No items match this selection.")
    sid = new_id()
    ts = now_iso()
    scope = {"preset": ns.preset, "subtopics": list(ns.subtopics), "formats": list(ns.formats), "length": ns.length,
             "priority": ns.priority, "time_limit_s": ns.time_limit_s, "assembled": len(result.picked),
             "note": result.note}
    conn.execute("""INSERT INTO sessions(session_id, installation_id, created_at, started_at, finished_at, status, mode,
                    scope_json, seed, app_version, bank_fingerprint, taxonomy_version, assembler_version, client_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (sid, installation_id, ts, None, None, "in_progress", ns.mode, dumps(scope), seed, __version__,
                  bank_fingerprint, str(load_taxonomy().version), ASSEMBLER_VERSION, dumps(ns.client or {})))
    for pos, p in enumerate(result.picked, start=1):
        conn.execute("""INSERT INTO session_items(session_id, position, item_id, item_version, section,
                        presented_order_json, attributed_subtopic) VALUES (?,?,?,?,?,?,?)""",
                     (sid, pos, p.item.item_id, p.item.version, p.section,
                      dumps(p.presented_order) if p.presented_order is not None else None, p.attributed))
    conn.commit()
    return sid, result


def get_session(conn: sqlite3.Connection, sid: str) -> dict | None:
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (sid,)).fetchone()
    if not row:
        return None
    s = dict(row)
    s["scope"] = loads(s["scope_json"], {})
    return s


def session_items(conn: sqlite3.Connection, sid: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM session_items WHERE session_id = ? ORDER BY position", (sid,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["presented_order"] = loads(d["presented_order_json"])
        out.append(d)
    return out


def latest_responses(conn: sqlite3.Connection, sid: str) -> dict[int, dict]:
    rows = conn.execute("""SELECT * FROM responses WHERE session_id = ? AND superseded = 0
                           ORDER BY created_at""", (sid,)).fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        d = dict(r)
        d["response"] = loads(d["response_json"], {})
        d["detail"] = loads(d["grader_detail_json"], {})
        out[d["position"]] = d
    return out


def get_response(conn: sqlite3.Connection, rid: str) -> dict | None:
    row = conn.execute("SELECT * FROM responses WHERE response_id = ?", (rid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["response"] = loads(d["response_json"], {})
    d["detail"] = loads(d["grader_detail_json"], {})
    return d


def position_states(items: list[dict], responses: dict[int, dict]) -> dict[int, str]:
    """pos -> 'answered' | 'skipped' | 'open'."""
    out = {}
    for si in items:
        r = responses.get(si["position"])
        out[si["position"]] = "open" if r is None else ("skipped" if r["skipped"] else "answered")
    return out


def resume_position(items: list[dict], responses: dict[int, dict]) -> int | None:
    for si in items:
        if si["position"] not in responses:
            return si["position"]
    return None


def next_position(items: list[dict], responses: dict[int, dict], after: int) -> int | None:
    for si in items:
        if si["position"] > after and si["position"] not in responses:
            return si["position"]
    return None


def mark_presented(conn: sqlite3.Connection, sid: str, pos: int) -> str:
    ts = now_iso()
    conn.execute("UPDATE session_items SET presented_at = ? WHERE session_id = ? AND position = ? AND presented_at IS NULL",
                 (ts, sid, pos))
    conn.execute("UPDATE sessions SET started_at = ? WHERE session_id = ? AND started_at IS NULL", (ts, sid))
    conn.commit()
    return ts


def add_server_event(conn: sqlite3.Connection, sid: str, pos: int, si: dict, type_: str, payload: dict) -> str:
    eid = new_id()
    conn.execute("""INSERT INTO events(event_id, session_id, position, item_id, item_version, type, client_ts_ms,
                    server_ts, payload_json) VALUES (?,?,?,?,?,?,?,?,?)""",
                 (eid, sid, pos, si["item_id"], si["item_version"], type_, None, now_iso(),
                  dumps({**payload, "source": "server"})))
    conn.commit()
    return eid


def audio_attempt(conn: sqlite3.Connection, sid: str, pos: int) -> dict | None:
    """The spoken-item attempt marker for (session, position): {token, started_at, interrupted_at} or None."""
    rows = conn.execute("""SELECT type, server_ts, payload_json FROM events WHERE session_id = ? AND position = ?
                           AND type IN ('audio_attempt', 'audio_attempt_interrupted') ORDER BY server_ts""",
                        (sid, pos)).fetchall()
    att = None
    for r in rows:
        if r["type"] == "audio_attempt" and att is None:
            att = {"token": loads(r["payload_json"], {}).get("token"), "started_at": r["server_ts"],
                   "interrupted_at": None}
        elif r["type"] == "audio_attempt_interrupted" and att is not None and not att["interrupted_at"]:
            att["interrupted_at"] = r["server_ts"]
    return att


def finish_session(conn: sqlite3.Connection, sid: str, status: str = "finished") -> None:
    conn.execute("UPDATE sessions SET status = ?, finished_at = ? WHERE session_id = ? AND status = 'in_progress'",
                 (status, now_iso(), sid))
    conn.commit()


def session_summary(conn: sqlite3.Connection, sid: str, items: list[dict] | None = None,
                    responses: dict[int, dict] | None = None) -> dict:
    items = items if items is not None else session_items(conn, sid)
    responses = responses if responses is not None else latest_responses(conn, sid)
    tax = load_taxonomy()

    def blank():
        return {"n": 0, "answered": 0, "correct": 0, "graded": 0, "pending": 0, "score_sum": 0.0, "skipped": 0}

    total, by_section, by_sub = blank(), defaultdict(blank), defaultdict(blank)
    for si in items:
        snap = snapshot(conn, si["item_id"], si["item_version"])
        r = responses.get(si["position"])
        for bucket in (total, by_section[si["section"]], by_sub[snap["subtopic"]]):
            bucket["n"] += 1
            if r is None:
                continue
            if r["skipped"]:
                bucket["skipped"] += 1
            else:
                bucket["answered"] += 1
            if r["correct"] is None:
                bucket["pending"] += 1
            else:
                bucket["graded"] += 1
                bucket["correct"] += r["correct"]
                bucket["score_sum"] += r["score"] or 0.0

    def finish(b):
        b["score_pct"] = round(100 * b["score_sum"] / b["n"], 1) if b["n"] else 0.0
        b["accuracy_pct"] = round(100 * b["correct"] / b["graded"], 1) if b["graded"] else None
        return b

    sections = [(s, finish(by_section[s])) for s in SECTION_ORDER if s in by_section]
    subs = []
    for tag, b in sorted(by_sub.items()):
        name = tax.subtopic(tag).name if tax.has_tag(tag) else tag
        subs.append((tag, name, finish(b)))
    return {"total": finish(total), "sections": sections, "subtopics": subs}


def recent_sessions(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute("SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        s = dict(r)
        s["scope"] = loads(s["scope_json"], {})
        s["summary"] = session_summary(conn, s["session_id"])["total"]
        out.append(s)
    return out


# ---------------------------------------------------------------- progress

def item_status_map(conn: sqlite3.Connection) -> dict[str, dict]:
    """item_id -> {status, attempts, last_at, history[...]} across all versions."""
    out: dict[str, dict] = defaultdict(lambda: {"status": "never_seen", "attempts": 0, "last_at": None,
                                                 "last_correct": None, "history": [], "seen": False,
                                                 "flagged": False})
    for r in conn.execute("SELECT DISTINCT item_id FROM session_items WHERE presented_at IS NOT NULL"):
        out[r["item_id"]]["seen"] = True
    for r in conn.execute("""SELECT r.*, s.mode AS session_mode FROM responses r
                             JOIN sessions s ON s.session_id = r.session_id
                             WHERE r.superseded = 0 ORDER BY r.submitted_at"""):
        st = out[r["item_id"]]
        st["seen"] = True
        st["attempts"] += 1
        st["last_at"] = r["submitted_at"]
        st["last_correct"] = r["correct"]
        st["flagged"] = st["flagged"] or bool(r["flagged"])
        st["history"].append({
            "submitted_at": r["submitted_at"], "version": r["item_version"], "correct": r["correct"],
            "score": r["score"], "skipped": r["skipped"], "time_ms": r["time_to_submit_ms"],
            "session_id": r["session_id"], "position": r["position"], "grader": r["grader"],
        })
    for st in out.values():
        if not st["seen"]:
            st["status"] = "never_seen"
        elif st["last_correct"] == 1:
            st["status"] = "right"
        elif st["last_correct"] == 0:
            st["status"] = "wrong"
        else:
            st["status"] = "seen"
    return out


def progress(conn: sqlite3.Connection) -> dict:
    tax = load_taxonomy()
    status = item_status_map(conn)
    cur = current_items(conn)
    by_sub_items: dict[str, list[str]] = defaultdict(list)
    for r in cur:
        by_sub_items[r["subtopic"]].append(r["item_id"])

    resp_rows = conn.execute("""SELECT v.subtopic, v.domain_id, r.correct, r.time_to_submit_ms, r.skipped
                                FROM responses r JOIN item_versions v
                                  ON v.item_id = r.item_id AND v.version = r.item_version
                                WHERE r.superseded = 0""").fetchall()
    acc: dict[str, list[int]] = defaultdict(list)
    times: dict[str, list[int]] = defaultdict(list)
    for r in resp_rows:
        for key in (r["subtopic"], r["domain_id"], "_all"):
            if r["correct"] is not None:
                acc[key].append(r["correct"])
            if r["time_to_submit_ms"] is not None and not r["skipped"]:
                times[key].append(r["time_to_submit_ms"])

    def stats(key: str, ids: list[str]) -> dict:
        c = Counter(status[i]["status"] if i in status else "never_seen" for i in ids)
        a = acc.get(key, [])
        t = times.get(key, [])
        return {
            "items": len(ids), "seen": len(ids) - c["never_seen"], "never_seen": c["never_seen"],
            "right": c["right"], "wrong": c["wrong"],
            "accuracy_pct": round(100 * sum(a) / len(a), 1) if a else None, "responses": len(a),
            "median_ms": int(statistics.median(t)) if t else None,
        }

    domains = []
    all_ids: list[str] = []
    for d in tax.domains:
        d_ids = [i for s in d.subtopics for i in by_sub_items.get(s.tag, [])]
        all_ids += d_ids
        subs = [{"tag": s.tag, "name": s.name, "weight": s.weight, **stats(s.tag, by_sub_items.get(s.tag, []))}
                for s in d.subtopics]
        domains.append({"id": d.id, "name": d.name, "weight": d.weight, **stats(d.id, d_ids), "subtopics": subs})
    return {"totals": stats("_all", all_ids), "domains": domains, "status": status}


# ---------------------------------------------------------------- feedback

def add_feedback(conn: sqlite3.Connection, installation_id: str, category: str, text: str,
                 session_id: str | None, item_id: str | None, item_version: int | None, context: dict) -> str:
    fid = new_id()
    conn.execute("""INSERT INTO feedback(feedback_id, installation_id, created_at, session_id, item_id, item_version,
                    category, text, context_json, resolved) VALUES (?,?,?,?,?,?,?,?,?,0)""",
                 (fid, installation_id, now_iso(), session_id, item_id, item_version, category, text, dumps(context)))
    conn.commit()
    return fid


def list_feedback(conn: sqlite3.Connection, include_resolved: bool = True) -> list[dict]:
    q = "SELECT * FROM feedback" + ("" if include_resolved else " WHERE resolved = 0") + " ORDER BY created_at DESC"
    out = []
    for r in conn.execute(q):
        d = dict(r)
        d["context"] = loads(d["context_json"], {})
        out.append(d)
    return out
