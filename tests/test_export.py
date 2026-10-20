import json

from app.export import read_export
from app.gemini import save_api_key
from tests.test_http import new_session, session_items, submit


def test_export_round_trip(make_client, data_dir):
    save_api_key(data_dir, "SUPER-SECRET-KEY-42")
    client = make_client(transport=None)
    sid = new_session(client, "realtime")
    for si in session_items(client, sid):
        client.get(f"/s/{sid}/item/{si['position']}")
        assert submit(client, sid, si).status_code == 200
    client.post("/api/events", json={"events": [{"type": "render", "session_id": sid, "position": 1}]})
    client.post("/api/feedback", json={"category": "typo", "text": "Fix wording", "session_id": sid, "position": 2})
    client.post(f"/s/{sid}/finish")

    r = client.get("/export/data.zip")
    assert r.status_code == 200
    assert "fde-data-" in r.headers["content-disposition"]
    assert b"SUPER-SECRET-KEY-42" not in r.content
    data = read_export(r.content)
    m = data["manifest"]
    ctx = client.app.state.ctx
    assert m["installation_id"] == ctx.installation_id and m["schema_version"] == 1
    assert m["bank_fingerprint"] == ctx.sync_report.fingerprint and m["audio_included"] is False
    with ctx.conn() as c:
        for name, table in [("sessions.jsonl", "sessions"), ("session_items.jsonl", "session_items"),
                            ("responses.jsonl", "responses"), ("events.jsonl", "events"),
                            ("feedback.jsonl", "feedback"), ("item_versions.jsonl", "item_versions")]:
            n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert len(data[name]) == n == m["files"][name], name
        db_resp = {r["response_id"]: dict(r) for r in c.execute("SELECT * FROM responses")}
    for row in data["responses.jsonl"]:
        orig = db_resp[row["response_id"]]
        assert row["item_id"] == orig["item_id"] and row["item_version"] == orig["item_version"]
        assert row["response"] == json.loads(orig["response_json"])
        assert row["time_to_submit_ms"] == orig["time_to_submit_ms"]
    iv = {(r["item_id"], r["version"]): r for r in data["item_versions.jsonl"]}
    assert iv[("D6-901", 1)]["content"]["stem"].startswith("A client wants")
    assert iv[("D6-901", 1)]["content_hash"].startswith("sha256:")
    assert not any(n.startswith("audio/") for n in data)

    with_audio = client.get("/export/data.zip?audio=1")
    import io
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(with_audio.content)).namelist()
    assert any(n.startswith("audio/") for n in names)


def test_feedback_exports(make_client):
    client = make_client()
    sid = new_session(client, "realtime", formats=["single"], length=1)
    client.get(f"/s/{sid}/item/1")
    client.post("/api/feedback", json={"category": "wrong_key", "text": "Option 2 is also right",
                                       "session_id": sid, "position": 1})
    client.post("/api/feedback", json={"category": "ui", "text": "Timer too small"})
    jl = client.get("/export/feedback.jsonl")
    rows = [json.loads(line) for line in jl.text.splitlines()]
    assert len(rows) == 2
    item_fb = next(r for r in rows if r["item_id"])
    assert item_fb["item"]["stem"] and item_fb["item"]["options"] and item_fb["item_content_hash"].startswith("sha256:")
    assert item_fb["context"]["position"] == 1
    md = client.get("/export/feedback.md").text
    assert f"## {item_fb['item_id']}@1" in md and "Option 2 is also right" in md and "General (no item)" in md
