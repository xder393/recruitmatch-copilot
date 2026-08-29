# CP2 Task 4 implementation report

Status: DONE

Commit: `1fd44e4c61a91cb335dd042be07bf1f6b754208d`

## Initial legacy inventory

The preserved initial inventory was produced with:

```text
rg -n "app\.knowledge\.index|source_index|source_chunks|resolve_citations|KnowledgeChunk|knowledge_chunks" app scripts tests alembic
```

It found production legacy dependencies in `app.main`, the Celery worker, resume/job/knowledge/backfill producers, semantic matching, grounded guidance, API composition, the evaluation script, `KnowledgeChunk` ORM/repositories, resume privacy deletion, knowledge deactivation, and affected tests. The complete raw output follows (the temporary source was `/tmp/cp2-task4-inventory.txt`):

```text
scripts/evaluate_ai_pipeline.py:23:from app.knowledge.index import RetrievedChunk  # noqa: E402
scripts/evaluate_ai_pipeline.py:133:    def source_chunks(self, tenant_id: str, source_type: str, source_id: str):
scripts/evaluate_ai_pipeline.py:147:    def resolve_citations(self, tenant_id: str, citation_ids: set[str]):
scripts/evaluate_ai_pipeline.py:236:            hits = index.source_chunks("synthetic-tenant", "resume", case["id"])
scripts/evaluate_ai_pipeline.py:237:            hits += index.source_chunks("synthetic-tenant", "job", top.job_version_id)
scripts/evaluate_ai_pipeline.py:241:                citation_resolver=index.resolve_citations,
app/services/resume_processing.py:27:        self, uow_factory: UnitOfWorkFactory, artifact_store: ArtifactReader, parser: ResumeParser, source_index=None
app/services/resume_processing.py:32:        self.source_index = source_index
app/services/resume_processing.py:108:        if self.source_index is not None:
app/services/resume_processing.py:112:                self.source_index.index_source(
alembic/versions/20260819_06_recruiting_knowledge.py:35:    if not inspector.has_table("knowledge_chunks"):
alembic/versions/20260819_06_recruiting_knowledge.py:37:            "knowledge_chunks",
alembic/versions/20260819_06_recruiting_knowledge.py:58:        op.create_index("ix_knowledge_chunks_tenant_id", "knowledge_chunks", ["tenant_id"])
alembic/versions/20260819_06_recruiting_knowledge.py:59:        op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
alembic/versions/20260819_06_recruiting_knowledge.py:60:        op.create_index("ix_knowledge_chunks_source_id", "knowledge_chunks", ["source_id"])
alembic/versions/20260819_06_recruiting_knowledge.py:61:        op.create_index("ix_knowledge_chunks_is_active", "knowledge_chunks", ["is_active"])
alembic/versions/20260819_06_recruiting_knowledge.py:63:            "ix_knowledge_chunks_tenant_document_generation_active",
alembic/versions/20260819_06_recruiting_knowledge.py:64:            "knowledge_chunks",
alembic/versions/20260819_06_recruiting_knowledge.py:70:    op.drop_table("knowledge_chunks")
app/services/jobs.py:20:    def __init__(self, jobs: JobRepository, source_index=None, *, uow: UnitOfWork):
app/services/jobs.py:23:        self.source_index = source_index
app/services/jobs.py:119:        if self.source_index is None:
app/services/jobs.py:124:            self.source_index.index_source(
app/services/matching.py:26:        source_index=None,
app/services/matching.py:37:        self.source_index = source_index
app/services/matching.py:144:        if self.explanation_service is None or self.source_index is None:
app/services/matching.py:152:                hits = self.source_index.source_chunks(tenant_id, "resume", resume_id)
app/services/matching.py:153:                hits += self.source_index.source_chunks(tenant_id, "job", item.job_version_id)
app/services/matching.py:154:                hits += self.source_index.search(
app/services/source_index_backfill.py:18:        source_index,
app/services/source_index_backfill.py:26:        self.source_index = source_index
app/services/source_index_backfill.py:56:            self.source_index.index_source(
tests/ai/test_grounded_explanations.py:9:from app.knowledge.index import RetrievedChunk
tests/ai/test_semantic_matching.py:50:        def source_chunks(self, tenant_id, source_type, source_id):
tests/ai/test_semantic_matching.py:62:    from app.knowledge.index import RetrievedChunk
tests/ai/test_semantic_matching.py:65:        def source_chunks(self, tenant_id, source_type, source_id):
tests/retrieval/test_generation_switch.py:216:def source_chunks(session: Session, reference: SourceRef) -> list[RecruitingChunk]:
tests/retrieval/test_generation_switch.py:254:        staged = [chunk for chunk in source_chunks(session, reference) if chunk.generation == next_generation]
tests/retrieval/test_generation_switch.py:264:        chunks = source_chunks(session, reference)
tests/retrieval/test_generation_switch.py:294:        assert source_chunks(session, reference) == []
tests/retrieval/test_generation_switch.py:325:        assert [chunk.generation for chunk in source_chunks(session, reference)] == [1]
tests/retrieval/test_generation_switch.py:365:        chunks = source_chunks(session, reference)
tests/retrieval/test_generation_switch.py:412:        assert source_chunks(session, reference) == []
tests/retrieval/test_generation_switch.py:457:        chunks = source_chunks(session, reference)
tests/retrieval/test_generation_switch.py:483:        assert [(chunk.generation, chunk.is_active) for chunk in source_chunks(session, reference)] == [(1, False)]
tests/retrieval/test_generation_switch.py:506:        chunks = source_chunks(session, reference)
tests/retrieval/test_generation_switch.py:634:        chunks = source_chunks(session, reference)
tests/retrieval/test_generation_switch.py:667:        chunks = source_chunks(session, reference)
tests/retrieval/test_generation_switch.py:705:        assert source_chunks(session, reference) == []
tests/retrieval/test_generation_switch.py:724:        assert [(chunk.generation, chunk.is_active) for chunk in source_chunks(session, reference)] == [
app/models/knowledge.py:50:    chunks: Mapped[List["KnowledgeChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")
app/models/knowledge.py:53:class KnowledgeChunk(Base):
app/models/knowledge.py:54:    __tablename__ = "knowledge_chunks"
tests/resumes/test_resume_api.py:10:from app.models.knowledge import KnowledgeChunk
tests/resumes/test_resume_api.py:182:                select(KnowledgeChunk).where(
tests/resumes/test_resume_api.py:183:                    KnowledgeChunk.source_type == "resume",
tests/resumes/test_resume_api.py:184:                    KnowledgeChunk.source_id == resume_id,
tests/knowledge/test_knowledge_index.py:6:from app.knowledge.index import RecruitingVectorIndex
tests/knowledge/test_knowledge_index.py:81:    resolved = index.resolve_citations(acme, {acme_id, globex_id})
tests/knowledge/test_knowledge_api.py:10:from app.models.knowledge import KnowledgeChunk
tests/knowledge/test_knowledge_api.py:168:        session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.tenant_id == tenant_id))
tests/ai/test_ai_readiness.py:59:            "failed_source_indexes": 0,
app/tasks/celery_app.py:19:from app.knowledge.index import RecruitingVectorIndex
app/tasks/celery_app.py:46:    source_index = RecruitingVectorIndex(session_factory, BGEEmbedder(settings.embedding_model))
app/tasks/celery_app.py:52:        source_index=source_index,
app/repositories/knowledge.py:9:from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
app/repositories/knowledge.py:49:    def active_chunks(self, tenant_id: str, document_id: str | None = None) -> list[KnowledgeChunk]:
app/repositories/knowledge.py:50:        statement = select(KnowledgeChunk).where(
app/repositories/knowledge.py:51:            KnowledgeChunk.tenant_id == tenant_id,
app/repositories/knowledge.py:52:            KnowledgeChunk.is_active.is_(True),
app/repositories/knowledge.py:55:            statement = statement.where(KnowledgeChunk.document_id == document_id)
app/repositories/knowledge.py:56:        return list(self.session.scalars(statement.order_by(KnowledgeChunk.start)))
app/repositories/knowledge.py:65:            update(KnowledgeChunk)
app/repositories/knowledge.py:67:                KnowledgeChunk.tenant_id == document.tenant_id,
app/repositories/knowledge.py:68:                KnowledgeChunk.document_id == document.id,
app/repositories/knowledge.py:69:                KnowledgeChunk.is_active.is_(True),
app/repositories/knowledge.py:75:                KnowledgeChunk(
app/repositories/knowledge.py:99:            update(KnowledgeChunk)
app/repositories/knowledge.py:101:                KnowledgeChunk.tenant_id == document.tenant_id,
app/repositories/knowledge.py:102:                KnowledgeChunk.document_id == document.id,
app/main.py:26:from app.knowledge.index import RecruitingVectorIndex
app/main.py:77:    source_index = knowledge_index if settings.ai_enabled or knowledge_embedder is not None else None
app/main.py:79:    processor = ResumeProcessingService(uow_factory, artifact_store, parser, source_index=source_index)
app/main.py:86:    app.state.recruiting_source_index = source_index
app/main.py:104:        citation_resolver=knowledge_index.resolve_citations,
app/repositories/resumes.py:14:from app.models.knowledge import KnowledgeChunk
app/repositories/resumes.py:92:            delete(KnowledgeChunk).where(
app/repositories/resumes.py:93:                KnowledgeChunk.tenant_id == tenant_id,
app/repositories/resumes.py:94:                KnowledgeChunk.source_type == "resume",
app/repositories/resumes.py:95:                KnowledgeChunk.source_id == resume.id,
app/models/__init__.py:5:from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
app/models/__init__.py:21:    "KnowledgeChunk",
app/api/v1/knowledge.py:21:from app.services.source_index_backfill import SourceIndexBackfillService
app/ai/semantic_matching.py:39:        source_index,
app/ai/semantic_matching.py:47:        self.source_index = source_index
app/ai/semantic_matching.py:57:            resume_hits = self.source_index.source_chunks(tenant_id, "resume", resume_id)
app/ai/semantic_matching.py:58:            job_hits = self.source_index.source_chunks(tenant_id, "job", job_version_id)
tests/matching/test_matching_api.py:87:    from app.knowledge.index import RetrievedChunk
tests/matching/test_matching_api.py:115:        def source_chunks(self, tenant_id, source_type, source_id):
tests/matching/test_matching_api.py:147:        def source_chunks(self, *args, **kwargs):
app/knowledge/index.py:13:from app.models.knowledge import KnowledgeChunk
app/knowledge/index.py:34:def _citation_id(chunk: KnowledgeChunk) -> str:
app/knowledge/index.py:75:                update(KnowledgeChunk)
app/knowledge/index.py:77:                    KnowledgeChunk.tenant_id == tenant_id,
app/knowledge/index.py:78:                    KnowledgeChunk.source_type == source_type,
app/knowledge/index.py:79:                    KnowledgeChunk.source_id == source_id,
app/knowledge/index.py:80:                    KnowledgeChunk.is_active.is_(True),
app/knowledge/index.py:86:                    KnowledgeChunk(
app/knowledge/index.py:125:                    select(KnowledgeChunk).where(
app/knowledge/index.py:126:                        KnowledgeChunk.tenant_id == tenant_id,
app/knowledge/index.py:127:                        KnowledgeChunk.source_type.in_(source_types),
app/knowledge/index.py:128:                        KnowledgeChunk.is_active.is_(True),
app/knowledge/index.py:160:    def source_chunks(self, tenant_id: str, source_type: str, source_id: str) -> list[RetrievedChunk]:
app/knowledge/index.py:165:                    select(KnowledgeChunk)
app/knowledge/index.py:167:                        KnowledgeChunk.tenant_id == tenant_id,
app/knowledge/index.py:168:                        KnowledgeChunk.source_type == source_type,
app/knowledge/index.py:169:                        KnowledgeChunk.source_id == source_id,
app/knowledge/index.py:170:                        KnowledgeChunk.is_active.is_(True),
app/knowledge/index.py:172:                    .order_by(KnowledgeChunk.start, KnowledgeChunk.id)
app/knowledge/index.py:189:    def resolve_citations(self, tenant_id: str, citation_ids: set[str]) -> list[RetrievedChunk]:
app/knowledge/index.py:196:                    select(KnowledgeChunk)
app/knowledge/index.py:198:                        KnowledgeChunk.tenant_id == tenant_id,
app/knowledge/index.py:199:                        KnowledgeChunk.is_active.is_(True),
app/knowledge/index.py:201:                    .order_by(KnowledgeChunk.source_type, KnowledgeChunk.source_id, KnowledgeChunk.start)
app/api/v1/operations.py:80:        "failed_source_indexes": failed_indexes,
app/ai/citations.py:7:from app.knowledge.index import RetrievedChunk
app/ai/explanations.py:13:from app.knowledge.index import RetrievedChunk
app/api/v1/jobs.py:76:        JobService(jobs, request.app.state.recruiting_source_index, uow=uow).create_job(
app/api/v1/jobs.py:112:        JobService(jobs, request.app.state.recruiting_source_index, uow=uow).update_job(
app/api/v1/matching.py:73:            source_index=request.app.state.knowledge_index,
```

