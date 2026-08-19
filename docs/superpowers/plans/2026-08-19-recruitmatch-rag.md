# RecruitMatch Recruiting RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add tenant-scoped recruiting knowledge ingestion, local embeddings, safe retrieval, and citation-validated generated guidance.

**Architecture:** Recruiting documents have an independent lifecycle and artifact namespace. RecruitingVectorIndex is a replaceable embedding/index adapter that stores generation-versioned chunks with tenant ownership. Generated claims survive only when their citation IDs resolve to authorized retrieval results.

**Tech Stack:** Python 3.9+, SQLAlchemy 2, Alembic, sentence-transformers BGE, NumPy, FastAPI, Celery, Pydantic v2, pytest.

**Spec:** docs/superpowers/specs/2026-08-19-recruitmatch-ai-layer-design.md

## Global Constraints

- Requires the AI foundation plan.
- Embeddings run locally; chat context contains only minimum evidence.
- Every repository and retrieval call requires tenant_id.
- PDF, DOCX, and TXT reuse existing validation and safe paths.
- Failed re-indexing retains the previous active generation.
- Unknown or unauthorized citations are rejected.
- Test cases instantiate ChunkInput and RetrievedChunk explicitly rather than relying on hidden fixtures.

---

### Task 1: Knowledge Domain and Migration

**Files:**
- Create: app/models/knowledge.py
- Create: app/knowledge/schemas.py
- Create: app/repositories/knowledge.py
- Modify: app/models/__init__.py
- Create: alembic/versions/20260819_06_recruiting_knowledge.py
- Test: tests/knowledge/test_knowledge_repository.py
- Modify: tests/foundation/test_migrations.py

**Interfaces:**
- Produces: KnowledgeDocument(tenant_id, document_type, status, checksum, artifact_key, active_generation).
- Produces: KnowledgeChunk(tenant_id, document_id, generation, offsets, page, content, vector, is_active).
- Produces: KnowledgeRepository.get_document(tenant_id, document_id) -> KnowledgeDocument | None.
- Produces: KnowledgeRepository.replace_generation(document, generation, chunks) -> None.

- [ ] **Step 1: Write tenant and generation tests**

~~~python
def test_cross_tenant_document_lookup_returns_none(session, acme_document):
    assert KnowledgeRepository(session).get_document("globex", acme_document.id) is None

def test_new_generation_replaces_old_chunks(repository, document):
    item = ChunkInput(source_type="policy", content="policy", start=0, end=6, page=1)
    repository.replace_generation(document, 2, [item])
    assert repository.active_chunks(document.tenant_id, document.id)[0].generation == 2
~~~

- [ ] **Step 2: Run and confirm red state**

Run: .venv/bin/pytest tests/knowledge/test_knowledge_repository.py tests/foundation/test_migrations.py -q  
Expected: FAIL because knowledge models and migration are absent.

- [ ] **Step 3: Implement persistence**

Use string UUIDs and UTC timestamps. Add unique (tenant_id, checksum, document_type), plus indexes on (tenant_id, status) and (tenant_id, document_id, generation, is_active). Generation replacement activates new chunks and deactivates old ones in one transaction.

- [ ] **Step 4: Verify and commit**

~~~bash
TASK_DB=$(mktemp -d)/knowledge.db
DATABASE_URL="sqlite:///$TASK_DB" .venv/bin/alembic upgrade head
.venv/bin/pytest tests/knowledge/test_knowledge_repository.py tests/foundation/test_migrations.py -q
git add app/models/knowledge.py app/models/__init__.py app/knowledge/schemas.py app/repositories/knowledge.py alembic/versions/20260819_06_recruiting_knowledge.py tests/knowledge tests/foundation/test_migrations.py
git commit -m "feat: persist tenant recruiting knowledge"
~~~

### Task 2: Safe Ingestion and Chunking

**Files:**
- Create: app/knowledge/artifacts.py
- Create: app/knowledge/chunking.py
- Create: app/services/knowledge_processing.py
- Create: app/services/knowledge_documents.py
- Test: tests/knowledge/test_knowledge_ingestion.py

