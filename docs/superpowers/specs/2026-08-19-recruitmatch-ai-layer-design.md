# RecruitMatch Copilot AI Layer Design

**Date:** 2026-08-19  
**Status:** Approved  
**Scope:** Add production-shaped LLM extraction, tenant-scoped recruiting RAG, hybrid matching, grounded explanations, model observability, and comparative evaluation to the existing RecruitMatch modular monolith.

## 1. Objective

Turn RecruitMatch from a deterministic recruiting workflow into an enterprise-oriented AI application without weakening its existing reliability guarantees. The AI layer must improve semantic understanding and recruiter assistance while keeping decisions explainable, evidence-bound, tenant-isolated, measurable, and available when model services fail.

The product remains a decision-support tool. It never automatically hires or rejects a candidate, and it must not use gender, age, photo, ethnicity, marital status, fertility status, or similar sensitive traits as matching inputs.

## 2. Success Criteria

The implementation is successful when:

1. Recruiters can upload tenant-owned recruiting policies, interview guides, and competency standards in PDF, DOCX, or TXT format.
2. The system chunks and embeds resumes, immutable JD versions, and recruiting knowledge with a local `BAAI/bge-small-zh-v1.5` embedding model.
3. An OpenAI-compatible model can extract a structured resume profile whose non-null facts carry verifiable source evidence.
4. Matching supports versioned `rules-v1` and `hybrid-v1` modes. The rule score remains deterministic; the model contributes only a bounded semantic-project score.
5. Generated explanations and interview questions contain citations that resolve to authorized source chunks.
6. Missing API keys, model timeouts, rate limits, malformed output, or weak retrieval evidence degrade safely to deterministic behavior.
7. Model calls expose model, prompt version, latency, token usage, estimated cost, status, and fallback reason without persisting full resume text, API keys, or full prompts.
8. A repeatable offline harness compares rule, LLM-assisted, and hybrid behavior using extraction quality, recommendation quality, grounding, latency, and cost metrics.
9. Tests run without network access by using fake model and embedding implementations.

## 3. Chosen Architecture

Use a hybrid architecture rather than pure LLM ranking or a multi-agent workflow.

```text
resume / JD / policy upload
        |
safe extraction and tenant-scoped persistence
        |
chunking -> local embedding -> tenant vector index
        |
LLM structured resume extraction -> evidence validation -> rule fallback
        |
rules-v1 base score + bounded semantic project score
        |
Top 3 candidates -> evidence retrieval -> grounded explanation
        |
citation validation -> persisted result -> recruiter feedback
```

Reasons:

- Deterministic rules provide reproducibility and a reliable no-model baseline.
- LLM extraction handles varied resume language better than keyword-only parsing.
- RAG is used only where source knowledge materially improves the task: evidence-backed explanations, policy-aware interview guidance, and semantic project comparison.
- A multi-agent topology is deliberately excluded because it adds orchestration and context-loss risk without a justified business boundary.

## 4. Component Boundaries

### 4.1 ModelGateway

`ModelGateway` is the only component allowed to call an external chat model. Its production adapter uses an OpenAI-compatible API configured by environment variables; tests use a deterministic fake.

Responsibilities:

- request timeouts and bounded retry policy;
- structured JSON response requests and Pydantic validation;
- normalized error categories for timeout, rate limit, authentication, transport, and invalid output;
- token, latency, model, and cost metadata;
- prompt version identifiers;
- no logging of API keys or full prompt bodies.

It does not know about database sessions, HTTP requests, or recruiting business rules.

### 4.2 LLMResumeParser

`LLMResumeParser` implements the existing resume parser protocol. It requests skills, experience duration, project facts, education, and evidence spans. Every extracted fact must be supported by a substring of the original resume.

Validation rules:

- evidence offsets must be within the normalized source text;
- evidence text must equal the indicated source slice;
- unsupported facts are removed rather than repaired by invention;
- sensitive traits are discarded;
- invalid model output receives one schema-repair retry;
- a second failure invokes `HeuristicResumeParser` and records `degraded_to_rules`.