## TDD evidence

- RED: `tests/retrieval/test_indexing_pipeline.py` initially failed collection with `ModuleNotFoundError: app.retrieval.indexing`.
- GREEN: the new complete-generation `SourceIndexer` passed staging/activation, stable citation, incomplete embedding, and stable failure tests (`3 passed`).
- RED: the job API propagation assertion observed `search_index_status == "pending"` after a successful separate writer transaction.
- GREEN: repository reload after indexing made the same test and job service suite pass (`3 passed`).
- Existing legacy-index tests were removed or rewritten against typed retrieval/generation behavior; no shim or dual write remains.

## Implemented cutover

- Added `EmbeddingAdapter`, `GenerationWriterPort`, and `SourceIndexer`; citation IDs hash tenant + canonical Source identity + generation + offsets.
- Migrated resume, `job_version`, `knowledge_document`, and backfill producers to stage/activate full generations through `GenerationWriter` after business commits. Index failures do not roll back valid business state and record bounded failure codes.
- Migrated semantic scoring and guidance to repository-derived `SearchScope`, query embeddings with matching model identity, pgvector `search`, and `resolve_active_citations`.
- Updated citations and responses to `start_offset`, `end_offset`, and `page_number` only.
- `app.main` and `build_worker_dependencies()` now explicitly construct `PgVectorRecruitingIndex`, `GenerationWriter`, one embedding adapter, and `SourceIndexer` for PostgreSQL.
- SQLite API/unit composition explicitly injects `FakeRecruitingVectorIndex`, `FakeGenerationWriter`, and a deterministic 512-dimensional embedder.
- Migrated the deterministic evaluation pipeline to the same typed port.
- Removed `app/knowledge/index.py`, `KnowledgeChunk`, its repository API, and the unused JSON vector field from `ChunkInput`.
- Added PostgreSQL-only reversible revision `20260824_12`, which drops the legacy table on upgrade and recreates its exact schema only on downgrade.
- Resume privacy scrub deletes every `RecruitingChunk` generation; knowledge deactivation marks the Source inactive and removes chunks from current retrieval while authorized historical resolution remains possible.

