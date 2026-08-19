# RecruitMatch Hybrid AI Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver versioned hybrid matching, comparative AI evaluation, recruiter-facing AI controls, and honest portfolio documentation.

**Architecture:** rules-v1 remains unchanged and forms 80% of hybrid-v1; HybridMatchingService adds a citation-backed semantic project score of at most 20%. Persisted score components and model metadata feed EvaluationHarness for reproducible comparison and a transparent recruiter UI.

**Tech Stack:** Python 3.9+, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, pytest, vanilla HTML/CSS/JavaScript.

**Spec:** docs/superpowers/specs/2026-08-19-recruitmatch-ai-layer-design.md

## Global Constraints

- Requires the AI foundation and recruiting RAG plans.
- Existing rules-v1 clients remain compatible.
- hybrid-v1 = 0.80 * rule score + 0.20 * validated semantic-project score.
- Semantic scoring requires resume and JD citations; invalid scores contribute zero.
- Synthetic results are labelled synthetic_ai.
- UI shows fallback state and human decision ownership.

---

### Task 1: Hybrid Match Persistence

**Files:**
- Modify: app/models/matching.py
- Modify: app/matching/schemas.py
- Create: alembic/versions/20260819_07_hybrid_matching.py
- Test: tests/matching/test_hybrid_match_models.py

**Interfaces:**
- Produces nullable rule_score, semantic_score, grounding_status, fallback_reason, and citations on MatchResult.
- Produces HybridMatchRecommendation with those fields.

- [ ] **Step 1: Write backward-compatible round-trip tests**

~~~python
def test_rules_result_allows_empty_ai_fields(session, match_run):
    result = MatchResult(job_version_id="jv", rank=1, total_score=.8, dimension_scores={})
    match_run.results.append(result)
    session.commit()
    assert result.semantic_score is None

def test_hybrid_components_round_trip(session, match_run):
    result = MatchResult(rule_score=.75, semantic_score=.9, total_score=.78, citations=[{"id": "c1"}])
    match_run.results.append(result)
    session.commit()
    assert result.total_score == .78
~~~

- [ ] **Step 2: Run and confirm red state**

Run: .venv/bin/pytest tests/matching/test_hybrid_match_models.py -q  
Expected: FAIL because hybrid fields are absent.

- [ ] **Step 3: Add fields and migration**

Keep columns nullable for old rows, use list defaults only for new Python objects, preserve cascades, and index (match_run_id, grounding_status).

- [ ] **Step 4: Verify and commit**

~~~bash
TASK_DB=$(mktemp -d)/hybrid.db
DATABASE_URL="sqlite:///$TASK_DB" .venv/bin/alembic upgrade head
.venv/bin/pytest tests/matching/test_hybrid_match_models.py tests/matching/test_match_models.py -q
git add app/models/matching.py app/matching/schemas.py alembic/versions/20260819_07_hybrid_matching.py tests/matching/test_hybrid_match_models.py
git commit -m "feat: persist hybrid matching evidence"
~~~

### Task 2: Bounded Semantic Scoring

**Files:**
- Create: app/ai/semantic_matching.py
- Create: app/matching/hybrid.py
- Test: tests/ai/test_semantic_matching.py
- Test: tests/matching/test_hybrid_engine.py

**Interfaces:**
- Produces: SemanticProjectScore(score, rationale, resume_citation_ids, job_citation_ids).
- Produces: SemanticMatcher.score(tenant_id, resume_id, job_version_id) -> SemanticProjectScore | None.
- Produces: HybridMatchingEngine.rank(profile, jobs, tenant_context, top_k=3).

- [ ] **Step 1: Write formula, bounds, and citation tests**

~~~python
def test_hybrid_formula_is_fixed():
    assert combine_scores(.70, .90) == .74

def test_score_requires_both_source_types():
    score = SemanticProjectScore(.95, "fit", ["r1"], [])
    assert validate_semantic_score(score, {"r1", "j1"}) is None

def test_model_score_is_clamped():
    assert combine_scores(.5, 2.0) == .6
~~~

- [ ] **Step 2: Run and confirm red state**

Run: .venv/bin/pytest tests/ai/test_semantic_matching.py tests/matching/test_hybrid_engine.py -q  
Expected: FAIL because semantic and hybrid engines are absent.

- [ ] **Step 3: Implement semantic validation and combination**

Retrieve resume and selected JD chunks, request a [0,1] score plus citations, and require at least one authorized citation of each source type. Use round(0.8 * rule + 0.2 * semantic, 4). Missing semantic output contributes zero with an explicit fallback reason. Re-sort and return Top 3.

- [ ] **Step 4: Verify and commit**