### 4.3 KnowledgeDocumentService

This service manages tenant-owned recruiting knowledge documents. It reuses the existing safe artifact principles but stores knowledge in a separate namespace from resumes.

Supported content:

- recruiting policy;
- interview guide;
- role competency standard;
- technical assessment rubric.

Lifecycle states are `uploaded`, `processing`, `ready`, `failed`, and `inactive`. Deactivation immediately excludes the document's chunks from retrieval. Reprocessing creates a new index generation so readers never observe a partially replaced index.

### 4.4 RecruitingVectorIndex

The index accepts a replaceable embedder interface. The production default is the local Chinese BGE model; tests use deterministic vectors. Indexed sources are:

- resume text;
- immutable job-version JD text;
- active recruiting knowledge documents.

Every chunk contains tenant ID, source type, source ID, immutable source version or checksum, page/section metadata, character offsets, and index generation. Retrieval requires tenant ID and allowed source types at the repository boundary. Cross-tenant hits are therefore excluded before ranking, not filtered after retrieval.

The first implementation uses the existing local vector-store pattern with persisted metadata because the portfolio workload is single-node. The interface must allow replacement by pgvector or a managed vector database without changing domain services. This limitation is documented and not presented as distributed production storage.

### 4.5 HybridMatchingService

`rules-v1` remains available unchanged. `hybrid-v1` computes:

```text
final score = 0.80 * normalized rules-v1 score
            + 0.20 * validated semantic-project score
```

The semantic score is constrained to `[0, 100]` and must cite resume and JD evidence. A missing or invalid semantic score contributes zero and sets a fallback reason; it never prevents delivery of the rule recommendation.

The service persists the rule score, semantic score, final score, algorithm version, model version, prompt version, citations, and degradation status separately. This preserves auditability and makes rule-versus-hybrid evaluation possible.

### 4.6 GroundedExplanationService

For each Top 3 result, the service retrieves only authorized resume, JD, and optional policy chunks. It asks the model to produce:

- concise match rationale;
- verified strengths;
- missing or uncertain requirements;
- risk flags;
- suggested interview questions.

Every claim and question must reference one or more citation IDs supplied in the prompt. Post-generation validation rejects unknown citation IDs. Claims without citations are removed. When no retrieval result clears the configured threshold, the service does not call the model and returns an explicit insufficient-evidence response.

### 4.7 ModelTrace and EvaluationHarness

`ModelTrace` records operation type, tenant, model, prompt version, status, latency, input/output token counts, estimated cost, fallback reason, and correlation identifiers. It stores hashes or identifiers for source context, not full resume text or full prompts.

The evaluation harness compares:

- `rules-v1`;
- LLM extraction plus rule matching;
- `hybrid-v1`.

Metrics include structured-field precision/recall, Top-1 accuracy, Top-3 recall, citation coverage, citation validity, unsupported-claim rate, p50/p95 latency, token use, and estimated cost. Synthetic data remains explicitly labelled. Real performance claims require separately reviewed, anonymized recruiter-labelled data.

## 5. Persistence Model

Add tenant-scoped tables for:

- `knowledge_documents` — metadata, checksum, lifecycle, type, artifact reference;
- `knowledge_chunks` — source offsets, content, embedding reference, index generation, active status;
- `prompt_versions` — operation, version, schema version, status, non-secret template metadata;
- `ai_evaluation_runs` — dataset, algorithm/model/prompt versions, aggregate metrics, timestamps;
- `ai_evaluation_cases` — optional per-case outputs and failure categories without raw sensitive production documents.

Extend existing match persistence with nullable rule score, semantic score, grounding status, citation metadata, and fallback reason. Existing records and `rules-v1` APIs remain valid after migration.

## 6. API and User Experience

Add versioned endpoints for:

- create, list, inspect, deactivate, and re-index knowledge documents;
- report AI/model readiness independently from database readiness;
- request a `rules-v1` or `hybrid-v1` match;
- inspect safe model traces and evaluation summaries.