## Executable composition and lifecycle evidence

- Real PostgreSQL FastAPI composition uses concrete pgvector/generation adapters with a fake 512 embedder.
- It indexes Resume, JobVersion, and KnowledgeDocument evidence, asserts all three canonical source types are active, and executes both `rules-v1` and `hybrid-v1`; the hybrid path produced a validated semantic score.
- The factored Celery dependency builder injects the same concrete contracts into resume and knowledge processors.
- Citation lifecycle proves active/current, inactive/audit-only, resume privacy deletion/nowhere, and knowledge deactivation/current exclusion.
- Migration catalog proves `recruiting_chunks` remains and the legacy JSON-vector table is absent.

## Verification

### Review fix round 1

- Added one Resume row-lock protocol: matching obtains the tenant-qualified successful Resume with `FOR UPDATE` before model/retrieval work and holds it through MatchRun commit; privacy deletion locks the same row before scrubbing.
- Added real PostgreSQL barriers proving delete blocks while the semantic model is paused, then scrubs the newly committed MatchResult and all Resume citations; the reverse winner commits deletion first and matching rejects the deleted Resume.
- Semantic and explanation outputs now re-resolve only their returned citation IDs against current Source authority after model generation. Real PostgreSQL tests cover concurrent Job and Knowledge inactivation degrading without persisting stale citations.
- Generation staging supports only byte-for-byte-equivalent replay of the current `N+1` staging set. A first activation failure can retry without duplicate citation rows; partial or mismatched replay fails closed. The SQLite fake now enforces the same Source, generation, 512-dimensional finite L2 embedding, citation, cleanup-key, staging and retry rules.
- Backfill never mutates stale Source ORM status. Missing text and every caught failure flow through writer authority with bounded classification, followed by repository expiry/reload; an old active generation remains `ready` with its stable error code.
- Semantic retrieval uses the specific JD as the Resume query and the server-derived project/skill summary as the current Job query. `top_k` and `min_score` come from `Settings`; either side below threshold yields deterministic rules fallback. Chinese multi-chunk adversarial coverage selects only relevant RAG chunks.
- `SemanticProjectScore` and persisted/API `semantic_score` are explicitly 0–100 and clamped; hybrid weighting converts it to 0–1 before applying 0.8/0.2. `ChunkInput.source_type` was removed so canonical Source type exists only in `SourceRef`.
- Knowledge authorization retains an old ready generation during uploaded/processing refresh and excludes inactive/deleted business state. Inactive evidence remains historical/audit-only; privacy-deleted evidence resolves nowhere in fake and PostgreSQL contracts.

