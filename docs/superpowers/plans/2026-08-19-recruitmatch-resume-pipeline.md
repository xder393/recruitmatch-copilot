# RecruitMatch Resume Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add tenant-isolated, idempotent resume upload, artifact storage, PDF/DOCX/TXT extraction, structured profiling, lifecycle status, and asynchronous processing contracts.

**Architecture:** Add a resume bounded context to the existing modular monolith. Persist metadata and structured profiles in SQLAlchemy, place original files behind an artifact-store interface, and dispatch processing behind a task interface with inline tests and a Celery production adapter.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, pypdf, python-docx, Celery, Redis, local/S3-compatible artifact interface, pytest

**Spec:** `docs/superpowers/specs/2026-08-19-recruitmatch-copilot-design.md`

## Global Constraints

- Accept only PDF, DOCX, and TXT files up to 10 MiB.
- Generate physical artifact names from UUIDs; never use caller filenames as paths.
- Deduplicate within a tenant by SHA-256 while keeping different tenants isolated.
- Preserve extracted text and evidence offsets; unknown fields remain unknown.
- Sensitive attributes may be redacted but never enter a matching profile.
- Tests never require Redis, object storage, or a live model.

---

### Task 1: Resume persistence and lifecycle migration

**Files:**
- Modify: `app/domain/enums.py`
- Create: `app/models/resumes.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/20260819_02_resumes.py`
- Test: `tests/resumes/test_resume_models.py`

**Interfaces:**
- Produces: `ResumeStatus`, `Resume`, `ResumeArtifact`; tenant/hash uniqueness and lifecycle fields.
- Consumes: `Tenant`, `User`, `Base`.

- [ ] **Step 1: Write a failing tenant/hash persistence test**

```python
def test_same_hash_is_unique_only_inside_tenant(session):
    session.add_all([
        Resume(tenant_id="acme", sha256="abc", original_filename="a.pdf", status=ResumeStatus.QUEUED),
        Resume(tenant_id="globex", sha256="abc", original_filename="a.pdf", status=ResumeStatus.QUEUED),
    ])
    session.commit()
    assert session.query(Resume).count() == 2
```

- [ ] **Step 2: Run the test and verify it fails because resume models are absent**

Run: `python -m pytest tests/resumes/test_resume_models.py -v`

- [ ] **Step 3: Implement models and migration**

`Resume` contains `tenant_id`, `uploaded_by`, `sha256`, original filename, media type, byte size, `queued/running/succeeded/failed/deleted` status, JSON profile, error code/message, and timestamps. `ResumeArtifact` contains `resume_id`, storage key, extracted text, and created time. Add `UniqueConstraint("tenant_id", "sha256", name="uq_resume_tenant_hash")`.

- [ ] **Step 4: Run model and migration verification**

Run: `python -m pytest tests/resumes/test_resume_models.py -v`

- [ ] **Step 5: Commit**

```bash
git add app/domain app/models alembic/versions tests/resumes/test_resume_models.py
git commit -m "feat: add resume lifecycle persistence"
```

### Task 2: Secure artifact storage and text extraction

**Files:**
- Create: `app/resumes/__init__.py`
- Create: `app/resumes/artifacts.py`
- Create: `app/resumes/extractors.py`
- Modify: `requirements.txt`
- Test: `tests/resumes/test_artifacts.py`
- Test: `tests/resumes/test_extractors.py`

**Interfaces:**
- Produces: `FilePolicy.validate(filename, media_type, content)`, `LocalArtifactStore.put/read/delete`, `extract_text(filename, content)`.
- Consumes: bytes from an upload boundary.

- [ ] **Step 1: Write failing file safety tests**

```python
def test_storage_key_does_not_contain_caller_filename(tmp_path):
    stored = LocalArtifactStore(tmp_path).put("tenant", "../../secret.txt", b"Python engineer")
    assert "secret.txt" not in stored.key
    assert LocalArtifactStore(tmp_path).read(stored.key) == b"Python engineer"

def test_extractor_rejects_unsupported_extension():
    with pytest.raises(UnsupportedFileError):
        extract_text("resume.html", b"<script>bad</script>")
```

- [ ] **Step 2: Verify tests fail due to absent artifact and extractor modules**

Run: `python -m pytest tests/resumes/test_artifacts.py tests/resumes/test_extractors.py -v`

- [ ] **Step 3: Implement safety policy, local store, and extractors**

Use a UUID storage key under a validated tenant UUID segment. Decode TXT as UTF-8 with BOM support. Use `PdfReader(BytesIO(content))` for PDF and `Document(BytesIO(content))` for DOCX. Normalize NUL and line endings, reject empty extracted text, and enforce 10 MiB before parsing.

- [ ] **Step 4: Verify extraction and storage pass**

Run: `python -m pytest tests/resumes/test_artifacts.py tests/resumes/test_extractors.py -v`

- [ ] **Step 5: Commit**

```bash
git add app/resumes requirements.txt tests/resumes/test_artifacts.py tests/resumes/test_extractors.py
git commit -m "feat: add secure resume artifact extraction"
```

### Task 3: Structured resume profile and processing state machine

