import html
import json
import re

import yaml

from app import store
from app.gemini import save_api_key
from tests.conftest import FIXTURES, FakeGemini

ITEMS = {d["id"]: d for d in yaml.safe_load((FIXTURES / "D06-sample.yaml").read_text())["items"]}
SCOPE = ["agents.when", "agents.patterns", "agents.state", "agents.multi"]
ALL = ["single", "multi", "numeric", "open", "audio", "sql", "code"]


def new_session(client, mode, length=7, formats=ALL, seed="7", **extra):
    data = {"subtopics": SCOPE, "formats": formats, "length": str(length), "mode": mode,
            "priority": "unseen_first", "seed": seed, "preset": "", **extra}
    r = client.post("/new", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"].rsplit("/", 1)[-1]


def session_items(client, sid):
    with client.app.state.ctx.conn() as c:
        return store.session_items(c, sid)


def right_answer(item, perm):
    t = item["type"]
    if t in ("single", "multi"):
        correct = {i for i, o in enumerate(item["options"]) if o.get("correct")}
        return {"selected_presented": [p for p, orig in enumerate(perm) if orig in correct]}
    return {"numeric": {"value": str(item.get("answer", {}).get("value"))},
            "open": {"text": "Recommend a workflow with two model steps, approvals and idempotency."},
            "sql": {"sql": item.get("sql", {}).get("reference")},
            "code": {"code": item.get("code", {}).get("reference")}}.get(t, {})


TEL = {"time_to_first_ms": 1200, "time_to_submit_ms": 9000, "active_ms": 8500, "answer_changes": 1,
       "focus_losses": 2, "paste_count": 1, "runs": 0}


def submit(client, sid, si, answer=None, skipped=False):
    item = ITEMS[si["item_id"]]
    if item["type"] == "audio" and not skipped:
        meta = {"answer": {"duration_ms": 4000}, "telemetry": TEL, "confidence": 3}
        return client.post(f"/api/s/{sid}/item/{si['position']}/audio", data={"meta": json.dumps(meta)},
                           files={"file": ("answer.webm", b"\x1aE\xdf\xa3 fake webm audio", "audio/webm")})
    body = {"skipped": skipped, "telemetry": TEL, "confidence": 2, "flagged": si["position"] == 1}
    if not skipped:
        body["answer"] = answer if answer is not None else right_answer(item, si["presented_order"])
    return client.post(f"/api/s/{sid}/item/{si['position']}/submit", json=body)


def test_realtime_flow(make_client):
    client = make_client()
    sid = new_session(client, "realtime")
    r = client.get(f"/s/{sid}", follow_redirects=False)
    assert r.headers["location"].endswith("/item/1")
    items = session_items(client, sid)
    assert len(items) == 7 and {si["item_id"] for si in items} == set(ITEMS)
    sections = [si["section"] for si in items]
    assert sections[0] == "Multiple choice" and sections[-1] == "Spoken"

    for si in items:
        item = ITEMS[si["item_id"]]
        page = client.get(f"/s/{sid}/item/{si['position']}")
        assert page.status_code == 200
        # nothing that gives the answer away before answering
        for o in item.get("options") or []:
            assert o["why"] not in page.text
        if item.get("model_answer"):
            assert item["model_answer"].strip()[:30] not in page.text
        if item["type"] == "code":
            assert "min(cap" not in page.text  # reference implementation
        if item["type"] == "sql":
            assert "HAVING" not in page.text
        if item["type"] in ("sql", "code"):
            run = client.post(f"/api/s/{sid}/item/{si['position']}/run",
                              json={"sql": "SELECT * FROM calls", "code": item.get("code", {}).get("starter")})
            assert run.status_code == 200
        r = submit(client, sid, si)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "reveal_html" in d
        if item["type"] in ("single", "multi"):
            assert d["verdict"] == "correct"
            for o in item["options"]:
                assert o["why"].split(";")[0][:40].replace("'", "&#39;") in d["reveal_html"]
        elif item["type"] in ("open", "audio"):
            assert d["verdict"] == "ungraded"  # no key: self-grade path
            assert "Save self-grade" in d["reveal_html"]
        else:
            assert d["verdict"] == "correct", (item["id"], d)
        assert client.get(f"/s/{sid}/item/{si['position']}").status_code == 200  # revisit shows reveal

    # double submit rejected
    assert submit(client, sid, items[0]).status_code == 409

    with client.app.state.ctx.conn() as c:
        rows = {r["item_id"]: dict(r) for r in c.execute("SELECT * FROM responses WHERE session_id=?", (sid,))}
        sq = c.execute("SELECT runs FROM responses WHERE item_id='D6-906'").fetchone()
        ev_runs = c.execute("SELECT COUNT(*) FROM events WHERE session_id=? AND type IN ('run_sql','run_code')",
                            (sid,)).fetchone()[0]
    assert ev_runs == 2 and sq is not None
    r901 = rows["D6-901"]
    assert r901["item_version"] == 1 and r901["time_to_first_ms"] == 1200 and r901["active_ms"] == 8500
    assert r901["focus_losses"] == 2 and r901["paste_count"] == 1 and r901["confidence"] == 2
    assert r901["presented_at"] and r901["first_interaction_at"] and r901["feedback_revealed_at"]
    assert r901["mode"] == "realtime" and r901["grader"] == "auto"
    assert json.loads(r901["response_json"])["selected"] == [0]  # stored as ORIGINAL option index
    audio = rows["D6-905"]
    ans = json.loads(audio["response_json"])
    assert ans["audio"].startswith("audio/") and ans["mime"] == "audio/webm"
    assert (client.app.state.ctx.data_dir / ans["audio"]).exists()
    assert client.get(f"/media/audio/{audio['response_id']}").status_code == 200

    # self-grade the open answer
    open_rid = rows["D6-904"]["response_id"]
    sg = client.post(f"/api/responses/{open_rid}/selfgrade", json={"points": [2, 2, 1]})
    assert sg.status_code == 200 and sg.json()["verdict"] == "correct"
    assert client.post(f"/api/responses/{open_rid}/selfgrade", json={"points": [1]}).status_code == 400

    # raw events
    ev = client.post("/api/events", json={"events": [
        {"type": "render", "session_id": sid, "position": 1, "client_ts_ms": 10.5},
        {"type": "input", "session_id": sid, "position": 2, "client_ts_ms": 20, "payload": {"len": 12}},
        {"type": "not-a-type", "session_id": sid, "position": 1},
    ]})
    assert ev.json()["stored"] == 2

    end = client.get(f"/s/{sid}", follow_redirects=False)
    assert end.headers["location"].endswith("/end")
    review = client.post(f"/s/{sid}/finish", follow_redirects=True)
    assert review.status_code == 200 and "Results" in review.text
    assert ITEMS["D6-901"]["explanation"].split()[0] in review.text
    assert client.get(f"/s/{sid}/item/1", follow_redirects=False).status_code == 303

    dash = client.get("/")
    assert dash.status_code == 200 and "Recent tests" in dash.text


def test_end_mode_with_gemini_background_grading_skip_and_resume(make_client, data_dir):
    fake = FakeGemini(judge={"transcript": "spoken words", "criteria": [
        {"criterion": "c", "points": 2, "max_points": 2, "justification": "j"}] * 3, "overall_feedback": "good"})
    save_api_key(data_dir, "SECRET-KEY-XYZ")
    client = make_client(transport=fake.transport())
    sid = new_session(client, "end")
    items = session_items(client, sid)

    # skip the first item
    r = submit(client, sid, items[0], skipped=True)
    assert r.status_code == 200 and "reveal_html" not in r.json() and r.json()["next_url"].endswith("/item/2")
    # resume goes to the first never-answered position (2), not the skipped one
    assert client.get(f"/s/{sid}", follow_redirects=False).headers["location"].endswith("/item/2")
    for si in items[1:]:
        assert client.get(f"/s/{sid}/item/{si['position']}").status_code == 200
        d = submit(client, sid, si).json()
        assert "reveal_html" not in d and "verdict" not in d
    # answered items are locked in end mode
    assert client.get(f"/s/{sid}/item/2", follow_redirects=False).status_code == 303
    # come back to the skipped item and answer it
    assert client.get(f"/s/{sid}/item/1").status_code == 200
    assert submit(client, sid, items[0]).status_code == 200
    end = client.get(f"/s/{sid}/end")
    assert end.status_code == 200

    with client.app.state.ctx.conn() as c:
        rows = c.execute("SELECT * FROM responses WHERE session_id=? ORDER BY created_at", (sid,)).fetchall()
        live = [r for r in rows if not r["superseded"]]
        assert len(rows) == 8 and len(live) == 7
        assert [r["skipped"] for r in rows if r["superseded"]] == [1]
        graded = {r["item_id"]: r for r in live}
        assert graded["D6-904"]["grader"] == "gemini" and graded["D6-904"]["correct"] == 1
        assert graded["D6-905"]["grader"] == "gemini"
        assert json.loads(graded["D6-905"]["grader_detail_json"])["transcript"] == "spoken words"
        assert graded["D6-904"]["grader_model"] == "gemini-flash-latest"
        assert all(r["feedback_revealed_at"] is None for r in live)
    assert any(":generateContent" in str(req.url) for req in fake.requests)

    review = client.post(f"/s/{sid}/finish", follow_redirects=True)
    assert review.status_code == 200
    with client.app.state.ctx.conn() as c:
        assert c.execute("""SELECT COUNT(*) FROM responses WHERE session_id=? AND superseded=0
                            AND feedback_revealed_at IS NULL""", (sid,)).fetchone()[0] == 0
    assert "spoken words" in review.text


def test_gemini_failure_falls_back_to_self_grade(make_client, data_dir):
    save_api_key(data_dir, "k")
    client = make_client(transport=FakeGemini(fail_status=500).transport())
    sid = new_session(client, "realtime", formats=["open"], length=1)
    si = session_items(client, sid)[0]
    client.get(f"/s/{sid}/item/1")
    d = submit(client, sid, si).json()
    assert d["verdict"] == "ungraded" and "Save self-grade" in d["reveal_html"]
    with client.app.state.ctx.conn() as c:
        r = c.execute("SELECT grader, correct FROM responses").fetchone()
    assert r["grader"] == "none" and r["correct"] is None


def test_progress_hides_never_seen_content(make_client):
    client = make_client()
    page = client.get("/progress")
    assert page.status_code == 200
    assert "D6-901" not in page.text  # domains are collapsed: no items until one is opened
    frag = client.get("/progress/items?domain=D6")
    assert frag.status_code == 200 and "D6-901" in frag.text and "Never seen" in frag.text
    for text in (page.text, frag.text, client.get("/progress?domain=D6").text):
        for item in ITEMS.values():
            assert item["stem"].split("\n")[0][:40] not in text
    sid = new_session(client, "realtime", formats=["numeric"], length=1)
    client.get(f"/s/{sid}/item/1")
    submit(client, sid, session_items(client, sid)[0], answer={"value": "1"})
    wrong = client.get("/progress/items?domain=D6&status=wrong").text
    assert "D6-903" in wrong and "voting" in wrong and "D6-901" not in wrong
    opened = client.get("/progress?domain=D6&status=wrong")
    assert "D6-903" in opened.text and 'id="d-D6" open' in opened.text
    never = client.get("/progress/items?domain=D6&status=never_seen").text
    assert "D6-901" in never and "D6-903" not in never
    assert "D6-903" in client.get("/progress/items?domain=D6&status=flagged").text  # flagged on submit (pos 1)
    assert "D6-901" not in client.get("/progress/items?domain=D6&status=flagged").text


def test_feedback_context_and_list(make_client):
    client = make_client()
    sid = new_session(client, "end", formats=["single"], length=1)
    si = session_items(client, sid)[0]
    client.get(f"/s/{sid}/item/1")
    r = client.post("/api/feedback", json={"category": "ambiguous", "text": "Two options look right",
                                           "session_id": sid, "position": 1, "page": "/s/x"})
    assert r.status_code == 200
    submit(client, sid, si)
    client.post("/api/feedback", json={"category": "wrong_key", "text": "after answering", "session_id": sid,
                                       "position": 1})
    client.post(f"/s/{sid}/finish")
    client.post("/api/feedback", json={"category": "typo", "text": "after reveal", "session_id": sid, "position": 1})
    client.post("/api/feedback", json={"category": "ui", "text": "general"})
    client.post("/api/feedback", json={"category": "too_easy", "text": "", "item_id": "D6-903"})
    assert client.post("/api/feedback", json={"category": "nope", "text": "x"}).status_code == 400
    assert client.post("/api/feedback", json={"category": "typo", "text": "x", "item_id": "D9-999"}).status_code == 404
    with client.app.state.ctx.conn() as c:
        fb = store.list_feedback(c)
    by_text = {f["text"]: f for f in fb}
    before, mid, after = by_text["Two options look right"], by_text["after answering"], by_text["after reveal"]
    assert before["item_id"] == si["item_id"] and before["item_version"] == 1
    assert before["context"]["answered"] is False and "user_answer" not in before["context"]
    assert mid["context"]["answered"] is True and mid["context"]["revealed"] is False and "correct" not in mid["context"]
    assert after["context"]["revealed"] is True and after["context"]["correct"] == 1 and after["context"]["user_answer"]
    assert by_text[""]["item_version"] == 1 and by_text["general"]["item_id"] is None
    page = client.get("/feedback")
    assert "Two options look right" in page.text
    fid = before["feedback_id"]
    client.post(f"/feedback/{fid}/resolve", data={"resolved": "1"})
    assert "Two options look right" not in client.get("/feedback").text
    assert "Two options look right" in client.get("/feedback?show=all").text


def test_time_limit_finishes_session(make_client):
    client = make_client()
    sid = new_session(client, "end", formats=["single"], length=2, time_limit_min="1")
    client.get(f"/s/{sid}/item/1")
    with client.app.state.ctx.conn() as c:
        c.execute("UPDATE sessions SET started_at='2020-01-01T00:00:00.000Z' WHERE session_id=?", (sid,))
        c.commit()
    r = client.get(f"/s/{sid}/item/1", follow_redirects=False)
    assert r.headers["location"].endswith("/review")
    with client.app.state.ctx.conn() as c:
        assert store.get_session(c, sid)["status"] == "finished"


def test_new_session_errors_and_preview(make_client):
    client = make_client()
    r = client.post("/new", data={"subtopics": ["obs.slo"], "formats": ["single"], "length": "5"},
                    follow_redirects=False)
    assert r.status_code == 303 and "/new?error=" in r.headers["location"]
    assert client.get(r.headers["location"]).status_code == 200
    p = client.post("/api/preview", json={"subtopics": SCOPE, "formats": ALL, "length": 3}).json()
    assert p["pool_size"] == 7 and p["count"] == 3
    with client.app.state.ctx.conn() as c:
        sid = new_session(client, "realtime", length=3)
        s = store.get_session(c, sid)
    assert s["assembler_version"] == "2" and s["bank_fingerprint"].startswith("sha256:")
    assert s["taxonomy_version"] == "1" and s["seed"] == 7 and s["installation_id"]
    assert json.loads(s["scope_json"])["formats"] == ALL


def test_scope_is_exactly_the_ticked_subtopics(make_client):
    """Secondary tags never widen a test: D6-901 is tagged discovery.selection, D6-906 obs.cost, D6-907 int.resilience."""
    client = make_client()
    only_tags = ["discovery.selection", "obs.cost", "int.resilience"]
    p = client.post("/api/preview", json={"subtopics": only_tags, "formats": ALL, "length": 5}).json()
    assert p["pool_size"] == 0 and p["count"] == 0
    r = client.post("/new", data={"subtopics": only_tags, "formats": ALL, "length": "5"}, follow_redirects=False)
    assert r.status_code == 303 and "/new?error=" in r.headers["location"]

    scope = ["agents.state", "discovery.selection"]  # D6-902 and D6-907 are agents.state; D6-901 is only tagged
    p = client.post("/api/preview", json={"subtopics": scope, "formats": ALL, "length": 7}).json()
    assert p["pool_size"] == 2 and p["count"] == 2
    sid = new_session(client, "realtime", length=7, subtopics=scope)
    assert {si["item_id"] for si in session_items(client, sid)} == {"D6-902", "D6-907"}

    # the counts beside each checkbox on the New test form use the same rule
    page = client.get("/new").text
    counts = {m.group(1): json.loads(html.unescape(m.group(2)))
              for m in re.finditer(r'name="subtopics" value="([^"]+)"[^>]*data-counts="([^"]*)"', page)}
    assert counts["agents.patterns"] == {"numeric": 1, "sql": 1}
    assert counts["obs.cost"] == {} and counts["discovery.selection"] == {}


def test_pages_render_and_404s(make_client):
    client = make_client()
    for path in ["/", "/new", "/new?preset=gorilla-logic-fde", "/progress", "/feedback", "/export", "/settings"]:
        assert client.get(path).status_code == 200, path
    assert client.get("/s/not-a-session").status_code == 404
    assert client.get("/s/00000000-0000-4000-8000-000000000000").status_code == 404
    assert client.post("/api/s/00000000-0000-4000-8000-000000000000/item/1/submit", json={}).status_code == 404
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/media/audio/../../etc/passwd").status_code == 404


def test_settings_key_and_model(make_client, data_dir):
    client = make_client()
    r = client.post("/settings/key", data={"api_key": "abc123", "action": "save"}, follow_redirects=True)
    assert "Key saved locally" in r.text and "abc123" not in r.text
    r = client.post("/settings/model", data={"model": "models/gemini-2.5-flash"}, follow_redirects=True)
    assert 'value="gemini-2.5-flash"' in r.text
    client.post("/settings/key", data={"action": "clear"})
    assert not (data_dir / "secrets.json").exists()
    assert client.post("/api/gemini/test").json()["ok"] is False
    r = client.post("/settings/reload-bank", follow_redirects=True)
    assert "Bank reloaded: 7 items" in r.text


def test_create_app_uses_env(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    monkeypatch.setenv("FDE_DATA_DIR", str(tmp_path / "envdata"))
    app = create_app()
    assert app.state.ctx.sync_report.loaded == 7
    assert (tmp_path / "envdata" / "fde.sqlite3").exists()
    assert TestClient(app).get("/").status_code == 200
