# CP4 Task 3 implementer report

Status: DONE. Base: `9fdeecffebf8cd14494253d70ee9d58844f19e97`.
Worktree: `/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2`.
Branch: `codex/recruitmatch-v2`. No merge, push, scheduler, health service or cleanup performed.
Worked alone. Used test-driven-development (including writing-good-tests) and
verification-before-completion skills. Read using-superpowers; its subagent-stop
instruction applies. Read the full implementer prompt, Task 3 brief/context,
reviewed ADR and frozen specification §7 before implementation. Controller approved
the concrete retry contract and strengthened claim cap/Artifact snapshot/DB failure
boundaries before those changes. The transport interruption preserved all work;
the resumed task continued the same implementation and captured RED evidence.

## Implemented contract

- Added eight validated timing/budget settings, `.env.example` defaults, and shared
  Compose propagation: lease 300s, max claims 5, soft 540s, hard 600s, Redis
  visibility 900s, short retry 30s, long retry 300s, margin 30s. Values must be
  positive integers (including rejection of bool/NaN/infinity), lease <=86400,
  soft<hard, visibility>max(hard,short)+margin, and long>short.
- Celery late ACK, worker-loss rejection, prefetch 1, ignored results and ignored
  error results, no backend, configured actual soft/hard/Redis transport limits.
  Nonempty CELERY_RESULT_BACKEND is rejected because the locked Celery version
  otherwise lets this environment variable override an explicit None config.
- Actual task entrypoints ACK COMPLETED, DUPLICATE_ACTIVE, TERMINAL, DEFERRED and
  LEASE_LOST. Only RETRY_SHORT invokes the fixed configured countdown after the
  processor has committed. `max_retries=None` avoids a competing Celery business
  budget: request.retries can be high while PostgreSQL is the sole claim authority.
  Automatic publish retry is disabled. A failed Celery retry publish (Reject)
  produces a bounded dispatch error log and ACK, preserving durable queued work.
- Narrow task logging redacts argument representations in dispatch and worker
  request context. PersistenceUnavailable produces only a bounded warning and ACK.
  Unknown/programming errors remain actual Celery FAILURE using a sanitized
  ProcessingTaskFailed exception, without storing an error result or logging free
  exception text. The real Celery trace logging path has regression coverage.
- API and Worker composition both inject lease duration and RetryPolicy. Worker
  timeout exception types are injected, so services do not import Celery/billiard.
- Existing parsed Resume pair inference, deterministic LLM fallback, exact
  Artifact/tenant/lifecycle/epoch fencing, old active generations and atomic
  profile/trace/generation publication remain intact.

## Exact persistence APIs and Task 4 handoff

`LeaseRepository` port and SQL adapter now provide:

```python
claim(tenant_id, source_type, source_id, owner, *, duration, max_attempts=5) -> ClaimResult
schedule_retry(lease, error_code, *, short_delay, long_delay, max_attempts) -> ProcessDisposition
requeue_due(tenant_id, source_type, source_id, *, expected_epoch, expected_artifact_id, max_attempts) -> bool
```

`RetryPolicy(max_attempts=5, short_seconds=30, long_seconds=300)` lives in
`app/processing/retry.py`. Transient codes are exactly `storage_unavailable`,
`embedding_failed`, `processing_timeout`. All methods leave commit to their caller.

The cap is checked in the actual claim transaction under Source→Artifact locks.
A live fifth-attempt duplicate is unchanged. Eligible expired/queued work already
at the cap is durably FAILED/processing_attempts_exhausted, owner/expiry/due cleared,
with no epoch or attempt increment. **Both processors commit non-CLAIMED results**,
because a TERMINAL cap result now includes that guarded write. The real-PG
`test_processor_claim_cap_is_committed_on_terminal_return` exercises a fifth
expired RUNNING delivery through both ordinary processors and verifies FAILED
after reopening the database session.