**Files:**
- Create: `app/resumes/schemas.py`
- Create: `app/resumes/parser.py`
- Create: `app/services/resume_processing.py`
- Test: `tests/resumes/test_resume_parser.py`
- Test: `tests/resumes/test_resume_processing.py`

**Interfaces:**
- Produces: `Evidence(start, end, text)`, `ResumeProfile`, `ResumeParser.parse(text)`, `HeuristicResumeParser`, `ResumeProcessingService.process(tenant_id, resume_id)`.
- Consumes: artifact store, SQLAlchemy session factory, extracted text.

- [ ] **Step 1: Write failing parser and state tests**

```python
def test_profile_skills_have_source_evidence():
    profile = HeuristicResumeParser().parse("项目：使用 Python 和 FastAPI 构建 RAG 系统")
    assert profile.skills[0].evidence.text in "项目：使用 Python 和 FastAPI 构建 RAG 系统"

def test_processing_failure_records_stable_error(session_factory, broken_store):
    ResumeProcessingService(session_factory, broken_store, HeuristicResumeParser()).process("tenant", "resume")
    resume = session_factory().get(Resume, "resume")
    assert resume.status is ResumeStatus.FAILED
    assert resume.error_code == "artifact_read_failed"
```

- [ ] **Step 2: Verify parser/state tests fail**

Run: `python -m pytest tests/resumes/test_resume_parser.py tests/resumes/test_resume_processing.py -v`

- [ ] **Step 3: Implement deterministic profile and processor**

The baseline parser recognizes a versioned technical skill dictionary, year expressions, education keywords, work/project sections, and emits evidence offsets. The processor atomically transitions queued/failed to running, extracts text, parses a profile with no sensitive fields, stores text/profile, and writes stable failure codes without leaking full resume content into logs.

- [ ] **Step 4: Verify parser/state tests pass**

Run: `python -m pytest tests/resumes/test_resume_parser.py tests/resumes/test_resume_processing.py -v`

- [ ] **Step 5: Commit**

```bash
git add app/resumes app/services/resume_processing.py tests/resumes
git commit -m "feat: add explainable resume profiling"
```

### Task 4: Resume API, idempotency, and task dispatch

**Files:**
- Create: `app/repositories/resumes.py`
- Create: `app/services/resumes.py`
- Create: `app/tasks/__init__.py`
- Create: `app/tasks/dispatcher.py`
- Create: `app/tasks/celery_app.py`
- Create: `app/api/v1/resumes.py`
- Modify: `app/api/v1/router.py`
- Modify: `app/api/v1/schemas.py`
- Modify: `app/config.py`
- Modify: `app/main.py`
- Modify: `docker-compose.yml`
- Test: `tests/resumes/test_resume_api.py`

**Interfaces:**
- Produces: `POST/GET /api/v1/resumes`, `GET/DELETE /api/v1/resumes/{id}`, `TaskDispatcher.dispatch_resume(resume_id, tenant_id)`.
- Consumes: authenticated principal, artifact store, resume repository, processor worker.

- [ ] **Step 1: Write failing upload, idempotency, and isolation API tests**

```python
def test_duplicate_upload_returns_same_resume(authenticated_client):
    first = authenticated_client.post("/api/v1/resumes", files={"file": ("a.txt", b"Python", "text/plain")})
    second = authenticated_client.post("/api/v1/resumes", files={"file": ("copy.txt", b"Python", "text/plain")})
    assert first.status_code == 202
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

def test_other_tenant_cannot_read_resume(two_tenant_clients):
    resume_id = two_tenant_clients.acme_upload()
    assert two_tenant_clients.globex.get(f"/api/v1/resumes/{resume_id}").status_code == 404
```

- [ ] **Step 2: Verify resume API tests fail with 404**

Run: `python -m pytest tests/resumes/test_resume_api.py -v`

- [ ] **Step 3: Implement repository, service, API, and dispatcher**

The service validates before storage, computes SHA-256, returns an existing non-deleted tenant resume on duplicate, creates metadata/artifact transactionally, and dispatches only after commit. Tests inject `InlineTaskDispatcher`; production uses Celery/Redis through `CELERY_BROKER_URL`. Deletion marks the record deleted and schedules artifact cleanup.

- [ ] **Step 4: Add worker service to Compose and verify all tests**

Run: `python -m pytest tests/resumes -q && python -m pytest -q && python -m ruff check app tests scripts`

- [ ] **Step 5: Commit**

```bash
git add app docker-compose.yml requirements.txt tests/resumes
git commit -m "feat: deliver tenant isolated resume pipeline"
```

## Plan Self-Review

- Spec coverage: upload policy, local artifact abstraction, PDF/DOCX/TXT extraction, idempotency, lifecycle, retryable processing contract, structured profile evidence, tenant isolation, migration, API, Celery/Redis production path.
- Explicitly deferred: S3 adapter and live LLM structured parser are infrastructure/provider enhancements; deterministic parsing keeps CI and fallback behavior reliable.
- Types remain consistent: `ResumeProfile` is the persisted profile contract; all service/repository resource reads accept authenticated `tenant_id`.
- No implementation placeholders remain.
