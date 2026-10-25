"""Exports: feedback.jsonl / feedback.md (for fixing items with an LLM) and the full data zip
(the future upload/aggregation format). JSON columns (`*_json`) are decoded into objects under the
column name without the `_json` suffix; everything else is exported verbatim. Never includes the API key
(it is not stored in the DB) and excludes audio unless asked.
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .db import SCHEMA_VERSION, get_meta, loads, now_iso
from .taxonomy import load_taxonomy

TABLES = {
    "sessions.jsonl": "SELECT * FROM sessions ORDER BY created_at",
    "session_items.jsonl": "SELECT * FROM session_items ORDER BY session_id, position",
    "responses.jsonl": "SELECT * FROM responses ORDER BY created_at",
    "events.jsonl": "SELECT * FROM events ORDER BY server_ts",
    "feedback.jsonl": "SELECT * FROM feedback ORDER BY created_at",
    "item_versions.jsonl": "SELECT * FROM item_versions ORDER BY item_id, version",
    "plans.jsonl": "SELECT * FROM plans ORDER BY created_at",
}


def _decode_row(row: sqlite3.Row) -> dict:
    out = {}
    for k in row.keys():
        v = row[k]
        if k.endswith("_json"):
            out[k[: -len("_json")]] = loads(v)
        else:
            out[k] = v
    return out


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def _item_snapshot(conn: sqlite3.Connection, item_id: str | None, version: int | None) -> dict | None:
    if not item_id:
        return None
    if version is None:
        row = conn.execute("SELECT current_version FROM items WHERE item_id = ?", (item_id,)).fetchone()
        version = row[0] if row else None
    row = conn.execute("SELECT content_json, content_hash FROM item_versions WHERE item_id = ? AND version = ?",
                       (item_id, version)).fetchone()
    if not row:
        return None
    return {"version": version, "content_hash": row["content_hash"], "content": loads(row["content_json"])}


def feedback_records(conn: sqlite3.Connection, only_open: bool = False) -> list[dict]:
    """Feedback notes with their item snapshot. `only_open` leaves out the ones marked resolved."""
    out = []
    where = "WHERE resolved = 0 " if only_open else ""
    for r in conn.execute(f"SELECT * FROM feedback {where}ORDER BY created_at"):
        rec = _decode_row(r)
        snap = _item_snapshot(conn, r["item_id"], r["item_version"])
        rec["item"] = snap["content"] if snap else None
        rec["item_content_hash"] = snap["content_hash"] if snap else None
        out.append(rec)
    return out


def feedback_jsonl(conn: sqlite3.Connection, only_open: bool = False) -> str:
    return _jsonl(feedback_records(conn, only_open))


def feedback_md(conn: sqlite3.Connection, only_open: bool = False) -> str:
    recs = feedback_records(conn, only_open)
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        key = f"{r['item_id']}@{r['item_version']}" if r["item_id"] else "General (no item)"
        groups[key].append(r)
    title = "Open item feedback" if only_open else "Item feedback"
    lines = [f"# {title} ({len(recs)} notes)", "", f"Exported {now_iso()}.", ""]
    if only_open:
        lines += ["Open notes only: notes already marked resolved are left out.", ""]
    for key in sorted(groups, key=lambda k: (k.startswith("General"), k)):
        rs = groups[key]
        lines += [f"## {key}", ""]
        item = rs[0].get("item")
        if item:
            lines += [f"- type: `{item.get('type')}` · subtopic: `{item.get('subtopic')}` · "
                      f"difficulty: {item.get('difficulty')}", "", "**Stem**", "", str(item.get("stem", "")).strip(), ""]
            for i, o in enumerate(item.get("options") or []):
                mark = "x" if o.get("correct") else " "
                lines.append(f"- [{mark}] {o.get('text')}")
            if item.get("options"):
                lines.append("")
            if item.get("answer"):
                lines += [f"**Answer:** `{json.dumps(item['answer'], ensure_ascii=False)}`", ""]
        lines.append("**Feedback**")
        lines.append("")
        for r in rs:
            status = "resolved" if r["resolved"] else "open"
            ctx = r.get("context") or {}
            extra = []
            if ctx.get("answered") is not None:
                extra.append("answered" if ctx.get("answered") else "not answered")
            if ctx.get("correct") is not None:
                extra.append("was correct" if ctx["correct"] else "was wrong")
            if ctx.get("user_answer"):
                extra.append(f"user answer: {str(ctx['user_answer'])[:200]!r}")
            suffix = f" ({'; '.join(extra)})" if extra else ""
            text = " ".join(str(r["text"]).split())
            lines.append(f"- **{r['category']}** [{status}, {r['created_at'][:10]}]{suffix}: {text}")
        lines.append("")
    return "\n".join(lines)


def build_data_zip(conn: sqlite3.Connection, data_dir: Path, include_audio: bool = False,
                   bank_fingerprint: str | None = None) -> tuple[str, bytes]:
    installation_id = get_meta(conn, "installation_id")
    exported_at = now_iso()
    buf = io.BytesIO()
    counts = {}
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, query in TABLES.items():
            rows = [_decode_row(r) for r in conn.execute(query)]
            counts[name] = len(rows)
            zf.writestr(name, _jsonl(rows))
        audio_files = []
        if include_audio:
            audio_dir = Path(data_dir) / "audio"
            for p in sorted(audio_dir.glob("*")) if audio_dir.exists() else []:
                if p.is_file():
                    zf.write(p, f"audio/{p.name}")
                    audio_files.append(p.name)
        manifest = {
            "format": "fde-test-export", "format_version": 1,
            "installation_id": installation_id, "schema_version": SCHEMA_VERSION,
            "app_version": __version__, "first_run_app_version": get_meta(conn, "app_version"),
            "installation_created_at": get_meta(conn, "created_at"),
            "bank_fingerprint": bank_fingerprint, "taxonomy_version": load_taxonomy().version,
            "exported_at": exported_at, "files": counts, "audio_included": include_audio,
            "audio_files": len(audio_files), "json_columns": "decoded; key = column name without _json",
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"fde-data-{installation_id}-{date}.zip", buf.getvalue()


def read_export(data: bytes | str | Path) -> dict:
    """Parse an export zip back into {"manifest": {...}, "<name>.jsonl": [rows...]} (for aggregation/tests)."""
    src = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
    out: dict = {}
    with zipfile.ZipFile(src) as zf:
        for name in zf.namelist():
            if name == "manifest.json":
                out["manifest"] = json.loads(zf.read(name))
            elif name.endswith(".jsonl"):
                out[name] = [json.loads(line) for line in zf.read(name).decode("utf-8").splitlines() if line.strip()]
    return out
