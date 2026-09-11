# RecruitMatch v2 Checkpoint 3 Artifact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace local files and naked object keys with a persistent Artifact state machine and least-privilege MinIO storage.

**Architecture:** PostgreSQL resolves upload idempotency and owns Artifact lifecycle; MinIO owns bytes. Application services pass immutable `ArtifactLocation` values to an S3 adapter. Privacy deletion clears database PII before best-effort object cleanup, and reconciliation repairs recoverable partial failures.

**Tech Stack:** SQLAlchemy, Alembic, boto3-compatible S3 client, MinIO, PostgreSQL, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-24-recruitmatch-productionization-design.md`

## Global Constraints

- One private bucket: `recruitmatch-artifacts`; no original filename or extension in object keys.
- Maximum upload is 10 MiB; multipart upload is disabled.
- ETag is never treated as SHA-256.
- Worker messages contain tenant and owner IDs only.
- Orphan objects are reported, never auto-deleted in v2.

## Implementation alignment (2026-09-11)

- CP2 already owns revision `20260824_12` (legacy chunk-table removal). Task 1 uses `20260824_13`, with `down_revision = "20260824_12"`. Further cutover migrations and CP4 must continue the actual single head rather than reuse a planned number.
- Artifact states are exactly `PENDING`, `AVAILABLE`, `FAILED`, `CLEANUP_PENDING`, `CLEANUP_FAILED`, `DELETED` from frozen §6.2. Source processing states such as `QUEUED` never become Artifact states; `AVAILABLE/QUEUED` below means two separate records.
- Task 1 is additive: add nullable Artifact references to existing Resume/Knowledge owners while keeping the current producers runnable. Task 3 removes the old local-storage fields/adapters in its coordinated cutover; do not backfill old local files or fabricate available S3 objects.
- PostgreSQL owns concurrent deduplication on `(tenant_id, owner_type, sha256)` for active checksums. A newly proposed owner ID must not bypass this uniqueness; concurrent claims return the winning Artifact and owner identity. Tenant-scoped transitions must check persisted state under a row lock or compare-and-set, not rely on a stale ORM instance.
- Tests depending on PostgreSQL or MinIO must enter the real-infrastructure CI lane and remain excluded from SQLite. Update the selectors as each new test directory is introduced.
- All verification below runs under an explicit isolated Compose project name. Cleanup only that project's disposable test volumes; the default project may contain user data.

---

### Task 1: Persist Artifact as an independent state machine

**Files:**
- Create: `app/models/artifacts.py`
- Create: `app/domain/artifacts.py`
- Create: `app/repositories/artifacts.py`
- Create: `alembic/versions/20260824_13_artifacts.py`
- Modify: `app/models/resumes.py`
- Modify: `app/models/knowledge.py`
- Test: `tests/artifacts/test_artifact_repository.py`

**Interfaces:**
- Produces: `ArtifactStatus`, `Artifact`, `ArtifactRepository.claim_upload()`, `mark_available()`, `mark_cleanup_pending()`.
- Consumes: `ArtifactLocation` from Checkpoint 1.

- [ ] **Step 1: Write state and active-checksum tests**

```python
def test_deleted_checksum_can_be_uploaded_again(repo):
    first = repo.claim_upload(upload("same-sha"))
    repo.mark_available(first.id)
    repo.mark_cleanup_pending(first.id)
    repo.mark_deleted(first.id)
    second = repo.claim_upload(upload("same-sha"))
    assert second.id != first.id


def test_active_checksum_is_idempotent(repo):
    assert repo.claim_upload(upload("sha")).id == repo.claim_upload(upload("sha")).id
```

- [ ] **Step 2: Run against PostgreSQL and confirm the Artifact entity is absent**

Run: `docker compose run --rm test-integration pytest tests/artifacts/test_artifact_repository.py -q`

Expected: FAIL importing `ArtifactRepository`.

- [ ] **Step 3: Implement model, partial uniqueness and guarded transitions**

Fields are `tenant_id`, `owner_type`, `owner_id`, nullable `sha256`, `media_type`, `size_bytes`, `status`, `error_code`, timestamps. Add a PostgreSQL partial unique index over active `(tenant_id, owner_type, sha256)` states. Transition methods reject invalid state changes and clear/replace SHA-256 at privacy deletion, when entering cleanup rather than waiting for S3 success. Implement `mark_failed`, `mark_cleanup_failed`, `mark_deleted`, and tenant-qualified immutable `ArtifactLocation` resolution as required by §6.2–6.5. ORM and migration must agree on enum persistence, constraints, and index definitions. Verify concurrent claims with distinct proposed owner IDs, tenant isolation, persisted-state transition guards, cleanup-failure retry, deleted-checksum re-upload, and exact PostgreSQL catalog definitions. Fast SQLite tests may cover the pure transition rules; they cannot substitute for the PostgreSQL tests.

- [ ] **Step 4: Verify repository and clean migration**

Run: `docker compose run --rm test-integration pytest tests/artifacts/test_artifact_repository.py tests/integration/test_pgvector_migration.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Artifact persistence**

