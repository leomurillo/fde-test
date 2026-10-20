#!/usr/bin/env python3
"""Count the words in the two FDE handbooks and estimate reading time.

Sources (English editions only):
  - RichardXu001/the-fde-handbook  -> en/
  - goday-org/FDE-Handbook         -> book_en/

The repos are shallow-cloned with git into .cache/books/ on first run
(override with --cache-dir, or pass --offline to only use what is there).
Standard library only.

Two counts are reported per book:
  - prose: what you actually read. Fenced code blocks, inline HTML/SVG,
    image links, bare URLs and table rule lines are stripped.
  - raw:   every whitespace-delimited token in the .md files.
Reading time is computed on prose words at --wpm (default 300); the
"incl. code" column applies the same speed to raw tokens as an upper bound.

Usage:
  python tools/reading_time.py                # table to stdout
  python tools/reading_time.py --wpm 250      # different reading speed
  python tools/reading_time.py --per-file     # per-chapter breakdown
  python tools/reading_time.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Book:
    key: str
    title: str
    repo: str
    subdir: str


BOOKS = [
    Book("richardxu", "The FDE Handbook (Richard Xu)",
         "https://github.com/RichardXu001/the-fde-handbook.git", "en"),
    Book("goday", "FDE Handbook (goday-org)",
         "https://github.com/goday-org/FDE-Handbook.git", "book_en"),
]

FENCE_RE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.MULTILINE | re.DOTALL)
SVG_RE = re.compile(r"<svg\b.*?</svg>", re.DOTALL | re.IGNORECASE)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
URL_RE = re.compile(r"https?://\S+")
TABLE_RULE_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$", re.MULTILINE)
MATH_BLOCK_RE = re.compile(r"\$\$.*?\$\$", re.DOTALL)
# A word must contain at least one letter or digit; bare "#", "|", "*" etc. do not count.
WORD_RE = re.compile(r"[^\W_](?:[\w'’.-]*[^\W_])?", re.UNICODE)


@dataclass
class FileCount:
    path: str
    raw: int
    prose: int


@dataclass
class BookCount:
    book: Book
    commit: str | None = None
    files: list[FileCount] = field(default_factory=list)

    @property
    def raw(self) -> int:
        return sum(f.raw for f in self.files)

    @property
    def prose(self) -> int:
        return sum(f.prose for f in self.files)


def prose_text(md: str) -> str:
    text = FENCE_RE.sub(" ", md)
    text = SVG_RE.sub(" ", text)
    text = HTML_COMMENT_RE.sub(" ", text)
    text = MATH_BLOCK_RE.sub(" ", text)
    text = IMAGE_RE.sub(" ", text)
    text = LINK_RE.sub(r"\1", text)
    text = URL_RE.sub(" ", text)
    text = TAG_RE.sub(" ", text)
    text = TABLE_RULE_RE.sub(" ", text)
    return text


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def natural_key(path: Path) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(path))]


def ensure_clone(book: Book, cache_dir: Path, offline: bool) -> Path:
    dest = cache_dir / book.key
    if (dest / book.subdir).is_dir():
        return dest
    if offline:
        sys.exit(f"error: {dest / book.subdir} not found and --offline was given")
    if shutil.which("git") is None:
        sys.exit("error: git is required to download the books (or pass --cache-dir to existing clones)")
    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"cloning {book.repo} -> {dest}", file=sys.stderr)
    subprocess.run(["git", "clone", "--depth", "1", "--quiet", book.repo, str(dest)], check=True)
    return dest


def head_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


def count_book(book: Book, root: Path) -> BookCount:
    result = BookCount(book, commit=head_commit(root))
    base = root / book.subdir
    for path in sorted(base.rglob("*.md"), key=natural_key):
        md = path.read_text(encoding="utf-8", errors="replace")
        result.files.append(FileCount(
            path=str(path.relative_to(base)),
            raw=len(md.split()),
            prose=count_words(prose_text(md)),
        ))
    return result


def fmt_minutes(minutes: float) -> str:
    h, m = divmod(round(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wpm", type=int, default=300, help="reading speed in words per minute (default 300)")
    ap.add_argument("--cache-dir", type=Path, default=REPO_ROOT / ".cache" / "books")
    ap.add_argument("--offline", action="store_true", help="do not clone; use what is in --cache-dir")
    ap.add_argument("--per-file", action="store_true", help="print a per-file breakdown")
    ap.add_argument("--json", type=Path, help="also write results as JSON to this path")
    args = ap.parse_args()

    counts = [count_book(b, ensure_clone(b, args.cache_dir, args.offline)) for b in BOOKS]

    if args.per_file:
        for c in counts:
            print(f"\n## {c.book.title}  ({c.book.subdir}/)")
            width = max(len(f.path) for f in c.files)
            print(f"{'file':<{width}} {'prose':>7} {'raw':>7} {'time':>8}")
            for f in c.files:
                print(f"{f.path:<{width}} {f.prose:>7,} {f.raw:>7,} {fmt_minutes(f.prose / args.wpm):>8}")

    print(f"\nReading speed: {args.wpm} words/minute (reading, not studying)\n")
    print(f"{'book':<34} {'files':>5} {'prose words':>12} {'raw tokens':>11} {'reading time':>13} {'incl. code':>11}")
    for c in counts:
        print(f"{c.book.title:<34} {len(c.files):>5} {c.prose:>12,} {c.raw:>11,} "
              f"{fmt_minutes(c.prose / args.wpm):>13} {fmt_minutes(c.raw / args.wpm):>11}")
    total_prose = sum(c.prose for c in counts)
    total_raw = sum(c.raw for c in counts)
    total_files = sum(len(c.files) for c in counts)
    print(f"{'TOTAL':<34} {total_files:>5} {total_prose:>12,} {total_raw:>11,} "
          f"{fmt_minutes(total_prose / args.wpm):>13} {fmt_minutes(total_raw / args.wpm):>11}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "wpm": args.wpm,
            "books": [{
                "key": c.book.key, "title": c.book.title, "repo": c.book.repo, "subdir": c.book.subdir,
                "commit": c.commit,
                "files": len(c.files), "prose_words": c.prose, "raw_tokens": c.raw,
                "reading_minutes": round(c.prose / args.wpm, 1),
                "per_file": [f.__dict__ for f in c.files],
            } for c in counts],
            "total": {"prose_words": total_prose, "raw_tokens": total_raw,
                      "reading_minutes": round(total_prose / args.wpm, 1)},
        }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