**Interfaces:**
- Produces: KnowledgeArtifactStore.save(tenant_id, document_id, filename, content) -> str.
- Produces: chunk_document(text, source_id, page_map, chunk_size=700, overlap=100) -> list[ChunkInput].
- Produces: KnowledgeProcessingService.process(tenant_id, document_id) -> None.

- [ ] **Step 1: Write path, offset, and failure tests**

~~~python
def test_chunk_offsets_resolve_to_source():
    text = "A" * 900
    chunks = chunk_document(text, "doc-1", [])
    assert all(text[item.start:item.end] == item.content for item in chunks)

def test_artifact_key_hides_filename(store):
    key = store.save("tenant-1", "doc-1", "机密制度.docx", b"content")
    assert "机密制度" not in key
    assert key.startswith("knowledge/tenant-1/doc-1/")
~~~

- [ ] **Step 2: Run and confirm red state**

Run: .venv/bin/pytest tests/knowledge/test_knowledge_ingestion.py -q  
Expected: FAIL because ingestion components are absent.

- [ ] **Step 3: Implement safe storage, extraction, and lifecycle**

Reuse the 10 MiB MIME/extension checks and PDF/DOCX/TXT extractors. Store UUID filenames under the knowledge namespace. Use 700-character chunks with 100 overlap and exact offsets. Activate a generation only after all embeddings persist; otherwise set failed with a stable code and retain the previous generation.

- [ ] **Step 4: Verify and commit**

~~~bash
.venv/bin/pytest tests/knowledge/test_knowledge_ingestion.py tests/resumes/test_extractors.py -q
git add app/knowledge app/services/knowledge_processing.py app/services/knowledge_documents.py tests/knowledge/test_knowledge_ingestion.py
git commit -m "feat: ingest recruiting knowledge safely"
~~~

### Task 3: Local Tenant-Scoped Vector Index

**Files:**
- Create: app/knowledge/embeddings.py
- Create: app/knowledge/index.py
- Test: tests/knowledge/test_knowledge_index.py

**Interfaces:**
- Produces: Embedder.embed_documents(texts) and embed_query(text).
- Produces: KnowledgeIndex.index_generation(tenant_id, document_id, generation, chunks).
- Produces: KnowledgeIndex.index_source(tenant_id, source_type, source_id, source_version, chunks).
- Produces: KnowledgeIndex.search(tenant_id, query, source_types, top_k, min_score) -> list[RetrievedChunk].
- Produces: RetrievedChunk(citation_id, source_type, source_id, content, start, end, page, score).

- [ ] **Step 1: Write tenant, threshold, and citation tests**

~~~python
def test_search_never_returns_other_tenant(index):
    acme = ChunkInput(source_type="policy", content="Python", start=0, end=6, page=1)
    globex = ChunkInput(source_type="policy", content="Python secret", start=0, end=13, page=1)
    index.index_generation("acme", "a", 1, [acme])
    index.index_generation("globex", "g", 1, [globex])
    assert {hit.source_id for hit in index.search("acme", "Python", {"policy"}, 5, 0)} == {"a"}

def test_citation_id_is_stable(index):
    first = index.search("acme", "Python", {"policy"}, 5, 0)[0]
    second = index.search("acme", "Python", {"policy"}, 5, 0)[0]
    assert first.citation_id == second.citation_id
~~~

- [ ] **Step 2: Run and confirm red state**

Run: .venv/bin/pytest tests/knowledge/test_knowledge_index.py -q  
Expected: FAIL because index interfaces are absent.

- [ ] **Step 3: Implement adapters and tenant-first retrieval**

Normalize vectors. Load SentenceTransformer lazily. Select active chunks by tenant and allowed source types before cosine scoring. Reject embedding dimension changes. Citation ID is SHA-256 of tenant, source type, source ID, generation, start, and end, truncated to 20 hex characters. Wire resume processing and immutable job-version creation to index resume and job sources after their transactions succeed; test both source types for tenant isolation.

- [ ] **Step 4: Verify and commit**

~~~bash
.venv/bin/pytest tests/knowledge/test_knowledge_index.py -q
.venv/bin/ruff check app/knowledge tests/knowledge
git add app/knowledge/embeddings.py app/knowledge/index.py tests/knowledge/test_knowledge_index.py
git commit -m "feat: add tenant scoped recruiting index"
~~~

