import json
import re

from app.db import SCHEMA_VERSION
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
    assert m["installation_id"] == ctx.installation_id and m["schema_version"] == SCHEMA_VERSION
    assert m["bank_fingerprint"] == ctx.sync_report.fingerprint and m["audio_included"] is False
    with ctx.conn() as c:
        for name, table in [("sessions.jsonl", "sessions"), ("session_items.jsonl", "session_items"),
                            ("responses.jsonl", "responses"), ("events.jsonl", "events"),
                            ("feedback.jsonl", "feedback"), ("item_versions.jsonl", "item_versions"),
                            ("plans.jsonl", "plans")]:
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


def test_feedback_exports_can_leave_out_resolved_notes(make_client):
    client = make_client()
    sid = new_session(client, "realtime", formats=["single"], length=1)
    client.get(f"/s/{sid}/item/1")
    fixed = client.post("/api/feedback", json={"category": "wrong_key", "text": "Option 2 is also right",
                                               "session_id": sid, "position": 1}).json()["feedback_id"]
    client.post("/api/feedback", json={"category": "typo", "text": "Typo in the stem", "session_id": sid,
                                       "position": 1})
    client.post("/api/feedback", json={"category": "ui", "text": "Timer too small"})
    client.post(f"/feedback/{fixed}/resolve", data={"resolved": "1"})

    # no filter (and any value but "open") still exports every note, resolved or not
    for url in ("/export/feedback.jsonl", "/export/feedback.jsonl?show=all"):
        r = client.get(url)
        assert len(r.text.splitlines()) == 3 and 'filename="feedback.jsonl"' in r.headers["content-disposition"]
    assert "Option 2 is also right" in client.get("/export/feedback.md").text

    r = client.get("/export/feedback.jsonl?show=open")
    assert 'filename="feedback-open.jsonl"' in r.headers["content-disposition"]
    rows = [json.loads(line) for line in r.text.splitlines()]
    assert {x["text"] for x in rows} == {"Typo in the stem", "Timer too small"}
    assert all(x["resolved"] == 0 for x in rows)
    assert next(x for x in rows if x["item_id"])["item"]["stem"]  # still carries the item snapshot

    r = client.get("/export/feedback.md?show=open")
    assert 'filename="feedback-open.md"' in r.headers["content-disposition"]
    assert r.text.startswith("# Open item feedback (2 notes)")
    assert "Typo in the stem" in r.text and "Timer too small" in r.text and "Option 2 is also right" not in r.text
    assert "Open notes only" in r.text
    assert "# Open item feedback" not in client.get("/export/feedback.md").text

    # reopening a note brings it back; resolving the rest leaves an empty file
    client.post(f"/feedback/{fixed}/resolve", data={"resolved": "0"})
    assert len(client.get("/export/feedback.jsonl?show=open").text.splitlines()) == 3
    with client.app.state.ctx.conn() as c:
        c.execute("UPDATE feedback SET resolved = 1")
        c.commit()
    assert client.get("/export/feedback.jsonl?show=open").text == ""
    assert client.get("/export/feedback.md?show=open").text.startswith("# Open item feedback (0 notes)")

    # the full data export is the portability format: it keeps resolved notes
    data = read_export(client.get("/export/data.zip").content)
    assert len(data["feedback.jsonl"]) == 3


def test_feedback_pages_offer_open_only_export(make_client):
    client = make_client()
    client.post("/api/feedback", json={"category": "ui", "text": "one"})
    done = client.post("/api/feedback", json={"category": "ui", "text": "two"}).json()["feedback_id"]
    client.post(f"/feedback/{done}/resolve", data={"resolved": "1"})

    def box(url):
        """The 'Only open issues' checkbox on a page: (present, checked)."""
        m = re.search(r'<input type="checkbox" name="show" value="open"([^>]*)>', client.get(url).text)
        return (m is not None, m is not None and "checked" in m.group(1))

    assert box("/feedback") == (True, True)                # Open tab: ticked, so the files match what is listed
    assert box("/feedback?show=all") == (True, False)      # All tab: unticked
    assert box("/export") == (True, True)
    page = client.get("/export").text
    assert "2 notes (1 open)" in page and "(1 of 2)" in page
    assert 'formaction="/export/feedback.jsonl"' in page and 'formaction="/export/feedback.md"' in page
