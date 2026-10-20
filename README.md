# FDE Test

A practice exam for the Forward Deployed Engineer (FDE) role, styled after CandiTech assessments. It will be a Python website you can clone and run locally. Single-choice questions are auto-graded; open-ended and AI-assisted questions are graded by Gemini (bring your own key).

**Status: phase 1 (topics).** The exam taxonomy, the research behind it, and the reading-time estimate are done. The website and question bank come next.

## What's here

| Path | What it is |
|---|---|
| [`docs/taxonomy.md`](docs/taxonomy.md) | **Weighted topic taxonomy**: 11 domains, 49 subtopics, tags, MCQ allocation, JD coverage, terminology policy (generated) |
| [`docs/research.md`](docs/research.md) | CandiTech formats and reviews, comparison of the two handbooks, other FDE exams, weighting method |
| [`docs/reading-time.md`](docs/reading-time.md) | **Reading-time estimate** for both handbooks at 300 wpm |
| [`data/taxonomy.yaml`](data/taxonomy.yaml) | Source of truth for domains, weights, tags, formats and JD coverage |
| [`data/terminology.yaml`](data/terminology.yaml) | Which terms are mainstream and which are book-coined, and what questions may use |
| [`data/reading_time.json`](data/reading_time.json) | Raw word counts per file |
| [`tools/reading_time.py`](tools/reading_time.py) | Word counter and reading-time estimator (stdlib only) |
| [`tools/taxonomy.py`](tools/taxonomy.py) | Validates the taxonomy and renders `docs/taxonomy.md` |

## Quick start

```bash
git clone https://github.com/leomurillo/fde-test.git && cd fde-test
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python tools/reading_time.py          # clones the two handbooks into .cache/ and prints the estimate
python tools/taxonomy.py --check      # validate weights, tags and JD coverage
python tools/taxonomy.py --render     # regenerate docs/taxonomy.md after editing the YAML
```

## Sources

- [RichardXu001/the-fde-handbook](https://github.com/RichardXu001/the-fde-handbook/tree/master/en) (`en/`)
- [goday-org/FDE-Handbook](https://github.com/goday-org/FDE-Handbook/tree/main/book_en) (`book_en/`)
- Primary sources and exam syllabi are cited in [`docs/research.md`](docs/research.md).

Questions test principles and mainstream terminology, not either book's coined frameworks; see [`data/terminology.yaml`](data/terminology.yaml).
