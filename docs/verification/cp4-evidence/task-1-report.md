# CP4 Task 1 implementation report

Status: DONE. Implementation commit: `e4920ea29a2131dcbb4c317279391c11cb80167f`
(`feat: fence asynchronous processing with database leases`). This report is
force-tracked in the following evidence-only commit; the implementation SHA
identifies the exact tested production/test tree.

Base: `6ab5e58636ec938422df104569456ac1c073fac1`, branch `codex/recruitmatch-v2`.
Work directory: `/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2`.
Only Task 1 was implemented. No processors, SourceIndexer or GenerationWriter
behavior was changed. No push, merge, real environment, default Compose project,
model call, host Python/venv test, subagent or reviewer was used.

## Implementation and consumer contract

Added six lease fields to both Sources: processing_attempts, BIGINT NOT NULL
processing_lease_epoch, processing_lease_expires_at, processing_lease_owner,
next_retry_at and non-null queued_at. Existing error_code remains the sole
processing error column. Existing status columns and public state translations
are retained: ResumeStatus; Knowledge uploaded/processing/ready/failed.
Knowledge inactive remains deactivation and lifecycle_status must remain active.

Revision `20260912_18` follows `20260911_17`, preserving existing Source content,
timestamps, status and derived data. queued_at is backfilled from updated_at,
with created_at fallback. Attempts/epoch start at zero; owner/expiry remain NULL.
Legacy RUNNING remains RUNNING and can later be claimed; migration does not
execute, dispatch, retry or reset anything. There is one Alembic head. Downgrade
drops only new lease metadata. ADR 0005 documents stopped-worker upgrade policy.

The infrastructure-independent port is `app.repositories.ports.LeaseRepository`,
composed as `RecruitingUnitOfWork.leases`. SQLAlchemy adapter is
`app.processing.leases.LeaseRepository`. Exact consumer signatures:

```python
claim(tenant_id: str, source_type: ArtifactOwnerType | str,
      source_id: str, owner: str, *, duration: timedelta) -> ClaimResult
renew(lease: ClaimedLease, *, duration: timedelta) -> bool
finalize(lease: ClaimedLease) -> bool
fail(lease: ClaimedLease, error_code: str,
     *, next_retry_at: datetime | None = None) -> bool
finalize_owned(lease: ClaimedLease) -> AbstractContextManager[None]
```

Immutable `ClaimedLease` fields: tenant_id, source_type, source_id, artifact_id,
owner, epoch, expires_at. Exact Artifact ID fences the input lineage as well as
tenant/type/source/owner/epoch. expires_at is the claim-time observation, not an
additional ownership predicate; a renewed token remains usable. ClaimResult
contains disposition and optional lease. ClaimDisposition values are CLAIMED,
DUPLICATE_ACTIVE, TERMINAL, DEFERRED; ProcessDisposition values are COMPLETED,
DUPLICATE_ACTIVE, TERMINAL, RETRY_SHORT.

Claim locks Source then exact owned Artifact, requiring AVAILABLE for acquisition.
Eligible QUEUED and expired/unleased RUNNING increment attempts and epoch exactly
once. A live duplicate changes no counters, errors or timestamps. Missing,
FAILED/SUCCEEDED, privacy-deleted and inactive Sources return TERMINAL. Future
retry or missing/unavailable Artifact returns DEFERRED. FAILED is never silently
claimed even when retry is due; Task 4 must explicitly requeue it. Each operation
samples PostgreSQL clock_timestamp() after relevant lock waits, not transaction
start now(). Renewal and terminal writes revalidate persisted lifecycle, status,
identity, exact AVAILABLE Artifact, epoch, owner and non-expired lease; False
means lost ownership without writes. Terminal writes clear owner/expiry.

Task 2 publication contract:

```python
# Clean UoW, with no pending writes before the guard.
with uow.leases.finalize_owned(lease):
    # Derived writes via repositories sharing this exact UoW.
    # No independent transaction or commit inside this block.
    ...
uow.commit()  # Only after successful context exit.
```

