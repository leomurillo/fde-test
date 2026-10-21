import yaml

from app.gemini import GeminiClient
from app.grading import grade_deterministic, grade_rubric, self_grade, skipped_outcome
from app.views import map_selected, public_item, reveal
from tests.conftest import FIXTURES, FakeGemini

ITEMS = {d["id"]: d for d in yaml.safe_load((FIXTURES / "D06-sample.yaml").read_text())["items"]}


def test_single_and_multi():
    single = ITEMS["D6-901"]
    assert grade_deterministic(single, {"selected": [0]}).correct is True
    o = grade_deterministic(single, {"selected": [2]})
    assert o.correct is False and o.score == 0 and o.grader == "auto" and o.max_points == 1
    multi = ITEMS["D6-902"]
    assert grade_deterministic(multi, {"selected": [0, 1]}).correct is True
    partial = grade_deterministic(multi, {"selected": [0, 2]})
    assert partial.correct is False and partial.score == 0.0
    half = grade_deterministic(multi, {"selected": [0]})
    assert half.correct is False and half.score == 0.5


def test_presented_order_mapping_hides_original_indexes():
    item = ITEMS["D6-901"]
    perm = [2, 0, 3, 1]          # presented position i shows original option perm[i]
    pub = public_item(item, perm)
    assert [o["text"] for o in pub["options"]] == [item["options"][i]["text"] for i in perm]
    assert all(set(o) == {"index", "text"} for o in pub["options"])
    assert map_selected(item, perm, [1]) == [0]       # presented #1 is original #0 (the key)
    assert map_selected(item, perm, [9, "x", 1]) == [0]


def test_public_item_never_contains_answers():
    for item in ITEMS.values():
        pub = repr(public_item(item, None))
        for secret in ("why", "misconception", "model_answer", "rubric", "reference", "'tests'", "correct': True"):
            assert secret not in pub, (item["id"], secret)
    code_pub = public_item(ITEMS["D6-907"], None)
    assert code_pub["examples"] and "tests" not in code_pub


def test_numeric():
    item = ITEMS["D6-903"]
    assert grade_deterministic(item, {"value": "5.25"}).correct
    assert grade_deterministic(item, {"value": "5.29"}).correct
    assert not grade_deterministic(item, {"value": "5.4"}).correct
    assert not grade_deterministic(item, {"value": "abc"}).correct


def test_sql():
    item = ITEMS["D6-906"]
    ok = grade_deterministic(item, {"sql": item["sql"]["reference"]})
    assert ok.correct and ok.grader == "sql"
    bad = grade_deterministic(item, {"sql": "SELECT run_id FROM calls"})
    assert not bad.correct and "error" in bad.detail
    blocked = grade_deterministic(item, {"sql": "DROP TABLE calls"})
    assert not blocked.correct


def test_code():
    item = ITEMS["D6-907"]
    ok = grade_deterministic(item, {"code": item["code"]["reference"]})
    assert ok.correct and ok.grader == "code" and ok.detail["passed"] == ok.detail["total"] == 4
    bad = grade_deterministic(item, {"code": "def backoff_delays(a, b, c):\n    return []\n"})
    assert not bad.correct and 0 < bad.score < 1


def test_open_without_key_is_ungraded_then_self_graded():
    item = ITEMS["D6-904"]
    o = grade_rubric(item, {"text": "A workflow."}, None)
    assert o.correct is None and o.grader == "none" and o.max_points == 7
    sg = self_grade(item, [2, 3, 1], o.detail)
    assert sg.grader == "self" and sg.points == 6 and sg.correct is True and round(sg.score, 3) == round(6 / 7, 3)
    clamped = self_grade(item, [5, -1, 2], o.detail)
    assert clamped.points == 4


def test_empty_open_answer_scores_zero_without_calling_gemini():
    fake = FakeGemini()
    client = GeminiClient("k", transport=fake.transport(), sleep=lambda s: None)
    o = grade_rubric(ITEMS["D6-904"], {"text": "   "}, client)
    assert o.correct is False and o.score == 0 and fake.requests == []


def test_open_with_gemini():
    fake = FakeGemini(judge={"criteria": [
        {"criterion": "Bottom line first", "points": 2, "max_points": 2, "justification": "leads"},
        {"criterion": "Matches autonomy to variability", "points": 9, "max_points": 3, "justification": "x"},
        {"criterion": "Controls for side effects", "points": 0, "max_points": 2, "justification": "none"},
    ], "overall_feedback": "ok"})
    client = GeminiClient("k", "gemini-2.5-flash", transport=fake.transport(), sleep=lambda s: None)
    o = grade_rubric(ITEMS["D6-904"], {"text": "Workflow with two LLM steps."}, client)
    assert o.grader == "gemini" and o.grader_model == "gemini-2.5-flash"
    assert o.points == 5 and o.max_points == 7 and o.correct is True  # 9 clamped to 3


def test_skipped_and_reveal():
    item = ITEMS["D6-901"]
    sk = skipped_outcome(item)
    assert sk.correct is False and sk.score == 0
    resp = {"response": {"selected": [1]}, "detail": {}, "skipped": 0, "correct": 0, "score": 0.0,
            "points": 0, "max_points": 1, "grader": "auto", "grader_model": None, "response_id": "r",
            "time_to_submit_ms": 1000}
    rv = reveal(item, [1, 0, 2, 3], resp)
    assert rv["verdict"] == "incorrect"
    assert rv["options"][0]["chosen"] and not rv["options"][0]["correct"]
    assert rv["options"][1]["correct"] and rv["options"][1]["why"]


def test_code_case_can_require_unchanged_arguments():
    from app.graders.code import grade_code
    mutating = "def tail(xs):\n    del xs[:-1]\n    return xs\n"
    pure = "def tail(xs):\n    return xs[-1:]\n"
    cases = [{"args": [[1, 2, 3]], "expected": [3], "unchanged": True}]
    assert grade_code(pure, "tail", cases).correct
    bad = grade_code(mutating, "tail", cases)
    assert not bad.correct and bad.detail["cases"][0]["error"] == "modified its input arguments"
    assert grade_code(mutating, "tail", [{"args": [[1, 2, 3]], "expected": [3]}]).correct  # flag is opt-in
