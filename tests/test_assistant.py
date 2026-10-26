"""AI assistant sidebar: transcript capture, the AI-use assessment, results, plans and exports."""
import json

from app import assistant, store
from app.export import read_export
from app.gemini import save_api_key
from tests.conftest import FakeGemini
from tests.test_http import ITEMS, new_session, session_items, submit
from tests.test_plans import load_ok, start_part

JUDGE = {"criteria": [
    {"criterion": "Prompting and context", "points": 2, "max_points": 3, "justification": "Gave the scenario."},
    {"criterion": "Critical evaluation of AI output", "points": 3, "max_points": 3, "justification": "Pushed back."},
    {"criterion": "Ownership of the final answer", "points": 2.5, "max_points": 3, "justification": "Own words."},
    {"criterion": "Efficient, well-placed use", "points": 9, "max_points": 3, "justification": "Quick."},
], "overall_feedback": "State constraints up front."}


def chat(client, sid, pos, text, pasted=0):
    return client.post(f"/api/s/{sid}/ai/chat", json={"position": pos, "text": text, "pasted_chars": pasted})


def test_quotes_item_detects_copied_question_text():
    item = {"stem": "A client wants to automate invoice intake: PDFs arrive by email, fields are extracted.",
            "options": [{"text": "A fixed workflow that calls an LLM only for field extraction"}]}
    assert assistant.quotes_item("Help: a client wants to automate invoice intake PDFs arrive by email", item)
    assert assistant.quotes_item("is 'a fixed workflow that calls an LLM only for field' right?", item)
    assert not assistant.quotes_item("When should I prefer a workflow over an agent for document intake?", item)


def test_sidebar_only_when_enabled_and_needs_a_key(make_client):
    client = make_client()
    plain = new_session(client, "realtime")
    page = client.get(f"/s/{plain}/item/1").text
    assert 'id="ai-panel"' not in page and "assistant.js" not in page
    assert chat(client, plain, 1, "hi").status_code == 403

    sid = new_session(client, "end", ai_assistant="1")
    page = client.get(f"/s/{sid}/item/1").text
    assert 'id="ai-toggle"' in page and 'id="ai-panel"' in page and "assistant.js" in page
    assert "Add a Gemini API key" in page and 'id="ai-form"' not in page
    r = chat(client, sid, 1, "What is idempotency?")
    assert r.status_code == 200 and r.json()["role"] == "error" and "No Gemini API key" in r.json()["text"]
    assert chat(client, sid, 1, "   ").status_code == 400
    assert chat(client, sid, 1, "x" * 9000).status_code == 413
    assert chat(client, sid, 99, "hi").status_code == 404


def test_transcript_is_recorded_with_context_and_history(make_client, data_dir):
    fake = FakeGemini(judge=JUDGE)
    save_api_key(data_dir, "k")
    client = make_client(transport=fake.transport())
    sid = new_session(client, "end", ai_assistant="1")
    items = session_items(client, sid)
    stem = ITEMS[items[0]["item_id"]]["stem"]
    copied = " ".join(stem.split()[:20])
    assert chat(client, sid, 1, copied, pasted=len(copied)).json()["role"] == "model"
    r = chat(client, sid, 2, "Why would that be?")
    assert r.json()["ok"] and r.json()["html"].startswith("<p>")
    with client.app.state.ctx.conn() as c:
        msgs = assistant.messages(c, sid)
    assert [m["role"] for m in msgs] == ["user", "model", "user", "model"]
    assert msgs[0]["position"] == 1 and msgs[0]["item_id"] == items[0]["item_id"]
    assert msgs[0]["meta"] == {"pasted_chars": len(copied), "quotes_item": True}
    assert msgs[2]["meta"] == {"pasted_chars": 0, "quotes_item": False} and msgs[2]["position"] == 2
    second = json.loads(fake.requests[-1].content)
    assert [t["role"] for t in second["contents"]] == ["user", "model", "user"]  # whole session history
    assert "general-purpose" in second["systemInstruction"]["parts"][0]["text"]
    assert "test" not in second["systemInstruction"]["parts"][0]["text"]  # it behaves like a plain assistant
    page = client.get(f"/s/{sid}/item/2").text
    assert "Why would that be?" in page and "Item 1" in page  # earlier messages stay in the sidebar


