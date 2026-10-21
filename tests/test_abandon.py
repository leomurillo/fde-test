"""Ending a test from the runner and dashboard, abandoned tests in Progress/exports, and the stale-version notice."""
import yaml

from app import store
from app.export import read_export
from tests.test_http import new_session, session_items, submit

CONFIRM = "Answers already given are kept and still count in Progress."


def _answer_first(client, sid, n):
    items = session_items(client, sid)
    for si in items[:n]:
        client.get(f"/s/{sid}/item/{si['position']}")
        assert submit(client, sid, si).status_code == 200
    return items


def _revise(bank_dir, item_ids):
    """Bump the version of the given items in the fixture bank copy (what an author does after a review)."""
    path = bank_dir / "D06-sample.yaml"
    data = yaml.safe_load(path.read_text())
    for it in data["items"]:
        if it["id"] in item_ids:
            it["version"] += 1
            it["stem"] += " (Revised.)"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def test_runner_has_end_test_menu_and_dashboard_has_abandon(make_client):
    client = make_client()
    sid = new_session(client, "realtime")
    runner = client.get(f"/s/{sid}/item/1").text
    assert "End test" in runner and "Finish and see results" in runner and "Abandon test" in runner
    assert f'action="/s/{sid}/finish"' in runner and f'action="/s/{sid}/abandon"' in runner
    assert CONFIRM in runner
    assert "not answered will score zero" in runner  # finishing early confirms the unanswered items

    dash = client.get("/").text
    assert "Resume test" in dash and f'href="/s/{sid}"' in dash
    assert f'action="/s/{sid}/abandon"' in dash and CONFIRM in dash


def test_abandoned_session_keeps_answers_in_progress_and_exports(make_client, data_dir):
    client = make_client()
    sid = new_session(client, "realtime")
    items = _answer_first(client, sid, 2)
    answered = {si["item_id"] for si in items[:2]}

    r = client.post(f"/s/{sid}/abandon", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"

    with client.app.state.ctx.conn() as c:
        assert store.get_session(c, sid)["status"] == "abandoned"
        status = store.item_status_map(c)
        prog = store.progress(c)
        live = c.execute("SELECT COUNT(*) FROM responses WHERE session_id = ? AND superseded = 0", (sid,)).fetchone()[0]
    assert live == 2
    for item_id in answered:
        assert status[item_id]["status"] == "right" and status[item_id]["attempts"] == 1
    assert {i for i, st in status.items() if st["seen"]} == answered  # unopened items stay never seen
    assert prog["totals"]["right"] == 2 and prog["totals"]["seen"] == 2 and prog["totals"]["responses"] == 2

    dash = client.get("/").text
    assert "Abandoned" in dash and "Resume test" not in dash  # still listed under recent tests
    assert client.get(f"/s/{sid}/review").status_code == 200
    assert client.get(f"/s/{sid}/item/3", follow_redirects=False).headers["location"].endswith("/review")

    data = read_export(client.get("/export/data.zip").content)
    assert [s["status"] for s in data["sessions.jsonl"]] == ["abandoned"]
    assert {x["item_id"] for x in data["responses.jsonl"]} == answered


def test_abandon_after_finish_is_a_no_op(make_client):
    client = make_client()
    sid = new_session(client, "realtime")
    _answer_first(client, sid, 1)
    client.post(f"/s/{sid}/finish")
    client.post(f"/s/{sid}/abandon")
    with client.app.state.ctx.conn() as c:
        assert store.get_session(c, sid)["status"] == "finished"


def test_stale_notice_counts_only_unopened_unanswered_positions(make_client, bank_copy):
    client = make_client(bank_dir=bank_copy)
    sid = new_session(client, "realtime")
    items = session_items(client, sid)
    ids = [si["item_id"] for si in items]
    assert "newer version" not in client.get(f"/s/{sid}/item/1").text and "newer version" not in client.get("/").text

    # position 1 was opened above; revise it and positions 3 and 5, then restart the app so it syncs the bank
    _revise(bank_copy, {ids[0], ids[2], ids[4]})
    client = make_client(bank_dir=bank_copy)
    with client.app.state.ctx.conn() as c:
        assert store.stale_unopened_count(c, sid) == 2  # position 1 was opened, so it is not "unopened"

    page = client.get(f"/s/{sid}/item/2").text
    assert "2 unopened items have newer versions. Abandon and start a new test to get them." in page
    assert f'action="/s/{sid}/abandon"' in page
    dash = client.get("/").text
    assert "2 unopened items have newer versions" in dash

    # opening a stale position removes it from the count: it is being shown now, in the version it was pinned to
    assert "1 unopened item has a newer version." in client.get(f"/s/{sid}/item/3").text

    # nothing is swapped: the test keeps serving the pinned versions
    assert [si["item_version"] for si in session_items(client, sid)] == [1] * len(items)
    with client.app.state.ctx.conn() as c:
        assert "(Revised.)" not in store.snapshot(c, ids[4], 1)["stem"]
        assert c.execute("SELECT current_version FROM items WHERE item_id = ?", (ids[4],)).fetchone()[0] == 2
    assert "(Revised.)" not in client.get(f"/s/{sid}/item/5").text

    # with every revised position opened, no notice is left; the answer stays tied to the version it saw
    assert submit(client, sid, items[4]).status_code == 200
    with client.app.state.ctx.conn() as c:
        assert store.stale_unopened_count(c, sid) == 0
    assert "newer version" not in client.get("/").text

    client.post(f"/s/{sid}/abandon")
    with client.app.state.ctx.conn() as c:
        status = store.item_status_map(c)
    assert status[ids[4]]["history"][0]["version"] == 1
