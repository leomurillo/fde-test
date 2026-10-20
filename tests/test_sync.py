import logging

import yaml

from app.db import connect, migrate
from app.sync import load_bank, SyncReport, sync_bank
from app.store import pool_items, snapshot


def _conn(tmp_path):
    conn = connect(tmp_path / "db.sqlite3")
    migrate(conn)
    return conn


def _edit(bank_dir, fn):
    path = bank_dir / "D06-sample.yaml"
    data = yaml.safe_load(path.read_text())
    fn(data["items"])
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def test_initial_sync_inserts_versions_with_seeds(tmp_path, bank_copy):
    conn = _conn(tmp_path)
    rep = sync_bank(conn, bank_copy)
    assert rep.loaded == 7 and rep.skipped == 0 and len(rep.new_versions) == 7
    assert rep.fingerprint.startswith("sha256:")
    rows = {r["item_id"]: r for r in conn.execute("SELECT * FROM item_versions")}
    assert rows["D6-901"]["irt_c"] == 0.25 and rows["D6-901"]["irt_b"] == 0.0 and rows["D6-901"]["irt_a"] == 1.0
    assert rows["D6-903"]["irt_b"] == -1.0 and rows["D6-903"]["irt_c"] == 0.0
    assert rows["D6-901"]["irt_source"] == "seed" and rows["D6-901"]["domain_id"] == "D6"
    assert snapshot(conn, "D6-901", 1)["stem"].startswith("A client wants")
    # second sync is a no-op
    rep2 = sync_bank(conn, bank_copy)
    assert rep2.new_versions == [] and rep2.retired == [] and rep2.fingerprint == rep.fingerprint


def test_new_version_and_retired_item(tmp_path, bank_copy):
    conn = _conn(tmp_path)
    sync_bank(conn, bank_copy)

    def bump(items):
        items[0]["version"] = 2
        items[0]["stem"] = items[0]["stem"] + " (Revised.)"
        del items[1]  # D6-902 removed from the bank
    _edit(bank_copy, bump)
    rep = sync_bank(conn, bank_copy)
    assert rep.new_versions == ["D6-901@2"]
    assert rep.retired == ["D6-902"]
    versions = [r[0] for r in conn.execute("SELECT version FROM item_versions WHERE item_id='D6-901' ORDER BY version")]
    assert versions == [1, 2]
    assert conn.execute("SELECT current_version FROM items WHERE item_id='D6-901'").fetchone()[0] == 2
    assert conn.execute("SELECT retired FROM items WHERE item_id='D6-902'").fetchone()[0] == 1
    assert "D6-902" not in {p.item_id for p in pool_items(conn)}
    # old snapshot still there for interpreting old responses
    assert "(Revised.)" not in snapshot(conn, "D6-901", 1)["stem"]
    assert "(Revised.)" in snapshot(conn, "D6-901", 2)["stem"]


def test_retired_item_comes_back(tmp_path, bank_copy):
    conn = _conn(tmp_path)
    sync_bank(conn, bank_copy)
    original = (bank_copy / "D06-sample.yaml").read_text()
    _edit(bank_copy, lambda items: items.pop(1))
    sync_bank(conn, bank_copy)
    (bank_copy / "D06-sample.yaml").write_text(original)
    sync_bank(conn, bank_copy)
    assert conn.execute("SELECT retired FROM items WHERE item_id='D6-902'").fetchone()[0] == 0


def test_changed_hash_without_bump_warns_and_keeps_snapshot(tmp_path, bank_copy, caplog):
    conn = _conn(tmp_path)
    sync_bank(conn, bank_copy)
    _edit(bank_copy, lambda items: items[0].__setitem__("stem", "Silently edited stem."))
    with caplog.at_level(logging.WARNING, logger="fde.sync"):
        rep = sync_bank(conn, bank_copy)
    assert any("D6-901@1" in w and "without a version bump" in w for w in rep.warnings)
    assert any("without a version bump" in r.message for r in caplog.records)
    assert snapshot(conn, "D6-901", 1)["stem"].startswith("A client wants")


def test_invalid_items_are_skipped_not_fatal(tmp_path, bank_copy):
    conn = _conn(tmp_path)

    def break_some(items):
        items[0]["options"] = items[0]["options"][:2]          # invalid option count
        items[1]["rubric"] = ["not", "a", "mapping"]           # structurally weird (validator must not crash)
        items[2]["subtopic"] = "nonexistent.tag"
        items.append(dict(items[3]))                            # duplicate id D6-904
        items.append("garbage")
    _edit(bank_copy, break_some)
    (bank_copy / "D07-broken.yaml").write_text("domain: D7\nitems: [ {id: D7-001, \n")  # YAML error
    (bank_copy / "D08-half.yaml").write_text("domain: D8\nitems:\n  - id: D8-001\n    version: 1\n")
    rep = sync_bank(conn, bank_copy)
    loaded = {r[0] for r in conn.execute("SELECT item_id FROM items")}
    assert "D6-901" not in loaded and "D6-903" not in loaded
    assert {"D6-902", "D6-904", "D6-905", "D6-906", "D6-907"} <= loaded
    assert rep.errors and rep.skipped >= 4
    assert any("YAML error" in e for e in rep.errors)


def test_load_bank_on_missing_dir(tmp_path):
    rep = SyncReport()
    assert load_bank(tmp_path / "nope", rep) == []