def test_finishing_grades_ai_use_and_results_show_it(make_client, data_dir):
    fake = FakeGemini(judge=JUDGE)
    save_api_key(data_dir, "k")
    client = make_client(transport=fake.transport())
    sid = new_session(client, "end", ai_assistant="1")
    items = session_items(client, sid)
    chat(client, sid, 1, "Which option is safest for auditors?")
    client.get(f"/s/{sid}/item/1")
    submit(client, sid, items[0])
    client.post(f"/s/{sid}/finish")  # TestClient runs background tasks before returning

    with client.app.state.ctx.conn() as c:
        a = assistant.latest_assessment(c, sid)
    assert a["grader"] == "gemini" and a["max_points"] == 12 and a["points"] == 10.5  # 9 clamped to 3
    assert [x["criterion"] for x in a["detail"]["criteria"]] == [r["criterion"] for r in assistant.AI_RUBRIC]
    assert a["detail"]["usage"]["prompts"] == 1 and a["rubric_version"] == assistant.AI_RUBRIC_VERSION
    judge_req = json.loads(fake.requests[-1].content)
    prompt = judge_req["contents"][0]["parts"][0]["text"]
    assert "Which option is safest for auditors?" in prompt and "Answer key (for the assessor only)" in prompt
    assert "Candidate's final answer:" in prompt and "hiring manager" in judge_req["systemInstruction"]["parts"][0]["text"]

    review = client.get(f"/s/{sid}/review").text
    assert 'id="ai-use"' in review and "10.5 / 12" in review and "State constraints up front." in review
    assert "Full transcript (2 messages)" in review and "AI assistant on this item (2 messages)" in review
    assert client.post(f"/s/{sid}/ai-grade", follow_redirects=False).status_code == 303
    with client.app.state.ctx.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM ai_assessments WHERE session_id = ?", (sid,)).fetchone()[0] == 2

    data = read_export(client.get("/export/data.zip").content)
    assert len(data["ai_messages.jsonl"]) == 2 and data["ai_assessments.jsonl"][0]["detail"]["usage"]["prompts"] == 1


def test_unused_or_ungraded_assistant_is_explained(make_client, data_dir):
    client = make_client()
    unused = new_session(client, "end", ai_assistant="1")
    client.post(f"/s/{unused}/finish")
    assert "available but not used" in client.get(f"/s/{unused}/review").text

    used = new_session(client, "end", ai_assistant="1")
    chat(client, used, 1, "hello")
    client.post(f"/s/{used}/finish")
    with client.app.state.ctx.conn() as c:
        a = assistant.latest_assessment(c, used)
    assert a["grader"] == "none" and "No Gemini API key" in a["detail"]["reason"]
    plain = new_session(client, "end")
    client.post(f"/s/{plain}/finish")
    assert 'id="ai-use"' not in client.get(f"/s/{plain}/review").text
    assert client.post(f"/s/{plain}/ai-grade").status_code == 409


def test_modeled_test_parts_enable_the_assistant(make_client, data_dir):
    save_api_key(data_dir, "k")
    client = make_client(transport=FakeGemini(judge=JUDGE).transport())
    spec = """
name: Assisted run
ai_assistant: true
parts:
  - title: With AI
    items: [D6-901]
  - title: Without AI
    ai_assistant: false
    items: [D6-902]
"""
    pid = load_ok(client, spec)
    assert "AI assistant available" in client.get(f"/m/{pid}").text
    sid = start_part(client, pid)
    with client.app.state.ctx.conn() as c:
        assert store.get_session(c, sid)["scope"]["ai_assistant"] is True
    chat(client, sid, 1, "help me think")
    client.post(f"/s/{sid}/finish")
    sid2 = start_part(client, pid)
    assert 'id="ai-panel"' not in client.get(f"/s/{sid2}/item/1").text
    client.post(f"/s/{sid2}/finish")
    page = client.get(f"/m/{pid}").text
    assert "10.5 / 12" in page
    bad = client.post("/plans/load", files={"file": ("b.yaml", b"name: x\nparts:\n  - items: [D6-901]\n    ai_assistant: maybe\n")})
    assert bad.status_code == 400 and "must be true or false" in bad.text.replace("&#39;", "'")
