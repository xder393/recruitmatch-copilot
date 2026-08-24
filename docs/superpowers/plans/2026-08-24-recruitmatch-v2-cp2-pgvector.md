# RecruitMatch v2 Checkpoint 2 pgvector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace JSON/NumPy retrieval with tenant-authorized PostgreSQL/pgvector search, atomic generations, and separate current/historical citation resolution.

**Architecture:** A `RecruitingChunk` table stores normalized `vector(512)` embeddings for Resume, JobVersion, and KnowledgeDocument. A port exposes typed search and citation operations; the PostgreSQL adapter keeps authorization filters in the same SQL as distance ordering. Source rows own the active generation.

**Tech Stack:** PostgreSQL 16, pgvector 0.8+, SQLAlchemy 2, Alembic, psycopg 3, pytest.

**Spec:** `docs/superpowers/specs/2026-08-24-recruitmatch-productionization-design.md`

## Global Constraints

- Production migrations only target PostgreSQL/pgvector; SQLite uses fakes.
- Embedding contract is 512 dimensions, L2-normalized, cosine distance, `vector_cosine_ops`.
- `embedding_model`, tenant, active state, source type, and authorized source IDs are mandatory search predicates.
- No Python-side tenant/authorization filtering and no bare `source_id` destructive operation.
- HNSW Recall claims require Exact-vs-HNSW benchmark evidence.

---

### Task 1: Add pgvector schema and source-owned index state

**Files:**
- Create: `app/models/retrieval.py`
- Create: `alembic/versions/20260824_11_pgvector_recruiting_chunks.py`
- Modify: `app/models/__init__.py`
- Modify: `app/models/resumes.py`
- Modify: `app/models/jobs.py`
- Modify: `app/models/knowledge.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `docker-compose.yml`
- Test: `tests/integration/test_pgvector_migration.py`

**Interfaces:**
- Produces: `RecruitingChunk`, `active_index_generation`, `search_index_status`, `search_index_error_code`, `search_indexed_at` on all three source types.
- Consumes: PostgreSQL service and frozen Python image from Checkpoint 1.

- [ ] **Step 1: Write the PostgreSQL migration test**

```python
from sqlalchemy import text


def test_recruiting_chunks_schema(postgres_session):
    version = postgres_session.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
    assert tuple(map(int, version.split(".")[:2])) >= (0, 8)
    columns = {row[0] for row in postgres_session.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='recruiting_chunks'"
    ))}
    assert {"tenant_id", "source_type", "source_id", "source_version", "generation",
            "citation_id", "embedding", "embedding_model", "is_active"} <= columns
```

- [ ] **Step 2: Run against clean PostgreSQL and confirm the table is absent**

Run: `docker compose run --rm test-integration pytest tests/integration/test_pgvector_migration.py -q`

Expected: FAIL because `recruiting_chunks`/vector extension are absent.

- [ ] **Step 3: Implement the model and migration**

Add `pgvector>=0.3,<1` to runtime dependencies and refresh `uv.lock`. Define `RecruitingChunk` with `Vector(512)` and the exact unique/index contracts from spec section 5.3. Migration performs:

```python
op.execute("CREATE EXTENSION IF NOT EXISTS vector")
op.create_table(
    "recruiting_chunks",
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
    sa.Column("document_id", sa.String(36), sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=True),
    sa.Column("source_type", sa.String(30), nullable=False),
    sa.Column("source_id", sa.String(100), nullable=False),
    sa.Column("source_version", sa.String(100), nullable=False),
    sa.Column("generation", sa.Integer(), nullable=False),
    sa.Column("citation_id", sa.String(64), nullable=False),
    sa.Column("page_number", sa.Integer(), nullable=True),
    sa.Column("section", sa.String(200), nullable=True),
    sa.Column("start_offset", sa.Integer(), nullable=False),
    sa.Column("end_offset", sa.Integer(), nullable=False),
    sa.Column("content", sa.Text(), nullable=False),
    sa.Column("embedding", Vector(512), nullable=False),
    sa.Column("embedding_model", sa.String(200), nullable=False),
    sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("tenant_id", "citation_id", name="uq_recruiting_chunk_citation"),
    sa.UniqueConstraint("tenant_id", "source_type", "source_id", "source_version", "generation",
                        "start_offset", "end_offset", name="uq_recruiting_chunk_identity"),
)
op.create_index("ix_recruiting_chunks_tenant_type_active", "recruiting_chunks",
                ["tenant_id", "source_type", "is_active"])
