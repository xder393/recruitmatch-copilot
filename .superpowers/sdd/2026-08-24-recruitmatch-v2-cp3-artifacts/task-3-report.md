# CP3 Task 3 implementation report

Status: DONE. This final report replaces all earlier pre-implementation gate prose. Base `f0bdd3b`; branch `codex/recruitmatch-v2`. The task commit contains this report; its hash is returned separately.

## Implementation

- Resume and Knowledge uploads validate/hash a bounded stream, atomically commit winning Source plus PENDING Artifact, Put at the persisted typed ArtifactLocation, commit AVAILABLE/queue state, then dispatch tenant/owner IDs only. Losing proposals create no extra Source, Put or dispatch. Knowledge same bytes/same classification is idempotent; different classification returns stable `knowledge_document_type_conflict` (409).
- Storage exceptions map explicitly to bounded ArtifactErrorCode values. Failed uploads stay FAILED without implicit duplicate retry. Broker failure preserves durable queued Source with `task_dispatch_failed` for later recovery.
- Workers resolve exact tenant/type/owner/Artifact anchors and require AVAILABLE. Bounded reads are checked against trusted database byte length and SHA-256 before extraction. Early/missing/cleanup messages do not process; terminal failures do not implicitly retry. Publication revalidates Source then Artifact.
- Async routes use Starlette's bounded thread pool for synchronous orchestration, sequentially using one UnitOfWork. Normal uploads read the underlying stream in 64 KiB chunks into a bounded SpooledTemporaryFile, hash incrementally, and close both request and temporary streams on all paths.
- Bounds: compressed input 10 MiB, DOCX 512 entries/16 MiB expanded aggregate/8 MiB entry/200:1 ratio, extracted output 1 MiB. Locked pypdf 6.16.2 filter/stream limits are configured once at module import: 8 MiB filter/declared/array caps, 1 MiB recovery input, external JBIG2 decoder disabled. Before extraction, referenced traversal is bounded to 10,000 objects/16 MiB decoded resources, 100 pages, 2 MiB page content/8 MiB total. The installed version uses `FLATE_MAX_BUFFER_SIZE`; code and type checks verified the actual constant. Signatures, parse failures and synthetic resource bombs reject before storage; no OCR or model calls.
- API/Celery composition uses S3ArtifactStore(S3Settings.from_env().client()), with explicit typed Fake injection for tests. Removed local adapters, persisted naked keys, settings and local-volume wiring; API/Worker now depend on MinIO initialization. Services/messages contain no SDK or local key construction.
- Resume extracted text is Source-owned. GenerationWriter/Fake and source-index backfill enforce exact linked Artifact semantics. Added cutover revision 20260911_15 and ADR 0004, including retained tombstone recovery contract; the six storage states and separate Source authority remain unchanged.

## Command conventions and isolation

All commands ran in `/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2`. `DC` below means exactly:

```sh
docker compose --env-file .env.example -p recruitmatch-cp3-task3-sep11 --profile test
```

Focused iterations used `DC run --rm --no-deps` followed by these `MOUNTED` arguments:

```sh
-v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app:/app/app:ro
-v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro
```

Migration iterations also mounted `-v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/alembic:/app/alembic:ro`. These were current read-only sources on matching locked dependency layers, not a claim that an older image contained final sources. Final verification used newly rebuilt images **without source mounts**. Test services forced AI off/key empty. Only the dedicated synthetic project and unique `cp3_migration_<uuid>` databases were used. Historical migration tests create/drop isolated databases rather than downgrade the main synthetic schema.

## RED/GREEN evidence

