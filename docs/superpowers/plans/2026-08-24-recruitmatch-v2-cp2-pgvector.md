# RecruitMatch v2 Checkpoint 2 pgvector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace JSON/NumPy retrieval with tenant-authorized PostgreSQL/pgvector search, atomic generations, and separate current/historical citation resolution.

**Architecture:** A `RecruitingChunk` table stores normalized `vector(512)` embeddings for Resume, JobVersion, and KnowledgeDocument. A port exposes typed search and citation operations; the PostgreSQL adapter keeps authorization filters in the same SQL as distance ordering. Source rows own the active generation. The legacy `app/knowledge/index.py` remains available through Tasks 1–3 and is removed only in Task 4, where every production consumer and composition root moves together.

**Tech Stack:** PostgreSQL 16, pgvector 0.8+, SQLAlchemy 2, Alembic, psycopg 3, pytest.

**Spec:** `docs/superpowers/specs/2026-08-24-recruitmatch-productionization-design.md`

## Global Constraints

- Production migrations target real PostgreSQL/pgvector only. SQLite unit tests use ORM-created schemas, fakes, and test-only adapters; they never execute the pgvector Alembic revision.
- Embedding contract is 512 dimensions, L2-normalized, cosine distance, `vector_cosine_ops`.
- `embedding_model`, tenant, active state, source type, and authorized source IDs are mandatory search predicates.
- No Python-side tenant/authorization filtering and no bare `source_id` destructive operation.
- HNSW Recall claims require Exact-vs-HNSW benchmark evidence.
- Each task ends with a runnable tree and its own commit. No task may leave `app.main`, the Celery worker, or retained tests importing a deleted module.
- `app/knowledge/index.py` cannot be deleted in Tasks 1–3. Task 4 deletes it only after all old imports, `source_index` consumers, and production adapter injection have migrated in that same task.

---

### Task 1: Add a PostgreSQL-only pgvector schema and CI lane

**Files:**
- Create: `app/models/retrieval.py`
- Create: `alembic/versions/20260824_11_pgvector_recruiting_chunks.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_pgvector_migration.py`
- Modify: `app/models/__init__.py`
- Modify: `app/models/resumes.py`
- Modify: `app/models/jobs.py`
- Modify: `app/models/knowledge.py`
- Modify: `docker-compose.yml`
- Modify: `.github/workflows/ci.yml`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `tests/foundation/test_schema_upgrade.py`
- Modify: `tests/support/database.py`

**Interfaces:**
- Produces: `RecruitingChunk`, `active_index_generation`, `search_index_status`, `search_index_error_code`, `search_indexed_at` on all three source types.
- Consumes: the Checkpoint 1 bootstrap-only migration path and frozen Python image.
- CI boundary: static/unit checks never run Alembic; migration and schema evidence run only against the Compose PostgreSQL service using a pgvector 0.8+ image.

- [ ] **Step 1: Write PostgreSQL migration and SQLite-boundary tests**

`tests/integration/test_pgvector_migration.py` must run against the real Compose database and assert the extension version, vector type/dimension, all source-state columns, both uniqueness constraints, all required B-tree indexes, and the partial HNSW index. It also runs bootstrap twice and verifies a single Alembic head and 30 reference templates.

`tests/foundation/test_schema_upgrade.py` retains the application-startup-no-schema-write test but removes SQLite calls to `upgrade_database(..., head)`. URL precedence and migration idempotency move to the PostgreSQL integration fixture. `tests/support/database.py` remains test-only ORM setup and must not import or call Alembic.

Add a unit boundary test that replaces Alembic's upgrade entry point with a failing sentinel, calls `prepare_test_database()` on SQLite, and proves the sentinel is never reached.

- [ ] **Step 2: Confirm the missing PostgreSQL contract and clean unit boundary**

Run:

```bash
docker compose -p recruitmatch-cp2-task1 up -d postgres
docker compose -p recruitmatch-cp2-task1 run --rm bootstrap
docker compose -p recruitmatch-cp2-task1 run --rm test-integration \
  pytest tests/integration/test_pgvector_migration.py -q
docker compose run --rm --no-deps test-unit \
  pytest tests/foundation/test_schema_upgrade.py -q
```