schedule_retry requires the exact live lease and AVAILABLE owner-linked Artifact.
At attempts 1–2 below budget it atomically records failure and explicitly transitions
RUNNING→QUEUED/uploaded with DB-clock+short due time and new queued_at, returning
RETRY_SHORT. Attempts 3–4 below budget become FAILED with DB-clock+long due time and
COMPLETED. At the configured cap it stores terminal FAILED with the original bounded
failure code and no due time. Permanent failures use ordinary fenced fail with no
due time. False/lost retry ownership causes callers to roll back the whole UoW,
including any preceding Knowledge index error; stale workers cannot leak derived
failure writes while returning LEASE_LOST.

Task 4's bounded due scan selects active FAILED with a known transient code,
next_retry_at <= database clock, and attempts < configured cap. requeue_due consumes
the scan's exact epoch and Artifact ID, relocks and revalidates every predicate,
then sets QUEUED/uploaded, clears due/owner/expiry, updates queued_at and preserves
attempts/epoch/error. Commit first, dispatch the ordinary tenant/Source task second.
Concurrent consumers cannot requeue the same snapshot twice. FAILED ordinary
delivery is always terminal, even after its due time.

Short QUEUED next_retry_at is execution eligibility. Early messages ACK DEFERRED;
at due time a normal claim is eligible. Beat must not dispatch future due QUEUED
rows, and must not overwrite queued_at to pace redispatch. The timestamp measures
the actual queue cycle. Task 4 owns bounded dispatch pacing and stable dispatch
failure recording. Its expired-lease recovery must preserve the same cap, never
reset attempts/epoch or claim RUNNING itself. At-cap expired rows must eventually
terminalize (ordinary processor delivery already performs the guarded cap write),
not be retried as a sixth attempt or silently treated as retry-eligible FAILED.

Crash windows: precommit failure leaves the old lease; committed short queue plus
publish failure/death leaves due QUEUED; long FAILED requires explicit guarded
requeue, whose commit/publish gap likewise leaves QUEUED. The future Task 4 stale
queue/expired lease lanes recover these windows. No special Resume index-mode
payload is introduced; the persisted valid parse pair remains its authority.

SQL UoW and independent GenerationWriter transaction boundaries translate only
OperationalError, DisconnectionError and SQLAlchemy pool TimeoutError to
PersistenceUnavailable, retaining the original cause. ProgrammingError and other
unknown/schema/type errors remain faults. EmbeddingFailure is raised only around
the external embedding call. Narrow processor catches prevent DB/unknown writer
errors from becoming embedding fallback. Tests physically close real PostgreSQL
connections during staging and retry UPDATE, verifying the prior lease remains
and no fabricated FAILED/next_retry_at/profile/index error is committed.

The complete human-facing policy is appended to ADR 0005. No migration was needed.

## TDD and diagnostic evidence

All commands below ran from the worktree. Diagnostic source mounts were restricted
to this synthetic project; final gates below used no source mounts.

1. Initial unit RED:
   `docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-unit pytest tests/processing/test_celery_delivery.py -q`
   Exit 1: 12 failed, 10 passed. Expected failures: task_ignore_result was False,
   timing/budget fields were absent, and configured short countdown was absent.
   Existing duplicate outcome branches already ACKed; no false claim that this
   predecessor still retried live duplicates.
2. PG retry RED, same prefix and tests mount with test-integration:
   `pytest tests/integration/processing/test_retry.py -q`: exit 1, six expected
   missing durable-transition failures. Adding `-k crash` yielded two expected
   failures: the expired fifth lease was still CLAIMED.
3. PG processor failure classification RED:
   `pytest tests/integration/processing/test_retry_processors.py -q`: exit 1,
   six expected failures: storage errors returned COMPLETED instead of short
   retry, and OperationalError/ProgrammingError became soft indexing results.
4. Actual Celery/backend/privacy RED with predecessor image and tests mount:
   `pytest tests/processing/test_celery_delivery.py -q -k 'environment or actual_retry or unknown_task'`:
   exit 1, four expected failures. Backend env was accepted; broker retries=999
   tripped the old Celery retry limit; unknown exceptions leaked private text.