The recruiter console adds:

1. **AI status** — configured model, embedding provider, enabled state, latest latency, and degradation status;
2. **Recruiting knowledge base** — upload, status, document type, re-index, and deactivate controls;
3. **AI recommendation detail** — rule score, semantic contribution, final score, citations, model/prompt version, and fallback reason.

The existing endpoints remain backward compatible. AI features are additive and disabled safely when no key is configured.

## 7. Configuration

Use explicit settings for:

- AI feature enablement;
- OpenAI-compatible base URL, API key, and model;
- model timeout and retry count;
- prompt versions;
- embedding model and index directory;
- retrieval count and minimum score;
- maximum evidence characters;
- per-operation cost table used only for estimates.

Startup must not require a model key. Database readiness and AI readiness are separate signals so a model outage does not mark the core application unavailable.

## 8. Security and Privacy

- Tenant ID is mandatory in document, chunk, retrieval, trace, and evaluation repositories.
- Source authorization is checked before retrieval and again before citation resolution.
- API keys are read from environment variables and never persisted.
- Full resumes, full prompts, and model responses are excluded from operational logs and traces.
- Only the minimum retrieved evidence is sent to the model.
- Upload MIME, extension, size, path, and checksum validation applies to knowledge documents.
- Sensitive recruiting traits are removed before matching and excluded from prompts.
- The UI states that results support recruiters and do not make employment decisions.

## 9. Failure Semantics

| Failure | Behavior |
|---|---|
| Missing API key / AI disabled | Run rule parser and `rules-v1`; expose disabled status |
| Model timeout, rate limit, or transport failure | Bounded retry, then rule fallback with trace |
| Invalid structured output | One repair request, then heuristic parser fallback |
| Unsupported extracted fact | Remove fact and record validation count |
| Embedding failure | Mark indexing failed; keep prior active generation |
| Retrieval below threshold | Do not generate; return insufficient evidence |
| Unknown or unauthorized citation | Reject the claim and record grounding failure |
| Knowledge document deactivated | Exclude its chunks immediately |
| Model explanation failure | Preserve match scores and return deterministic explanation |

## 10. Testing Strategy

Testing follows dependency inversion and requires no external network:

- unit tests for schema validation, evidence offsets, citation validation, score bounds, fallback decisions, and tenant filters;
- repository tests for knowledge lifecycle, index generation, prompt versions, trace privacy, and migrations;
- API tests for upload, deactivation, re-indexing, AI readiness, hybrid matching, and cross-tenant access;
- contract tests for the OpenAI-compatible adapter using mocked responses;
- regression tests proving the default no-key startup and `rules-v1` behavior remain intact;
- offline evaluation tests comparing versioned outputs and verifying that generated metrics are reproducible;
- UI smoke tests for knowledge management, AI status, grounded results, and fallback display;
- Ruff, migration-to-head, full pytest, evaluation regeneration, and diff checks in CI.

## 11. Delivery Boundaries

Included:

- one OpenAI-compatible chat-model adapter;
- one local BGE embedding adapter;
- tenant-scoped local vector-index implementation behind an interface;
- PDF, DOCX, and TXT recruiting knowledge;
- structured extraction, hybrid ranking, grounded explanations, model traces, evaluation, APIs, UI, docs, and tests.

Excluded from this iteration:

- autonomous hiring decisions;
- multi-agent orchestration;
- OCR for scanned documents;
- antivirus scanning service;
- managed vector database or distributed index;
- real recruiter-labelled production dataset;
- fairness certification or legal-compliance certification;
- high-availability deployment and real SLA claims.

These exclusions must remain visible in the README so the portfolio does not overstate production evidence.

## 12. Documentation and Resume Claims

The README will explain why RAG is used, how citations are enforced, how failures degrade, how to configure any OpenAI-compatible provider, and how to reproduce evaluation results. Resume bullets may claim implementation and measured repository metrics only. They must not claim real users, hiring outcomes, production availability, legal compliance, or real-world accuracy without supporting evidence.

