# Taxonomy backlog

Gaps the item authors and reviewers noticed while writing v1 of the bank. None blocked v1. Each is a candidate for
a new subtopic, a widened subtopic, or more items in a neighbouring one. Weights in `data/taxonomy.yaml` stay
unchanged until we decide.

| Gap | Nearest home today | Suggestion |
|---|---|---|
| Estimating and negotiating fixed-price / fixed-scope commitments; the commercial side of scope creep | between `role.economics` and `delivery.scoping` | Widen `delivery.scoping` to include estimation and change control |
| Incident communication to customers; postmortems | `adoption.handoff`, `obs.slo` | Add concepts to `obs.slo` (customer-facing incident updates, blameless postmortems) |
| Build vs buy vs partner decisions beyond TCO | `role.economics` | Add a concept line; 1–2 items |
| AI supply-chain risk (model, dataset, plugin/MCP server provenance) | `gov.guardrails` | New concept in D8 (OWASP LLM supply-chain entry) |
| Agent identity federated across organizations | `gov.identity` | Awareness-level concept |
| Multimodal and document inputs (images, scanned PDFs) to LLMs | `llm.fundamentals`, `rag.ingestion` | Concept line in D4 |
| Fine-tuning mechanics (data prep, LoRA-style adaptation, eval before/after) | `llm.model-selection` | Concept line; keep at decision level |
| Vector database operations (sharding, filtered ANN, quantization) | `rag.retrieval` | Concept line; numeric items |
| API contracts: OpenAPI, gRPC | `int.apis` | Concept line |
| Eval tooling and CI integration; LLM-judge cost | `eval.gates`, `obs.cost` | Concept lines |
| Prompt caching and batch pricing appear in both D4 and D11 | `llm.fundamentals` (mechanics), `obs.cost` (fleet economics) | Keep the ownership split explicit in AUTHORING.md |

Grader limitation also noted: `code` items cannot assert that a function *raises*; invalid-input behaviour is
specified as a return value. A future `raises:` field in code cases would fix it.