Review-fix verification on current images:

```text
focused RED: 9 expected failures across score scale, post-model citation resolution, query inputs, hybrid weighting, and canonical chunks
focused GREEN/unit/API contracts: passed
PostgreSQL retrieval + migration + composition + lifecycle + privacy concurrency: 101 passed
retained local unit/API/evaluation/operations/UI suites: 153 passed
head -> 20260824_11 -> head: passed; recruiting_chunks present, knowledge_chunks absent
uv lock --check: passed
ruff check app tests scripts: passed
ruff format --check app tests scripts: passed
mypy app: Success, 96 source files
production imports and strict legacy scan: passed
live api: healthy; readiness returned {"status":"ready","database":"ok"}
live worker: connected to Redis and ready
git diff --check: passed
```

Fresh/current images and an empty PostgreSQL volume:

```text
bootstrap #1: upgraded through 20260824_12; seeded_job_templates=30
bootstrap #2: seeded_job_templates=0
PostgreSQL migration + composition + retrieval/lifecycle: 91 passed
retained unit/API/evaluation/operations/UI suites: 129 passed
uv lock --check: passed
ruff check app tests scripts: passed
ruff format --check app tests scripts: passed
mypy app: Success, 96 source files
production import command: passed
strict legacy scan: no matches
live api: healthy; GET /api/v1/health/ready -> {"status":"ready","database":"ok"}
live worker: healthy
git diff --check: passed
```