Expected: the integration test fails because the vector extension/table and source state columns are absent; the unit boundary stays on SQLite and never runs Alembic.

- [ ] **Step 3: Implement a PostgreSQL-only model and migration**

Pin the Compose database to a pgvector 0.8+ PostgreSQL 16 image and add `pgvector>=0.3,<1` to runtime dependencies. Define `RecruitingChunk` with `Vector(512)` and the exact contracts from spec sections 5.1–5.3. The revision must check `op.get_bind().dialect.name == "postgresql"` before issuing PostgreSQL/vector DDL and fail with a clear error on any other dialect; do not add a SQLite no-op or stamp path that would make a pgvector migration appear successful.

Create the two uniqueness constraints, both full B-tree indexes, the document partial B-tree index, and the partial `vector_cosine_ops` HNSW index. Add source index-state fields consistently to Resume, JobVersion, and KnowledgeDocument. Do not backfill legacy JSON vectors and do not remove the legacy index yet.

- [ ] **Step 4: Split CI into unit and PostgreSQL integration evidence**

The unit/static job runs lock, Ruff, mypy, and SQLite/fake unit suites without `alembic upgrade head` or a bootstrap dependency. A separate PostgreSQL integration job starts the pgvector Compose service, runs the one-shot bootstrap, then runs `tests/integration/test_pgvector_migration.py` and the later `tests/retrieval` suite. Remove the existing `DATABASE_URL=sqlite:... alembic upgrade head` CI step.

Run:

```bash
docker compose -p recruitmatch-cp2-task1 run --rm bootstrap
docker compose -p recruitmatch-cp2-task1 run --rm test-integration \
  pytest tests/integration/test_pgvector_migration.py -q
docker compose run --rm --no-deps test-unit pytest tests/foundation tests/ai tests/matching -q
docker compose -p recruitmatch-cp2-task1 down -v
```

Expected: both bootstrap runs succeed on PostgreSQL, migration assertions pass, and the SQLite unit lane does not execute the pgvector revision.

- [ ] **Step 5: Commit the runnable schema and CI lane**

```bash
git add app/models alembic/versions/20260824_11_pgvector_recruiting_chunks.py \
  docker-compose.yml .github/workflows/ci.yml pyproject.toml uv.lock \
  tests/integration tests/foundation/test_schema_upgrade.py tests/support/database.py
git commit -m "feat: add PostgreSQL pgvector migration lane"
```

### Task 2: Implement the retrieval port, fake, and PostgreSQL adapter

**Files:**
- Modify: `app/retrieval/ports.py`
- Create: `app/retrieval/pgvector_index.py`
- Create: `tests/fakes/retrieval.py`
- Create: `tests/retrieval/test_pgvector_search.py`
- Create: `tests/retrieval/test_retrieval_contract.py`
- Retain unchanged: `app/knowledge/index.py`

**Interfaces:**
- Consumes: `RecruitingChunk`, normalized 512-dimensional embeddings, `EmbeddingIdentity`.
- Produces:
  - `SearchScope(tenant_id, source_types, authorized_sources)`;
  - `RecruitingVectorIndex.search(scope, query_embedding, embedding_model, top_k, min_score)`;
  - `resolve_active_citations(scope, citation_ids)`;
  - `resolve_historical_citations(tenant_id, citation_ids)`;
  - a fake implementing the same public port for SQLite unit tests.

- [ ] **Step 1: Write cross-tenant, authorization, model-space, and contract tests**

The real-adapter test must seed misleading closer vectors for another tenant, an unauthorized source, an inactive generation, and a different embedding model. The expected hit set is derived literally and contains only authorized rows. Run the same port contract against the fake and PostgreSQL adapter.

- [ ] **Step 2: Confirm the typed adapter is absent**

Run:

```bash
docker compose -p recruitmatch-cp2-task2 up -d postgres
docker compose -p recruitmatch-cp2-task2 run --rm bootstrap
docker compose -p recruitmatch-cp2-task2 run --rm test-integration \
  pytest tests/retrieval/test_pgvector_search.py tests/retrieval/test_retrieval_contract.py -q
```

