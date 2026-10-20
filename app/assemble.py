"""Test assembly: scope + length + formats + priority -> ordered, sectioned item list.

Deterministic for a given (pool, history, request, seed). See docs/app-spec.md "Test assembly".
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .taxonomy import Taxonomy

ASSEMBLER_VERSION = "1"

CHOICE = ("single", "multi")
HEAVY = ("open", "sql", "code", "audio")
ALL_TYPES = CHOICE + ("numeric",) + HEAVY
SMALL_N = 10  # below this, choice-only unless the user selected no choice formats

SECTION_OF = {
    "single": "Multiple choice", "multi": "Multiple choice", "numeric": "Estimation",
    "sql": "Hands-on", "code": "Hands-on", "open": "Written", "audio": "Spoken",
}
SECTION_ORDER = ["Multiple choice", "Estimation", "Hands-on", "Written", "Spoken"]
PRIORITIES = ("unseen_first", "weak_first", "random")


@dataclass(frozen=True)
class PoolItem:
    item_id: str
    version: int
    type: str
    subtopic: str
    tags: tuple[str, ...] = ()
    time_s: int = 60
    n_options: int = 0

    @property
    def all_tags(self) -> tuple[str, ...]:
        return (self.subtopic,) + tuple(t for t in self.tags if t != self.subtopic)


@dataclass
class History:
    n_answered: int = 0
    last_correct: bool | None = None
    last_at: str | None = None
    mean_score: float | None = None


@dataclass
class Request:
    subtopics: list[str]
    length: int
    formats: list[str]
    priority: str = "unseen_first"
    seed: int = 0


@dataclass
class Picked:
    item: PoolItem
    section: str
    attributed: str
    presented_order: list[int] | None = None


@dataclass
class Assembly:
    picked: list[Picked] = field(default_factory=list)
    pool_size: int = 0
    requested: int = 0
    allocation: dict[str, int] = field(default_factory=dict)
    format_targets: dict[str, int] = field(default_factory=dict)
    note: str | None = None

    @property
    def est_seconds(self) -> int:
        return sum(p.item.time_s for p in self.picked)

    def by_section(self) -> dict[str, int]:
        c = Counter(p.section for p in self.picked)
        return {s: c[s] for s in SECTION_ORDER if c[s]}


def largest_remainder(n: int, weights: dict[str, float]) -> dict[str, int]:
    """Split n proportionally to weights; ties broken by remainder, then weight, then key."""
    total = sum(weights.values())
    if n <= 0 or total <= 0:
        return {k: 0 for k in weights}
    exact = {k: n * w / total for k, w in weights.items()}
    out = {k: int(v) for k, v in exact.items()}
    rest = n - sum(out.values())
    order = sorted(weights, key=lambda k: (-(exact[k] - out[k]), -weights[k], k))
    for k in order[:rest]:
        out[k] += 1
    return out


def allocate(n: int, weights: dict[str, float], caps: dict[str, int]) -> dict[str, int]:
    """Weight-proportional allocation that never exceeds a subtopic's capacity; shortfalls are
    redistributed by weight across the remaining subtopics."""
    alloc = {s: 0 for s in weights}
    active = {s for s in weights if caps.get(s, 0) > 0}
    remaining = min(n, sum(caps.get(s, 0) for s in active))
    while remaining > 0 and active:
        shares = largest_remainder(remaining, {s: weights[s] for s in active})
        over = [s for s in active if shares[s] > caps[s] - alloc[s]]
        if not over:
            for s, k in shares.items():
                alloc[s] += k
            break
        for s in over:
            take = caps[s] - alloc[s]
            alloc[s] += take
            remaining -= take
            active.discard(s)
    return alloc


def format_targets(n: int, formats: list[str], available: Counter) -> dict[str, int]:
    """Targets per category: 'choice', 'numeric' and each heavy format individually."""
    choice_ok = any(f in formats and available[f] for f in CHOICE)
    numeric_ok = "numeric" in formats and available["numeric"] > 0
    heavy = [f for f in HEAVY if f in formats and available[f] > 0]
    if n <= 0:
        return {}
    if n < SMALL_N:
        if choice_ok:
            return {"choice": n}
        cats = (["numeric"] if numeric_ok else []) + heavy
        return largest_remainder(n, {c: 1.0 for c in cats}) if cats else {}
    shares: dict[str, float] = {}
    if choice_ok:
        shares["choice"] = 0.70
    if numeric_ok:
        shares["numeric"] = 0.10
    for f in heavy:
        shares[f] = 0.20 / len(heavy)
    if not shares:
        return {}
    targets = largest_remainder(n, shares)
    if n >= 20:
        for f in heavy:
            if targets[f] == 0:
                donor = max(targets, key=lambda k: (targets[k], k))
                targets[donor] -= 1
                targets[f] = 1
    return targets


def _rank_key(priority: str, h: History | None, tiebreak: float):
    if priority == "random":
        return (tiebreak,)
    answered = h is not None and h.n_answered > 0
    if priority == "weak_first":
        if not answered:
            return (2, 0.0, tiebreak)
        group = 0 if not h.last_correct else 1
        return (group, h.mean_score if h.mean_score is not None else 0.0, tiebreak)
    # unseen_first: never answered > answered wrong > answered right longest ago
    if not answered:
        return (0, "", tiebreak)
    return (1 if not h.last_correct else 2, h.last_at or "", tiebreak)


def assemble(pool: list[PoolItem], history: dict[str, History], tax: Taxonomy, req: Request) -> Assembly:
    rng = random.Random(req.seed)
    scope = [s for s in dict.fromkeys(req.subtopics) if tax.has_tag(s)]
    scope_set = set(scope)
    formats = [f for f in ALL_TYPES if f in set(req.formats)]
    priority = req.priority if req.priority in PRIORITIES else "unseen_first"
    out = Assembly(requested=req.length)

    # 1. candidates, attributed to primary subtopic if in scope else first in-scope tag
    cands: list[tuple[PoolItem, str]] = []
    for it in sorted(pool, key=lambda p: p.item_id):
        if it.type not in formats:
            continue
        attributed = next((t for t in it.all_tags if t in scope_set), None)
        if attributed:
            cands.append((it, attributed))
    out.pool_size = len(cands)
    if not cands or req.length <= 0:
        out.note = "No items match this scope and these formats." if not cands else None
        return out

    tiebreak = {it.item_id: rng.random() for it, _ in cands}
    key = {it.item_id: _rank_key(priority, history.get(it.item_id), tiebreak[it.item_id]) for it, _ in cands}

    by_sub: dict[str, list[PoolItem]] = defaultdict(list)
    for it, sub in cands:
        by_sub[sub].append(it)
    for sub in by_sub:
        by_sub[sub].sort(key=lambda it: key[it.item_id])
    attributed_of = {it.item_id: sub for it, sub in cands}

    # 2. allocation across subtopics by effective taxonomy weight
    weights = {s: tax.subtopic(s).weight for s in scope}
    caps = {s: len(by_sub.get(s, [])) for s in scope}
    n = min(req.length, len(cands))
    alloc = allocate(n, weights, caps)
    out.allocation = {s: k for s, k in alloc.items() if k}

    # 3. format mix
    available = Counter(it.type for it, _ in cands)
    targets = format_targets(n, formats, available)
    out.format_targets = dict(targets)

    picked_ids: set[str] = set()
    picked_by_sub: Counter = Counter()
    chosen: list[PoolItem] = []

    def pick_one(types: tuple[str, ...]) -> bool:
        best = None
        for s in scope:
            pool_s = [it for it in by_sub.get(s, []) if it.type in types and it.item_id not in picked_ids]
            if not pool_s:
                continue
            a = alloc.get(s, 0)
            deficit = (a - picked_by_sub[s]) / a if a else -(picked_by_sub[s] + 1.0)
            rank = (deficit, weights[s])
            if best is None or rank > best[0]:
                best = (rank, s, pool_s[0])
        if best is None:
            return False
        _, s, it = best
        picked_ids.add(it.item_id)
        picked_by_sub[s] += 1
        chosen.append(it)
        return True

    heavy_order = sorted((f for f in HEAVY if targets.get(f)), key=lambda f: (available[f], f))
    for cat in heavy_order + ["numeric", "choice"]:
        types = CHOICE if cat == "choice" else (cat,)
        for _ in range(targets.get(cat, 0)):
            if len(chosen) >= n or not pick_one(types):
                break
    # fill any remaining slots (a format ran short) from whatever the targets allow, choice first
    fill_types = tuple(f for f in formats if (f in CHOICE and targets.get("choice"))
                       or (f == "numeric" and targets.get("numeric")) or targets.get(f))
    while len(chosen) < n and pick_one(fill_types or tuple(formats)):
        pass
    while len(chosen) < n and pick_one(tuple(formats)):
        pass

    # 5. sections, shuffled within; 6. option permutations
    sections: dict[str, list[PoolItem]] = defaultdict(list)
    for it in sorted(chosen, key=lambda p: p.item_id):
        sections[SECTION_OF[it.type]].append(it)
    for sec in SECTION_ORDER:
        items = sections.get(sec, [])
        rng.shuffle(items)
        for it in items:
            perm = None
            if it.type in CHOICE and it.n_options:
                perm = list(range(it.n_options))
                rng.shuffle(perm)
            out.picked.append(Picked(it, sec, attributed_of[it.item_id], perm))

    if len(out.picked) < req.length:
        out.note = (f"Only {len(out.picked)} items are available for this scope and these formats "
                    f"(requested {req.length}).")
    return out