1. `DC run --rm --build test-integration pytest tests/artifacts/test_upload_saga.py -q`: RED 3 failed; `upload must use persistent typed ArtifactLocation` observed the old tenant string. GREEN these three passed after Resume cutover (8 passed alongside original extractors).
2. `DC run --rm --no-deps MOUNTED test-integration pytest tests/artifacts/test_upload_saga.py tests/resumes/test_extractors.py -q`: RED 4 failed/8 passed; misleading signatures and DOCX expansion bomb did not raise. GREEN these four after bounded validation; 12 passed with a newly added storage-failure test still RED.
3. Failed-Put RED escaped ArtifactChecksumMismatch. `pytest tests/artifacts/test_artifact_repository.py -q -k tombstone`: RED 2 failures from missing repository methods. GREEN combined `pytest tests/artifacts/test_upload_saga.py tests/artifacts/test_artifact_repository.py -q`: 65 passed in 0.85s, including bounded persisted errors, no implicit retry, and retained tombstone methods.
4. Knowledge RED (`test_upload_saga.py -q -k knowledge`) failed on naked-string Put. Late-Put RED (`-k late_put`) had 3 failures because deletion left PENDING. GREEN `pytest tests/artifacts/test_upload_saga.py -q`: 8 passed in 0.73s after Knowledge cutover and approved cancellation/compensation protocol.
5. API RED `DC run --rm --no-deps MOUNTED test-unit pytest tests/resumes/test_resume_api.py -q -k slow_put`: controlled store hold prevented health completing until release, 1 failed. Worker RED `test_upload_saga.py -q -k 'early_message or trusted_claim'`: 6 failed, early Source mutation and incorrect legacy missing error. GREEN broader `pytest tests/artifacts/test_artifact_catalog.py tests/artifacts/test_upload_saga.py tests/resumes tests/knowledge -q`: 60 passed in 5.33s, including the API/Worker regressions after fixture/model cutover.
6. Migration RED `pytest tests/artifacts/test_cutover_migration.py -q`: 2 failed, missing Source text/legacy refusal; lineage RED `-k lineage`: 2 failed, unmapped/unrefused lineage. GREEN 4 passed in 0.88s. Added inactive Knowledge and sanitized/unsanitized deletion cases; these pass in final integration.
7. Focused GREEN `pytest tests/resumes/test_extractors.py tests/artifacts/test_upload_saga.py tests/retrieval/test_generation_switch.py -q`: 69 passed in 1.26s, including the explicit real PostgreSQL+MinIO upload/Worker path.
8. Self-review regression RED: late storage error after recovered AVAILABLE publication tried deleting the live object and hit a transition error. Guarded cleanup-state selection; GREEN `pytest tests/artifacts/test_upload_saga.py -q`: 18 passed in 0.90s.
9. Broad focused `pytest tests/artifacts/test_cutover_migration.py tests/artifacts/test_upload_saga.py tests/foundation tests/matching tests/operations tests/integration/test_retrieval_composition.py -q`: 128 passed/1 failed, stale expected Alembic head. Corrected expectation to 15; final lanes pass.
10. New tombstone guard test initially had a fixture ambiguity: expired Artifact ID access autoflushed before repository invocation. Corrected fixture to `expire_on_commit=False`, then explicitly removed the implementation's no-autoflush guard. `DC run --rm --no-deps MOUNTED test-integration pytest tests/artifacts/test_artifact_repository.py -q -k persisted_state_without_autoflush`: RED 1 failed/61 deselected (`DID NOT RAISE ArtifactTransitionError`). Restored persisted-state guard: GREEN 1 passed/61 deselected in 0.54s. The corrected RED/GREEN test is committed.

Intermediate fixture errors involved removed Settings/keys/models and were fixed, not skipped. Historical migrations now use raw old-schema inserts in isolated databases. Obsolete local-adapter tests were removed with the deleted adapters; typed Fake and real S3/saga coverage replaces them.

## Final verification on final sources

After the last production/test edit, no source mounts:

```sh
DC build bootstrap test-integration minio
DC run --rm --no-deps test-unit
DC run --rm test-integration
DC run --rm --no-deps test-unit sh -c 'ruff check app tests scripts && ruff format --check app tests scripts && mypy app/models app/retrieval app/services app/ai app/tasks && uv lock --check'
DC run --rm --no-deps test-unit python -c 'import app.main; import app.tasks.celery_app; import app.artifacts.s3; import app.services.resume_processing; import app.services.knowledge_processing; print("production imports passed")'
```

- Build: exit 0, all three images Built. Docker reused identical locked dependency layers and copied current source/tests/migrations. Final test image config `sha256:f60f1c12688d0d21bf673689749ced5114b8e78967498b4fb96992993e652907`; manifest list `sha256:c086d888679e9ac13897b2337370c0f944de136249485036318f99bf5ee10aaa`.
- Unit default lane: **225 passed in 15.37s**, exit 0. Includes Resume/Knowledge API, controlled slow-Put/stream closure, extractors, foundation, matching, operations and web. Configured PostgreSQL-only files/artifacts/integration are exercised below.
- Integration default lane (`pytest tests/integration tests/retrieval tests/artifacts -q`): **214 passed in 14.44s**, exit 0. Includes actual PostgreSQL+MinIO service upload → persistent location → Worker read/extraction/index publication, duplicate processing, S3 adapter, concurrency, migrations and retrieval. This is not only Fake saga plus unchanged adapter coverage.
- Static: `All checks passed!`; `176 files already formatted`; `Success: no issues found in 38 source files`; lock check `Resolved 110 packages in 1ms`; exit 0.
- Import check: `production imports passed`, exit 0.
- `git diff --check`: clean. `rg -n 'LocalArtifactStore|KnowledgeArtifactStore|storage_key|artifact_key|ARTIFACT_DIR|KNOWLEDGE_ARTIFACT_DIR' app docker-compose.yml .env.example`: no matches. Historical identifiers remain only where needed for old migrations/fixtures, not runtime.

