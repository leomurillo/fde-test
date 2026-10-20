# Research: CandiTech, the two handbooks, and existing FDE exams

Compiled 2026-10-20. This is the evidence behind the weights in [taxonomy.md](taxonomy.md). Where sources disagree, the mainstream reading wins. The term-by-term decisions are in [`data/terminology.yaml`](../data/terminology.yaml).

---

## 1. CandiTech: what the platform actually tests and how

CandiTech is a skills-assessment platform built around "job simulations". It has no public FDE test. The relevant facts are the formats it supports and how employers build tests.

### 1.1 Question types (from the official help center)

| Type | What the candidate does | Scoring |
|---|---|---|
| Multiple Choice | Pick one answer, or several with Multiple Select. Answers are shuffled by default. | Auto |
| Value | Enter one number, text or percentage | Auto |
| Open Text | Free text with a character limit | Manual star rating, or **AI auto-scoring** on Enterprise plans |
| Video | Record a timed answer to camera | Manual, or AI auto-scoring |
| Code | Write a function in a chosen language and run it against test cases | Auto, using hidden scored test cases; optional AI scoring |
| Code Project (Beta) | Multi-file project in a full IDE **with embedded AI coding assistants** | Auto, using hidden tests; optional custom AI scoring agent |
| SQL | Query the provided tables (3 at most is recommended) | Auto, by comparing the result set |
| Excel / Google Sheet | Edit a spreadsheet; specific cells are checked | Auto |
| File Upload | Upload an image, code or zip | Manual |

