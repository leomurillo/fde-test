# App specification (v1)

A local, single-user Python website that runs CandiTech-style FDE practice tests assembled from the item bank,
tracks every response and its telemetry in a schema built for later CAT/IRT calibration, and collects
the user's feedback on items.

## Stack and layout

- Python ≥ 3.11, **FastAPI + Jinja2 templates + vanilla JS** (no build step, no CDN required at runtime),
  **SQLite** via stdlib `sqlite3`. Deps pinned in `requirements.txt`. `httpx` for Gemini REST.
- Run: `python -m app` (uvicorn on `127.0.0.1:8000`). Data dir default `./var/` (gitignored), override `FDE_DATA_DIR`.

```
app/
  __main__.py          uvicorn entry
  main.py              FastAPI app factory, routes
  db.py                connection, migrations (PRAGMA user_version), helpers
  bank.py              ✅ exists — load/validate/hash items (do not change its CLI contract)
  taxonomy.py          ✅ exists
  graders/basic.py     ✅ exists — single/multi/numeric
  graders/sql.py       ✅ exists — run_query, grade_sql
  graders/code.py      ✅ exists — run_cases, grade_code
  gemini.py            Gemini client + rubric grading (text and audio)
  assemble.py          test assembly
  sync.py              bank → DB item/version sync at startup
  export.py            JSONL/ZIP exports
  templates/  static/
tests/                 pytest; fixture bank in tests/fixtures/D06-sample.yaml
```

## Identity and data portability (aggregation across clones later)

- On first run create `installation_id` (UUIDv4) in table `meta`. Anonymous; no PII.
- **Every row id is a UUIDv4 string** (sessions, responses, events, feedback) so exports from many
  installations merge without collisions. Timestamps: UTC ISO-8601 with milliseconds.
- `meta` also stores `schema_version`, `created_at`, `app_version` of first run.
- Every session records `app_version`, `bank_fingerprint` (sha256 over sorted `id@version:hash` of the whole bank),
  `taxonomy_version`, `assembler_version`.

## Schema (migration 1)

```
meta(key TEXT PK, value TEXT)
item_versions(item_id, version, content_hash, type, domain_id, subtopic, tags_json, difficulty, level, time_s,
              content_json,              -- full item snapshot (what the candidate saw and was graded on)
              irt_a REAL, irt_b REAL, irt_c REAL, irt_source TEXT,   -- seeds: a=1.0, b={1:-1,2:0,3:1}[difficulty],
                                                                      -- c=1/n_options for choice else 0; source='seed'
              first_loaded_at, PRIMARY KEY(item_id, version))
items(item_id PK, current_version, retired INT DEFAULT 0, updated_at)   -- retired = no longer in bank files
sessions(session_id PK, installation_id, created_at, started_at, finished_at, status,  -- in_progress|finished|abandoned
         mode,                    -- realtime | end
         scope_json,              -- {preset, subtopics[], formats[], length, priority, time_limit_s}
         seed INT, app_version, bank_fingerprint, taxonomy_version, assembler_version, client_json)
session_items(session_id, position, item_id, item_version, section, presented_order_json,  -- option permutation
              PRIMARY KEY(session_id, position))
responses(response_id PK, session_id, position, installation_id, item_id, item_version,
          response_json,          -- raw answer (selected original option indexes / text / sql / code / audio ref)
          correct INT NULL, score REAL NULL, max_points REAL NULL, points REAL NULL,
          grader TEXT,            -- auto | sql | code | gemini | self | none
          grader_model TEXT NULL, grader_detail_json,
          presented_at, first_interaction_at NULL, submitted_at,
          time_to_first_ms INT NULL, time_to_submit_ms INT, active_ms INT,   -- active = visible & focused
          answer_changes INT, focus_losses INT, paste_count INT, runs INT,   -- runs = sql/code executions
          confidence INT NULL,    -- optional 1..3 (guess, unsure, sure)
          flagged INT DEFAULT 0, skipped INT DEFAULT 0,
          mode, feedback_revealed_at NULL, created_at)
events(event_id PK, session_id, position, item_id, item_version, type, client_ts_ms, server_ts, payload_json)
feedback(feedback_id PK, installation_id, created_at, session_id NULL, item_id NULL, item_version NULL,
         category,                -- wrong_key | ambiguous | too_easy | too_hard | typo | explanation | ui | other
         text, context_json, resolved INT DEFAULT 0)
settings(key PK, value)           -- gemini_model, ui prefs; API key is NOT stored here (see Gemini)
```

