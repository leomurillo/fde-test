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

The subtopics you tick are the whole scope. An item is drawn only when its own (primary) subtopic is ticked;
the secondary tags an item carries never pull it into another subtopic's test, so a test on one domain
contains only that domain's items. The number beside each subtopic, and the *Pool* figure in the preview,
count exactly the items you can draw from.

Runner shortcuts: `1`–`6` select options, `Enter` submits (and goes to the next item after a reveal),
`R` marks *Done reading*, `F` flags, `Ctrl+Enter` submits from a text box, `Ctrl+'` runs SQL/code. You can skip items and come back to
them before finishing; reopening a test resumes at the first item you have not reached.

**Copy as text.** The **Copy** button next to Feedback (on every runner item and on the results page) copies the
item as Markdown to paste into an AI chat: `id vN · subtopic · type`, the stem, and the options lettered A, B, C…
in the order you saw them. Before the answer is revealed it contains only the question; after the reveal
(real-time mode after you submit, or the results page) it also has your answer, the correct answer, the per-option
reasons and the explanation. It never contains your email or any id other than the item id and version.

**Ending a test.** The runner header has an **End test** menu with *Finish and see results* (it asks first if
items are unanswered, which score zero) and *Abandon test*. The dashboard shows a **Test in progress** card
with *Resume test* and *Abandon*. Abandoning asks for confirmation and closes the test without a results
page. Answers already given are kept: the test stays under Recent tests marked *Abandoned*, you can still open
its review, and its answers keep counting in Progress and in exports. Only unanswered items are dropped.

**Done reading.** Press *Done reading* (or `R`) the moment you finish your first read of the question and its
options. It is optional and only measures: the results page then shows your median first-reading time, your
reading speed (words of stem and options per minute), and how long you spent deciding after reading, next to how
many answers fit each item's budget. Use it to tell slow reading apart from slow deciding.

## AI assistant sidebar

Some assessments embed a chat assistant and hand the transcript to the hiring team, who judge how you worked with
AI. To practise that, tick **AI assistant** on **New test** (or set `ai_assistant: true` in a modeled-test file,
for the whole test or per part). The runner then has an **AI assistant** button that opens a Gemini chat beside the
item, using the same key as AI grading.

- The assistant is a plain general-purpose chat: it is not told it sits inside a test, and it does not see the
  question unless you type or paste it. One conversation runs through the whole test (or part).
- Every message is stored with the item that was on screen, whether you pasted text, and whether the message repeats
  the question's wording. `Enter` sends, `Shift+Enter` adds a line; runner shortcuts are off while you type there.
- When the test (or part) finishes, Gemini grades **AI use** from the transcript, your final answers and the answer
  keys, on four criteria worth 3 points each: prompting and context, critical evaluation of AI output, ownership of
  the final answer, and efficient, well-placed use. Each score comes with the exchange that justifies it.
- The results page shows the AI-use score, the evidence, one concrete improvement, the rubric and the full
  transcript; each item card also shows the conversation held on that item. *Grade again* re-runs the grading. In a
  modeled test, the test page shows each part's AI-use score.
- Without a key the assistant cannot answer, and an unused assistant is reported as "available but not used".

## Modeled tests (loaded from a file)

**Modeled tests** in the top bar loads a test you built by hand: a fixed list of items per part, each part with its
own clock, run back to back. The file lives on your machine, so a mock of a specific assessment never has to be
committed to this public repository: keep it outside the clone, or in `private/` (git ignores that folder).

```yaml
name: Practice run
mode: end              # end (default): results after the last part · realtime: reveal after every item
description: optional note shown on the test page
ai_assistant: false    # default for every part: the Gemini chat sidebar (see above)
parts:
  - title: Part 1
    time_limit_min: 8  # or time_limit_s; leave out for an untimed part
    items: [D4-101, D6-105, D2-103]
  - title: Part 2
    time_limit_min: 5
    ai_assistant: true          # this part only
    items: [D7-102, D9-104@1]   # id@version pins a specific version
```

- The file is checked against the bank when you load it; every problem (unknown or retired id, a version that was
  never stored, an item used twice, a bad time limit) is listed, and nothing is saved until the file is clean.
- Items keep the order you list and their versions are pinned at load time; options are shuffled per part.
- Each part starts when you press *Start* on the test page. Its clock starts on its first item and does not stop;
  when it runs out, the part ends and unanswered items score zero. You then land back on the test page, ready for
  the next part, the way a sectioned assessment moves from one section to the next.
- In `end` mode no answers are revealed, and part reviews stay locked, until the last part is done. The finished
  test page shows, per part, the time used, score and pace (including how many answers fit the part's time per
  item), with links to each part's review.
