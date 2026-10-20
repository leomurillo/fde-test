"""Bank -> DB sync at startup (and on demand from the settings page).

* Loads bank/items/*.yaml (or $FDE_BANK_DIR), validates every item on its own and skips only the
  invalid ones; a half-written file never crashes the app.
* Inserts unseen (id, version) pairs into item_versions with a full content snapshot + hash and IRT seeds.
* Updates items.current_version; marks ids missing from the bank as retired (and un-retires them if they return).
* If a stored (id, version) has a different hash than the file, logs a loud warning and keeps the stored
  snapshot: responses must stay interpretable, so content changes need a version bump.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import bank as bankmod
from .db import dumps, now_iso
from .taxonomy import load_taxonomy

log = logging.getLogger("fde.sync")

DIFFICULTY_TO_B = {1: -1.0, 2: 0.0, 3: 1.0}


@dataclass
class LoadedItem:
    data: dict
    content_hash: str
    file: str

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def version(self) -> int:
        return self.data["version"]


@dataclass
class SyncReport:
    bank_dir: str = ""
    files: int = 0
    loaded: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    new_versions: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    fingerprint: str = ""
    synced_at: str = ""


def bank_dir_default() -> Path:
    return Path(os.environ.get("FDE_BANK_DIR") or bankmod.BANK_DIR / "items")


def load_bank(bank_dir: Path, report: SyncReport) -> list[LoadedItem]:
    """Load and validate item by item. Returns only valid items (no executable checks, for speed)."""
    tax = load_taxonomy()
    files = sorted(Path(bank_dir).glob("D*.yaml"))
    report.bank_dir = str(bank_dir)
    report.files = len(files)
    raw_report = bankmod.Report()
    try:
        raw_items = bankmod.load_items(files, raw_report)
    except Exception as exc:  # unreadable directory, permission error, ...
        report.errors.append(f"bank load failed: {exc}")
        return []
    report.errors.extend(raw_report.errors)

    valid: list[bankmod.Item] = []
    seen_ids: set[str] = set()
    for item in raw_items:
        rep = bankmod.Report()
        try:
            bankmod.validate([item], tax, rep, run_executables=False)
        except Exception as exc:  # malformed structure the validator did not anticipate
            rep.err(f"{item.file.name}:{item.data.get('id', '?')}", f"unreadable item ({type(exc).__name__}: {exc})")
        item_id = item.data.get("id")
        if not rep.errors and item_id in seen_ids:
            rep.err(f"{item.file.name}:{item_id}", "duplicate id (skipped)")
        if rep.errors:
            report.errors.extend(rep.errors)
            report.skipped += 1
            continue
        seen_ids.add(item_id)
        valid.append(item)

    lock_rep = bankmod.Report()
    try:
        bankmod.check_lock(valid, lock_rep)
    except Exception as exc:
        report.warnings.append(f"could not read bank/lock.json: {exc}")
    if lock_rep.errors:
        bad = {e.split(": ", 1)[0] for e in lock_rep.errors}
        report.errors.extend(lock_rep.errors)
        kept = [it for it in valid if f"{it.file.name}:{it.id}" not in bad]
        report.skipped += len(valid) - len(kept)
        valid = kept

    out = []
    for it in valid:
        data = bankmod._strip_private(it.data)
        out.append(LoadedItem(data, bankmod.content_hash(data), it.file.name))
    report.loaded = len(out)
    return out


def fingerprint(items: list[LoadedItem]) -> str:
    lines = sorted(f"{it.id}@{it.version}:{it.content_hash}" for it in items)
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def irt_seeds(data: dict) -> tuple[float, float, float]:
    a = 1.0
    b = DIFFICULTY_TO_B.get(data.get("difficulty"), 0.0)
    if data.get("type") in ("single", "multi") and data.get("options"):
        c = round(1.0 / len(data["options"]), 4)
    else:
        c = 0.0
    return a, b, c


def domain_of(data: dict) -> str:
    return load_taxonomy().subtopic(data["subtopic"]).domain_id


def sync_items(conn: sqlite3.Connection, items: list[LoadedItem], report: SyncReport) -> SyncReport:
    ts = now_iso()
    stored = {(r["item_id"], r["version"]): r["content_hash"]
              for r in conn.execute("SELECT item_id, version, content_hash FROM item_versions")}
    for it in items:
        key = (it.id, it.version)
        if key not in stored:
            a, b, c = irt_seeds(it.data)
            conn.execute(
                """INSERT INTO item_versions(item_id, version, content_hash, type, domain_id, subtopic, tags_json,
                   difficulty, level, time_s, content_json, irt_a, irt_b, irt_c, irt_source, first_loaded_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (it.id, it.version, it.content_hash, it.data["type"], domain_of(it.data), it.data["subtopic"],
                 dumps(it.data.get("tags") or []), it.data.get("difficulty"), it.data.get("level"),
                 it.data.get("time_s"), dumps(it.data), a, b, c, "seed", ts),
            )
            report.new_versions.append(f"{it.id}@{it.version}")
        elif stored[key] != it.content_hash:
            msg = (f"{it.file}:{it.id}@{it.version} content changed without a version bump; "
                   f"keeping the stored snapshot ({stored[key][:19]}...). Bump 'version' in the bank file.")
            log.warning("!!! %s", msg)
            report.warnings.append(msg)
        conn.execute(
            """INSERT INTO items(item_id, current_version, retired, updated_at) VALUES (?,?,0,?)
               ON CONFLICT(item_id) DO UPDATE SET
                 updated_at = CASE WHEN items.current_version != excluded.current_version OR items.retired != 0
                              THEN excluded.updated_at ELSE items.updated_at END,
                 current_version = excluded.current_version, retired = 0""",
            (it.id, it.version, ts),
        )
    present = {it.id for it in items}
    for row in conn.execute("SELECT item_id FROM items WHERE retired = 0").fetchall():
        if row["item_id"] not in present:
            conn.execute("UPDATE items SET retired = 1, updated_at = ? WHERE item_id = ?", (ts, row["item_id"]))
            report.retired.append(row["item_id"])
    conn.commit()
    report.fingerprint = fingerprint(items)
    report.synced_at = ts
    return report


def sync_bank(conn: sqlite3.Connection, bank_dir: Path | None = None) -> SyncReport:
    report = SyncReport()
    items = load_bank(Path(bank_dir or bank_dir_default()), report)
    sync_items(conn, items, report)
    for e in report.errors:
        log.error("bank: %s", e)
    log.info("bank sync: %d items loaded, %d skipped, %d new versions, %d retired",
             report.loaded, report.skipped, len(report.new_versions), len(report.retired))
    return report