```bash
git add app/domain/artifacts.py app/models/artifacts.py app/models/resumes.py app/models/knowledge.py app/repositories/artifacts.py alembic/versions/20260824_13_artifacts.py tests/artifacts/test_artifact_repository.py
git commit -m "feat: persist artifact lifecycle and upload idempotency"
```

### Task 2: Implement bounded S3 Artifact adapter and MinIO initialization

**Files:**
- Create: `app/artifacts/s3.py`
- Create: `ops/minio/init.sh`
- Create: `ops/minio/app-policy.json`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Test: `tests/artifacts/test_s3_artifact_store.py`

**Interfaces:**
- Produces: `S3ArtifactStore.put/read_bounded/delete`, `S3ArtifactStoreHealthProbe.probe`.
- Consumes: typed `ArtifactLocation` and application S3 credentials.

- [ ] **Step 1: Write MinIO contract tests**

```python
def test_key_is_server_derived(store, location, payload):
    store.put(location, payload.stream, len(payload.bytes), payload.sha256)
    assert store.read_bounded(location, 10 * 1024 * 1024) == payload.bytes
    assert payload.filename not in store.debug_key(location)


def test_read_rejects_size_over_limit(store, oversized_location):
    with pytest.raises(ArtifactTooLarge):
        store.read_bounded(oversized_location, 10)
```

- [ ] **Step 2: Run with MinIO and confirm no S3 adapter exists**

Run: `docker compose run --rm test-integration pytest tests/artifacts/test_s3_artifact_store.py -q`

Expected: FAIL importing `S3ArtifactStore`.

- [ ] **Step 3: Implement adapter and one-shot init**

Add `boto3>=1.35,<2` to runtime dependencies and refresh `uv.lock`. Generate keys only inside the adapter. `put()` sets backend-supported checksum, then Head-validates existence, Content-Length and checksum behavior proven by the test. `read_bounded()` performs Head, reads no more than `max_bytes + 1`, and always closes the body. `minio-init` creates/checks the private bucket, application account and policy with admin credentials, then exits.

- [ ] **Step 4: Verify Put/Head/Get/Delete and least privilege**

Run:

```bash
docker compose up -d minio
docker compose run --rm minio-init
docker compose run --rm test-integration pytest tests/artifacts/test_s3_artifact_store.py -q
```

Expected: tests pass; application credentials cannot list users, change policy, or create buckets.

- [ ] **Step 5: Commit MinIO adapter**

```bash
git add app/artifacts/s3.py ops/minio pyproject.toml uv.lock docker-compose.yml .env.example tests/artifacts/test_s3_artifact_store.py
git commit -m "feat: store private artifacts in MinIO"
```

### Task 3: Move resume and knowledge upload/read flows to ArtifactLocation

**Files:**
- Modify: `app/services/resumes.py`
- Modify: `app/services/knowledge_documents.py`
- Modify: `app/services/resume_processing.py`
- Modify: `app/services/knowledge_processing.py`
- Modify: `app/main.py`
- Modify: `app/tasks/celery_app.py`
- Delete: `app/resumes/artifacts.py`
- Delete: `app/knowledge/artifacts.py`
- Test: `tests/artifacts/test_upload_saga.py`

**Interfaces:**
- Consumes: `ArtifactRepository`, `ArtifactStore`, `ArtifactLocation`.
- Produces: validate/hash → DB PENDING → S3 verify → AVAILABLE/QUEUED → dispatch saga.

- [ ] **Step 1: Write concurrent upload and message-payload tests**

