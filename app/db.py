"""SQLite storage: connection, append-only migrations (PRAGMA user_version), small helpers.

Every row id is a UUIDv4 string and every timestamp is UTC ISO-8601 with milliseconds, so
exports from many installations can be merged later without collisions.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .taxonomy import REPO_ROOT

DB_FILENAME = "fde.sqlite3"

# Append-only. Never edit a migration that may have been applied somewhere; add a new one.
MIGRATIONS: list[str] = [
    # 1 -- initial schema (docs/app-spec.md). Extensions beyond the spec are marked "ext".
    """
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);

    CREATE TABLE item_versions(
        item_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        content_hash TEXT NOT NULL,
        type TEXT NOT NULL,
        domain_id TEXT NOT NULL,
        subtopic TEXT NOT NULL,
        tags_json TEXT NOT NULL,
        difficulty INTEGER,
        level TEXT,
        time_s INTEGER,
        content_json TEXT NOT NULL,
        irt_a REAL, irt_b REAL, irt_c REAL, irt_source TEXT,
        first_loaded_at TEXT NOT NULL,
        PRIMARY KEY(item_id, version)
    );

    CREATE TABLE items(
        item_id TEXT PRIMARY KEY,
        current_version INTEGER NOT NULL,
        retired INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE sessions(
        session_id TEXT PRIMARY KEY,
        installation_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        status TEXT NOT NULL,
        mode TEXT NOT NULL,
        scope_json TEXT NOT NULL,
        seed INTEGER NOT NULL,
        app_version TEXT,
        bank_fingerprint TEXT,
        taxonomy_version TEXT,
        assembler_version TEXT,
        client_json TEXT
    );

    CREATE TABLE session_items(
        session_id TEXT NOT NULL,
        position INTEGER NOT NULL,
        item_id TEXT NOT NULL,
        item_version INTEGER NOT NULL,
        section TEXT NOT NULL,
        presented_order_json TEXT,
        attributed_subtopic TEXT,          -- ext: subtopic the assembler counted it under
        presented_at TEXT,                 -- ext: first time the server served this position
        PRIMARY KEY(session_id, position),
        FOREIGN KEY(item_id, item_version) REFERENCES item_versions(item_id, version)
    );

    CREATE TABLE responses(
        response_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        position INTEGER NOT NULL,
        installation_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        item_version INTEGER NOT NULL,
        response_json TEXT NOT NULL,
        correct INTEGER,
        score REAL,
        max_points REAL,
        points REAL,
        grader TEXT NOT NULL,
        grader_model TEXT,
        grader_detail_json TEXT,
        presented_at TEXT,
        first_interaction_at TEXT,
        submitted_at TEXT NOT NULL,
        time_to_first_ms INTEGER,
        time_to_submit_ms INTEGER,
        active_ms INTEGER,
        answer_changes INTEGER,
        focus_losses INTEGER,
        paste_count INTEGER,
        runs INTEGER,
        confidence INTEGER,
        flagged INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0,
        mode TEXT NOT NULL,
        feedback_revealed_at TEXT,
        created_at TEXT NOT NULL,
        superseded INTEGER NOT NULL DEFAULT 0,   -- ext: 1 when a later response replaced a skip
        FOREIGN KEY(item_id, item_version) REFERENCES item_versions(item_id, version)
    );
    CREATE INDEX idx_responses_item ON responses(item_id, item_version);
    CREATE INDEX idx_responses_session ON responses(session_id);

    CREATE TABLE events(
        event_id TEXT PRIMARY KEY,
        session_id TEXT,
        position INTEGER,
        item_id TEXT,
        item_version INTEGER,
        type TEXT NOT NULL,
        client_ts_ms REAL,
        server_ts TEXT NOT NULL,
        payload_json TEXT
    );
    CREATE INDEX idx_events_session ON events(session_id);

    CREATE TABLE feedback(
        feedback_id TEXT PRIMARY KEY,
        installation_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        session_id TEXT,
        item_id TEXT,
        item_version INTEGER,
        category TEXT NOT NULL,
        text TEXT NOT NULL,
        context_json TEXT,
        resolved INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
    """,
    # 2 -- reading time ("Done reading" button) and modeled tests loaded from a file (timed parts, back to back).
    """
    ALTER TABLE responses ADD COLUMN read_ms INTEGER;

    CREATE TABLE plans(
        plan_id TEXT PRIMARY KEY,
        installation_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        name TEXT NOT NULL,
        mode TEXT NOT NULL,
        source_name TEXT,
        spec_json TEXT NOT NULL,           -- the validated file: parts with titles, time limits, pinned items
        status TEXT NOT NULL DEFAULT 'in_progress',
        finished_at TEXT
    );

    ALTER TABLE sessions ADD COLUMN plan_id TEXT;
    ALTER TABLE sessions ADD COLUMN plan_part INTEGER;
    CREATE INDEX idx_sessions_plan ON sessions(plan_id);
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def default_data_dir() -> Path:
    return Path(os.environ.get("FDE_DATA_DIR") or (REPO_ROOT / "var"))


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def ms_between(a: str | None, b: str | None) -> int | None:
    da, db_ = parse_iso(a), parse_iso(b)
    if not da or not db_:
        return None
    return int((db_ - da).total_seconds() * 1000)


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations. Idempotent; returns the number applied."""
    current = schema_version(conn)
    applied = 0
    for number, sql in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
        applied += 1
    return applied


def init_meta(conn: sqlite3.Connection, app_version: str) -> str:
    """Create installation_id etc. on first run; return installation_id."""
    ts = now_iso()
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('installation_id', ?)", (new_id(),))
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('created_at', ?)", (ts,))
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('app_version', ?)", (app_version,))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()
    return get_meta(conn, "installation_id")


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    else:
        conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def loads(text: str | None, default=None):
    if text is None or text == "":
        return default
    return json.loads(text)


def open_db(data_dir: Path, app_version: str) -> tuple[sqlite3.Connection, str]:
    """Create the data dir, connect, migrate, init meta. Returns (conn, installation_id)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(data_dir / DB_FILENAME)
    migrate(conn)
    return conn, init_meta(conn, app_version)
