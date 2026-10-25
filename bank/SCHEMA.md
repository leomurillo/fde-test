# Item bank schema (v1)

Items live in `bank/items/<DOMAIN>-<slug>.yaml`, one file per taxonomy domain, e.g. `bank/items/D06-agents.yaml`.
Validate with:

```bash
python -m app.bank --check                      # whole bank
python -m app.bank --check bank/items/D06-agents.yaml
python -m app.bank --lint                       # distractor heuristics report
python -m app.bank --lock                       # record content hashes of new item versions (orchestrator only)
```

## File layout

```yaml
domain: D6            # must match the file name prefix (D06 → D6)
items:
  - id: D6-001
    ...
```

## Common fields (all types)

| Field | Req | Notes |
|---|---|---|
| `id` | ✓ | `D<n>-<nnn>`, unique across the bank, **never reused or renamed**. Prefix = file's domain. |
| `version` | ✓ | Integer ≥ 1. Bump whenever the content a candidate sees or is graded on changes (stem, options, answer, rubric, tests). Typo fixes count too. |
| `type` | ✓ | `single` · `multi` · `numeric` · `open` · `audio` · `sql` · `code` |
| `subtopic` | ✓ | Primary taxonomy tag (e.g. `agents.when`). Must belong to the file's domain. |
| `tags` | | Extra taxonomy tags from **any** domain. Use them for items that sit between topics. |
| `competencies` | | Cross-cutting ids: `x.ai-assisted`, `x.decomposition`, `x.python`, `x.sql`, `x.estimation`, `x.written-comms` |
| `difficulty` | ✓ | `1` easy · `2` medium · `3` hard (seed for IRT b-parameter) |
| `level` | ✓ | `recall` · `apply` · `analyze` · `evaluate` |
| `time_s` | ✓ | Expected seconds for a prepared candidate (MCQ 40–120, numeric 60–180, open 300–600, audio = prep+answer, sql/code 300–900). Choice items with `time_s` ≤ 70 are the **fast tier** and must fit a reading budget: stem + options ≤ 1.5 words per second of `time_s` (40 s → 60 words, 60 s → 90, 70 s → 105; `--lint` and a test enforce it). |
| `stem` | ✓ | Markdown. Scenario-first. For `multi`, do **not** write "select N" — the UI states the count. |
| `explanation` | ✓* | (*optional for open/audio) Markdown teaching note shown after answering: the principle, why the right answer wins. |
| `refs` | | List of URLs to primary/mainstream sources. Only URLs you are confident exist. |

**Line breaks in Markdown fields** (`stem`, `explanation`, `work`, `model_answer`) follow CommonMark. Wrap source
lines wherever you like (about 110 columns): a single newline is a soft wrap and renders as a space. Leave a blank
line to start a new paragraph, or end a line with `\` to force a line break, e.g. one calculation step per line
in `work`.

## Type-specific fields

### `single` — exactly one correct option, 4 or 5 options
### `multi` — 4–6 options, 2 or 3 correct, at least 2 incorrect

```yaml
options:
  - text: "Option text (markdown inline ok)"
    correct: true
    why: "Why this is right."
  - text: "Distractor"
    why: "Specific reason this is wrong in THIS scenario."
    misconception: "short label of the belief that makes it attractive"
```
Every option needs `why`. Every incorrect option needs `misconception`. Options are shuffled at runtime; never reference positions ("A", "the option above").

### `numeric`
```yaml
answer:
  value: 1.44
  tolerance: 0.05          # how far off is still correct
  tolerance_type: abs      # abs | rel  (rel: 0.05 = ±5%)
  unit: "USD"              # shown next to the input; optional
work: |                    # worked solution shown in feedback
  ...
```

### `open` (typed answer) and `audio` (spoken answer, CandiTech "Video" equivalent)
```yaml
rubric:                    # 3–6 criteria; points sum is the max score
  - criterion: "Names the core risk"
    points: 2
    guidance: "Full marks if ...; 1 if ...; 0 if ..."
model_answer: |            # an exemplary answer, shown in feedback
  ...
max_chars: 2000            # open only (default 2000)
prep_s: 30                 # audio only: preparation time before recording
max_s: 120                 # audio only: max recording length
```

### `sql` — graded by comparing result sets on SQLite
```yaml
sql:
  setup: |                 # CREATE TABLE + INSERT statements; ≤ 3 tables, ≤ 40 rows each
    ...
  reference: |             # one SELECT/WITH statement producing the expected result
    ...
  order_matters: false     # true only if the stem asks for an ordering
  float_round: 2           # floats rounded before comparison
```
The stem must describe the tables (or say "see the schema below" — the UI shows the `setup` DDL) and specify exactly which columns to return and in what order.

### `code` — Python function graded by hidden tests
```yaml
code:
  function: retry_delays
  starter: |               # shown in the editor
    def retry_delays(attempts: int, base: float, cap: float) -> list[float]:
        ...
  reference: |             # passes every case; never shown
    ...
  examples:                # visible to the candidate, runnable
    - args: [3, 0.5, 4.0]
      expected: [0.5, 1.0, 2.0]
  tests:                   # hidden; ≥ 4; include edge cases
    - args: [0, 1.0, 1.0]
      expected: []
    - kwargs: {attempts: 2, base: 1.0, cap: 1.5}
      expected: [1.0, 1.5]
      approx: 1.0e-9       # optional float tolerance
    - args: [[1, 2, 3]]
      expected: [3]
      unchanged: true      # optional: fail the case if the function mutates its arguments
      must_call: hmac.compare_digest   # optional: fail the case unless this module.function is called
  timeout_s: 5
```
Standard library only. Deterministic (no randomness, time, network). Arguments and expected values must be JSON-serializable.

## Validation rules (enforced by `python -m app.bank --check`)
- ids unique and well-formed; `subtopic` in the file's domain; `tags`/`competencies` exist.
- Choice items: option counts as above; banned texts: "all of the above", "none of the above", "both … and …" combos, "A and B".
- `numeric`: `value` numeric, `tolerance` ≥ 0.
- `open`/`audio`: ≥ 3 rubric criteria, `model_answer` present.
- `sql`: reference runs on setup and returns ≥ 1 row; read-only.
- `code`: reference passes all examples and tests within the timeout.
- Locked versions: if `bank/lock.json` has a hash for `id@version` and the content differs, the check fails ("bump the version").