Expected: FAIL importing `PgVectorRecruitingIndex` or on the missing secure query behavior.

- [ ] **Step 3: Implement one authorized SQL statement and both resolvers**

Search validates dimension and L2 norm, then applies tenant, active generation, embedding identity, source type, and authorized `(source_type, source_id, source_version)` predicates in the same SQL statement as cosine ordering. Apply `score = 1 - distance`, deterministic ID tie-breaking, `top_k`, and `min_score` without broadening the scope.

Active citation resolution uses the same authorized scope and current generation. Historical resolution omits active state but never tenant. Privacy-deleted evidence resolves nowhere. Keep `app/knowledge/index.py`; no production composition changes occur in this task.

- [ ] **Step 4: Verify exact/HNSW behavior and both implementations**

Run:

```bash
docker compose -p recruitmatch-cp2-task2 run --rm test-integration pytest tests/retrieval -q
docker compose run --rm --no-deps test-unit pytest tests/retrieval/test_retrieval_contract.py -q
docker compose -p recruitmatch-cp2-task2 down -v
```

Expected: fake and PostgreSQL contract tests agree; `EXPLAIN` evidence covers exact and HNSW paths without making an unsupported Recall claim.

- [ ] **Step 5: Commit the runnable adapter while retaining the legacy index**

```bash
git add app/retrieval tests/fakes/retrieval.py tests/retrieval
git commit -m "feat: search recruiting evidence with pgvector"
```

### Task 3: Make generation replacement atomic and fenced-ready

**Files:**
- Create: `app/retrieval/generations.py`
- Modify: `app/repositories/knowledge.py`
- Create: `tests/retrieval/test_generation_switch.py`
- Retain unchanged: `app/knowledge/index.py`, production composition roots, and old consumers

**Interfaces:**
- Consumes: inactive embedded `RecruitingChunk` rows and source-owned generation state.
- Produces: `GenerationWriter.stage()`, `fail()`, and `activate()`; Checkpoint 4 adds the Lease fencing predicate.

- [ ] **Step 1: Write failure-preserves-old-generation and concurrency tests**

Cover incomplete staging, embedding identity mismatch, injected rollback, concurrent activation, wrong-tenant source IDs, and success leaving exactly one active generation. Expectations query both the source row and chunk rows from PostgreSQL.

- [ ] **Step 2: Confirm current generation switching is not atomic**

Run:

```bash
docker compose -p recruitmatch-cp2-task3 up -d postgres
docker compose -p recruitmatch-cp2-task3 run --rm bootstrap
docker compose -p recruitmatch-cp2-task3 run --rm test-integration \
  pytest tests/retrieval/test_generation_switch.py -q
```

Expected: FAIL because the generation writer does not exist and current legacy replacement cannot provide the transaction contract.

- [ ] **Step 3: Implement stage-validate-activate as one PostgreSQL transaction**

`stage()` writes `is_active=false`. `activate()` locks the tenant-qualified Source, verifies the requested generation, staged count, citation completeness, and embedding identity, then deactivates old rows, activates new rows, and updates source generation/status in one transaction. `fail()` records a stable index error without changing the old active generation. Expose the future fencing argument at the boundary but do not implement CP4 Lease fields.

Because current retrieval requires `search_index_status = 'ready'`, `fail()` must preserve `ready` when the Source already has an active generation and record the failed refresh in `search_index_error_code`; only a Source with no active generation transitions to `failed`. This keeps the old generation searchable while still exposing the refresh failure.

This task deliberately does not wire production services and does not delete the legacy adapter. The new writer is independently runnable and covered; the coordinated producer/consumer cutover occurs in Task 4.

- [ ] **Step 4: Verify rollback and concurrent activation**

Run:

```bash
docker compose -p recruitmatch-cp2-task3 run --rm test-integration \
  pytest tests/retrieval/test_generation_switch.py tests/retrieval/test_pgvector_search.py -q
docker compose -p recruitmatch-cp2-task3 down -v
```

- [ ] **Step 5: Commit the runnable generation writer**