The guard locks/revalidates ownership before a savepoint, flushes derived writes
inside it, and rechecks expiry before SUCCEEDED publication. Body exceptions or
exit-time ownership loss roll back all guarded writes. Successful guard exit
still leaves the caller's outer transaction uncommitted and locks retained.
Initial `Session.begin_nested()` autoflush is accounted for: pending
new/dirty/deleted state raises ValueError before guard queries/savepoint creation.
Clean loaded objects are permitted. No Session or ORM object crosses the port.
The controller accepted this design and owns Task 2 transaction-bound generation
publication; current independent GenerationWriter.activate must not be called
under these Source locks.

Exceptions:

- `LeaseOwnershipLost("processing_lease_lost")` on guard entry/exit ownership loss.
- ValueError for malformed identities, type, owner, duration, lease, epoch, clock,
  processing code or retry timestamp. IDs are nonempty strings at most 36 chars
  without surrounding whitespace; owner matches `[A-Za-z0-9_.:@/-]{1,100}`;
  duration is positive and at most one day; epoch is a positive signed BIGINT;
  error_code matches `[a-z][a-z0-9_]{0,99}`; timestamps must be timezone-aware.
- `ValueError("processing_publication_requires_clean_session")` for pending
  writes before finalize_owned. No methods commit the outer transaction.
- Existing database/UoW exceptions retain their existing handling; derived-write
  exceptions propagate after savepoint rollback.

## TDD evidence

Every Compose command used the exact required synthetic project prefix. All
commands below ran from the work directory above.

RED 1, before lease implementation (rebuilt unmounted baseline test image):

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-integration pytest tests/integration/processing/test_leases.py -q -x
```

Exit 1: `1 failed in 0.55s`, AttributeError at the behavioral claim call:
`'SqlAlchemyUnitOfWork' object has no attribute 'leases'`. Missing arbitration
was the expected failure; fixtures created real PostgreSQL Source/Artifact rows.

RED 2, before migration implementation (diagnostic test-only read-only mount):

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/integration/processing:/app/tests/integration/processing:ro test-integration pytest tests/integration/processing/test_lease_migration.py -q -x
```

Exit 1: `1 failed in 0.76s`; existing-data assertions reached the absent
`queued_at` result column. The baseline migrated only through revision 17.

RED 3 / first implementation diagnostic check:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app:/app/app:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/alembic:/app/alembic:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-integration sh -c 'python scripts/bootstrap.py && pytest tests/integration/processing -q'
```

Exit 1: `2 failed, 83 passed in 1.17s`. Both Source variants failed
`test_publication_requires_guard_before_pending_writes`: the missing clean-session
precondition allowed entering publication with already-pending changes. Added
the guard-first precondition only after this failure.

GREEN diagnostic expanded suite:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app:/app/app:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/alembic:/app/alembic:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-integration pytest tests/integration/processing -q
```

Exit 0: `99 passed in 1.97s`. Self-review then removed two inapplicable
Resume/inactive parameter combinations which had returned early; the final
suite has 97 meaningful cases (92 lease cases and 5 migration cases), no skips.

## Final rebuilt-image verification

Source images were rebuilt with:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 build bootstrap test-integration
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 build test-integration
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps bootstrap
```

All exit 0; bootstrap reported `seeded_job_templates=0`. The test image was
rebuilt again after a final test-only SQL whitespace wrap to fix Ruff E501. The
controller requested repeating both complete lanes on that exact final tree.
Final acceptance used no source mounts. Final Docker build config/image IDs:

- Runtime `recruitmatch-cp4-sep12-app`: `sha256:fb08de826005c02b06f957ea6a73c73100a754353da66a5080145169d6c976a9`;
  manifest list `sha256:2902843201cd24e64e2a95b58c58a221785f208f99f634c02fb46a436a5d51fc`.
- Test `recruitmatch-cp4-sep12-app-test`: `sha256:eff0e91a72b431f6bb4ec57bc75554f8ebe290ae0a02026b2c683f52858e4de1`;
  manifest list `sha256:f9a5a3be50fe7720ca657f8d341348846b0070f8cb9ac9c63ace1bd3da2e1f45`.

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-integration
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-integration pytest tests/integration/processing -q
```