### Task 4: Knowledge APIs and Dispatch

**Files:**
- Create: app/api/v1/knowledge.py
- Modify: app/api/v1/router.py
- Create: app/tasks/knowledge_tasks.py
- Modify: app/tasks/celery_app.py
- Modify: app/main.py
- Modify: app/services/resume_processing.py
- Modify: app/services/jobs.py
- Test: tests/knowledge/test_knowledge_api.py

**Interfaces:**
- Produces: POST/GET /api/v1/knowledge-documents.
- Produces: GET /api/v1/knowledge-documents/{id}.
- Produces: POST /api/v1/knowledge-documents/{id}/reindex.
- Produces: POST /api/v1/knowledge-documents/{id}/deactivate.

- [ ] **Step 1: Write API isolation and idempotency tests**

~~~python
def test_cross_tenant_document_is_hidden(acme_client, globex_client):
    document = upload_policy(acme_client)
    assert globex_client.get(f"/api/v1/knowledge-documents/{document['id']}").status_code == 404

def test_duplicate_upload_is_idempotent(client):
    assert upload_policy(client)["id"] == upload_policy(client)["id"]
~~~

- [ ] **Step 2: Run and confirm red state**

Run: .venv/bin/pytest tests/knowledge/test_knowledge_api.py -q  
Expected: FAIL with missing routes.

- [ ] **Step 3: Implement routes and dispatch**

Require Principal on every route and a fixed document-type enum. Inline mode processes synchronously; Celery queues only tenant_id and document_id. Deactivation is idempotent and disables chunks transactionally.

- [ ] **Step 4: Verify and commit**

~~~bash
.venv/bin/pytest tests/knowledge/test_knowledge_api.py tests/resumes/test_resume_api.py -q
git add app/api/v1/knowledge.py app/api/v1/router.py app/tasks/knowledge_tasks.py app/tasks/celery_app.py app/main.py app/services/resume_processing.py app/services/jobs.py tests/knowledge/test_knowledge_api.py
git commit -m "feat: expose recruiting knowledge lifecycle"
~~~

### Task 5: Citation-Validated Explanations

**Files:**
- Create: app/ai/citations.py
- Create: app/ai/explanations.py
- Test: tests/ai/test_grounded_explanations.py

**Interfaces:**
- Produces: GroundedClaim(text, citation_ids).
- Produces: GroundedExplanation(summary, strengths, gaps, risk_flags, interview_questions, citations, grounding_status).
- Produces: GroundedExplanationService.generate(tenant_id, resume_id, job_version_id, rule_result).

- [ ] **Step 1: Write unsupported-claim and insufficient-evidence tests**

~~~python
def test_unknown_citation_removes_claim(service):
    output = GroundedClaim(text="expert", citation_ids=["invented"])
    hit = RetrievedChunk("known", "resume", "resume-1", "Python", 0, 6, 1, .9)
    result = service(model_output=output, hits=[hit]).generate(context)
    assert result.strengths == []
    assert result.grounding_status == "rejected_unsupported_claims"

def test_no_hits_skips_model(service, fake_model):
    assert service(hits=[]).generate(context).grounding_status == "insufficient_evidence"
    assert fake_model.calls == []
~~~

- [ ] **Step 2: Run and confirm red state**

Run: .venv/bin/pytest tests/ai/test_grounded_explanations.py -q  
Expected: FAIL because grounded generation is absent.

- [ ] **Step 3: Implement context, validation, and fallback**

Format context as [citation:<id>] content, cap total evidence characters, and include only authorized resume, selected JD, and active policy hits. Remove claims/questions with empty or unknown citation lists. When disabled or failed, map deterministic matched/missing/uncertain items into rules_fallback output.

- [ ] **Step 4: Verify and commit**

~~~bash
.venv/bin/pytest tests/knowledge tests/ai/test_grounded_explanations.py -q
.venv/bin/ruff check app tests scripts
git diff --check
git add app/ai/citations.py app/ai/explanations.py tests/ai/test_grounded_explanations.py
git commit -m "feat: generate citation grounded recruiting guidance"
~~~