```python
def test_concurrent_same_checksum_creates_one_active_artifact(upload_twice):
    first, second = upload_twice()
    assert first.resume_id == second.resume_id
    assert first.artifact_id == second.artifact_id


def test_task_payload_has_no_object_key(fake_dispatcher, service):
    service.upload(
        tenant_id="tenant-a",
        uploaded_by="user-a",
        filename="resume.txt",
        media_type="text/plain",
        content=b"Python developer",
    )
    assert fake_dispatcher.calls == [("tenant-a", "resume-id")]
```

- [ ] **Step 2: Run and confirm local stores/naked keys fail the contract**

Run: `docker compose run --rm test-integration pytest tests/artifacts/test_upload_saga.py -q`

Expected: FAIL because services write local paths/storage keys.

- [ ] **Step 3: Implement the upload saga and bounded Worker reads**

Use a SpooledTemporaryFile limited to 10 MiB, hash while reading, commit PENDING before S3 Put, mark AVAILABLE/QUEUED after verified Put, and dispatch after commit. Dispatch failure leaves a recoverable QUEUED row. Worker resolves `ArtifactLocation` from Repository and calls `read_bounded()`.

- [ ] **Step 4: Run resume, knowledge and upload saga suites**

Run: `docker compose run --rm test-integration pytest tests/artifacts/test_upload_saga.py tests/resumes tests/knowledge -q`

Expected: all upload, duplicate and bounded-read tests pass.

- [ ] **Step 5: Commit upload migration**

```bash
git add -A app tests/artifacts tests/resumes tests/knowledge
git commit -m "refactor: route uploads through persistent artifacts"
```

### Task 4: Implement privacy deletion and reconciliation

**Files:**
- Create: `app/services/artifact_reconciliation.py`
- Modify: `app/services/resumes.py`
- Modify: `app/services/knowledge_documents.py`
- Modify: `app/api/v1/resumes.py`
- Modify: `app/api/v1/knowledge.py`
- Test: `tests/artifacts/test_privacy_delete.py`
- Test: `tests/artifacts/test_reconciliation.py`

**Interfaces:**
- Produces: DB-first privacy delete, retryable cleanup, conservative reconciliation report.
- Consumes: Artifact state machine and S3 adapter.

- [ ] **Step 1: Write DB-first delete and repair tests**

```python
def test_minio_failure_does_not_retain_database_pii(service, failing_store, resume):
    with pytest.raises(ArtifactCleanupPending):
        service.delete(resume.tenant_id, resume.id)
    deleted = service.get_for_audit(resume.tenant_id, resume.id)
    assert deleted.profile == {}
    assert deleted.original_filename is None
    assert deleted.sha256 is None


def test_valid_pending_object_repairs_to_available(reconciler, pending_with_valid_object):
    reconciler.run_once()
    assert pending_with_valid_object.reload().status == ArtifactStatus.AVAILABLE
```

- [ ] **Step 2: Run and confirm current delete order fails**

Run: `docker compose run --rm test-integration pytest tests/artifacts/test_privacy_delete.py tests/artifacts/test_reconciliation.py -q`

Expected: FAIL because privacy cleanup/reconciliation semantics are absent.

- [ ] **Step 3: Implement DB redaction, object cleanup and conservative reconciliation**

In one DB transaction mark lifecycle deleted, clear filename/checksum/profile/text, delete chunks/evidence/citations/explanations, and set `CLEANUP_PENDING`. Then delete S3; map failure to 503 `artifact_cleanup_pending`. Reconciler repairs valid PENDING, fails/cleans invalid PENDING, retries cleanup, and reports orphan counts without deleting them.

- [ ] **Step 4: Verify privacy and reconciliation suites**

Run: `docker compose run --rm test-integration pytest tests/artifacts -q`

Expected: all tests pass, including re-upload after completed deletion.

- [ ] **Step 5: Commit privacy lifecycle**

```bash
git add app/services/artifact_reconciliation.py app/services/resumes.py app/services/knowledge_documents.py app/api/v1/resumes.py app/api/v1/knowledge.py tests/artifacts
git commit -m "feat: reconcile and privacy-delete artifacts"
```

## Checkpoint 3 Gate

```bash
docker compose down -v
docker compose up -d postgres minio
docker compose run --rm minio-init
docker compose run --rm bootstrap
docker compose run --rm test-integration pytest tests/artifacts tests/resumes tests/knowledge -q
! rg "LocalArtifactStore|KnowledgeArtifactStore|storage_key|artifact_key" app
```

Expected: real MinIO tests pass and production code contains no local Artifact adapter or naked persisted key.
