# Running the practice exam (README section draft)

## Setup

```bash
git clone https://github.com/leomurillo/fde-test.git && cd fde-test
python -m venv .venv && source .venv/bin/activate      # Python 3.11+
pip install -r requirements.txt                         # add -r requirements-dev.txt for tests
```

## Run

```bash
python -m app                      # http://127.0.0.1:8000
python -m app --port 8080          # other port
python -m app --host 0.0.0.0       # listen on the LAN (no auth: only do this on a trusted network)
```

Then open the dashboard, click **Start a new test**, pick a preset (or tick subtopics), length, formats,
item priority and the feedback mode:

- **Real-time feedback**: after each item you see whether you were right, the correct option(s), why every
  option is right or wrong, the explanation, references, and the worked solution / model answer / rubric.
- **Feedback at the end**: exam conditions; everything is revealed on the results page.

Runner shortcuts: `1`–`6` select options, `Enter` submits (and goes to the next item after a reveal),
`F` flags, `Ctrl+Enter` submits from a text box, `Ctrl+'` runs SQL/code. You can skip items and come back to
them before finishing; reopening a test resumes at the first item you have not reached.

Spoken items emulate CandiTech's "Video" questions: a preparation countdown, then one take recorded with
the microphone that stops automatically at the time limit. Without a microphone you get a timed text box
in which you cannot edit what you already wrote.

## Gemini key (optional, for AI grading)

Open-text and spoken answers are graded by Gemini against each item's rubric. Bring your own key:

- set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) in the environment, or
- paste it on the **Settings** page, which writes `var/secrets.json` with file mode 600.

The key is sent only in the `x-goog-api-key` header. It is never logged, stored in the database or
exported. The model defaults to `gemini-flash-latest`; if that name ever 404s, the app picks the newest
stable `gemini-*-flash` model, remembers it, and retries. Settings can list the models your key can use
and test the connection.

Without a key (or when Gemini fails) nothing blocks: the answer is stored as ungraded, and when it is
revealed you score yourself against the rubric and the model answer.

## Where data lives

Everything is local, in `var/` at the repo root (gitignored); override with `FDE_DATA_DIR=/some/dir`.

| Path | Contents |
|---|---|
| `var/fde.sqlite3` | sessions, responses, telemetry events, feedback, item version snapshots, settings |
| `var/audio/{response_id}.webm` | spoken answers (ogg/m4a depending on the browser) |
| `var/secrets.json` | Gemini key, only if saved from Settings |

The item bank is read from `bank/items/*.yaml` at startup (override with `FDE_BANK_DIR`). Invalid items are
skipped and listed under **Settings → Item bank**; use **Reload bank** there after editing the YAML.

## Exports

From **Export** (or **Feedback**):

- `feedback.jsonl`: one object per feedback note, with a snapshot of the exact item version it is about,
  ready to paste into an LLM to fix items. `feedback.md` is the same, human readable, grouped by item.
- `fde-data-{installation_id}-{date}.zip`: `manifest.json` plus `sessions`, `session_items`, `responses`,
  `events`, `feedback` and `item_versions` as JSONL. Columns stored as JSON (`*_json`) appear decoded
  under the name without the suffix. Audio files are excluded unless you tick the checkbox. This is the
  format meant for pooling data from many installations later (`app.export.read_export` reads it back).

## How item versioning works

- Every item has a stable id (`D6-001`, never reused) and an integer `version`. Authors bump the version
  whenever anything a candidate sees or is graded on changes. `python -m app.bank --lock` records the
  content hash of each `id@version`, and `--check` fails if locked content changes without a bump.
- At startup the app copies each new `(id, version)` into `item_versions` with the full content snapshot,
  its hash and seed IRT parameters (a = 1, b from difficulty, c = 1/options for choice items). Items that
  disappear from the bank are marked retired, never deleted.
- Every response stores `(item_id, item_version)`, and all rendering, grading and review uses the stored
  snapshot for that version, so old answers stay interpretable after the bank changes. If a file changes
  an already-stored version without a bump, the app logs a loud warning and keeps the stored snapshot.
- Each session also records the app version, the bank fingerprint (sha256 over every `id@version:hash`),
  the taxonomy version, the assembler version and the random seed, so a test can be reproduced.
- Every row id is a UUIDv4 and each installation has an anonymous `installation_id`, so exports from
  many clones merge without collisions.

## What is measured (for later CAT/IRT calibration)

Per response: time to first interaction, time to submit, active time (page visible and focused),
answer changes, focus losses, pastes, SQL/code runs, optional confidence (guess / unsure / sure),
flag and skip, plus server-side `presented_at` / `submitted_at` timestamps. Answer changes count
re-selections for single choice, de-selections for multiple select, and editing bursts separated by
1.5 s pauses for typed answers. Raw events (`render`, `select`, `input` with length only, `paste`, `blur`,
`visibility`, `run_sql`, `audio_start`, `reveal`, …) are batched to the `events` table every 5 s.

## Progress

**Progress** shows, per domain and subtopic, the items in the bank, seen / never seen, last result right
or wrong, accuracy and median time, plus an item browser with each item's history. Items you have never
seen show metadata only, never their stem or answers, so they stay fresh for future tests.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # uses the fixture bank in tests/fixtures, never bank/items
```