The first bootstrap attempt reused a pre-Task-4 image and therefore stopped at the old head. Images were explicitly rebuilt, the volume was recreated, and both bootstrap passes plus every PostgreSQL gate were rerun against the current worktree.

## Self-review

- Confirmed tenant/source/version scopes come only from authenticated principal plus repository-loaded Resume, current JobVersion, and ready KnowledgeDocument rows.
- Confirmed search and citation readers depend only on `RecruitingVectorIndex`; producers depend on the generation writer through `SourceIndexer`.
- Confirmed no compatibility adapter, positional search, generic citation resolver, old source type, or JSON-vector persistence remains.
- Confirmed business and generation transactions are separate and fallback keeps rules/auth/catalog paths usable.

Concerns: none blocking. CP4 still owns real Lease fencing; CP2 continues to fail closed when a non-null fencing token is supplied.

### Final continuation verification

The final continuation preserved and audited the complete dirty fix set before making any additional change. It also found one integration-boundary defect that the earlier targeted commands did not expose: the GitHub Actions SQLite unit command collected PostgreSQL-only retrieval files and produced `157 passed, 66 errors`. The unit selector now excludes only the three PostgreSQL-only retrieval files, while the PostgreSQL job executes all of `tests/integration` and `tests/retrieval`, including production composition and the Resume privacy concurrency barriers.