```bash
git add app/retrieval/generations.py app/repositories/knowledge.py \
  tests/retrieval/test_generation_switch.py
git commit -m "feat: switch recruiting index generations atomically"
```

### Task 4: Cut every production consumer and composition root over atomically

**Files:**
- Modify: `app/main.py`
- Modify: `app/tasks/celery_app.py`
- Modify: `app/ai/semantic_matching.py`
- Modify: `app/ai/citations.py`
- Modify: `app/ai/explanations.py`
- Modify: `app/services/matching.py`
- Modify: `app/services/resume_processing.py`
- Modify: `app/services/jobs.py`
- Modify: `app/services/source_index_backfill.py`
- Modify: `app/services/knowledge_processing.py`
- Modify: `app/api/v1/jobs.py`
- Modify: `app/api/v1/knowledge.py`
- Modify: `app/api/v1/matching.py`
- Modify: `scripts/evaluate_ai_pipeline.py`
- Modify: affected tests/fakes importing `app.knowledge.index`
- Create: `tests/integration/test_retrieval_composition.py`
- Create: `tests/retrieval/test_citation_lifecycle.py`
- Create: `tests/foundation/test_retrieval_production_imports.py`
- Modify: `tests/ai/test_grounded_explanations.py`
- Modify: `tests/ai/test_semantic_matching.py`
- Modify: `tests/matching/test_matching_service.py`
- Delete last: `app/knowledge/index.py`

**Interfaces:**
- Producers consume `GenerationWriter` plus the embedding adapter; readers consume only the focused `RecruitingVectorIndex` port.
- Semantic matching and generated explanations receive an explicit `SearchScope` built from the authenticated tenant and current Resume/JobVersion/KnowledgeDocument versions; they no longer call `source_chunks()` or the legacy positional `search()` API.
- Current grounding uses `resolve_active_citations(scope, ids)`. Authorized audit paths use `resolve_historical_citations(tenant_id, ids)`. Neither path accepts a caller-selected tenant from an API payload.
- `app.main` and the worker composition factory inject `PgVectorRecruitingIndex`, `GenerationWriter`, and the embedding adapter explicitly. SQLite unit composition injects the retrieval fake and never constructs the PostgreSQL adapter.

- [ ] **Step 1: Inventory every legacy consumer and write cutover tests**

Before editing production, run and preserve the inventory:

```bash
rg -n "app\.knowledge\.index|source_index|source_chunks|resolve_citations" app scripts tests
```

Write failing tests that exercise, rather than merely grep, the production boundaries:

- `test_retrieval_composition.py` builds the real FastAPI app against PostgreSQL with a fake 512-dimensional embedder, verifies the app's writer/read ports are PostgreSQL adapters, creates/indexes Resume and JobVersion evidence, and executes both `rules-v1` and `hybrid-v1` paths;
- the same file calls a factored Celery worker dependency factory and proves resume and knowledge tasks receive the same PostgreSQL adapter/generation writer contracts;
- `test_retrieval_production_imports.py` imports `app.main`, `app.tasks.celery_app`, `app.ai.semantic_matching`, `app.services.matching`, and `scripts.evaluate_ai_pipeline`, then proves `app.knowledge.index` is absent after cutover;
- citation tests prove inactive evidence is audit-only and privacy-deleted evidence resolves nowhere;
- fake-based unit tests prove authorized scope is passed intact through semantic scoring, matching explanations, job indexing, resume indexing, knowledge processing, and source backfill.

- [ ] **Step 2: Confirm production still depends on legacy methods**

Run:

```bash
docker compose -p recruitmatch-cp2-task4 up -d postgres redis
docker compose -p recruitmatch-cp2-task4 run --rm bootstrap
docker compose -p recruitmatch-cp2-task4 run --rm test-integration \
  pytest tests/integration/test_retrieval_composition.py tests/retrieval/test_citation_lifecycle.py -q
docker compose run --rm --no-deps test-unit \
  pytest tests/foundation/test_retrieval_production_imports.py \
  tests/ai/test_semantic_matching.py tests/matching/test_matching_service.py -q
```

Expected: FAIL on legacy composition/imports or missing scope/resolver behavior.

