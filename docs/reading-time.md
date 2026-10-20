# Reading-time estimate for the two handbooks

Generated with `python tools/reading_time.py` at **300 words per minute**, which is reading speed, not study speed. The raw numbers are in [`data/reading_time.json`](../data/reading_time.json), pinned to these commits:

- `RichardXu001/the-fde-handbook` @ `c6da668`, folder `en/`
- `goday-org/FDE-Handbook` @ `229db7b`, folder `book_en/`

## Totals

| Book | Files | Prose words | Raw tokens | Reading time (prose) | Incl. code (upper bound) |
|---|---:|---:|---:|---:|---:|
| The FDE Handbook (Richard Xu) | 41 | 75,190 | 78,767 | **4h 11m** | 4h 23m |
| FDE Handbook (goday-org) | 11 | 7,864 | 12,885 | **26m** | 43m |
| **Total** | 52 | **83,054** | 91,652 | **4h 37m** | 5h 06m |

- **Prose words** are what you actually read. The script strips fenced code, inline SVG/HTML, images, URLs, table rule lines and `$$` math.
- **Raw tokens** are every whitespace-separated token in the Markdown. goday is about 40% code and diagrams by raw volume, so its "incl. code" time is a loose upper bound; you skim code faster than you read prose.

At other speeds: 250 wpm gives 5h 32m (prose); 200 wpm gives 6h 55m.

## Richard Xu, by part

| Part | Prose words | Time |
|---|---:|---:|
| Front matter + README | 2,933 | 10m |
| Part One · Role and Boundaries (ch 1–5) | 7,868 | 26m |
| Part Two · Discovery to Production (ch 6–8) | 9,290 | 31m |
| Part Three · Engineering Foundations (ch 9–14) | 12,951 | 43m |
| Part Four · Adoption and Handoff (ch 15–18) | 14,114 | 47m |
| Part Five · Business and Productization (ch 19–22) | 9,132 | 30m |
| Part Six · People and Organization (ch 23–28) | 12,550 | 42m |
| Appendices A–C | 6,352 | 21m |

goday is 11 short chapters of 1–5 minutes each. Its chapter 11 (glossary + bibliography) is the longest at 5 minutes.

## A shorter path aligned with the exam taxonomy

Some chapters are useful career context but are marked `do-not-test` in [`data/terminology.yaml`](../data/terminology.yaml): ch 18 (China), ch 23–28 (people and organization), and Appendix C (sources). Skipping them saves about **58m**, which leaves **about 3h 39m** for both books.

Highest yield for the exam, in order: Richard Xu Part Three (43m), goday ch 7–9 (5m), Richard Xu Part Two (31m), and Richard Xu Appendix A checklists (10m).

## Re-running

```bash
python tools/reading_time.py                 # clones both repos into .cache/books/ on first run (needs git)
python tools/reading_time.py --per-file      # per-chapter breakdown
python tools/reading_time.py --wpm 250       # different speed
python tools/reading_time.py --json data/reading_time.json
```