~~~bash
.venv/bin/pytest tests/ai/test_semantic_matching.py tests/matching/test_hybrid_engine.py tests/matching/test_matching_engine.py -q
git add app/ai/semantic_matching.py app/matching/hybrid.py tests/ai/test_semantic_matching.py tests/matching/test_hybrid_engine.py
git commit -m "feat: rank jobs with bounded semantic evidence"
~~~

### Task 3: Hybrid Service and API

**Files:**
- Modify: app/services/matching.py
- Modify: app/api/v1/matching.py
- Modify: app/repositories/matching.py
- Test: tests/matching/test_hybrid_matching_service.py
- Modify: tests/matching/test_matching_api.py

**Interfaces:**
- Produces: MatchingService.run(principal, resume_id, mode="rules-v1") -> MatchRun.
- Produces: POST /api/v1/resumes/{id}/matches?mode=rules-v1|hybrid-v1.

- [ ] **Step 1: Write default-mode and hybrid tests**

~~~python
def test_default_mode_remains_rules(client, resume):
    body = client.post(f"/api/v1/resumes/{resume.id}/matches").json()
    assert body["algorithm_version"] == "rules-v1"

def test_hybrid_persists_components(ai_client, resume):
    first = ai_client.post(f"/api/v1/resumes/{resume.id}/matches?mode=hybrid-v1").json()["results"][0]
    assert first["rule_score"] is not None
    assert first["citations"]
~~~

- [ ] **Step 2: Run and confirm red state**

Run: .venv/bin/pytest tests/matching/test_hybrid_matching_service.py tests/matching/test_matching_api.py -q  
Expected: FAIL because mode selection is absent.

- [ ] **Step 3: Implement orchestration**

Accept only rules-v1 and hybrid-v1. Preserve the existing rule path. Hybrid mode invokes semantic scoring and grounded explanations, persists metadata, and returns match results even when model work degrades.

- [ ] **Step 4: Verify and commit**

~~~bash
.venv/bin/pytest tests/matching -q
git add app/services/matching.py app/api/v1/matching.py app/repositories/matching.py tests/matching
git commit -m "feat: expose resilient hybrid recommendations"
~~~

### Task 4: Comparative AI Evaluation

**Files:**
- Create: app/evaluation/ai_metrics.py
- Create: app/models/evaluation.py
- Create: app/repositories/evaluation.py
- Create: alembic/versions/20260819_08_ai_evaluation_runs.py
- Create: scripts/evaluate_ai_pipeline.py
- Create: evaluation/recruitmatch-ai-v1.json
- Create: evaluation/recruitmatch-ai-v1-results.json
- Test: tests/evaluation/test_ai_metrics.py
- Test: tests/evaluation/test_ai_evaluation_script.py

**Interfaces:**
- Produces: evaluate_ai_cases(cases) -> AIMetrics.
- Produces CLI modes rules-v1, llm-rules-v1, and hybrid-v1 with --fake-model.
- Produces: AIEvaluationRun and AIEvaluationCase keyed by tenant, dataset, algorithm, model, prompt, and embedding versions.

- [ ] **Step 1: Write exact grounding metric tests**

~~~python
def test_grounding_metrics():
    cases = [
        {"claims": [{"citation_ids": ["c1"]}], "authorized_ids": ["c1"]},
        {"claims": [{"citation_ids": ["bad"]}], "authorized_ids": ["c2"]},
    ]
    result = evaluate_grounding(cases)
    assert result.citation_coverage == 1.0
    assert result.citation_validity == .5
    assert result.unsupported_claim_rate == .5
~~~

- [ ] **Step 2: Run and confirm red state**

Run: .venv/bin/pytest tests/evaluation/test_ai_metrics.py tests/evaluation/test_ai_evaluation_script.py -q  
Expected: FAIL because AI metrics and script are absent.

- [ ] **Step 3: Implement deterministic dataset and metrics**

Commit at least 150 fixed synthetic cases with expected facts, job families, evidence spans, and policy citations. Aggregate extraction precision/recall, Top-1, Top-3, citation coverage/validity, unsupported-claim rate, p50/p95 latency, tokens, and estimated cost. Persist safe aggregates and per-case failure categories in AIEvaluationRun/AIEvaluationCase without production resume text. Include dataset, algorithm, model, prompt, and embedding versions.

- [ ] **Step 4: Prove deterministic output and commit**

