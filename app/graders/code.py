"""Run a candidate's Python function against test cases in a subprocess.

This executes code typed by the local user on their own machine. It is
isolated from the app process (separate interpreter, -I, temp dir, timeout,
POSIX resource limits) to contain mistakes like infinite loops, not to sandbox
hostile code.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .basic import GradeResult

HARNESS = r'''
import json, math, sys, traceback
sys.path.insert(0, sys.argv[1])
out_path = sys.argv[2]
cases = json.load(open(sys.argv[3]))
fn_name = sys.argv[4]
results = []

def same(a, b, tol):
    if tol is not None and isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=0, abs_tol=tol)
    if tol is not None and isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y, tol) for x, y in zip(a, b))
    return a == b

try:
    import solution
    fn = getattr(solution, fn_name)
except Exception:
    json.dump({"fatal": traceback.format_exc(limit=3)}, open(out_path, "w"))
    sys.exit(0)

for case in cases:
    try:
        got = fn(*case.get("args", []), **case.get("kwargs", {}))
        got = json.loads(json.dumps(got))  # tuples -> lists, like the expected values
        results.append({"passed": same(got, case["expected"], case.get("approx")), "got": got})
    except Exception as exc:
        results.append({"passed": False, "error": f"{type(exc).__name__}: {exc}"})
json.dump({"results": results}, open(out_path, "w"))
'''


def _limits():  # pragma: no cover - runs in the child
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    except Exception:
        pass


def run_cases(source: str, function: str, cases: list[dict], timeout_s: float = 5.0) -> dict:
    """Return {"results": [...]} or {"fatal": "..."}; never raises for candidate errors."""
    with tempfile.TemporaryDirectory(prefix="fde-code-") as tmp:
        d = Path(tmp)
        (d / "solution.py").write_text(source, encoding="utf-8")
        (d / "harness.py").write_text(HARNESS, encoding="utf-8")
        (d / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
        out = d / "out.json"
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(d / "harness.py"), str(d), str(out), str(d / "cases.json"), function],
                cwd=d, capture_output=True, text=True, timeout=timeout_s,
                preexec_fn=_limits if sys.platform != "win32" else None,
            )
        except subprocess.TimeoutExpired:
            return {"fatal": f"timed out after {timeout_s:g}s (infinite loop?)"}
        if not out.exists():
            return {"fatal": (proc.stderr or "no output")[-2000:]}
        data = json.loads(out.read_text(encoding="utf-8"))
        data["stdout"] = (proc.stdout or "")[-2000:]
        return data


def grade_code(source: str, function: str, tests: list[dict], timeout_s: float = 5.0) -> GradeResult:
    data = run_cases(source, function, tests, timeout_s)
    if "fatal" in data:
        return GradeResult(False, 0.0, {"error": data["fatal"], "passed": 0, "total": len(tests)})
    passed = sum(1 for r in data["results"] if r["passed"])
    total = len(tests)
    # Hidden test inputs are not revealed; only pass/fail and error types.
    summary = [{"passed": r["passed"], **({"error": r["error"]} if "error" in r else {})} for r in data["results"]]
    return GradeResult(passed == total, round(passed / total, 4) if total else 0.0,
                       {"passed": passed, "total": total, "cases": summary})