Indexes on `responses(item_id, item_version)`, `responses(session_id)`, `events(session_id)`.
Migrations are append-only; never edit an applied migration. Responses reference `(item_id, item_version)`
which always exists in `item_versions` because sync inserts versions before they can be served.

## Bank sync

At startup: `load_valid_bank()` (no executable checks for speed). If report has errors, log them and **skip only the
invalid items**; never crash. Upsert `item_versions` for unseen `(id, version)`; update `items.current_version`;
mark ids missing from the bank as `retired`. If an existing `(id, version)` has a different hash than stored,
log a loud warning and keep the stored snapshot (responses must remain interpretable).

## Test assembly (`assemble.py`, `ASSEMBLER_VERSION = "1"`)

Input: subtopic tags (from a preset in `bank/scopes/*.yaml` and/or a custom selection), length N,
allowed formats, priority (`unseen_first` default | `weak_first` | `random`), mode, optional time limit, seed.

1. Candidates: current, non-retired items whose **primary subtopic or any tag** is in scope and whose type is allowed.
2. Allocate N across in-scope subtopics proportional to taxonomy effective weight (largest remainder). Items are
   attributed to their primary subtopic if in scope, else to their first in-scope tag. If a subtopic lacks items,
   redistribute its shortfall by weight; if the whole pool is short, return what exists and say so.
3. Format mix: if non-choice formats are allowed, aim for CandiTech-like mix: ≈ 70% choice, ≈ 10% numeric,
   ≈ 20% split across open/sql/code/audio (at least one of each allowed heavy format when N ≥ 20). Small N: choice-only
   unless the user only selected heavy formats.
4. Within a subtopic, rank by priority. `unseen_first`: never-answered (any version) > answered wrong > answered right
   longest ago. `weak_first`: wrong first, then lowest mean score, then unseen. Ties broken by seeded RNG.
5. Sections (like CandiTech): `Multiple choice` (single+multi, shuffled), `Estimation` (numeric), `Hands-on` (sql, code),
   `Written` (open), `Spoken` (audio). Empty sections omitted.
6. Option order shuffled per presentation with the seeded RNG; store the permutation in `session_items`.

## Session flow and modes

- `/` dashboard: totals (bank size, seen, never seen, right, wrong), per-domain bars, recent sessions, resume button.
- `/new`: preset dropdown (scopes), domain/subtopic tree with checkboxes (pre-filled by preset), length, formats,
  priority, mode (**Real-time feedback** | **Feedback at the end**), optional time limit. Shows estimated duration
  (sum of `time_s`) and pool size live.
- `/s/{session_id}` runner: one item per page. Header: section, `position/N`, elapsed timer, optional countdown,
  flag button, feedback (💬) button. Keyboard: 1–6 select, Enter submit, F flag.
  - **Real-time** mode: submit → grade → reveal: correct/incorrect, the correct option(s), *why* for every option
    (highlight chosen), explanation, refs, worked solution / model answer / rubric breakdown → "Next".
  - **End** mode: submit → next item, no reveal. Final page → `/s/{id}/review`.
  - Skipping allowed (records `skipped=1`, score 0, `correct=0`); user can come back to skipped items before finishing.
  - Resume: reopening an in-progress session continues at the first unanswered position.
- `/s/{id}/review`: score overall/by section/by subtopic, every item with answer, correctness, explanation,
  time taken, and a feedback button per item.
- `/progress`: per domain and subtopic: items in bank, seen, never seen, last result right/wrong, accuracy, median time.
  Item browser filterable by status (never seen / last wrong / last right) and domain; each item shows its history
  (dates, versions, results, times). Never-seen items show metadata but **not** stem/answers (keeps them fresh).
- `/feedback`: list, mark resolved, export.
- `/settings`: Gemini key status (env or local file), model picker, "test connection" button.

## Item rendering per type

- `single`: radio list. `multi`: checkboxes + "Select N" (N = number of correct options).
- `numeric`: input + unit. `open`: textarea with char counter (`max_chars`).
- `sql`: schema panel (setup DDL + first rows of each table), editor textarea (monospace, tab inserts spaces),
  "Run" (shows up to 50 rows or error, via `run_query`), "Submit" (grades via `grade_sql`). Count runs.