~~~bash
.venv/bin/python scripts/evaluate_ai_pipeline.py --dataset evaluation/recruitmatch-ai-v1.json --output /tmp/ai-a.json --mode hybrid-v1 --fake-model
.venv/bin/python scripts/evaluate_ai_pipeline.py --dataset evaluation/recruitmatch-ai-v1.json --output /tmp/ai-b.json --mode hybrid-v1 --fake-model
diff -u /tmp/ai-a.json /tmp/ai-b.json
.venv/bin/pytest tests/evaluation -q
git add app/evaluation/ai_metrics.py app/models/evaluation.py app/repositories/evaluation.py alembic/versions/20260819_08_ai_evaluation_runs.py scripts/evaluate_ai_pipeline.py evaluation/recruitmatch-ai-v1.json evaluation/recruitmatch-ai-v1-results.json tests/evaluation
git commit -m "feat: evaluate grounded hybrid matching"
~~~

### Task 5: Recruiter Console AI Experience

**Files:**
- Modify: web/index.html
- Test: tests/ui/test_recruiter_console.py

**Interfaces:**
- Consumes: /api/v1/ai/status, /api/v1/knowledge-documents, and hybrid match fields.
- Produces: AI status, knowledge lifecycle, and grounded recommendation views.

- [ ] **Step 1: Write static UI contract tests**

~~~python
def test_console_contains_ai_surfaces():
    html = Path("web/index.html").read_text(encoding="utf-8")
    for text in ["AI 状态", "招聘知识库", "规则分", "语义补充分", "证据不足", "最终招聘决定由招聘人员作出"]:
        assert text in html
~~~

- [ ] **Step 2: Run and confirm red state**

Run: .venv/bin/pytest tests/ui/test_recruiter_console.py -q  
Expected: FAIL because AI surfaces are absent.

- [ ] **Step 3: Implement transparent UI**

Show enabled/model/embedding/degradation status; authenticated upload, list, re-index, and deactivate controls; rule, semantic, and final scores; algorithm/model/prompt versions; citation cards; fallback reason; and interview questions. Never display artifact paths or raw prompts.

- [ ] **Step 4: Verify desktop/mobile and commit**

Run the UI test, then use the local app to check desktop and 390px layouts with non-sensitive fixtures and fake model results.

~~~bash
.venv/bin/pytest tests/ui/test_recruiter_console.py -q
git add web/index.html tests/ui/test_recruiter_console.py
git commit -m "feat: show transparent ai recruiting workflow"
~~~

### Task 6: CI, Documentation, and Final Verification

**Files:**
- Modify: .github/workflows/ci.yml
- Modify: README.md
- Modify: docs/architecture.md
- Modify: docs/evaluation.md
- Modify: docs/resume-bullets.md
- Modify: docker-compose.yml
- Modify: .env.example
- Modify: tests/operations/test_health_and_metrics.py

**Interfaces:**
- Produces reproducible no-key and hybrid setup plus CI migration/test/lint/evaluation gates.

- [ ] **Step 1: Add operational tests**

Assert database readiness remains ready when AI is disabled, AI status exposes degradation without secrets, and analytics contain no resume or knowledge text.

- [ ] **Step 2: Run and confirm red state**

Run: .venv/bin/pytest tests/operations/test_health_and_metrics.py -q  
Expected: the new AI status assertions fail before implementation.

- [ ] **Step 3: Update CI and deployment**

Compose reads AI credentials only from environment. CI applies a fresh migration, runs Ruff and pytest, regenerates fake-model AI evaluation, and diffs it against the committed result.

- [ ] **Step 4: Update honest documentation**

Document hybrid architecture, RAG justification, citation enforcement, provider switching, fallback behavior, knowledge workflow, metric reproduction, privacy, and all spec exclusions. Resume bullets label synthetic results and make no real hiring claims.

- [ ] **Step 5: Run complete verification**

~~~bash
TASK_DIR=$(mktemp -d)
DATABASE_URL="sqlite:///$TASK_DIR/final.db" .venv/bin/alembic upgrade head
DATABASE_URL="sqlite:///$TASK_DIR/final.db" .venv/bin/python scripts/seed_job_templates.py
.venv/bin/python scripts/evaluate_ai_pipeline.py --dataset evaluation/recruitmatch-ai-v1.json --output "$TASK_DIR/ai-results.json" --mode hybrid-v1 --fake-model
diff -u evaluation/recruitmatch-ai-v1-results.json "$TASK_DIR/ai-results.json"
.venv/bin/pytest -q
.venv/bin/ruff check .
git diff --check
~~~

Expected: migrations reach head, 30 templates seed, result diff is empty, tests and Ruff pass, and diff check emits no output.

- [ ] **Step 6: Commit delivery**

~~~bash
git add .github/workflows/ci.yml README.md docs/architecture.md docs/evaluation.md docs/resume-bullets.md docker-compose.yml .env.example tests/operations/test_health_and_metrics.py
git commit -m "docs: deliver RecruitMatch hybrid ai platform"
~~~