- Abandoning a part abandons the whole test; answers already given are kept.
- Fast-tier items (ids `-101` and up, `time_s` 40, 60 or 70) are the natural building blocks for timed
  multiple-choice parts. In the item browser on **Progress**, never-seen items show only metadata, so you can pick
  ids without spoiling them.

Spoken items emulate CandiTech's "Video" questions: a preparation countdown, then one take recorded with
the microphone that stops automatically at the time limit. Without a microphone you get a timed text box
in which you cannot edit what you already wrote.

- **Single take, enforced by the server.** When the preparation countdown starts, the page registers an
  attempt for that item. If the page is reloaded (or you come back to it) after that, the server refuses a
  new recording and shows only the timed written answer; the response is stored with `attempt_used: true`.
- **Audio format.** The browser decodes the recording and converts it to 16 kHz mono 16-bit PCM WAV before
  uploading, because WAV is a format Gemini documents. If decoding fails, the original recording
  (webm/ogg/mp4) is uploaded instead, and `encode_error` is stored with the response.

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
| `var/audio/{response_id}.wav` | spoken answers (webm/ogg/m4a only when WAV conversion failed) |
| `var/secrets.json` | Gemini key, only if saved from Settings |

The item bank is read from `bank/items/*.yaml` at startup (override with `FDE_BANK_DIR`). Invalid items are
skipped and listed under **Settings → Item bank**; use **Reload bank** there after editing the YAML.

## Exports

From **Export** (or **Feedback**):

- `feedback.jsonl`: one object per feedback note, with a snapshot of the exact item version it is about,
  ready to paste into an LLM to fix items. `feedback.md` is the same, human readable, grouped by item.
  Tick **Only open issues** next to the download buttons to leave out the notes you have marked *Resolved*, so a
  reviewer only sees what is still unaddressed (the files are then named `feedback-open.jsonl` / `feedback-open.md`;
  by URL: `/export/feedback.jsonl?show=open`). The box starts ticked on the Open tab and on Export, and unticked on
  the All tab. Mark a note resolved on the Feedback page once its item is fixed.
- `fde-data-{installation_id}-{date}.zip`: `manifest.json` plus `sessions`, `session_items`, `responses`,
  `events`, `feedback`, `item_versions`, `plans` (modeled tests), `ai_messages` (assistant transcripts) and
  `ai_assessments` (AI-use grades) as JSONL. Columns stored as JSON (`*_json`) appear decoded
  under the name without the suffix. Audio files are excluded unless you tick the checkbox. This is the
  format meant for pooling data from many installations later (`app.export.read_export` reads it back).
  It always holds every feedback note, resolved or not.

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
- A test pins `(item_id, version)` for every position when it is created, and never swaps them. After a
  bank revision, an in-progress test therefore keeps serving the old versions of items you have not opened
  yet. When that happens the runner and the dashboard's *Test in progress* card show "N unopened items have
  newer versions. Abandon and start a new test to get them." with an *Abandon* button. The count covers
  positions you have not opened or answered whose pinned version is older than the bank's current one
  (`items.current_version`). Swapping versions in place would break reproducibility, so abandoning and starting
  a new test is the way to get the revised items; the answers you already gave stay tied to the version you saw.
- Each session also records the app version, the bank fingerprint (sha256 over every `id@version:hash`),
  the taxonomy version, the assembler version and the random seed, so a test can be reproduced.
- Every row id is a UUIDv4 and each installation has an anonymous `installation_id`, so exports from
  many clones merge without collisions.

## What is measured (for later CAT/IRT calibration)

Per response: time to first interaction, time to submit, first-reading time (*Done reading*, optional), active
time (page visible and focused),
answer changes, focus losses, pastes, SQL/code runs, optional confidence (guess / unsure / sure),
flag and skip, plus server-side `presented_at` / `submitted_at` timestamps. Answer changes count
re-selections for single choice, de-selections for multiple select, and editing bursts separated by
1.5 s pauses for typed answers. Raw events (`render`, `select`, `input` with length only, `paste`, `blur`,
`visibility`, `run_sql`, `audio_start`, `reveal`, `read_done`, …) are batched to the `events` table every 5 s.

## Progress

Answers from abandoned tests count here like any other.

**Progress** shows, per domain and subtopic, the items in the bank, seen / never seen, last result right
or wrong, accuracy and median time. Below that is an item browser grouped by domain: domains are collapsed,
opening one loads its items 50 at a time, and you can filter by status (never seen, last wrong, last right,
flagged) and format. Each item shows its history. Items you have never
seen show metadata only, never their stem or answers, so they stay fresh for future tests.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # uses the fixture bank in tests/fixtures, never bank/items
```

Browser tests in `tests/e2e/` drive Chromium with a fake microphone: a full session in both modes, WAV
upload, reload after the countdown, and the progress browser. They are skipped automatically when the
`playwright` package or a Chromium build is missing. To enable them, run `playwright install chromium`.