Final results: respectively exit 0, `229 passed in 13.72s`; exit 0,
`370 passed in 17.89s`; exit 0, `97 passed in 2.20s`. Unit service's configured
SQLite lane excludes tests/integration; all new lease tests used PostgreSQL.
Initial full lanes before the test SQL whitespace fix also passed 229/370 in
14.47s/18.49s; these are supplementary, not the final-tree acceptance evidence.

Exact final static gate:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit sh -c 'ruff format --check app/processing app/models/resumes.py app/models/knowledge.py app/repositories/ports.py app/repositories/unit_of_work.py app/repositories/sqlalchemy_unit_of_work.py alembic/versions/20260912_18_processing_leases.py tests/integration/processing tests/foundation/test_migrations.py tests/integration/test_pgvector_migration.py tests/artifacts/test_artifact_repository.py tests/artifacts/test_privacy_migration.py && ruff check app/processing app/models/resumes.py app/models/knowledge.py app/repositories/ports.py app/repositories/unit_of_work.py app/repositories/sqlalchemy_unit_of_work.py alembic/versions/20260912_18_processing_leases.py tests/integration/processing tests/foundation/test_migrations.py tests/integration/test_pgvector_migration.py tests/artifacts/test_artifact_repository.py tests/artifacts/test_privacy_migration.py && mypy --follow-imports=silent app/processing app/repositories/ports.py app/repositories/unit_of_work.py app/repositories/sqlalchemy_unit_of_work.py app/models/resumes.py app/models/knowledge.py && uv lock --check --offline'
```

Exit 0: `15 files already formatted`; `All checks passed!`;
`Success: no issues found in 8 source files`; `Resolved 110 packages in 1ms`.
`git diff --check` also exited 0. Final output contained no test/static warnings.
During iteration mypy found three Optional/union narrowing errors, corrected
with explicit casts/narrowing; Ruff found one long test SQL line, corrected.
No dependency or lockfile changes were made.

## Files and self-review

Added: app/processing/__init__.py, leases.py, outcomes.py;
alembic/versions/20260912_18_processing_leases.py;
docs/adr/0005-processing-lease-contract.md;
tests/integration/processing/test_leases.py and test_lease_migration.py.

Modified: app/models/resumes.py and knowledge.py; app/repositories/ports.py,
unit_of_work.py and sqlalchemy_unit_of_work.py; four existing test files with
legitimately affected current-head assertions: tests/foundation/test_migrations.py,
tests/integration/test_pgvector_migration.py,
tests/artifacts/test_artifact_repository.py and test_privacy_migration.py.
Only this report is force-tracked from ignored SDD evidence.

Self-review checked exact Source→Artifact order, no transaction commits in the
adapter, no ORM in ports, status translation, no-op mutations, clock placement,
stale identity-map refresh under no_autoflush, savepoint rollback, explicit
requeue policy, existing error storage, migration data preservation and scope.
Real PostgreSQL tests cover simultaneous claim winner/duplicate, blocked-clock
claim/renew/finalize/fail, old epochs, expired renewal/finalization/failure,
attempt/epoch and BIGINT range, wrong tenant/type/id/owner, same IDs across types,
exact Artifact lineage, privacy deletion/deactivation, unavailable Artifacts,
future retry, terminal no-op, malformed input, bounded error code, caller rollback,
successful guard/entry loss/exit loss/body error/outer rollback, and dirty-session
fences. Migration snapshots compare every pre-existing Source column across five
state combinations, verify BIGINT/non-null and retained extracted content, and
exercise recovery and metadata-only downgrade.

No unresolved implementation concerns. Operational boundary: Task 1 supplies the
lease infrastructure; existing processors are not yet lease-aware. Do not treat
this checkpoint alone as enabling end-to-end processing fencing. Task 2 owns
processor/generation integration; Task 4 owns explicit requeue and recovery.
The synthetic Compose PostgreSQL/Redis/MinIO services were retained for controller
review and cleanup. No volume removal or environment teardown was performed.
