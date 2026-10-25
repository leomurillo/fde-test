# FDE Test

A local practice exam for the **Forward Deployed Engineer** role, modelled on CandiTech-style assessments.
It runs as a Python website on your machine and tracks every answer by item version, with telemetry suited to
a later move to adaptive testing (CAT/IRT).

- **496 items** across 11 weighted domains and 49 subtopics, including a **fast tier** of 134 short items timed at
  40, 60 and 70 seconds (concept checks and compact scenarios) next to the longer case studies. Every item went through two stages: misconception-first authoring, then an adversarial review whose job was to make sure you can't find the answer by eliminating options.
- **Seven formats**, mapped to CandiTech's question types: single choice, multiple select, numeric value,
  open text (AI-graded), SQL, Python code, and spoken answers (CandiTech's "Video" format, recorded with your microphone).
- **Tests are assembled on demand** from a scope (a preset or any subtopics), a length and a set of formats. Items are drawn in
  proportion to the taxonomy weights; unseen items come first, then the ones you got wrong.
- **Two modes:** real-time feedback after every item, or exam conditions with a review at the end.
- **Modeled tests:** load a hand-built test from a file on your machine (fixed items, timed parts run back to
  back), so a mock of a specific assessment never has to be committed. See [`docs/running.md`](docs/running.md).
- **Pace:** an optional *Done reading* button (`R`) times your first read of each item; results show reading speed,
  time spent deciding, and how many answers fit each item's budget and each part's clock.
- **Progress:** seen, never seen, right and wrong, per item and per subtopic. **Feedback:** a 💬 button on every item, exportable (all notes, or only the open ones) to send back
  for item fixes.

## Quick start

```bash
git clone https://github.com/leomurillo/fde-test.git && cd fde-test
python -m venv .venv && source .venv/bin/activate        # Python 3.11+
pip install -r requirements.txt
python -m app                                             # → http://127.0.0.1:8000
```

AI grading for open and spoken answers is optional. Set `GEMINI_API_KEY`, or paste a key on the Settings page.
Without a key, you score those answers yourself against the rubric and a model answer.

Full guide: [`docs/running.md`](docs/running.md). It covers modes, shortcuts, the Gemini key, where data lives, exports and item versioning.

## Repository map

| Path | What it is |
|---|---|
| `app/` | FastAPI app: assembly, grading, telemetry, exports (`python -m app`) |
| `bank/items/D*.yaml` | The item bank, one file per domain |
| `bank/SCHEMA.md`, `bank/AUTHORING.md` | Item format, and the authoring process including confounder design |
| `bank/quotas.yaml`, `bank/scopes/` | Per-subtopic minimums; test presets (full exam, Gorilla Logic JD) |
| `bank/lock.json` | Content hash per `id@version`; `--check` fails if locked content changes without a version bump |
| `data/taxonomy.yaml` | Domains, weights, tags and JD coverage. Rendered as [`docs/taxonomy.md`](docs/taxonomy.md) |
| `data/terminology.yaml` | Mainstream terms vs terms the handbooks coined; what questions may test |
| `docs/research.md` | CandiTech formats, the two handbooks, other FDE exams, how the weights were set |
| `docs/reading-time.md` | Word counts and reading-time estimate for the two handbooks |
| `docs/app-spec.md` | App specification: data model, telemetry, assembly, grading |
| `docs/taxonomy-backlog.md` | Gaps noticed while writing v1 |
| `tools/` | `reading_time.py`, `taxonomy.py`, `bank_similarity.py` (near-duplicate scan) |

## Working on the bank

```bash
python -m app.bank --check            # validate (runs SQL/code reference solutions)
python -m app.bank --lint             # elimination-cue heuristics (e.g. correct option is the longest)
python -m app.bank --stats            # counts vs quotas
python tools/bank_similarity.py       # near-duplicate items
python -m app.bank --lock             # after a release: pin content hashes of new id@version pairs
pip install -r requirements-dev.txt && python -m pytest -q
```

To fix an item after it has been locked, edit it **and** bump its `version`. Past answers stay tied to the
exact version you saw.

## Sources

- [RichardXu001/the-fde-handbook](https://github.com/RichardXu001/the-fde-handbook/tree/master/en) and
  [goday-org/FDE-Handbook](https://github.com/goday-org/FDE-Handbook/tree/main/book_en)
- Primary sources and exam syllabi are cited in [`docs/research.md`](docs/research.md) and on individual items.
