"""The Copy button: item text for pasting into an AI chat. No key before reveal; options in presented order."""

import pytest

from app import markdown, views
from tests.test_http import ITEMS, new_session, session_items, submit


def copy(client, sid, pos):
    r = client.get(f"/api/s/{sid}/item/{pos}/copy")
    assert r.status_code == 200, r.text
    return r.json()


def choice_session(client, mode, formats=("single",)):
    sid = new_session(client, mode, length=1, formats=list(formats))
    si = session_items(client, sid)[0]
    return sid, si, ITEMS[si["item_id"]]


def letters_in_order(item, perm):
    return [(chr(65 + p), item["options"][orig]["text"]) for p, orig in enumerate(perm)]


def test_copy_before_reveal_has_presented_order_and_no_key(make_client):
    client = make_client()
    sid, si, item = choice_session(client, "realtime")
    client.get(f"/s/{sid}/item/1")
    d = copy(client, sid, 1)
    assert d["revealed"] is False
    text = d["text"]
    assert text.startswith(f"{item['id']} v{item['version']} · {item['subtopic']} · single choice\n")
    # stem soft wraps are joined with spaces
    assert " ".join(item["stem"].split()) in text and "\n      " not in text
    # options lettered in the order the session presented them
    expected = [f"{k}. {t}" for k, t in letters_in_order(item, si["presented_order"])]
    positions = [text.index(line) for line in expected]
    assert positions == sorted(positions)
    for needle in ("Correct answer", "My answer", "Explanation", "Why", "Result:"):
        assert needle not in text
    for o in item["options"]:
        assert o["why"] not in text
    assert item["explanation"].split(".")[0] not in text


def test_presented_order_is_respected_for_a_permuted_session(make_client):
    client = make_client()
    for seed in range(1, 12):
        sid = new_session(client, "realtime", length=1, formats=["single"], seed=str(seed))
        si = session_items(client, sid)[0]
        if si["presented_order"] != sorted(si["presented_order"]):
            break
    else:
        pytest.skip("no shuffled session found")
    item = ITEMS[si["item_id"]]
    text = copy(client, sid, 1)["text"]
    assert f"A. {item['options'][si['presented_order'][0]]['text']}" in text


def test_copy_after_reveal_has_answer_key_and_explanation(make_client):
    client = make_client()
    sid, si, item = choice_session(client, "realtime")
    client.get(f"/s/{sid}/item/1")
    wrong = next(p for p, orig in enumerate(si["presented_order"]) if not item["options"][orig].get("correct"))
    assert submit(client, sid, si, {"selected_presented": [wrong]}).status_code == 200
    d = copy(client, sid, 1)
    assert d["revealed"] is True
    text = d["text"]
    order = letters_in_order(item, si["presented_order"])
    right = next(p for p, orig in enumerate(si["presented_order"]) if item["options"][orig].get("correct"))
    assert f"My answer: {order[wrong][0]}. {order[wrong][1]}" in text
    assert f"Correct answer: {order[right][0]}. {order[right][1]}" in text
    assert "Result: Incorrect" in text
    for o in item["options"]:
        assert " ".join(o["why"].split()) in text
    assert " ".join(item["explanation"].split()) in text
    assert "(correct)" in text and "(incorrect, my pick)" in text


def test_exam_mode_hides_key_until_finished(make_client):
    client = make_client()
    sid, si, item = choice_session(client, "end")
    client.get(f"/s/{sid}/item/1")
    assert submit(client, sid, si).status_code == 200
    d = copy(client, sid, 1)
    assert d["revealed"] is False and "Correct answer" not in d["text"] and "My answer" not in d["text"]
    client.post(f"/s/{sid}/finish")
    d = copy(client, sid, 1)
    assert d["revealed"] is True and "My answer:" in d["text"] and "Correct answer:" in d["text"]
    review = client.get(f"/s/{sid}/review")
    assert "data-copy" in review.text


def test_skipped_item_in_realtime_is_not_revealed(make_client):
    client = make_client()
    sid, si, _ = choice_session(client, "realtime")
    client.get(f"/s/{sid}/item/1")
    assert submit(client, sid, si, skipped=True).status_code == 200
    d = copy(client, sid, 1)
    assert d["revealed"] is False and "Correct answer" not in d["text"]


def test_multi_select_says_select_all_that_apply(make_client):
    client = make_client()
    sid, si, item = choice_session(client, "realtime", formats=("multi",))
    text = copy(client, sid, 1)["text"]
    assert "multiple select" in text.splitlines()[0] and "Select all that apply" in text
    assert submit(client, sid, si).status_code == 200
    text = copy(client, sid, 1)["text"]
    assert "Result: Correct" in text and text.count("; ") >= 1  # two letters in "My answer"


def test_all_types_copy_before_and_after_reveal(make_client):
    client = make_client()
    sid = new_session(client, "realtime", length=7)
    for si in session_items(client, sid):
        item, pos = ITEMS[si["item_id"]], si["position"]
        client.get(f"/s/{sid}/item/{pos}")
        before = copy(client, sid, pos)["text"]
        assert before.startswith(f"{item['id']} v{item['version']} ·")
        for secret in (item.get("model_answer"), (item.get("sql") or {}).get("reference"),
                       (item.get("code") or {}).get("reference"), item.get("explanation"), item.get("work")):
            if secret:
                assert " ".join(secret.split()) not in " ".join(before.split())
        if item["type"] == "sql":
            assert "CREATE TABLE calls" in before and "| run_id |" in before
        if item["type"] == "code":
            assert "def backoff_delays" in before and "backoff_delays(3, 0.5, 4.0) -> [0.5, 1.0, 2.0]" in before
        if item["type"] == "numeric":
            assert "unit: ¢" in before
        assert submit(client, sid, si).status_code == 200
        after = copy(client, sid, pos)
        assert after["revealed"] is True and after["text"].startswith(before.rstrip("\n"))
        assert "My answer:" in after["text"]
    assert client.get(f"/api/s/{sid}/item/99/copy").status_code == 404
    assert client.get("/api/s/not-a-session/item/1/copy").status_code == 404


def test_copy_text_has_no_session_or_installation_ids(make_client):
    client = make_client()
    sid, si, _ = choice_session(client, "realtime")
    client.get(f"/s/{sid}/item/1")
    submit(client, sid, si)
    text = copy(client, sid, 1)["text"]
    ctx = client.app.state.ctx
    assert sid not in text and ctx.installation_id not in text and "@" not in text


def test_buttons_on_runner_and_review_pages(make_client):
    client = make_client()
    sid, si, _ = choice_session(client, "realtime")
    page = client.get(f"/s/{sid}/item/1").text
    assert "data-copy" in page and f'data-session-id="{sid}"' in page
    assert "Correct answer" not in page  # the text is fetched on click, never embedded in the page


def test_markdown_plain_joins_soft_wraps_and_keeps_structure():
    src = "A client wants\nto automate  \nthis `x\ny` ok\\\nnext\n\n- a\n  more\n- b\n\n```py\nx = 1\n  y\n```\n\n> q1\n> q2"
    out = markdown.plain(src)
    assert out == "A client wants to automate\nthis `x y` ok\nnext\n\n- a more\n- b\n\n```py\nx = 1\n  y\n```\n\n> q1 q2"
    assert markdown.plain(None) == "" and views.copy_text  # exported