Source: [Question types](https://helpcenter.canditech.io/en/articles/8512793-question-types).

### 1.2 How employers build a test

- **The AI Assessment Builder takes a job title and the JD** (a URL or pasted text), summarises the role, and generates sections, question types, time limits and an estimated duration. It draws on library sections plus custom content, and the employer edits the result before publishing. ([Creating a new assessment](https://helpcenter.canditech.io/en/articles/8503162-creating-a-new-assessment), [Additional AI features](https://helpcenter.canditech.io/en/articles/13762334-additional-ai-features))
- Tests are made of **sections**, each with its own time limit. The library holds 500+ tests, including personality (Big Five, DISC, Enneagram), cognitive, and **AI-skills sections**. ([Technology](https://www.canditech.io/technology/))
- In the **AI-skills sections**, candidates use an embedded ChatGPT or Google Search, and recent announcements add Claude and Claude Code. The transcript goes to the employer, who sees how the candidate prompted, used the output, and caught hallucinations. According to a G2 reviewer, employers can also replay how written answers were typed. ([G2](https://www.g2.com/products/canditech), [AI skills blog](https://www.canditech.io/blog/ai-skill-assessment-ai-skills-test/))
- Rubric-based AI agents grade open text and video. The published "strong performer" traits are logical prompt structure, purposeful iteration, fast detection of inaccuracies, evidence-based corrections, plain-language explanations, and confidence in overriding AI output.
- **Proctoring** flags ChatGPT usage, copy-paste, tab switching and multiple devices. The platform therefore distinguishes AI use *inside* the provided assistant (which is evaluated) from AI use outside it (which is flagged).

### 1.3 Library tests close to FDE

There is no FDE test. The nearest are:

- [AI Literacy: Basic](https://www.canditech.io/test-library/ai-literacy-basic-knowledge-test/) and [Intermediate](https://www.canditech.io/test-library/ai-literacy-intermediate-knowledge-test/) (MCQ on generative AI, prompting, limitations, data privacy, safe use)
- [AI Prompting & Validation](https://www.canditech.io/test-library/ai-prompting-validation-data-preparation-test/) (open text, hard)
- [AI-assisted Code Generation](https://www.canditech.io/test-library/ai-assisted-code-generation-word-search-puzzle-test/) (use AI to solve a coding task in an unfamiliar language; aimed at senior, ML and AI engineers)

### 1.4 Third-party reviews

- Ratings are high but come almost entirely from **employers**: about 57 reviews on G2 and about 52 on Capterra, both around 5.0 ([G2](https://www.g2.com/products/canditech), [Capterra](https://www.capterra.com/p/267592/Canditech/reviews/)). Praise covers customisation, ATS integration and realistic tasks. Complaints are minor: G2's summary notes a lack of pre-built technical questions; other reviewers mention notification gaps and drag-and-drop UX.
- **I found no independent candidate accounts** on Reddit, Glassdoor or elsewhere, and none specific to FDE. Anything about the candidate experience is therefore inferred from the product docs.

### 1.5 What this means for the simulator

1. **Expect a mixed, sectioned, timed test.** The likely mix is MCQ plus open text or video, possibly SQL or code, and possibly an AI-assisted section.
2. **The JD is a likely input.** The builder is designed to start from the JD, so a test tailored to the Gorilla Logic posting is plausible even though it isn't certain. The taxonomy stays role-agnostic as requested, but every non-generic JD item is covered, so tailoring would not leave gaps.
3. **AI-assisted work is assessed as a skill.** The transcript is graded, not just the final answer. We add an `ai_assisted` format with an embedded Gemini chat, and grade both the transcript and the deliverable against CandiTech's published traits (cross-cutting competency `x.ai-assisted`).
4. **Open answers are rubric-graded by AI.** Our Gemini grader should use explicit rubrics, which also teaches you what a rubric rewards: bottom line first, explicit trade-offs, and verification.

---

## 2. The two handbooks

| | Richard Xu, *The FDE Handbook* (`en/`) | goday-org, *FDE Handbook* (`book_en/`) |
|---|---|---|
| Size | 41 files, about 75k prose words (4h 11m) | 11 files, about 7.9k prose words plus a lot of code (26m) |
| Nature | Field playbook and management book: role, delivery, adoption, business and career | Technical primer: consulting basics, then GCP, data, RAG, agents and evals with runnable code |
| Strengths | Discovery, the PoC-to-production gap, evals, permissions and approval, adoption, handoff, productization economics | Concrete mechanics: hybrid search with RRF, LLM-as-judge code, RAG metrics, VPC perimeters, workload identity |
| Weaknesses for our purpose | Coins many named frameworks ("eight walls", "five filters", MVD, "three layers of trust"…); one chapter is China-specific; six chapters are career and organisation | GCP-specific; data-engineering depth (Spark salting, dbt) beyond FDE scope; redefines some mainstream terms |

### 2.1 Where they agree (and match mainstream sources)

- An FDE is accountable for outcomes in the customer's environment, and field work should flow back into the product.
- Scope to a measurable outcome with real data early. Demos are not deployments.
- Evaluation sits on golden datasets, regression suites, LLM-as-judge and release gates. This matches Anthropic's [evals guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
- Agents inherit and must not exceed user permissions. Approvals and audit trails are prerequisites for production.

### 2.2 Where they diverge from mainstream, and how we resolve it

| Topic | Book says | Mainstream | Resolution |
|---|---|---|---|
| **Dev vs Delta** | goday: an *architecture* split between core code (Dev) and client integration code (Delta) | Palantir's [original post](https://blog.palantir.com/dev-versus-delta-demystifying-engineering-roles-at-palantir-ad44c2a6e87) uses it for two *roles*. Devs build platform components ("one capability, many customers"). Deltas deploy to customers ("one customer, many capabilities"). Echo is the deployment-strategist role. | Test the role meaning. Test the code split separately as "core vs customer-specific code". |
| **RAG answer relevance** | goday: cosine(answer, query) | [RAGAS](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/answer_relevance/): generate N questions from the answer, then average their similarity to the original question | Use the RAGAS definition. |
| **"Data contract"** | Richard Xu: an access agreement covering scope, visibility, retention and audit | Data engineering: a schema, quality and SLA agreement between producers and consumers | Test Xu's idea as "data access agreement / DPA". |
| **Scoping unit** | Richard Xu: "Minimum Viable Deployment". goday: "Minimum Viable Value". | PoC, pilot, MVP, success metrics, time to value | Mainstream terms only. |
| **Eval lifecycle naming** | goday: "two-loop" (inner and outer) | Offline vs online evaluation | Mainstream terms only. |

Other coined labels (the four responsibilities, eight walls, five filters, three layers of trust, big vs small ledger, RaaS, the "trio", "Tu FDE"…) are listed in the terminology register as `book-coined`. Questions may test the idea behind a label but never the label itself.

### 2.3 Gaps in both books relative to the JD

Neither book covers these in useful depth: **MCP** (goday mentions A2A only), **agents-vs-workflows decision criteria**, **structured outputs and tool-calling mechanics**, **webhooks, queues and async patterns**, **OpenTelemetry**, **Kubernetes beyond one manifest**, **SDK/CLI developer experience**, and **SaaS integrations** (GitHub, Jira, Slack, Azure DevOps). For these the question bank will draw on primary sources: [Anthropic's Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents), the [MCP spec](https://modelcontextprotocol.io/specification), the [OWASP LLM Top 10](https://genai.owasp.org/llm-top-10/), and [OTel GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/).

---

## 3. Other FDE (and adjacent) exams

There is **no industry-standard FDE certification**. Several third-party programmes use the name, and they agree on themes more than structure.

| Exam | Format | Domains / topics | What we take from it |
|---|---|---|---|
| [GSDC CFDE](https://www.gsdcouncil.org/certified-forward-deployed-engineer) | 40 MCQ, 90 min, 65% to pass, closed book | 13 modules: mindset & problem structuring, production Python, backend & API design, data engineering, ML foundations, DL/NLP, prompt engineering & LLM workflows, RAG, agentic systems, UI prototyping, deployment/MLOps/cloud, client engagement, demo & storytelling | Breadth. Classic ML and deep learning appear here but **not** in the JD or either book, so we leave them out. |
| [AIU CFDE](https://aiu.ac/training/cfde/) | Capstone build reviewed by practitioners (Foundation); enterprise simulation plus oral defence (Professional) | Discovery, solution architecture, codebase navigation, data pipelines, LLM integration, prompt architecture, RAG, agents, deployment, production delivery, technical communication, rapid field execution; Professional adds legacy integration, zero-trust identity, air-gapped deployment, compliance, FinOps, governance | Supports performance-based formats such as `ai_assisted` and `code_project`, and the D8 and D10 content. |
| [ADaSci CFDE](https://adasci.org/certifications/certified-forward-deployed-engineer-cfde) | 60 questions, 1 hour, proctored | Not public | Format reference only. |
| [Adaface FDE test](https://www.adaface.com/assessment-test/forward-deployed-engineer-test) (closest to CandiTech) | 17 items, 40 min: 4 prompt engineering, 4 generative AI, 4 backend, 4 system design MCQs, plus 1 Python coding | Scenario MCQs, e.g. JSON prompt design, temperature, broker replication, load-balancer latency, sticky sessions, async UI notification | Evidence that a commercial skills test would give **about half** its weight to LLM topics and **about half** to backend/system design. This supports D4 + D9 + D11. |
| Anthropic Claude Certified Architect – Foundations (adjacent; [guide](https://rickhigh.substack.com/p/claude-certified-architect-the-complete)) | Reportedly 60 scenario MCQs, 120 min. These figures and the weights are third-party reports. | Agentic architecture & orchestration 27%, Claude Code config 20%, prompt engineering & structured output 20%, tool design & MCP 18%, context management & reliability 15% | Supports D6 as the largest domain and the tool/MCP and structured-output subtopics. We drop the vendor-specific Claude Code domain. |
| Palantir FDE interview ([guide](https://www.tryexponent.com/guides/palantir-forward-deployed-engineer-interview)) | Decomposition round: a vague real-world problem, no code. Plus coding and behavioural rounds. | Structured breakdown, user empathy, data reasoning, scoping, iterating under pushback | Supports D2 and the cross-cutting `x.decomposition` competency (open text and spoken). |

---

## 4. How the weights were set

For each domain, four signals were combined:

1. **JD emphasis.** Every non-generic JD item maps to at least one subtopic; `tools/taxonomy.py --check` enforces this. The JD's centre of gravity (the AI harness: orchestration, governance, observability, evaluation) pushes D6, D7, D8 and D11 up.
2. **Exam syllabi.** Topics present in most of GSDC, AIU, Adaface and CCA-F get more weight: LLM engineering, RAG, agents, deployment and client engagement. Topics present in only one get little or none; classic ML is dropped.
3. **Book coverage, discounted for coined theory.** Richard Xu's heavy coverage of role, business and career becomes only 6% (D1), because those chapters are mostly book-specific frameworks. Its engineering chapters (9–14) map directly to D6–D8 and D11.
4. **Format fit for CandiTech.** Domains that suit auto-graded MCQ, SQL or code get dense question banks. Domains that suit open text, video or AI-assisted work (D2, D3) keep meaningful weight but will be examined mostly through those formats.

The result: about 25% field craft, 46% AI engineering and 29% enterprise production. Weights are deliberately approximate (whole percentages), and the YAML is the single place to change them.

## 5. Open questions for the next turns

- **Exam blueprint for a simulated session.** A reasonable default for a 60–75 minute CandiTech-style run is 20 MCQ, 2 Value, 1 SQL, 1 Python, 2 open text, 1 spoken (timed text), and 1 AI-assisted scenario. Weights can then be confirmed against your own sense of the invite.
- **Does the invite say anything about duration, sections, or webcam/AI use?** If so, that sets the blueprint directly.
- **Gemini grading.** Each open-ended item needs a rubric, a reference answer and a model-answer exemplar. The `ai_assisted` format also needs a transcript rubric based on CandiTech's published traits.
