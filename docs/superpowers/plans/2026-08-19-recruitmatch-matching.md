# RecruitMatch Explainable Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recommend the three best active enterprise jobs for a parsed resume with deterministic dimension scores, source evidence, risk flags, durable runs/results, and recruiter feedback.

**Architecture:** Keep matching as a pure engine over `ResumeProfile` and immutable `JobVersion` profiles, then persist its output through a tenant-scoped orchestration service. The baseline is deterministic and CI-safe; later model enhancement must preserve the same validated result schema and fall back to this engine.

**Tech Stack:** Python, Pydantic v2, SQLAlchemy 2, FastAPI, Alembic, pytest

**Spec:** `docs/superpowers/specs/2026-08-19-recruitmatch-copilot-design.md`

## Global Constraints

- Return suitability guidance, never hire/reject decisions.
- Match only active jobs owned by the authenticated tenant.
- Every matched skill must reference exact resume evidence.
- Missing information is `uncertain`, not inferred as false.
- Results reference immutable job versions and an explicit algorithm version.
- Feedback never mutates historical scores.

---

### Task 1: Match and feedback persistence

**Files:**
- Modify: `app/domain/enums.py`
- Create: `app/models/matching.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/20260819_03_matching.py`
- Test: `tests/matching/test_match_models.py`

**Interfaces:** Produces `MatchStatus`, `FeedbackAction`, `MatchRun`, `MatchResult`, and `Feedback`.

- [ ] Write a failing test proving a result references a concrete `job_version_id` and feedback appends without changing it.
- [ ] Run `python -m pytest tests/matching/test_match_models.py -v` and observe missing models.
- [ ] Implement tenant/run/result/feedback tables, relationships, uniqueness for `(run_id, rank)` and one latest feedback record per result/user pair.
- [ ] Run the model test and a fresh `alembic upgrade head`.
- [ ] Commit with `git commit -m "feat: add explainable match persistence"`.

### Task 2: Pure deterministic matching engine

**Files:**
- Create: `app/matching/__init__.py`
- Create: `app/matching/schemas.py`
- Create: `app/matching/engine.py`
- Test: `tests/matching/test_matching_engine.py`

**Interfaces:** Produces `CandidateJob`, `DimensionScores`, `MatchRecommendation`, and `MatchingEngine.rank(profile, jobs, top_k=3)`.

- [ ] Write failing tests for Top-3 order, required/preferred skill scoring, experience uncertainty, and exact evidence.
- [ ] Run `python -m pytest tests/matching/test_matching_engine.py -v` and observe missing engine.
- [ ] Implement algorithm `rules-v1`: skills score is `0.8 * required coverage + 0.2 * preferred coverage`; experience score is capped ratio or `0.5` when unknown; project score is keyword overlap; configured weights are normalized; missing required skills add `hard_requirement_gap`; unknown experience adds `experience_unknown`.
- [ ] Verify engine tests pass and results contain no protected attributes or decision labels.
- [ ] Commit with `git commit -m "feat: add evidence based matching engine"`.

### Task 3: Tenant-scoped matching orchestration

**Files:**
- Create: `app/repositories/matching.py`
- Create: `app/services/matching.py`
- Test: `tests/matching/test_matching_service.py`

**Interfaces:** Produces `MatchingService.run(principal, resume_id) -> MatchRun`, `get_run`, and `latest_for_resume`.

- [ ] Write failing tests proving only active same-tenant jobs participate, Top 3 persist, and cross-tenant resume IDs return not found.
- [ ] Run service tests and observe missing service.
- [ ] Load succeeded resume profile/text, active latest job versions, call `MatchingEngine`, and persist one run plus ranked results in a transaction using `algorithm_version="rules-v1"` and `prompt_version="none"`.
- [ ] Verify service and all tenant isolation tests pass.
- [ ] Commit with `git commit -m "feat: persist tenant scoped recommendations"`.

### Task 4: Match and feedback APIs

**Files:**
- Create: `app/api/v1/matching.py`
- Modify: `app/api/v1/router.py`
- Modify: `app/api/v1/schemas.py`
- Create: `app/repositories/feedback.py`
- Create: `app/services/feedback.py`
- Test: `tests/matching/test_matching_api.py`

**Interfaces:** Produces `POST/GET /api/v1/resumes/{id}/matches`, `GET /api/v1/match-runs/{id}`, and `POST /api/v1/match-results/{id}/feedback`.

- [ ] Write a failing API test that creates three active jobs, uploads one resume, receives ordered Top 3 evidence, submits `confirm/reject/reassign`, and verifies another tenant receives 404.
- [ ] Run the API test and observe 404 routes.
- [ ] Implement response schemas, routes, tenant-scoped result lookup, feedback validation, and audit-friendly immutable feedback rows.
- [ ] Run `python -m pytest tests/matching -q && python -m pytest -q && python -m ruff check app tests scripts`.
- [ ] Commit with `git commit -m "feat: deliver explainable job recommendations"`.

## Plan Self-Review

- Covers immutable run/result persistence, deterministic scoring, Top 3, evidence, missing/uncertain/risk output, tenant filtering, feedback, migration, API, and tests.
- Defers embedding recall and LLM enhancement until evaluation proves the deterministic baseline needs them; the provider boundary remains the engine schema.
- All persisted results name `rules-v1` and concrete job versions, preventing historical drift.
- No incomplete implementation markers remain.
