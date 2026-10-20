import re

from app import __version__
from app.db import SCHEMA_VERSION, connect, get_meta, init_meta, migrate, new_id, now_iso, open_db, schema_version

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def test_migrations_are_idempotent(tmp_path):
    conn = connect(tmp_path / "x.sqlite3")
    assert migrate(conn) == SCHEMA_VERSION
    assert schema_version(conn) == SCHEMA_VERSION
    assert migrate(conn) == 0
    assert migrate(conn) == 0
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meta", "item_versions", "items", "sessions", "session_items", "responses", "events", "feedback",
            "settings"} <= tables
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_responses_item", "idx_responses_session", "idx_events_session"} <= indexes


def test_installation_id_is_stable_uuid4(tmp_path):
    conn, inst = open_db(tmp_path, __version__)
    assert UUID4.match(inst)
    conn.close()
    conn2, inst2 = open_db(tmp_path, "9.9.9")
    assert inst2 == inst
    assert get_meta(conn2, "app_version") == __version__  # first-run version is kept
    assert get_meta(conn2, "schema_version") == str(SCHEMA_VERSION)
    assert get_meta(conn2, "created_at")
    assert init_meta(conn2, "x") == inst


def test_ids_and_timestamps():
    assert UUID4.match(new_id())
    ts = now_iso()
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$", ts)