## Migration preservation proof

Head 20260911_15 follows actual 20260911_14; no revision reuse. Guards precede DDL, rejecting every non-privacy-deleted anchorless Resume and every retained anchorless Knowledge, including inactive. Anchorless deleted Resume requires validated sanitization. Unsupported installations need explicit operator export/backup and approved one-time reset; this migration never resets them or fabricates S3 availability. Automatic downgrade refuses because local identities cannot be reconstructed.

Tests compare valid Artifact rows before/after, preserve Source text, and compare chunk fields apart from authorized remapping. ResumeArtifact IDs map to the linked independent Artifact ID, **not Resume.id**. Invalid tenant/Source/non-null lineage fails before DDL; NULL, Knowledge document IDs and Job NULL semantics persist. Current generation tests enforce exact tenant/type/owner/linked Artifact. Historical Task 1 migrations retain revision-appropriate coverage instead of weakening invariants for the new ORM.

## Concurrency and recovery proof

- Barrier-controlled distinct proposed-owner races create one active claim/Source/Put, with tenant/winning-owner-only messages. Concurrent Knowledge classification tests preserve exactly one unchanged winner and conflict the other.
- A separate PostgreSQL session observes Owner+PENDING before Put. Commit failure before that boundary leaves no claim/object; failure after retains a recoverable pending anchor. Put and broker failures have distinct durable results.
- Publication/delete use Source→Artifact order. Duplicate claim holds Artifact but reads existing Source without locking and finishes the transaction, avoiding the reverse lock-order cycle.
- Controlled slow Put is held while deletion scrubs Source and follows PENDING→FAILED→CLEANUP_PENDING in the DB-first transaction, clearing checksum, then DELETED. Late Put cannot publish/dispatch. Live compensation deletes exact location; injected late Delete failure persists bounded cleanup error while retaining DELETED. Simulated process exit after Put bypasses compensation but retains an inspectable exact DELETED tombstone with its associated object.
- Tombstone paging is immutable, tenant-qualified, ascending ID/exclusive cursor, page limit 1–1000. Success/missing never removes the anchor. Result recording refreshes persisted state without dirty-instance autoflush, validates tenant/type/owner/DELETED, and changes only sanitized error metadata.
- Task 4 must fairly and repeatedly revisit all retained tombstones, including no-error/missing observations, and count DELETED cleanup errors in later health/metrics/invariants. This task supplies live compensation and the crash-recovery anchor/contract, not the future sweep. No DELETED resurrection, second state machine or unassociated orphan deletion.

## Files and self-review

Production: `.env.example`, `docker-compose.yml`; API Resume/Knowledge routes; config/main/Celery composition; Resume/Knowledge models/exports; Artifact/Resume/Knowledge/matching repositories and ports; upload/processing/backfill services; extractors and generation writer. Added `app/artifacts/cleanup.py`, `app/artifacts/errors.py`, revision `20260911_15_artifact_cutover.py`, and `docs/adr/0004-artifact-upload-cutover-and-tombstones.md`. Deleted both local Artifact adapters.

Tests: new upload-saga/cutover suites and typed Artifact/isolated migration helpers; updated Artifact catalog/repository, Resume, Knowledge, generation/pgvector, foundation migrations, composition integration, AI readiness, matching, operations, web and application/Fake fixtures. Deleted obsolete local-adapter test module. This report is force-tracked; controller brief/ledger remain unstaged and untouched.

Self-review checked final code against the brief: separate Source/storage authority, exact lineage, transactions/lock order, idempotency, no implicit retry, bounded errors, compensation and persisted late failure, stream/parser bounds and closure, event-loop boundary, pre-DDL migration preservation/refusal, Source text/index contract, S3-only runtime, synthetic isolation and final-image freshness. Found/fixed live-object deletion on late storage error and dirty-instance tombstone guard risk. Final whitespace, lint, formatting, types, imports and lock checks passed.

## Approved boundaries

No unresolved Task 3 blocker. Full cross-Source privacy redaction, pending/queue reconciliation, repeated fair tombstone sweep, scheduling and later metrics/invariants remain Task 4. Indefinite exact tombstone retention/inspection cost is deliberate; retirement needs a future proven completion/fencing protocol. Unsupported legacy data requires an explicit operator decision; restore a pre-cutover backup for rollback.

No real LLM, user `.env`, default demo reset, global proxy change, controller ledger edit, subagent/reviewer, main mutation, merge or push occurred. Dedicated synthetic services remain for independent review; no user data was removed.
