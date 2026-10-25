"""Modeled tests: a hand-built test loaded from a local file, run as timed parts back to back.

The file never needs to live in the repository. It names the items for every part, so the test is exactly the
one its author cooked; the app pins each item's version when the file is loaded, so the test stays reproducible
after bank revisions. YAML or JSON:

    name: Practice run 1
    mode: end                  # end (default): no reveals until the whole test is done · realtime
    parts:
      - title: Part 1
        time_limit_min: 12     # or time_limit_s; omit for an untimed part
        items: [D6-101, D4-120@1, ...]   # id, or id@version to pin an older version

Each part becomes a session when it is started, so its clock starts on its first item.
"""

from __future__ import annotations

import random
import re
import sqlite3
from dataclasses import dataclass

import yaml

from . import store
from .db import dumps, loads, ms_between, new_id, now_iso

MAX_FILE_BYTES = 1_000_000
MAX_PARTS = 12
MAX_ITEMS_PER_PART = 200
ITEM_REF_RE = re.compile(r"^(D\d{1,2}-\d{3})(?:@(\d+))?$")


class PlanError(ValueError):
    """The file is not a usable modeled test. `problems` lists every reason, for the load page."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def parse_spec(text: str, conn: sqlite3.Connection) -> dict:
    """Validate a modeled-test file against the bank in the DB; return the normalized spec with pinned versions."""
    problems: list[str] = []
    try:
        raw = yaml.safe_load(text)  # JSON is valid YAML
    except yaml.YAMLError as exc:
        raise PlanError([f"Not valid YAML or JSON: {exc}"])
    if not isinstance(raw, dict):
        raise PlanError(["The file must be a mapping with 'name' and 'parts'."])
    name = str(raw.get("name") or "").strip()
    if not name:
        problems.append("Missing 'name'.")
    mode = raw.get("mode", "end")
    if mode not in store.MODES:
        problems.append(f"'mode' must be one of {', '.join(store.MODES)}.")
    parts_raw = raw.get("parts")
    if not isinstance(parts_raw, list) or not parts_raw:
        raise PlanError(problems + ["'parts' must be a non-empty list."])
    if len(parts_raw) > MAX_PARTS:
        problems.append(f"At most {MAX_PARTS} parts.")

    current = {r["item_id"]: r["version"] for r in store.current_items(conn)}
    stored = {(r[0], r[1]) for r in conn.execute("SELECT item_id, version FROM item_versions")}
    seen: dict[str, str] = {}
    parts = []
    for n, p in enumerate(parts_raw, start=1):
        where = f"Part {n}"
        if not isinstance(p, dict):
            problems.append(f"{where}: must be a mapping with 'items'.")
            continue
        title = str(p.get("title") or f"Part {n}").strip()[:80]
        limit_s = _time_limit(p, where, problems)
        refs = p.get("items")
        if not isinstance(refs, list) or not refs:
            problems.append(f"{where}: 'items' must be a non-empty list.")
            continue
        if len(refs) > MAX_ITEMS_PER_PART:
            problems.append(f"{where}: at most {MAX_ITEMS_PER_PART} items.")
        items = []
        for ref in refs:
            m = ITEM_REF_RE.match(str(ref).strip())
            if not m:
                problems.append(f"{where}: '{ref}' is not an item id (D6-101 or D6-101@2).")
                continue
            item_id, version = m.group(1), int(m.group(2)) if m.group(2) else current.get(m.group(1))
            if version is None:
                problems.append(f"{where}: {item_id} is not in the bank (or is retired).")
                continue
            if (item_id, version) not in stored:
                problems.append(f"{where}: {item_id}@{version} is not a stored version.")
                continue
            if item_id in seen:
                problems.append(f"{where}: {item_id} is already used in {seen[item_id]}.")
                continue
            seen[item_id] = where
            items.append({"item_id": item_id, "version": version})
        parts.append({"title": title, "time_limit_s": limit_s, "items": items})
    if problems:
        raise PlanError(problems)
    return {"name": name[:120], "description": str(raw.get("description") or "").strip()[:500], "mode": mode,
            "parts": parts}


def _time_limit(p: dict, where: str, problems: list[str]) -> int | None:
    if p.get("time_limit_s") not in (None, ""):
        value, factor = p["time_limit_s"], 1
    elif p.get("time_limit_min") not in (None, ""):
        value, factor = p["time_limit_min"], 60
    else:
        return None
    try:
        seconds = int(round(float(value) * factor))
    except (TypeError, ValueError):
        problems.append(f"{where}: the time limit must be a number.")
        return None
    if not 30 <= seconds <= 6 * 3600:
        problems.append(f"{where}: the time limit must be between 30 seconds and 6 hours.")
        return None
    return seconds


def create_plan(conn: sqlite3.Connection, spec: dict, installation_id: str, source_name: str | None) -> str:
    pid = new_id()
    conn.execute("""INSERT INTO plans(plan_id, installation_id, created_at, name, mode, source_name, spec_json)
                    VALUES (?,?,?,?,?,?,?)""",
                 (pid, installation_id, now_iso(), spec["name"], spec["mode"], (source_name or "")[:200] or None,
                  dumps(spec)))
    conn.commit()
    return pid


def get_plan(conn: sqlite3.Connection, pid: str) -> dict | None:
    row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (pid,)).fetchone()
    if not row:
        return None
    p = dict(row)
    p["spec"] = loads(p["spec_json"], {})
    return p


def list_plans(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT plan_id FROM plans ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [plan_state(conn, get_plan(conn, r[0])) for r in rows]


@dataclass
class _Pick:
    """Duck-types assemble.Picked for store.insert_session."""
    item: object
    section: str
    attributed: str
    presented_order: list[int] | None


@dataclass
class _Ref:
    item_id: str
    version: int


def start_next_part(conn: sqlite3.Connection, pid: str, installation_id: str, bank_fingerprint: str,
                    client: dict | None = None) -> str:
    """Session id of the part to work on now: the one in progress, else a new session for the next part."""
    plan = get_plan(conn, pid)
    if plan is None:
        raise KeyError(pid)
    state = plan_state(conn, plan)
    if state["current"] is None:
        raise ValueError("Every part of this test is done.")
    part = state["current"]
    if part["session_id"]:
        return part["session_id"]
    spec_part = plan["spec"]["parts"][part["index"] - 1]
    seed = random.SystemRandom().randrange(1, 2**31)
    rng = random.Random(seed)
    picked = []
    for ref in spec_part["items"]:
        snap = store.snapshot(conn, ref["item_id"], ref["version"])
        perm = None
        if snap["type"] in ("single", "multi"):
            perm = list(range(len(snap.get("options") or [])))
            rng.shuffle(perm)
        picked.append(_Pick(_Ref(ref["item_id"], ref["version"]), spec_part["title"], snap["subtopic"], perm))
    types = sorted({store.snapshot(conn, r["item_id"], r["version"])["type"] for r in spec_part["items"]})
    scope = {"preset": plan["name"], "subtopics": [], "formats": types, "length": len(picked), "priority": "fixed",
             "time_limit_s": spec_part["time_limit_s"], "assembled": len(picked), "note": None,
             "plan": {"name": plan["name"], "part": part["index"], "parts": len(plan["spec"]["parts"]),
                      "title": spec_part["title"]}}
    return store.insert_session(conn, scope, plan["mode"], seed, picked, installation_id, bank_fingerprint, client,
                                plan_id=pid, plan_part=part["index"])


def plan_state(conn: sqlite3.Connection, plan: dict) -> dict:
    """Parts with their session (if started), status and summary; `current` is the part to work on next."""
    sessions = {r["plan_part"]: dict(r) for r in conn.execute(
        "SELECT * FROM sessions WHERE plan_id = ? ORDER BY created_at", (plan["plan_id"],))}
    parts, current = [], None
    for i, sp in enumerate(plan["spec"]["parts"], start=1):
        s = sessions.get(i)
        status = s["status"] if s else "not_started"
        part = {"index": i, "title": sp["title"], "n": len(sp["items"]), "time_limit_s": sp["time_limit_s"],
                "status": status, "session_id": s["session_id"] if s else None, "summary": None, "used_s": None}
        if s:
            part["summary"] = store.session_summary(conn, s["session_id"])["total"]
            if s["started_at"]:
                end = s["finished_at"] or now_iso()
                used = (ms_between(s["started_at"], end) or 0) / 1000
                part["used_s"] = min(used, sp["time_limit_s"]) if sp["time_limit_s"] else used
        if current is None and status in ("not_started", "in_progress") and plan["status"] == "in_progress":
            current = part
        parts.append(part)
    done = plan["status"] != "in_progress" or current is None
    if done and plan["status"] == "in_progress":
        conn.execute("UPDATE plans SET status = 'finished', finished_at = ? WHERE plan_id = ?",
                     (now_iso(), plan["plan_id"]))
        conn.commit()
        plan = get_plan(conn, plan["plan_id"])
    return {**plan, "parts": parts, "current": current, "done": done,
            "n_items": sum(p["n"] for p in parts),
            "time_limit_s": sum(p["time_limit_s"] or 0 for p in parts) or None}


def abandon_plan(conn: sqlite3.Connection, pid: str) -> None:
    for r in conn.execute("SELECT session_id FROM sessions WHERE plan_id = ? AND status = 'in_progress'", (pid,)):
        store.finish_session(conn, r[0], "abandoned")
    conn.execute("UPDATE plans SET status = 'abandoned', finished_at = ? WHERE plan_id = ? AND status = 'in_progress'",
                 (now_iso(), pid))
    conn.commit()


def session_plan(conn: sqlite3.Connection, s: dict) -> dict | None:
    """The plan a session belongs to, or None for an ordinary test."""
    return get_plan(conn, s["plan_id"]) if s.get("plan_id") else None
