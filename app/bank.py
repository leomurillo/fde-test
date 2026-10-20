"""Load, validate, hash and lint the item bank (bank/items/*.yaml).

  python -m app.bank --check [files...]   validate (exit 1 on errors)
  python -m app.bank --lint  [files...]   distractor-quality heuristics
  python -m app.bank --lock               record hashes of new id@version pairs in bank/lock.json
  python -m app.bank --stats              counts by domain / subtopic / type vs bank/quotas.yaml

Item identity for analytics is (id, version). The content hash pins what a
version meant; changing locked content without bumping `version` fails --check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .graders.code import run_cases
from .graders.sql import run_query
from .taxonomy import REPO_ROOT, Taxonomy, domain_file_prefix, load_taxonomy

BANK_DIR = REPO_ROOT / "bank"
ITEMS_DIR = Path(os.environ.get("FDE_BANK_DIR", BANK_DIR / "items"))  # tests point this at tests/fixtures
LOCK_PATH = BANK_DIR / "lock.json"
QUOTAS_PATH = BANK_DIR / "quotas.yaml"

TYPES = {"single", "multi", "numeric", "open", "audio", "sql", "code"}
CHOICE_TYPES = {"single", "multi"}
LEVELS = {"recall", "apply", "analyze", "evaluate"}
ID_RE = re.compile(r"^D(\d{1,2})-(\d{3})$")
BANNED_OPTION_RE = re.compile(
    r"\b(all|none) of the (above|options|preceding)\b|^\s*both\b.*\band\b|^\s*[A-F] and [A-F]\b|^\s*neither\b",
    re.IGNORECASE,
)
ABSOLUTE_RE = re.compile(r"\b(always|never|only|all|none|must|guarantees?)\b", re.IGNORECASE)


@dataclass
class Item:
    data: dict
    file: Path

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def version(self) -> int:
        return self.data["version"]

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def type(self) -> str:
        return self.data["type"]

    @property
    def subtopic(self) -> str:
        return self.data["subtopic"]

    @property
    def tags(self) -> list[str]:
        """Primary subtopic first, then secondary tags (deduplicated)."""
        out = [self.subtopic]
        for t in self.data.get("tags") or []:
            if t not in out:
                out.append(t)
        return out

    @property
    def content_hash(self) -> str:
        return content_hash(self.data)


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def err(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")


def content_hash(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def item_files(paths: list[Path] | None = None) -> list[Path]:
    if paths:
        return [Path(p) for p in paths]
    items_dir = Path(os.environ.get("FDE_BANK_DIR", ITEMS_DIR))
    return sorted(items_dir.glob("D*.yaml"))


def load_items(paths: list[Path] | None = None, report: Report | None = None) -> list[Item]:
    report = report or Report()
    items: list[Item] = []
    for path in item_files(paths):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            report.err(path.name, f"YAML error: {exc}")
            continue
        domain = raw.get("domain")
        for i, data in enumerate(raw.get("items") or []):
            if not isinstance(data, dict):
                report.err(f"{path.name}[{i}]", "item is not a mapping")
                continue
            data.setdefault("_domain", domain)
            items.append(Item(data, path))
    return items


def _strip_private(data: dict) -> dict:
    return {k: v for k, v in data.items() if not k.startswith("_")}


def validate(items: list[Item], tax: Taxonomy, report: Report, run_executables: bool = True) -> None:
    seen: dict[str, Path] = {}
    for item in items:
        domain = item.data.pop("_domain", None)
        d = item.data
        where = f"{item.file.name}:{d.get('id', '?')}"
        for req in ("id", "version", "type", "subtopic", "difficulty", "level", "time_s", "stem"):
            if d.get(req) in (None, ""):
                report.err(where, f"missing '{req}'")
        if d.get("type") not in ("open", "audio") and not d.get("explanation"):
            report.err(where, "missing 'explanation'")
        if report.errors and report.errors[-1].startswith(where + ": missing 'id'"):
            continue
        m = ID_RE.match(str(d.get("id", "")))
        if not m:
            report.err(where, "id must look like D6-001")
        elif domain and f"D{int(m.group(1))}" != domain:
            report.err(where, f"id prefix does not match file domain {domain}")
        if domain and item.file.name and not item.file.name.startswith(domain_file_prefix(domain)):
            report.err(where, f"file name should start with {domain_file_prefix(domain)}")
        if d.get("id") in seen:
            report.err(where, f"duplicate id (also in {seen[d['id']].name})")
        seen[d.get("id")] = item.file
        if not isinstance(d.get("version"), int) or d.get("version", 0) < 1:
            report.err(where, "version must be an integer >= 1")
        if d.get("type") not in TYPES:
            report.err(where, f"type must be one of {sorted(TYPES)}")
            continue
        if not tax.has_tag(str(d.get("subtopic"))):
            report.err(where, f"unknown subtopic '{d.get('subtopic')}'")
        elif domain and tax.subtopic(d["subtopic"]).domain_id != domain:
            report.err(where, f"subtopic '{d['subtopic']}' is not in domain {domain}")
        for t in d.get("tags") or []:
            if not tax.has_tag(t):
                report.err(where, f"unknown tag '{t}'")
        for c in d.get("competencies") or []:
            if c not in tax.competencies:
                report.err(where, f"unknown competency '{c}'")
        if d.get("difficulty") not in (1, 2, 3):
            report.err(where, "difficulty must be 1, 2 or 3")
        if d.get("level") not in LEVELS:
            report.err(where, f"level must be one of {sorted(LEVELS)}")
        if not isinstance(d.get("time_s"), int) or not 15 <= d.get("time_s", 0) <= 1800:
            report.err(where, "time_s must be an integer between 15 and 1800")
        refs = d.get("refs") or []
        if not isinstance(refs, list) or any(not str(r).startswith("http") for r in refs):
            report.err(where, "refs must be a list of URLs")

        t = d["type"]
        if t in CHOICE_TYPES:
            _validate_choice(d, where, report)
        elif t == "numeric":
            a = d.get("answer") or {}
            if not isinstance(a.get("value"), (int, float)):
                report.err(where, "answer.value must be a number")
            if not isinstance(a.get("tolerance"), (int, float)) or a.get("tolerance", -1) < 0:
                report.err(where, "answer.tolerance must be >= 0")
            if a.get("tolerance_type", "abs") not in ("abs", "rel"):
                report.err(where, "answer.tolerance_type must be abs or rel")
            if not d.get("work"):
                report.err(where, "numeric items need 'work' (worked solution)")
        elif t in ("open", "audio"):
            rubric = d.get("rubric") or []
            if len(rubric) < 3:
                report.err(where, "rubric needs >= 3 criteria")
            for r in rubric:
                if not r.get("criterion") or not isinstance(r.get("points"), int) or not r.get("guidance"):
                    report.err(where, "each rubric criterion needs criterion, integer points, guidance")
            if not d.get("model_answer"):
                report.err(where, "missing model_answer")
            if t == "audio" and not (isinstance(d.get("prep_s"), int) and isinstance(d.get("max_s"), int)):
                report.err(where, "audio items need integer prep_s and max_s")
        elif t == "sql":
            _validate_sql(d, where, report, run_executables)
        elif t == "code":
            _validate_code(d, where, report, run_executables)


def _validate_choice(d: dict, where: str, report: Report) -> None:
    opts = d.get("options") or []
    n_correct = sum(1 for o in opts if o.get("correct"))
    if d["type"] == "single":
        if len(opts) not in (4, 5):
            report.err(where, "single items need 4 or 5 options")
        if n_correct != 1:
            report.err(where, f"single items need exactly 1 correct option (has {n_correct})")
    else:
        if not 4 <= len(opts) <= 6:
            report.err(where, "multi items need 4-6 options")
        if n_correct not in (2, 3) or len(opts) - n_correct < 2:
            report.err(where, "multi items need 2-3 correct and >= 2 incorrect options")
    texts = set()
    for i, o in enumerate(opts):
        text = str(o.get("text", "")).strip()
        if not text:
            report.err(where, f"option {i} has no text")
        if text.lower() in texts:
            report.err(where, f"duplicate option text '{text[:40]}'")
        texts.add(text.lower())
        if BANNED_OPTION_RE.search(text):
            report.err(where, f"banned option pattern: '{text[:60]}'")
        if not o.get("why"):
            report.err(where, f"option {i} needs 'why'")
        if not o.get("correct") and not o.get("misconception"):
            report.err(where, f"incorrect option {i} needs 'misconception'")


def _validate_sql(d: dict, where: str, report: Report, run: bool) -> None:
    s = d.get("sql") or {}
    if not s.get("setup") or not s.get("reference"):
        report.err(where, "sql items need sql.setup and sql.reference")
        return
    if not re.match(r"^\s*(select|with)\b", s["reference"], re.IGNORECASE):
        report.err(where, "sql.reference must be a SELECT or WITH statement")
    if run:
        res = run_query(s["setup"], s["reference"])
        if res.error:
            report.err(where, f"sql.reference fails: {res.error}")
        elif not res.rows:
            report.err(where, "sql.reference returns no rows")


def _validate_code(d: dict, where: str, report: Report, run: bool) -> None:
    c = d.get("code") or {}
    for req in ("function", "starter", "reference"):
        if not c.get(req):
            report.err(where, f"code items need code.{req}")
    examples, tests = c.get("examples") or [], c.get("tests") or []
    if not examples:
        report.err(where, "code items need >= 1 example")
    if len(tests) < 4:
        report.err(where, "code items need >= 4 hidden tests")
    for case in examples + tests:
        if "expected" not in case:
            report.err(where, "every code case needs 'expected'")
    if run and c.get("reference") and c.get("function") and examples + tests:
        data = run_cases(c["reference"], c["function"], examples + tests, float(c.get("timeout_s", 5)))
        if "fatal" in data:
            report.err(where, f"code.reference crashed: {data['fatal'][-300:]}")
        else:
            failed = [i for i, r in enumerate(data["results"]) if not r["passed"]]
            if failed:
                detail = data["results"][failed[0]]
                report.err(where, f"code.reference fails cases {failed} (first: {detail})")


def check_lock(items: list[Item], report: Report) -> None:
    lock = json.loads(LOCK_PATH.read_text()) if LOCK_PATH.exists() else {}
    for item in items:
        locked = lock.get(item.key)
        if locked and locked != item.content_hash:
            report.err(f"{item.file.name}:{item.id}",
                       f"content of locked version {item.version} changed; bump 'version' instead of editing in place")


def write_lock(items: list[Item]) -> int:
    lock = json.loads(LOCK_PATH.read_text()) if LOCK_PATH.exists() else {}
    added = 0
    for item in items:
        if item.key not in lock:
            lock[item.key] = item.content_hash
            added += 1
    LOCK_PATH.write_text(json.dumps(dict(sorted(lock.items())), indent=1) + "\n", encoding="utf-8")
    return added


def lint(items: list[Item]) -> list[str]:
    """Heuristics for test-wise elimination cues. Returns human-readable lines."""
    lines: list[str] = []
    by_file: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        by_file[it.file.name].append(it)
    for fname, its in sorted(by_file.items()):
        singles = [i for i in its if i.type == "single"]
        longest = 0
        for it in [i for i in its if i.type in CHOICE_TYPES]:
            opts = it.data["options"]
            lens = [len(str(o["text"])) for o in opts]
            correct_lens = [len(str(o["text"])) for o in opts if o.get("correct")]
            wrong_lens = [len(str(o["text"])) for o in opts if not o.get("correct")]
            if it.type == "single" and correct_lens[0] == max(lens) and lens.count(max(lens)) == 1:
                longest += 1
                if correct_lens[0] > 1.3 * max(wrong_lens):
                    lines.append(f"  {it.id}: correct option is >30% longer than every distractor")
            if min(lens) * 2.2 < max(lens):
                lines.append(f"  {it.id}: option lengths vary a lot ({min(lens)}..{max(lens)} chars)")
            abs_wrong = sum(1 for o in opts if not o.get("correct") and ABSOLUTE_RE.search(str(o["text"])))
            abs_right = sum(1 for o in opts if o.get("correct") and ABSOLUTE_RE.search(str(o["text"])))
            if abs_wrong >= 2 and abs_right == 0:
                lines.append(f"  {it.id}: absolute words appear only in distractors ({abs_wrong})")
        rate = longest / len(singles) if singles else 0
        flag = "  <-- too high (target <= 35%)" if rate > 0.35 else ""
        lines.insert(0, f"{fname}: correct-is-longest on {longest}/{len(singles)} single items ({rate:.0%}){flag}")
    recall = Counter(i.file.name for i in items if i.data.get("level") == "recall")
    for fname, n in sorted(recall.items()):
        total = len(by_file[fname])
        if n / total > 0.10:
            lines.append(f"{fname}: {n}/{total} items are 'recall' (target <= 10%)")
    return lines


def stats(items: list[Item], tax: Taxonomy) -> list[str]:
    quotas = yaml.safe_load(QUOTAS_PATH.read_text()) if QUOTAS_PATH.exists() else {}
    q_sub = {s: v for d in quotas.get("domains", {}).values() for s, v in d.get("subtopics", {}).items()}
    by_sub = Counter(i.subtopic for i in items)
    tagged = Counter(t for i in items for t in i.tags)
    by_type = Counter(i.type for i in items)
    out = [f"items: {len(items)}  by type: {dict(sorted(by_type.items()))}"]
    for d in tax.domains:
        out.append(f"{d.id} {d.name}: {sum(by_sub[s.tag] for s in d.subtopics)} items")
        for s in d.subtopics:
            target = q_sub.get(s.tag, {}).get("choice", "?")
            out.append(f"   {s.tag:<24} primary {by_sub[s.tag]:>3}  any-tag {tagged[s.tag]:>3}  choice-quota {target}")
    return out


def load_valid_bank(run_executables: bool = False) -> tuple[list[Item], Report]:
    """For the app: load every item, validate, and return (items, report)."""
    report = Report()
    items = load_items(report=report)
    validate(items, load_taxonomy(), report, run_executables=run_executables)
    check_lock(items, report)
    for it in items:
        it.data = _strip_private(it.data)
    return items, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--lint", action="store_true")
    ap.add_argument("--lock", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--no-exec", action="store_true", help="skip running SQL/code references")
    args = ap.parse_args(argv)

    tax = load_taxonomy()
    report = Report()
    items = load_items(args.files or None, report)
    validate(items, tax, report, run_executables=not args.no_exec)
    check_lock(items, report)
    if args.files:
        # Cross-file duplicate ids are only detectable against the whole bank.
        others = [i for i in load_items(None, Report()) if i.file.resolve() not in {p.resolve() for p in args.files}]
        ids = {i.data.get("id") for i in others}
        for it in items:
            if it.id in ids:
                report.err(f"{it.file.name}:{it.id}", "id already used in another file")

    for w in report.warnings:
        print(f"warning: {w}")
    for e in report.errors:
        print(f"error: {e}", file=sys.stderr)
    if args.check:
        print(f"{'FAILED' if report.errors else 'ok'}: {len(items)} items, {len(report.errors)} errors")
    if args.lint:
        print("\n".join(lint(items)))
    if args.stats:
        print("\n".join(stats(items, tax)))
    if args.lock:
        if report.errors:
            print("refusing to lock: fix errors first", file=sys.stderr)
            return 1
        print(f"locked {write_lock(items)} new item versions -> {LOCK_PATH.relative_to(REPO_ROOT)}")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