op.execute("CREATE INDEX ix_recruiting_chunks_hnsw_active ON recruiting_chunks "
           "USING hnsw (embedding vector_cosine_ops) WHERE is_active")
```

Remove JSON vector columns/table ownership after the new model is wired; do not backfill old data. Add source index-state fields consistently to Resume, JobVersion and KnowledgeDocument.

- [ ] **Step 4: Recreate volumes and verify clean migration**

Run:

```bash
docker compose down -v
docker compose up -d postgres
docker compose run --rm bootstrap alembic upgrade head
docker compose run --rm test-integration pytest tests/integration/test_pgvector_migration.py -q
```

Expected: migration and test pass on an empty database.

- [ ] **Step 5: Commit schema**

```bash
git add app/models alembic/versions/20260824_11_pgvector_recruiting_chunks.py pyproject.toml uv.lock docker-compose.yml tests/integration/test_pgvector_migration.py
git commit -m "feat: add tenant-scoped pgvector recruiting chunks"
```

### Task 2: Implement the retrieval port, fake, and PostgreSQL adapter

**Files:**
- Modify: `app/retrieval/ports.py`
- Create: `app/retrieval/pgvector_index.py`
- Create: `tests/fakes/retrieval.py`
- Create: `tests/retrieval/test_pgvector_search.py`
- Delete: `app/knowledge/index.py`

**Interfaces:**
- Consumes: `RecruitingChunk`, `EmbeddingIdentity`.
- Produces:
  - `SearchScope(tenant_id, source_types, authorized_sources)`
  - `RecruitingVectorIndex.search(scope, query_embedding, top_k, min_score)`
  - `RecruitingVectorIndex.resolve_active_citations(scope: SearchScope, citation_ids: frozenset[str]) -> list[RetrievedChunk]`
  - `RecruitingVectorIndex.resolve_historical_citations(tenant_id: str, citation_ids: frozenset[str]) -> list[RetrievedChunk]`

- [ ] **Step 1: Write cross-tenant and model-space failing tests**

```python
def test_search_filters_tenant_authorization_and_model(pg_index, seeded_chunks):
    hits = pg_index.search(
        SearchScope("tenant-a", frozenset({"resume"}), frozenset({("resume", "r1", "v1")})),
        query_embedding=seeded_chunks.query,
        embedding_model="fake-hash-v1",
        top_k=5,
        min_score=0.0,
    )
    assert {hit.source_id for hit in hits} == {"r1"}
    assert all(hit.tenant_id == "tenant-a" for hit in hits)
```

- [ ] **Step 2: Run and confirm the typed adapter is missing**

Run: `docker compose run --rm test-integration pytest tests/retrieval/test_pgvector_search.py -q`

Expected: FAIL importing `PgVectorRecruitingIndex` or `SearchScope`.

- [ ] **Step 3: Implement one SQL statement for secure search**

The adapter query must follow this shape:

```python
distance = RecruitingChunk.embedding.cosine_distance(query_embedding)
statement = (
    select(RecruitingChunk, distance.label("distance"))
    .where(
        RecruitingChunk.tenant_id == scope.tenant_id,
        RecruitingChunk.is_active.is_(True),
        RecruitingChunk.embedding_model == embedding_model,
        RecruitingChunk.source_type.in_(scope.source_types),
        tuple_(RecruitingChunk.source_type, RecruitingChunk.source_id,
               RecruitingChunk.source_version).in_(scope.authorized_sources),
    )
    .order_by(distance, RecruitingChunk.id)
    .limit(top_k)
)
```

Reject embeddings whose length is not 512 or whose norm differs from 1 by more than `1e-5`. Map distance to `score = 1.0 - distance` and apply `min_score` without broadening the authorization set.

- [ ] **Step 4: Run search, query-plan, and fake contract tests**

Run:

```bash
docker compose run --rm test-integration pytest tests/retrieval/test_pgvector_search.py -q
docker compose run --rm test-integration pytest tests/retrieval -q
```

Expected: tenant/model/authorization tests pass and `EXPLAIN` evidence is captured for both Exact and HNSW test paths.

- [ ] **Step 5: Commit adapter**

```bash
git add app/retrieval tests/fakes/retrieval.py tests/retrieval app/knowledge/index.py
git commit -m "feat: search recruiting evidence with pgvector"
```

### Task 3: Make generation replacement atomic and fenced-ready

**Files:**
- Create: `app/retrieval/generations.py`
- Modify: `app/repositories/knowledge.py`
- Modify: `app/services/source_index_backfill.py`
- Modify: `app/services/knowledge_processing.py`
- Modify: `app/services/resume_processing.py`
- Test: `tests/retrieval/test_generation_switch.py`

**Interfaces:**
- Consumes: inactive embedded chunks; Checkpoint 4 adds `claimed_epoch` to activation.
- Produces: `GenerationWriter.stage()` and `GenerationWriter.activate()`.

- [ ] **Step 1: Write failure-preserves-old-generation tests**

```python
def test_failed_generation_never_replaces_active(writer, source):
    writer.stage(source, generation=2, chunks=[chunk("new")])
    writer.fail(source, generation=2, error_code="embedding_incomplete")
    assert writer.active_generation(source) == 1
    assert writer.active_contents(source) == ["old"]