- [ ] **Step 3: Migrate all producers, readers, fakes, and composition roots**

Make the complete cutover in this task:

1. `resume_processing.py`, `jobs.py`, `knowledge_processing.py`, and `source_index_backfill.py` stage and activate via `GenerationWriter`; no service calls legacy `index_source()`.
2. `semantic_matching.py` and `matching.py` receive typed authorized `SearchScope` values and use the new search/current-citation operations; no code calls `source_chunks()`.
3. `citations.py` and `explanations.py` import `RetrievedChunk` from `app.retrieval` and use the explicit active/historical resolver appropriate to the use case.
4. API composition derives tenant and source-version authorization from repositories and passes typed scopes; it never trusts API-provided source tuples.
5. `app/main.py` and a factored worker dependency builder in `app/tasks/celery_app.py` construct the PostgreSQL adapter and generation writer for production. Test composition injects fakes explicitly.
6. `scripts/evaluate_ai_pipeline.py` and every affected test fake implement the new port and construct the full `RetrievedChunk` shape.

Only after steps 1–6 import and pass, delete `app/knowledge/index.py`. Do not leave a compatibility shim or a second JSON/NumPy production adapter.

- [ ] **Step 4: Prove no consumer or import was stranded**

Run:

```bash
docker compose -p recruitmatch-cp2-task4 run --rm test-integration \
  pytest tests/integration/test_pgvector_migration.py \
  tests/integration/test_retrieval_composition.py tests/retrieval -q
docker compose run --rm --no-deps test-unit \
  pytest tests/foundation tests/resumes tests/knowledge tests/matching tests/ai tests/web -q
docker compose run --rm --no-deps test-unit \
  python -c "import app.main, app.tasks.celery_app, app.ai.semantic_matching, app.services.matching, scripts.evaluate_ai_pipeline"
! rg -n "app\.knowledge\.index|source_chunks|resolve_citations" app scripts tests
docker compose -p recruitmatch-cp2-task4 up -d --wait api worker
curl --fail http://127.0.0.1:8000/api/v1/health/ready
docker compose -p recruitmatch-cp2-task4 down -v
```

Expected: all production composition/import, PostgreSQL retrieval, existing API behavior, and live API/worker startup checks pass. The final scan has no legacy imports/methods; `source_index` may remain only if it is a clearly typed variable name for the new port, not a legacy API call.

- [ ] **Step 5: Commit the runnable production cutover and legacy deletion together**

```bash
git add app/main.py app/tasks/celery_app.py app/ai app/services app/api/v1 \
  app/retrieval scripts/evaluate_ai_pipeline.py tests
git add -u app/knowledge/index.py
git commit -m "feat: compose production retrieval with pgvector"
```

## Checkpoint 2 Gate

```bash
docker compose -p recruitmatch-cp2-gate up -d postgres redis
docker compose -p recruitmatch-cp2-gate run --rm bootstrap
docker compose -p recruitmatch-cp2-gate run --rm bootstrap
docker compose -p recruitmatch-cp2-gate run --rm test-integration \
  pytest tests/integration/test_pgvector_migration.py \
  tests/integration/test_retrieval_composition.py tests/retrieval -q
docker compose run --rm --no-deps test-unit uv lock --check
docker compose run --rm --no-deps test-unit ruff check app tests scripts
docker compose run --rm --no-deps test-unit ruff format --check app tests scripts
docker compose run --rm --no-deps test-unit mypy app/retrieval app/services app/ai app/tasks
docker compose run --rm --no-deps test-unit \
  pytest tests/foundation tests/resumes tests/knowledge tests/matching tests/ai tests/web -q
docker compose -p recruitmatch-cp2-gate up -d --wait api worker
curl --fail http://127.0.0.1:8000/api/v1/health/ready
! rg -n "app\.knowledge\.index|source_chunks|resolve_citations" app scripts tests
docker compose -p recruitmatch-cp2-gate down -v
```

Expected: bootstrap is idempotent on pgvector PostgreSQL; tenant/model/authorization isolation, generation atomicity, citation lifecycle, production API/worker composition, and all retained API schemas pass. SQLite unit paths never execute the pgvector Alembic revision.