- `code`: editor prefilled with `starter`, "Run examples" (visible examples via `run_cases`, show got/expected),
  "Submit" (hidden tests via `grade_code`; show passed/total only). Count runs.
- `audio` (CandiTech "Video"): show prompt + prep countdown (`prep_s`), then record with `MediaRecorder` up to `max_s`,
  **single take**, auto-stop. Upload blob (webm/ogg/mp4) → saved under `var/audio/{response_id}.{ext}`.
  Fallback when no mic: timed no-edit textarea for `max_s` seconds (record `response_json.fallback = "text"`).

## Grading

- single/multi/numeric/sql/code: deterministic graders (exists). Store `correct`, `score`, detail.
- open/audio: Gemini rubric grading → `points` per criterion, total, `score = points/max`, `correct = score >= 0.7`.
  Audio: send audio inline to Gemini; ask for transcript + rubric scores in one JSON response; store transcript.
  In **end** mode grade at submit anyway (reveal later). If Gemini is unavailable or errors: store `grader='none'`,
  `correct=NULL`; on reveal show rubric + model answer and let the user **self-grade** per criterion
  (`grader='self'`). Never block the test on Gemini.
- Prompt the judge with: item stem, rubric, model answer, candidate answer; require strict JSON
  `{criteria:[{criterion, points, max_points, justification}], overall_feedback}`; temperature 0; clamp points.

## Gemini (`gemini.py`)

- Key: env `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), else `var/secrets.json` written by the settings page
  (file mode 600; gitignored). Never log or export it. BYOK.
- REST: `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`, header
  `x-goog-api-key`. `generationConfig.responseMimeType = "application/json"` with `responseSchema`.
- Model: setting `gemini_model`, default `gemini-flash-latest`. On 404, list models
  (`GET /v1beta/models`), pick the newest stable `gemini-*-flash` (exclude lite/tts/live/image/preview/transcribe),
  persist it, retry once. Settings page lists models from the API for manual choice.
- Timeouts 60 s; 1 retry on 429/5xx with backoff. Record `grader_model` = model actually used.

## Telemetry (client → server)

`static/runner.js` measures with `performance.now()` relative to item render and posts on submit:
`time_to_first_ms`, `time_to_submit_ms`, `active_ms` (exclude time while `document.hidden` or window blurred),
`answer_changes`, `focus_losses`, `paste_count`, `runs`, `confidence`. Also batch-posts raw events
(`/api/events`, every 5 s and on submit/unload via `sendBeacon`): `render`, `select`, `deselect`, `input` (debounced,
length only — not content), `paste`, `blur`, `focus`, `visibility`, `run_sql`, `run_code`, `audio_start`,
`audio_stop`, `flag`, `reveal`, `feedback_open`. Server also stamps `presented_at` when it serves the item and
`submitted_at` on submit, so server-side durations exist even without JS.

## Feedback

💬 button on every item page (during and after) and on review/progress pages: category select + text.
Stored with `item_id`, `item_version`, `session_id`, and `context_json` (position, mode, whether answered, the
user's answer and correctness if already revealed). Never blocks the runner (modal, keyboard-friendly).

## Exports (`/export`)

- `feedback.jsonl` — one object per feedback row, **with a snapshot of the item stem/options** so it can be pasted to
  an LLM to fix items. Also `feedback.md` (human readable, grouped by item).
- `fde-data-{installation_id}-{date}.zip` — `manifest.json` (installation_id, schema_version, app_version,
  bank_fingerprint, export time), `sessions.jsonl`, `session_items.jsonl`, `responses.jsonl`, `events.jsonl`,
  `feedback.jsonl`, `item_versions.jsonl` (metadata + hash; content included). This is the future upload format.
- Response rows in exports never contain the API key; audio files are excluded by default (checkbox to include).

## Quality bar

- `pytest` green: migrations idempotent; sync (new version, retired item, changed-hash warning); assembly
  (scope filter, weighting, priority ordering, determinism with seed, shortfall); grading per type; HTTP flow for
  both modes using FastAPI `TestClient` with the fixture bank (`FDE_BANK_DIR` override); export round-trip; Gemini
  client with mocked transport (success, 404 fallback, failure → self-grade path).
- Clean, accessible UI: readable typography, light/dark via `prefers-color-scheme`, works at 1280px and phone width,
  no external network at runtime except Gemini.