def test_only_one_generation_is_active_after_switch(writer, source):
    writer.stage(source, generation=2, chunks=[chunk("new")])
    writer.activate(source, generation=2)
    assert writer.active_generations(source) == [2]
```

- [ ] **Step 2: Run and confirm current code activates while writing**

Run: `docker compose run --rm test-integration pytest tests/retrieval/test_generation_switch.py -q`

Expected: FAIL because current repository deactivates old chunks before safely staging all new chunks.

- [ ] **Step 3: Implement stage-validate-activate transaction**

`stage()` writes `is_active=False`. `activate()` locks the Source, verifies generation and embedding identity, then deactivates old rows, activates new rows, and updates `active_index_generation` in one transaction. Checkpoint 4 extends this final transaction with the processing epoch predicate after Lease fields exist.

- [ ] **Step 4: Verify atomicity under rollback and concurrency**

Run: `docker compose run --rm test-integration pytest tests/retrieval/test_generation_switch.py -q`

Expected: old generation survives injected failure and exactly one generation is active after success.

- [ ] **Step 5: Commit generation writer**

```bash
git add app/retrieval/generations.py app/repositories/knowledge.py app/services/source_index_backfill.py app/services/knowledge_processing.py app/services/resume_processing.py tests/retrieval/test_generation_switch.py
git commit -m "feat: switch recruiting index generations atomically"
```

### Task 4: Separate current grounding from historical audit citations

**Files:**
- Modify: `app/retrieval/ports.py`
- Modify: `app/retrieval/pgvector_index.py`
- Modify: `app/ai/citations.py`
- Modify: `app/ai/explanations.py`
- Test: `tests/retrieval/test_citation_lifecycle.py`
- Test: `tests/ai/test_grounded_explanations.py`

**Interfaces:**
- Produces: active resolver requiring authorized sources; historical resolver requiring tenant and citation ID.
- Consumes: `RetrievedChunk` and active Source generation.

- [ ] **Step 1: Write citation lifecycle tests**

```python
def test_inactive_citation_is_audit_only(index, inactive_citation):
    assert index.resolve_active_citations(inactive_citation.scope, {inactive_citation.id}) == []
    assert [x.citation_id for x in index.resolve_historical_citations(
        inactive_citation.tenant_id, {inactive_citation.id}
    )] == [inactive_citation.id]
```

- [ ] **Step 2: Run and confirm only active resolution exists**

Run: `docker compose run --rm test-integration pytest tests/retrieval/test_citation_lifecycle.py -q`

Expected: FAIL because historical resolver does not exist.

- [ ] **Step 3: Implement both resolvers and privacy-delete behavior**

Active resolution requires tenant, active generation and authorized source tuples. Historical resolution omits `is_active` but never omits tenant. Privacy deletion physically removes or irreversibly clears content/embedding so both resolvers return no sensitive evidence.

- [ ] **Step 4: Run citation and explanation suites**

Run:

```bash
docker compose run --rm test-integration pytest tests/retrieval/test_citation_lifecycle.py -q
docker compose run --rm test-unit pytest tests/ai/test_grounded_explanations.py -q
```

Expected: active/inactive/deleted semantics and generated-citation whitelist tests pass.

- [ ] **Step 5: Commit citation split**

```bash
git add app/retrieval app/ai/citations.py app/ai/explanations.py tests/retrieval tests/ai/test_grounded_explanations.py
git commit -m "feat: separate active and audit citation resolution"
```

## Checkpoint 2 Gate

```bash
docker compose down -v
docker compose up -d postgres
docker compose run --rm bootstrap alembic upgrade head
docker compose run --rm test-integration pytest tests/integration/test_pgvector_migration.py tests/retrieval -q
docker compose run --rm test-unit pytest tests/ai tests/matching -q
```

Expected: clean migration succeeds; tenant/model isolation, generation atomicity, citation lifecycle and existing matching tests pass.
