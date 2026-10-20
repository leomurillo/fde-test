from collections import Counter

from app.assemble import (SECTION_ORDER, History, PoolItem, Request, allocate, assemble, format_targets,
                          largest_remainder)
from app.taxonomy import load_taxonomy

TAX = load_taxonomy()
D6 = ["agents.when", "agents.patterns", "agents.tools-mcp", "agents.state", "agents.multi"]
ALL = ["single", "multi", "numeric", "open", "sql", "code", "audio"]


def make_pool(per_sub=10, subs=D6, type_="single", prefix="D6"):
    pool, n = [], 0
    for s in subs:
        for _ in range(per_sub):
            n += 1
            pool.append(PoolItem(f"{prefix}-{n:03d}", 1, type_, s, (), 60, 4 if type_ in ("single", "multi") else 0))
    return pool


def test_largest_remainder_and_allocate():
    assert sum(largest_remainder(7, {"a": 1, "b": 1, "c": 1}).values()) == 7
    alloc = allocate(20, {s: TAX.subtopic(s).weight for s in D6}, {s: 10 for s in D6})
    assert alloc == {"agents.when": 5, "agents.patterns": 4, "agents.tools-mcp": 4, "agents.state": 4, "agents.multi": 3}
    short = allocate(20, {s: TAX.subtopic(s).weight for s in D6}, {**{s: 10 for s in D6}, "agents.when": 2})
    assert short["agents.when"] == 2 and sum(short.values()) == 20


def test_weighting_by_taxonomy():
    res = assemble(make_pool(), {}, TAX, Request(D6, 20, ["single"], seed=1))
    counts = Counter(p.attributed for p in res.picked)
    assert counts == {"agents.when": 5, "agents.patterns": 4, "agents.tools-mcp": 4, "agents.state": 4,
                      "agents.multi": 3}
    assert res.note is None and res.pool_size == 50


def test_scope_filter_and_tag_attribution():
    pool = make_pool(3, ["agents.when"]) + [
        PoolItem("D7-001", 1, "single", "eval.methods", ("agents.state",), 60, 4),   # in scope via tag
        PoolItem("D7-002", 1, "single", "eval.methods", (), 60, 4),                  # out of scope
        PoolItem("D6-090", 1, "sql", "agents.when", (), 300, 0),                     # format not allowed
    ]
    res = assemble(pool, {}, TAX, Request(["agents.when", "agents.state"], 10, ["single", "multi"], seed=3))
    ids = {p.item.item_id for p in res.picked}
    assert "D7-001" in ids and "D7-002" not in ids and "D6-090" not in ids
    assert next(p for p in res.picked if p.item.item_id == "D7-001").attributed == "agents.state"
    assert res.pool_size == 4


def test_shortfall_redistributes_and_reports():
    pool = make_pool(10)
    pool = [p for p in pool if p.subtopic != "agents.when"] + make_pool(1, ["agents.when"], prefix="D6X")
    res = assemble(pool, {}, TAX, Request(D6, 20, ["single"], seed=1))
    counts = Counter(p.attributed for p in res.picked)
    assert counts["agents.when"] == 1 and len(res.picked) == 20 and res.note is None
    small = assemble(make_pool(1), {}, TAX, Request(D6, 20, ["single"], seed=1))
    assert len(small.picked) == 5
    assert "Only 5 items" in small.note


def test_format_mix_for_large_n():
    pool = []
    for t in ALL:
        pool += make_pool(10, D6, t, prefix=f"D6{t[:2]}")
    res = assemble(pool, {}, TAX, Request(D6, 20, ALL, seed=5))
    types = Counter(p.item.type for p in res.picked)
    assert len(res.picked) == 20
    assert types["single"] + types["multi"] == 14
    assert types["numeric"] == 2
    for heavy in ("open", "sql", "code", "audio"):
        assert types[heavy] >= 1
    # sections in CandiTech order
    seen = [p.section for p in res.picked]
    order = [s for s in SECTION_ORDER if s in seen]
    assert seen == sorted(seen, key=order.index)


def test_small_n_is_choice_only_unless_only_heavy_selected():
    pool = []
    for t in ALL:
        pool += make_pool(4, D6, t, prefix=f"D6{t[:2]}")
    res = assemble(pool, {}, TAX, Request(D6, 5, ALL, seed=2))
    assert {p.item.type for p in res.picked} <= {"single", "multi"}
    res = assemble(pool, {}, TAX, Request(D6, 4, ["sql", "code"], seed=2))
    assert Counter(p.item.type for p in res.picked) == {"sql": 2, "code": 2}
    assert format_targets(5, ALL, Counter({t: 5 for t in ALL})) == {"choice": 5}


def test_priority_unseen_first_then_wrong_then_oldest_right():
    pool = make_pool(4, ["agents.when"])
    ids = [p.item_id for p in pool]
    hist = {
        ids[0]: History(1, True, "2026-01-01T00:00:00.000Z", 1.0),
        ids[1]: History(1, False, "2026-01-02T00:00:00.000Z", 0.0),
        ids[2]: History(2, True, "2025-12-01T00:00:00.000Z", 1.0),
    }
    res = assemble(pool, hist, TAX, Request(["agents.when"], 3, ["single"], "unseen_first", seed=9))
    assert {p.item.item_id for p in res.picked} == {ids[3], ids[1], ids[2]}
    res = assemble(pool, hist, TAX, Request(["agents.when"], 1, ["single"], "unseen_first", seed=9))
    assert res.picked[0].item.item_id == ids[3]
    res = assemble(pool, hist, TAX, Request(["agents.when"], 2, ["single"], "unseen_first", seed=9))
    assert {p.item.item_id for p in res.picked} == {ids[3], ids[1]}


def test_priority_weak_first():
    pool = make_pool(4, ["agents.when"])
    ids = [p.item_id for p in pool]
    hist = {ids[0]: History(1, True, "2026-01-01T00:00:00.000Z", 0.9),
            ids[1]: History(1, False, "2026-01-02T00:00:00.000Z", 0.0),
            ids[2]: History(3, True, "2026-01-03T00:00:00.000Z", 0.5)}
    res = assemble(pool, hist, TAX, Request(["agents.when"], 1, ["single"], "weak_first", seed=1))
    assert res.picked[0].item.item_id == ids[1]
    res = assemble(pool, hist, TAX, Request(["agents.when"], 2, ["single"], "weak_first", seed=1))
    assert {p.item.item_id for p in res.picked} == {ids[1], ids[2]}


def test_deterministic_with_seed():
    pool = []
    for t in ALL:
        pool += make_pool(6, D6, t, prefix=f"D6{t[:2]}")
    req = Request(D6, 25, ALL, "random", seed=1234)
    a = assemble(pool, {}, TAX, req)
    b = assemble(list(reversed(pool)), {}, TAX, req)
    sig = lambda r: [(p.item.item_id, p.section, p.presented_order) for p in r.picked]
    assert sig(a) == sig(b)
    c = assemble(pool, {}, TAX, Request(D6, 25, ALL, "random", seed=4321))
    assert sig(a) != sig(c)
    for p in a.picked:
        if p.item.type in ("single", "multi"):
            assert sorted(p.presented_order) == [0, 1, 2, 3]
        else:
            assert p.presented_order is None


def test_empty_pool():
    res = assemble([], {}, TAX, Request(D6, 10, ALL, seed=1))
    assert res.picked == [] and res.note