The semantic-score scale change also exposed a deterministic evaluation mismatch. The offline fake still emitted `0..1` scores while production now consumes `0..100`, causing two Top-3 regressions. The fake now uses the canonical `0..100` contract and the machine-generated golden file was refreshed. Ranking metrics returned to `top1_accuracy=0.9733` and `top3_recall=1.0`; only deterministic token counts changed because the serialized score values are longer.

Fresh evidence after those corrections:

```text
focused PostgreSQL/retrieval/AI/matching review regressions: 112 passed
all PostgreSQL integration + retrieval suites: 99 passed
SQLite unit/API/evaluation/operations/UI selector used by CI: 157 passed
AI pipeline golden generation + diff: passed
head -> 20260824_11 -> head: passed
  20260824_11: recruiting_chunks present; knowledge_chunks present
  20260824_12: recruiting_chunks present; knowledge_chunks absent
uv lock --check: passed (105 packages)
ruff check app tests scripts: passed
ruff format --check app tests scripts: 162 files already formatted
mypy app: Success, 96 source files
production import command: passed
strict legacy scans: no matches
live API after forced rebuild: healthy
GET /api/v1/health/ready: {"status":"ready","database":"ok"}
live Worker after forced rebuild: connected to Redis; celery ready
git diff --check: passed
```

The raw legacy inventory above is the complete unchanged content of `/tmp/cp2-task4-inventory.txt`, captured before the cutover edits.

### Review fix round 2

The second independent review found two remaining fail-open boundaries. Both are now closed without expanding the Task 4 architecture:

- Semantic matching and grounded explanations derive a citation whitelist from the exact bounded evidence string sent to the model. Post-model active resolution receives only requested IDs that were actually visible in that prompt, and returned rows are filtered against both sets before validation. A resolver cannot make a prompt-external citation acceptable by resolving it from a wider active scope.
- Semantic matching constructs a pair-specific post-model `SearchScope` containing only the current Resume and current JobVersion, even when the caller's authorized scope contains additional jobs or knowledge.
- Semantic scores must be finite and inside the closed `0..100` interval. Values below zero, above 100, `NaN`, and both infinities are rejected rather than clamped. The hybrid engine independently treats an invalid semantic object as zero semantic contribution, removes its citations/persistence payload, and emits deterministic `rules_fallback` with `invalid_semantic_score`.
- Explanation validation rejects only the claims that request a prompt-external citation. Its citation dictionary is rebuilt solely from citations used by retained claims, so persistence cannot contain an unsupported claim/citation pair.

TDD evidence:

```text
RED semantic/explanation: 8 expected failures
  6 score failures: 120, negative, above 100, NaN, +Inf, -Inf were clamped/accepted
  1 semantic failure: another active job citation absent from the prompt was resolved by the wider scope
  1 explanation failure: an active citation truncated out of the prompt was retained in a claim
RED hybrid defense: 2 expected failures
  invalid score contributed after clamping and then failed response-model validation instead of degrading
RED prompt marker injection: 1 expected failure
  a citation-shaped string inside untrusted evidence content expanded a regex-derived whitelist
GREEN focused semantic/explanation/hybrid: 29 passed
related AI + matching suites: 60 passed
CI SQLite selector: passed (168 tests)
PostgreSQL integration + retrieval suites: 99 passed
uv lock --check: passed (105 packages)
ruff check app tests scripts: passed
ruff format --check app tests scripts: 162 files already formatted
mypy app: Success, 96 source files
git diff --check: passed
isolated PostgreSQL/Redis Compose project removed with volumes after verification
```
