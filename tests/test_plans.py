"""Modeled tests loaded from a file: validation, timed parts run back to back, exam conditions, pace stats."""
import json

from app import plans, store
from app.db import now_iso
from tests.test_http import ITEMS, TEL, right_answer, session_items

SPEC = """
name: Practice run
mode: end
parts:
  - title: Warm-up
    time_limit_min: 3
    items: [D6-902, D6-901]
  - title: Estimation
    time_limit_s: 120
    items: [D6-903@1]
"""


def load(client, text, name="run.yaml"):
    return client.post("/plans/load", files={"file": (name, text.encode(), "application/x-yaml")},
                       follow_redirects=False)


def load_ok(client, text=SPEC):
    r = load(client, text)
    assert r.status_code == 303, r.text
    return r.headers["location"].rsplit("/", 1)[-1]


def start_part(client, pid):
    r = client.post(f"/m/{pid}/start", follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].rsplit("/", 1)[-1]


def answer(client, sid, si, read_ms=None):
    client.get(f"/s/{sid}/item/{si['position']}")
    body = {"answer": right_answer(ITEMS[si["item_id"]], si["presented_order"]),
            "telemetry": {**TEL, "read_ms": read_ms}}
    r = client.post(f"/api/s/{sid}/item/{si['position']}/submit", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_load_pins_versions_and_starts_parts_lazily(make_client):
    client = make_client()
    pid = load_ok(client)
    page = client.get(f"/m/{pid}").text
    assert "Practice run" in page and "Next: Warm-up" in page and "3m" in page and "Start Warm-up" in page
    with client.app.state.ctx.conn() as c:
        plan = plans.get_plan(c, pid)
        assert [len(p["items"]) for p in plan["spec"]["parts"]] == [2, 1]
        assert plan["spec"]["parts"][0]["items"][0] == {"item_id": "D6-902", "version": 1}
        assert plan["spec"]["parts"][1]["time_limit_s"] == 120
        assert c.execute("SELECT COUNT(*) FROM sessions WHERE plan_id = ?", (pid,)).fetchone()[0] == 0

    sid = start_part(client, pid)
    assert start_part(client, pid) == sid  # starting again resumes the part in progress
    items = session_items(client, sid)
    assert [si["item_id"] for si in items] == ["D6-902", "D6-901"]  # the file's order, not the assembler's
    assert {si["section"] for si in items} == {"Warm-up"}
    with client.app.state.ctx.conn() as c:
        s = store.get_session(c, sid)
    assert s["plan_id"] == pid and s["plan_part"] == 1 and s["mode"] == "end"
    assert s["scope"]["time_limit_s"] == 180 and s["scope"]["plan"]["title"] == "Warm-up"
    runner = client.get(f"/s/{sid}/item/1").text
    assert "Warm-up" in runner and 'id="countdown"' in runner and "Finish this part" in runner
    assert 'id="read-btn"' in runner
    dash = client.get("/").text
    assert "Resume test" in dash and "Practice run · Warm-up" in dash


def test_exam_conditions_hold_until_the_last_part(make_client):
    client = make_client()
    pid = load_ok(client)
    sid1 = start_part(client, pid)
    for si in session_items(client, sid1):
        out = answer(client, sid1, si, read_ms=4000)
        assert "reveal_html" not in out  # exam mode: nothing revealed between parts
    r = client.post(f"/s/{sid1}/finish", follow_redirects=False)
    assert r.headers["location"] == f"/m/{pid}"
    # the part's results stay hidden while the test is running
    assert client.get(f"/s/{sid1}/review", follow_redirects=False).headers["location"] == f"/m/{pid}"
    page = client.get(f"/m/{pid}").text
    assert "Next: Estimation" in page and "Review" not in page
    dash = client.get("/").text
    assert "Modeled test in progress" in dash and f'href="/m/{pid}"' in dash

    sid2 = start_part(client, pid)
    assert sid2 != sid1
    answer(client, sid2, session_items(client, sid2)[0])
    client.post(f"/s/{sid2}/finish")
    page = client.get(f"/m/{pid}").text
    assert "Next:" not in page and f'href="/s/{sid1}/review"' in page and "Pace" in page
    assert "Answers within the part's pace (1m 30s per item)" in page
    with client.app.state.ctx.conn() as c:
        assert plans.get_plan(c, pid)["status"] == "finished"
    review = client.get(f"/s/{sid1}/review").text
    assert review.count("first reading 4s") == 2 and "words/min" in review
    assert "Median reading speed" in review and "Practice run</a> · Warm-up" in review
    assert client.post(f"/m/{pid}/start", follow_redirects=False).headers["location"] == f"/m/{pid}"


def test_read_ms_is_stored_and_optional(make_client):
    client = make_client()
    pid = load_ok(client)
    sid = start_part(client, pid)
    items = session_items(client, sid)
    answer(client, sid, items[0], read_ms=2500)
    answer(client, sid, items[1])
    with client.app.state.ctx.conn() as c:
        rows = {r["position"]: r["read_ms"] for r in c.execute("SELECT position, read_ms FROM responses")}
        timing = store.timing_rows(c, items, store.latest_responses(c, sid))
    assert rows == {1: 2500, 2: None}
    t = timing[1]
    assert t["read_s"] == 2.5 and t["decide_s"] == 6.5 and t["wpm"] == round(t["words"] / (2.5 / 60))
    assert timing[2]["read_s"] is None and timing[2]["wpm"] is None
    pace = store.pace_summary(list(timing.values()), slot_s=8)
    assert pace["n"] == 2 and pace["n_read"] == 1 and pace["within_slot"] == 0  # both took 9 s
    ev = client.post("/api/events", json={"events": [{"type": "read_done", "session_id": sid, "position": 1,
                                                       "payload": {"ms": 2500}}]})
    assert ev.json()["stored"] == 1


def test_time_up_on_a_part_returns_to_the_test_page(make_client):
    client = make_client()
    pid = load_ok(client)
    sid = start_part(client, pid)
    client.get(f"/s/{sid}/item/1")
    with client.app.state.ctx.conn() as c:
        c.execute("UPDATE sessions SET started_at = '2020-01-01T00:00:00.000Z' WHERE session_id = ?", (sid,))
        c.commit()
    r = client.get(f"/s/{sid}/item/1", follow_redirects=False)
    assert r.headers["location"] == f"/m/{pid}"
    assert "Next: Estimation" in client.get(f"/m/{pid}").text
    with client.app.state.ctx.conn() as c:
        assert store.get_session(c, sid)["status"] == "finished"
        state = plans.plan_state(c, plans.get_plan(c, pid))
    assert state["parts"][0]["used_s"] == 180  # capped at the limit


def test_invalid_files_list_every_problem(make_client):
    client = make_client()
    bad = """
name: Broken
mode: sometimes
parts:
  - title: A
    time_limit_min: 0.1
    items: [D6-901, D6-999, nonsense, D6-901@7]
  - items: [D6-901]
"""
    r = load(client, bad, "broken.yaml")
    assert r.status_code == 400
    text = r.text.replace("&#39;", "'")
    for msg in ("broken.yaml could not be loaded", "'mode' must be one of", "between 30 seconds and 6 hours",
                "D6-999 is not in the bank", "'nonsense' is not an item id", "D6-901@7 is not a stored version",
                "Part 2: D6-901 is already used in Part 1"):
        assert msg in text, msg
    assert load(client, "just a string").status_code == 400
    assert load(client, "name: x\nparts: []").status_code == 400
    assert "Not valid YAML or JSON" in load(client, "name: [unclosed").text
    with client.app.state.ctx.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0


def test_json_file_realtime_mode_and_abandon(make_client):
    client = make_client()
    spec = {"name": "JSON run", "mode": "realtime",
            "parts": [{"items": ["D6-901"]}, {"title": "Second", "items": ["D6-902"]}]}
    pid = load_ok(client, json.dumps(spec))
    sid = start_part(client, pid)
    with client.app.state.ctx.conn() as c:
        s = store.get_session(c, sid)
    assert s["scope"]["time_limit_s"] is None and session_items(client, sid)[0]["section"] == "Part 1"
    out = answer(client, sid, session_items(client, sid)[0])
    assert "reveal_html" in out  # real-time mode reveals as usual
    assert "JSON run" in client.get("/plans").text

    r = client.post(f"/s/{sid}/abandon", follow_redirects=False)
    assert r.headers["location"] == "/"
    with client.app.state.ctx.conn() as c:
        state = plans.plan_state(c, plans.get_plan(c, pid))
    assert state["status"] == "abandoned" and state["current"] is None
    assert [p["status"] for p in state["parts"]] == ["abandoned", "not_started"]
    assert client.get(f"/s/{sid}/review").status_code == 200  # results open once the test is over
    assert "Modeled test in progress" not in client.get("/").text


def test_abandon_from_the_test_page(make_client):
    client = make_client()
    pid = load_ok(client)
    sid = start_part(client, pid)
    client.post(f"/m/{pid}/abandon")
    with client.app.state.ctx.conn() as c:
        assert store.get_session(c, sid)["status"] == "abandoned"
        assert plans.get_plan(c, pid)["finished_at"] <= now_iso()
    assert client.get("/m/not-a-uuid").status_code == 404
