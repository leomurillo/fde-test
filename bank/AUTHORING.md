# Authoring guide

Read with [`SCHEMA.md`](SCHEMA.md), [`../docs/taxonomy.md`](../docs/taxonomy.md) and [`../data/terminology.yaml`](../data/terminology.yaml).

The candidate is a **senior engineer preparing for an FDE assessment**. They are good at test-taking: if wrong
options can be eliminated without domain knowledge, the item is worthless. Every item must make them *think*.

## The process for every choice item (do it, don't skip steps)

1. **Target.** Pick the subtopic and one specific concept from its "key concepts". Decide the cognitive level
   (prefer `apply` / `analyze` / `evaluate`; `recall` ≤ 10% of a file).
2. **Scenario.** Write a short, concrete situation an FDE actually meets (client, system, constraint, symptom).
   Two to five sentences. Include the details that make one answer *best* — and nothing that gives it away.
3. **Correct answer.** Write it, and write `why`.
4. **Confounder design (before writing distractors).** List the 3–4 most common *misconceptions* or
   *partial truths* a competent-but-wrong engineer would act on here. Good sources:
   - the right move in a *different* context (correct in general, wrong here because of a stated constraint);
   - the right idea at the wrong time / wrong layer / wrong owner;
   - a real best practice that addresses a *secondary* symptom, not the root cause;
   - an over-engineered option (more autonomy, more infra) when simpler wins — or the reverse;
   - a mainstream-sounding term used slightly wrong;
   - a book-specific framework treated as a universal rule.
5. **Distractors.** Turn each misconception into an option that a smart person could defend. Record its
   `misconception` label and a specific `why` it loses *in this scenario*.
6. **Parity check.** All options: same grammatical form, similar length (±30%), similar specificity and
   technical vocabulary, same level of hedging. Then ask: *could I pick the answer by elimination, length,
   absolute words, or "sounds most like a textbook"?* If yes, rewrite.

## Hard rules
- No joke or obviously-unsafe options. No "all/none of the above". No positional references.
- Avoid absolute words (`always`, `never`, `only`) as tells; if used, use them in correct and incorrect options alike.
- The correct option must not be systematically the longest or most detailed. Vary it: sometimes the shortest is right.
- No grammatical cues between stem and options (a/an, singular/plural).
- One defensibly-best answer. If experts could reasonably disagree, add the deciding constraint to the stem.
- Mainstream terminology only. Never test a label coined by either handbook (see `data/terminology.yaml`
  `book-coined`); test the underlying idea in plain language. Vendor product names only when the concept is
  inherently vendor-neutral and the name is incidental.
- No trivia on version numbers, prices, or release dates that will rot. Arithmetic items supply their own numbers.
- `multi`: 2–3 correct out of 4–6. Incorrect options must be as tempting as for `single`.
- Be technically correct as of 2026. When unsure of a fact, don't write the item.
- English, concise, no filler. Markdown allowed (code spans, short code blocks, lists). Source line wraps render as
  spaces; use a blank line or a trailing `\` when a break must show (see SCHEMA.md).

## Open-text and audio items
- A realistic prompt with constraints (time pressure, a skeptical stakeholder, conflicting goals).
- Rubric rewards what CandiTech-style graders reward: bottom line first, explicit trade-offs, a concrete plan,
  risks and how to verify, audience-appropriate language. Each criterion has graded `guidance` (full / partial / zero).
- `model_answer` is what a strong senior FDE would actually say in the time allowed (≈ 150–300 words for open,
  ≈ 2 minutes spoken for audio).

## Numeric items
- Provide every number needed; ask for one value; state the unit and rounding. Tolerance should accept
  correct-method answers with sensible rounding and reject common wrong methods (e.g. forgetting output tokens).
  Put the common wrong methods in the `explanation`.

## SQL and code items
- Realistic FDE data: logs of LLM calls, tickets, eval results, documents, permissions, webhooks, queues.
- SQL: SQLite dialect; window functions and CTEs allowed. Test the result with your reference before writing it down.
- Code: a small, self-contained function (≤ 40 lines reference) about a real FDE concern (backoff with jitter
  bounds, idempotency keys, RRF, chunking, schema validation, token budgeting, HMAC verification with `hmac`).
  Hidden tests cover edge cases.

## Self-check before finishing a file
```bash
python -m app.bank --check bank/items/<your-file>.yaml
python -m app.bank --lint  bank/items/<your-file>.yaml
```
Fix every error. Aim for: correct-is-longest rate ≤ 35% on single items; zero banned patterns.