5. Additional predecessor checks for `soft_time or claim_cap or short_publish`:
   exit 1, six failures proving missing timeout injection, missing committed cap,
   and absent durable next_retry_at after a failed publish. These were regression
   checks against the retained predecessor image after the initial RED cycles.
6. First focused GREEN with current app+tests diagnostic mounts: 14 PG cases passed;
   later expanded retry coverage: 35 passed. Unit delivery/resume/indexing focus:
   34 passed; expanded delivery tests: 26 passed at that point.
7. Self-review race RED on the working implementation:
   `pytest tests/integration/processing/test_retry_processors.py -q -k lost_retry`:
   exit 1, one failure: `search_index_error_code == 'embedding_failed'` leaked
   when the final retry fence lost ownership. Added UoW rollback before returning
   LEASE_LOST; the identical command then passed 1 test (exit 0).
8. First full unmounted suite exposed one stale test double: unit 267 passed/1
   failure while integration 430 passed. The double raised RuntimeError during
   reconciliation and labeled it an embedding error. Reworked that test to fail
   the real external embed adapter, asserting durable retry and retained active
   generation. Focused knowledge suite passed 6 (exit 0).
9. Final additional PostgreSQL retry-UPDATE connection-loss regression passed
   2 tests (exit 0), then rebuilt and reran all final gates on that test tree.

## Final rebuilt, unmounted gates

