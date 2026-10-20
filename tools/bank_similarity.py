#!/usr/bin/env python3
"""Find near-duplicate items across the bank (TF-IDF cosine over stem + correct option).

  python tools/bank_similarity.py                 # pairs with similarity >= 0.35
  python tools/bank_similarity.py --threshold 0.3 --top 40

Standard library + PyYAML. A high score is a prompt to look, not proof of duplication:
items may share a scenario but test different points.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bank import Report, load_items  # noqa: E402

STOP = set("""a an the and or of to in for on with by at from as is are be was were it its this that these those
which who whom what when where why how than then so but not no can could should would will may might must do does
did done has have had into over under about after before between their there they them you your we our i he she
his her one two three each per via any all some more most less such only also very just""".split())
TOKEN = re.compile(r"[a-z][a-z0-9_\-]+")


def text_of(item) -> str:
    d = item.data
    parts = [str(d.get("stem", ""))]
    for o in d.get("options") or []:
        if o.get("correct"):
            parts.append(str(o.get("text", "")))
    return " ".join(parts).lower()


def vectors(docs: list[str]) -> list[dict[str, float]]:
    toks = [[t for t in TOKEN.findall(d) if t not in STOP] for d in docs]
    df = Counter(t for ts in toks for t in set(ts))
    n = len(docs)
    out = []
    for ts in toks:
        tf = Counter(ts)
        v = {t: (1 + math.log(c)) * math.log((n + 1) / (df[t] + 1)) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({t: x / norm for t, x in v.items()})
    return out


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--threshold", type=float, default=0.35)
    ap.add_argument("--top", type=int, default=60)
    args = ap.parse_args()

    items = load_items(None, Report())
    vecs = vectors([text_of(i) for i in items])
    pairs = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            s = cosine(vecs[i], vecs[j])
            if s >= args.threshold:
                pairs.append((s, items[i], items[j]))
    pairs.sort(key=lambda p: -p[0])
    for s, a, b in pairs[: args.top]:
        print(f"{s:.2f}  {a.data['id']:<7} {b.data['id']:<7}  {a.data['stem'][:70].strip()!r} | {b.data['stem'][:70].strip()!r}")
    print(f"{len(pairs)} pairs >= {args.threshold}")


if __name__ == "__main__":
    main()
