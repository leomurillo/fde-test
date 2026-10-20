"""Run and grade SQL answers against an in-memory SQLite database.

The candidate query runs read-only (authorizer), single-statement, with an
instruction budget so runaway queries abort.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass

from .basic import GradeResult

MAX_ROWS = 500
OPCODE_BUDGET = 5_000_000  # progress handler fires every 1000 VM instructions

_READ_ONLY_ACTIONS = {
    sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE,
}


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool = False
    error: str | None = None


def _authorizer(action, *_args):
    return sqlite3.SQLITE_OK if action in _READ_ONLY_ACTIONS else sqlite3.SQLITE_DENY


def run_query(setup: str, query: str, max_rows: int = MAX_ROWS) -> QueryResult:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(setup)
        conn.set_authorizer(_authorizer)
        budget = {"ticks": 0}

        def tick():
            budget["ticks"] += 1
            return 1 if budget["ticks"] * 1000 > OPCODE_BUDGET else 0

        conn.set_progress_handler(tick, 1000)
        cur = conn.execute(query.strip().rstrip(";"))
        columns = [d[0] for d in (cur.description or [])]
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        return QueryResult(columns, [tuple(r) for r in rows[:max_rows]], truncated)
    except sqlite3.Error as exc:
        msg = str(exc)
        if "interrupted" in msg:
            msg = "query took too long (aborted)"
        elif "not authorized" in msg:
            msg = "only read-only SELECT / WITH queries are allowed"
        return QueryResult([], [], error=msg)
    finally:
        conn.close()


def _normalize(rows: list[tuple], float_round: int) -> list[tuple]:
    out = []
    for row in rows:
        norm = []
        for v in row:
            if isinstance(v, float):
                v = round(v, float_round)
                if v == int(v):
                    v = int(v)  # 3.0 and 3 compare equal
            norm.append(v)
        out.append(tuple(norm))
    return out


def grade_sql(setup: str, reference: str, candidate: str, order_matters: bool = False,
              float_round: int = 2) -> GradeResult:
    ref = run_query(setup, reference)
    if ref.error:
        raise ValueError(f"reference query failed: {ref.error}")
    got = run_query(setup, candidate)
    detail = {"columns": got.columns, "row_count": len(got.rows), "expected_row_count": len(ref.rows),
              "expected_columns": ref.columns}
    if got.error:
        return GradeResult(False, 0.0, {**detail, "error": got.error})
    if len(got.columns) != len(ref.columns):
        return GradeResult(False, 0.0, {**detail, "error": f"expected {len(ref.columns)} columns, got {len(got.columns)}"})
    a, b = _normalize(got.rows, float_round), _normalize(ref.rows, float_round)
    same = a == b if order_matters else Counter(map(repr, a)) == Counter(map(repr, b))
    if not same and not order_matters:
        detail["hint"] = "row set differs"
    elif not same:
        detail["hint"] = "rows or their order differ"
    return GradeResult(same, 1.0 if same else 0.0, detail)