Every command exited 0 on the final application/test tree:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 build bootstrap test-unit
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-integration
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit sh -c 'ruff check app tests scripts && ruff format --check app tests scripts && mypy app/models app/retrieval app/services app/ai app/tasks && mypy app/processing app/config.py app/repositories/ports.py app/repositories/sqlalchemy_unit_of_work.py alembic/versions/20260912_18_processing_leases.py && uv lock --check --offline'
```

- Unit: **268 passed in 16.93s** (predecessor 237; +31).
- Integration/retrieval/artifacts: **432 passed in 21.28s** (predecessor 394; +38).
- Ruff: all checks passed; 195 files already formatted.
- Actual CI mypy targets: success, 39 files.
- Additional processing/config/repository/lease migration mypy: success, 9 files.
- Offline lock: resolved 110 packages in 3ms, no lock changes.
- `git diff --check`: exit 0 before report/staging, repeated before commit.

Final images from `docker image inspect`:

- `recruitmatch-cp4-sep12-app:latest`: `sha256:e05dabc37e2f9b497669360087c25fba5a8b067576ca930c0f0532714357292d`.
- `recruitmatch-cp4-sep12-app-test:latest`: `sha256:88048658eeddba636bfe73d32bf4d02c7070ede6f50002bf765f99005ae35c04`.
- Runtime image application config digest: `sha256:29521835186ef2220d2103cf15c56a0b6983c4ad68a22317c4a814e34be444f0` (unchanged across test-only rebuilds).

## Compose/runtime configuration verification

Used synthetic nondefault values with command-scoped environment assignments:
PROCESSING_LEASE_SECONDS=73, PROCESSING_MAX_ATTEMPTS=3, TASK_SOFT_TIME_LIMIT=91,
TASK_HARD_TIME_LIMIT=121, REDIS_VISIBILITY_TIMEOUT=181, RETRY_SHORT_SECONDS=19,
RETRY_LONG_SECONDS=211, RETRY_SAFETY_MARGIN_SECONDS=23.

`docker compose --env-file .env.example -p recruitmatch-cp4-sep12 --profile test config --format json`
was piped directly through jq selecting only those eight keys for api, worker and
test-integration. The jq assertion required exactly three services and exactly the
literal eight-value mapping for every service. Exit 0. No full config or secret
environment was printed. An initial config probe omitted the test profile and
therefore showed only API/Worker; corrected it before recording full propagation.

With the same eight command-scoped values, ran an unmounted test-integration Python
assertion and an unmounted runtime-image Worker composition assertion:

```sh
PROCESSING_LEASE_SECONDS=73 PROCESSING_MAX_ATTEMPTS=3 TASK_SOFT_TIME_LIMIT=91 TASK_HARD_TIME_LIMIT=121 REDIS_VISIBILITY_TIMEOUT=181 RETRY_SHORT_SECONDS=19 RETRY_LONG_SECONDS=211 RETRY_SAFETY_MARGIN_SECONDS=23 docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -e AI_ENABLED=false -e OPENAI_API_KEY= worker python -c 'from types import SimpleNamespace; from app.config import Settings; from app.tasks.celery_app import celery_app, build_worker_dependencies; from celery.backends.base import DisabledBackend; s=Settings.load(); fields=("processing_lease_seconds","processing_max_attempts","task_soft_time_limit","task_hard_time_limit","redis_visibility_timeout","retry_short_seconds","retry_long_seconds","retry_safety_margin_seconds"); actual=tuple(getattr(s,k) for k in fields); assert actual==(73,3,91,121,181,19,211,23); assert not s.ai_enabled and not s.api_key; d=build_worker_dependencies(s, embedder=SimpleNamespace(model_name="synthetic"), artifact_store=object()); assert all(p.lease_seconds==73 and p.retry_policy.max_attempts==3 and p.retry_policy.short_seconds==19 and p.retry_policy.long_seconds==211 for p in (d.resume_processor,d.knowledge_processor)); assert (celery_app.conf.task_soft_time_limit,celery_app.conf.task_time_limit)==(91,121); assert isinstance(celery_app.backend,DisabledBackend); c=celery_app.connection_for_read(); ch=c.channel(); assert ch.visibility_timeout==181; ch.close(); c.close(); print(dict(zip(fields,actual))); print("runtime-image composition and Redis timing verified; AI disabled")'
```

The executed Python loaded Settings, asserted the literal tuple
`(73,3,91,121,181,19,211,23)`, asserted AI disabled/key empty, built WorkerDependencies
using a synthetic embedding object and inert artifact object, verified both
processors' lease/budget/delays, actual Celery soft/hard=(91,121), DisabledBackend,
and a real Redis channel visibility_timeout=181. It printed only the timing
allowlist and a fixed success line. Both commands exited 0. No model invocation,
download or source processing happened in these composition smokes. The runtime
smoke created the project's previously absent `recruitmatch-cp4-sep12_hf-cache-v2`
volume; left it for controller-owned cleanup. No user/default project was used.

## Files and self-review

Changed application files: app/config.py, app/main.py, app/processing/leases.py,
new app/processing/retry.py, app/repositories/ports.py,
app/repositories/sqlalchemy_unit_of_work.py, app/retrieval/generations.py,
app/retrieval/indexing.py, app/services/resume_processing.py,
app/services/knowledge_processing.py, app/tasks/celery_app.py, app/tasks/dispatcher.py.
Configuration/docs: .env.example, docker-compose.yml, ADR 0005.
Tests: new tests/processing/test_celery_delivery.py,
tests/integration/processing/test_retry.py, test_retry_processors.py;
updated tests/fakes/processing.py, tests/resumes/test_resume_processing.py,
tests/knowledge/test_knowledge_ingestion.py. Only this new scratch report is
force-tracked; controller ledger/brief/review files are not staged by this task.

Self-review fixed the lost-retry-fence rollback leak and corrected the obsolete
embedding test seam described above. No known implementation correctness concern
remains. Tests cover real PostgreSQL locking/rollback/connection loss and actual
Celery entrypoints/config/Redis channel; this task did not launch a worker and
physically SIGKILL its process. Beat/recovery scans/health are explicitly Task 4,
and root owns final Fake evaluation/golden and complete CP4 review. No claim that
CP4 as a whole, CP5 telemetry privacy, or CP6 documentation is complete.
