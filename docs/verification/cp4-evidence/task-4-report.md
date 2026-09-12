# CP4 Task 4 implementation report

Base: `945b86acb3dc6ec322a363190d3c5a86ea80773a`. Status: implementation verified, ready for controller review.

Scope: singleton Beat recovery and layered operational health; accepted Task 1–3
lease/retry/publication and CP3 Artifact contracts preserved. Existing approved
specification and worktree reused. No subagents or independent reviewers spawned.
Skills used: Superpowers TDD (including writing-good-tests), implementer template,
and verification-before-completion. using-superpowers was read and its dispatched
subagent exemption applies.

## TDD evidence

### Health RED (before production changes)

Command from the authorized worktree:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-unit pytest tests/operations/test_health_and_metrics.py -q
```

First run: `3 failed, 5 passed in 2.54s`. Readiness omitted schema/vector/Redis/
bucket fields; system health returned 404 instead of 200 for the admin and 401
without authentication. After adding non-admin roles: `5 failed, 5 passed in
2.75s`, with recruiter and lead receiving 404 instead of 403. These are expected
missing-feature failures, not dependency/network failures. Production code was
unchanged for these runs. Test-only bind used for focused RED; final acceptance
will use rebuilt, unmounted images.

### Recovery RED / GREEN

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-integration pytest tests/integration/processing/test_recovery.py -q --tb=short
```

RED: `8 failed in 0.62s`: missing `app.processing.recovery` and
`app.processing.recovery_repository`, for both Source types. These were failures
inside the test bodies, not collection/fixture failures. After implementing the
scanner/reservations and applying additive revision19 to the owned PostgreSQL
database, the same suite with the app bind added passed: `8 passed in 0.88s`.

### Production probes / heartbeat RED / GREEN

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app:/app/app:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-integration pytest tests/integration/test_operational_health.py -q --tb=short
```

RED: `6 failed in 0.35s`, missing production probes and heartbeat registry.
GREEN (same command plus the current Alembic bind): `6 passed in 0.72s`.
Expanded real-dependency/recovery coverage subsequently passed `48 passed in
2.04s`. That run included fair pacing, snapshot/race checks, due retry, terminal
exclusions, current-owner generation reconciliation, live schema/vector/bucket
checks, stale registry aggregation and admin role preservation under hard failure.

### Beat lifecycle RED / GREEN

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app:/app/app:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-unit pytest tests/processing/test_recovery.py -q --tb=short
```

RED before the adapter: `2 failed in 0.08s`, missing `app.tasks.beat`.
The initial combined Beat/API GREEN was `18 passed in 3.32s`.
Self-review then checked actual locked Celery bootstep inclusion, rather than
assuming the timer unit test established runtime installation. Celery's
`StartStopStep.include_if` reads `enabled`; the initial local flag conflicted.
Added actual `step.include` and worker step-list assertions, observed
`1 failed, 2 deselected in 0.68s` (`False is True`), renamed only the local flag to
`pulsing`, and reran: `3 passed in 0.98s`. Systematic-debugging was read/applied
for the root-cause investigation and minimal correction. Actual live Worker
assembly/heartbeat evidence is recorded separately below.

### Controller fairness follow-up RED / GREEN

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app:/app/app:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-integration pytest tests/integration/processing/test_recovery.py -k older_failed -q --tb=short
```

RED: `2 failed, 40 deselected in 1.10s`, both types selected the newly stale
never-reserved Source ahead of an older failed reservation. The controller's
approved ordering uses `COALESCE(recovery_dispatch_at, queued_at), tenant_id, id`.
GREEN with `-k 'older_failed or fair_pacing'`: `4 passed, 38 deselected in 1.23s`.
The finite-cohort test retains a cooldown scenario while preserving the relative
DB-time visit order in its synthetic timestamp fixture.

### Real transport bounds and maintenance

Actual closed-port broker publish test first hit a test-only lambda naming error;
that setup was corrected before accepting RED evidence. The meaningful RED was
`1 failed, 2 deselected in 2.95s`, measured publish delay 2.034 seconds exceeding
the 1.5-second assertion despite task_publish_retry=False. Inspection of locked
Kombu `default_channel` / `_extract_failover_opts` showed its separate initial
connection retry path. Setting transport `max_retries=0` makes the real closed-
port dispatch bounded: `3 passed in 0.78s` for the complete Beat unit suite.

Real PostgreSQL/Redis/S3 clients against a TCP peer which accepts but does not
answer, plus S3 client-close coverage: `4 passed in 3.58s` using
`pytest tests/operations/test_probe_budgets.py -q --tb=short`. No outer Future or
abandoned request thread is used. PostgreSQL connection timeout, Redis read wait,
S3 read wait and the real client's cleanup are exercised, not source-text checks.

Actual `reconcile_artifacts_task.run()` against PostgreSQL and MinIO, with
processor construction forbidden: `2 passed in 1.07s` using
`pytest tests/integration/processing/test_maintenance.py -q --tb=short`. Both
Source types repeatedly receive late Puts at the exact retained DELETED anchor;
the existing CP3 Worker service removes those objects again without retiring the
tombstone or invoking embedding/model composition.

## Implementation and actual interfaces

- `RecoveryScanner(repository, dispatcher).run_once() -> RecoveryReport` performs
  only bounded reservation/dispatch orchestration. There is intentionally no
  application `now` argument: PostgreSQL clock remains authoritative. A test seam
  is persisted synthetic eligibility timestamps and injectable repository/dispatch
  boundaries, not an application clock overriding ownership.
- `RecoveryRepository(session_factory, batch_size=50, stale_seconds=60,
  pacing_seconds=60, max_attempts=5)` implements `candidates`, `reserve`, and
  `dispatch_failed`. Candidate identity includes tenant/type/source/Artifact,
  epoch, public status, queue cycle and previous reservation UUID. Source→Artifact
  locks and a post-lock database clock revalidate eligibility. Expired leases
  requeue; exhausted eligible work terminalizes without claim/increment; eligible
  FAILED uses accepted `leases.requeue_due`. Dispatch commits first. A late failure
  cannot overwrite a new claim/cycle, and queued_at/next_retry_at are not used as
  dispatch cooldowns.
- Revision `20260912_19` adds three nullable dispatch fields and indexes to both
  Sources. Existing content, counters, lease metadata, generations and citation
  data are preserved. Existing migration suites compare predecessor data through
  head19; new fields begin null. There is no database reset/bootstrap fallback.
- Actual Celery `RecoveryScheduler` is configured in app.conf and Compose Beat;
  it invokes the scanner in Beat every ten seconds and schedules Worker-only CP3
  maintenance every sixty seconds, batch25. Beat does no object or model work.
  Ordinary recovered processors reuse the accepted current-lease
  `reconcile_staging`. Successful/permanent/exhausted terminal Sources are not
  reopened simply because inactive generations remain. Historical and active
  citations are retained; deliberate operator repair remains necessary there.
- `WorkerHeartbeatStep` uses Celery's Timer bootstep, a random process identity,
  ten-second pulses, serialized stop/cancel/remove, and no heartbeat_sent offline
  ambiguity. `OperationsHeartbeats` uses Redis TIME, one Worker sorted set with
  ninety-second retention pruning/TTL, and one Beat timestamp/TTL. Reads use
  fixed aggregate operations, not KEYS/all-ID scans. Freshness is thirty seconds.
  Count means fresh Worker main-process observations, not pool slots/capacity.
  Oldest age means the oldest retained observation, including stale records until
  pruned. No samples yields stale/count0/age null; Redis failure yields
  unknown/count null/age null. No new CP5 metrics are claimed.
- `/api/v1/health/live` is async and has no external I/O. `/health/ready` uses
  separate read-only PostgreSQL/schema/pgvector/Redis/HeadBucket probes. Dedicated
  PG NullPool connect2s, statement/lock500ms and TCP timeout/keepalive settings;
  Redis connect/read0.5s/no retry; dedicated S3 connect/read0.5s/one total attempt,
  application identity, client close. API healthcheck client8s/process9s.
- Admin-only `/api/v1/health/system` exposes finite sanitized statuses and bounded
  heartbeat aggregates. Hard failure→503/overall unavailable; stale or unknown
  Worker/Beat→200/degraded without alone failing readiness. All hard dependencies
  ok + both heartbeats fresh + AI deliberately disabled→overall ok. Enabled AI
  stays unknown/not_configured and therefore degraded. Optional
  telemetry:not_configured is disclosed and does not alone degrade the runtime.
  This is the controller-approved aggregation rule; it does not assert telemetry
  health or perform inference. JWT/role checks are preserved during outages.
- API production composition creates those probes separately from storage/vector
  domain ports. SQLite test applications explicitly inject health and Lease Fakes.
  Redis remains broker/operational observations only, with no result backend or
  business task status. Existing Task 3 retry/timing contracts remain injected.

## Live process evidence and its limits

All commands ran from the authorized worktree, with the exact synthetic project
and `.env.example`. No real .env was read, no legacy/default project was used,
and no host API port was bound.

```sh
AI_ENABLED=false OPENAI_API_KEY= docker compose --env-file .env.example -p recruitmatch-cp4-sep12 up -d --no-deps beat
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run -d --name recruitmatch-cp4-sep12-worker-fake-smoke --no-deps -e AI_ENABLED=false -e OPENAI_API_KEY= -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -v recruitmatch-cp4-sep12_hf-cache-v2:/home/recruitmatch/.cache/huggingface test-integration python -m tests.support.celery_fake_worker
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -e AI_ENABLED=false -e OPENAI_API_KEY= -e EXPECTED_WORKER=fresh test-integration python -m tests.support.health_smoke
```

The test entrypoint refuses enabled AI/nonempty keys/missing offline guard and
injects a deterministic 512-vector embedder into the actual Worker factory.
Ordinary AI_ENABLED=false alone was not relied on to prevent BGE downloads.
Actual Beat container was running/healthy; actual Worker bootstep registered:

```text
readiness:200 overall:ok database:ok schema:ok vector:ok redis:ok bucket:ok
worker:fresh worker_count:1 worker_oldest_heartbeat_age_seconds:3.366
beat:fresh beat_heartbeat_age_seconds:3.525 ai:disabled telemetry:not_configured
```

```sh
docker kill --signal KILL recruitmatch-cp4-sep12-worker-fake-smoke
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 --profile test run --rm --no-deps -e AI_ENABLED=false -e OPENAI_API_KEY= -e EXPECTED_WORKER=stale test-integration python -m tests.support.health_smoke
```

Kill occurred at `2026-09-12 10:48:54 UTC`; observed later:

```text
readiness:200 overall:degraded database:ok schema:ok vector:ok redis:ok bucket:ok
worker:stale worker_count:0 worker_oldest_heartbeat_age_seconds:89.968
beat:fresh beat_heartbeat_age_seconds:9.806 ai:disabled telemetry:not_configured
```

**This kill did not target a controlled in-flight job.** No assertion was made
that the Worker was idle or processing a particular task. It proves actual
process-loss heartbeat degradation, not physical in-flight lease recovery.

Separately, with the Worker absent and actual Beat still running, an inline
in-container driver reused the existing `test_leases.source` fixture for each
type, called `committed_lease` (actual claim attempt1/epoch1), then `expire`
(manually persisted expired lease). It polled PostgreSQL for at most25 seconds
per type, asserting QUEUED/uploaded, nonempty recovery token, unchanged
attempt1/epoch1, no lease owner and no next_retry_at. Its observed output was:

```text
resume: live Beat recovered while Worker absent; budget unchanged
knowledge_document: live Beat recovered while Worker absent; budget unchanged
```

This establishes autonomous actual Beat recovery of expired durable ownership,
including with Workers unavailable. It does not turn the manually expired seeds
into a claim of controlled in-flight SIGKILL recovery. The existing fixture
teardown removed only each driver's own synthetic tenant rows. Beat was then
stopped with `docker compose --env-file .env.example -p recruitmatch-cp4-sep12 stop beat`
before full suites, preventing fixture races. The controller independently
verified and removed only the stopped named Fake Worker container without
volumes. Earlier Compose orphan warnings from that retained helper were recorded;
the final gate outputs contain none. The four owned project volumes and stopped
Beat remain for controller cleanup; no unrelated service was stopped.

## Final rebuilt, unmounted gate

First full run exposed an obsolete container-test assumption: `1 failed, 284
passed in 22.04s` because it counted exactly two cache mount strings. Beat uses
the existing named cache mount to avoid creating an anonymous Docker VOLUME.
The test now parses service configuration and verifies API/Worker/Beat mounts,
instead of counting unrelated source-text occurrences. Focused GREEN: `4 passed
in 0.02s`. This was the only full-gate fix; no production behavior changed after
the successful live smoke. Images were rebuilt again before final acceptance.

Final exact commands (no app/tests binds and no host venv):

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 build bootstrap test-integration
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm bootstrap
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-integration
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit sh -c 'ruff check app tests scripts && ruff format --check app tests scripts && mypy app/models app/retrieval app/services app/ai app/tasks app/operations app/processing && uv lock --check --offline'
git diff --check
```

Bootstrap: exit0, `seeded_job_templates=0`; installed migration head19 retained.
Final unit: `285 passed in 21.72s`.
Final integration: `487 passed in 22.47s`.
Static: `All checks passed!`; `210 files already formatted`;
`Success: no issues found in 51 source files`.
Lock: `Resolved 110 packages in 1ms`, exit0, no dependency/model download.
All final gate exits0, with no test warnings or stray diagnostic output.

Final Docker image IDs (manifest/image identity from selective inspect):

- `recruitmatch-cp4-sep12-app:latest`:
  `sha256:4306bf5c7589948a7634aff7e8d890b20c96057e9298fcba7287eb7fe9548331`
- `recruitmatch-cp4-sep12-app-test:latest`:
  `sha256:b1ae2cb8f268edb3125c9fce9a1ca29b1a675cbf4ae8e7d0096642ad35cdc137`

Host `shasum -a 256` versus an unmounted test-container `sha256sum` over all33
changed runtime/config/migration/test files: exact match, zero mismatches. The
two Markdown documentation files and this report are not shipped in the runtime
image and were excluded from that comparison. The final rebuild changed only
the test configuration assertion; the runtime layer content matched the live
smoke runtime (image config `dd5983e574b41fc3a9b0b2d06854ce5b98a3fada37f26e84d54c3f438767c98b`).

## Changed files

New implementation:
`alembic/versions/20260912_19_recovery_dispatch.py`,
`app/processing/recovery.py`, `app/processing/recovery_repository.py`,
`app/tasks/beat.py`, `app/tasks/maintenance.py`,
`app/operations/health.py`, `app/operations/heartbeats.py`,
`app/operations/probes.py`.

Updated composition/config/models:
`app/api/v1/deps.py`, `app/api/v1/operations.py`, `app/main.py`,
`app/models/resumes.py`, `app/models/knowledge.py`, `app/tasks/celery_app.py`,
`app/tasks/dispatcher.py`, `docker-compose.yml`, `Dockerfile`.

New tests/support:
`tests/processing/test_recovery.py`,
`tests/integration/processing/test_recovery.py`,
`tests/integration/processing/test_maintenance.py`,
`tests/integration/test_operational_health.py`,
`tests/operations/test_probe_budgets.py`, `tests/fakes/operations.py`,
`tests/support/celery_fake_worker.py`, `tests/support/health_smoke.py`.

Updated tests/support and migration-head expectations:
`tests/operations/test_health_and_metrics.py`, `tests/support/application.py`,
`tests/foundation/test_container_contract.py`, `tests/foundation/test_migrations.py`,
`tests/integration/processing/test_lease_migration.py`,
`tests/integration/test_pgvector_migration.py`,
`tests/artifacts/test_privacy_migration.py`,
`tests/artifacts/test_artifact_repository.py`.

Documentation: `docs/operations/recovery-and-health.md`, a narrow Task4 pointer in
`docs/adr/0005-processing-lease-contract.md`, and this force-tracked primary report.
No controller briefs, ledger, unrelated scratch or predecessor report is staged.

## Self-review and handoff

Reviewed actual diff and new modules for task completeness, transaction ordering,
fairness/eligibility separation, terminal preservation, client lifecycle, finite
health responses, existing API interfaces and focused module boundaries. The
repository adapter is SQL-only; the orchestration module has no object/model
dependencies. Maintenance reuses CP3 rather than introducing a replacement state
graph. Scope/layout/schema choices were sent to and approved by the controller
before implementation. No broad file splits/refactors, merge, push or independent
reviewer dispatch occurred.

Found and fixed before handoff: NULL-first starvation, implicit initial Kombu
retries, the Celery `enabled` member collision, and the obsolete cache count test.
No known remaining Task4 implementation blocker. Operational limitations are
explicit: global oldest-visit fairness is not tenant quotas; probes have driver
budgets rather than a universal OS/DNS wall-time SLA; heartbeat counts are recent
process observations, not business progress; terminal staging needs deliberate
operator repair; controlled in-flight physical Worker termination recovery is
not established by this report. CP5 telemetry, CP6 load/security/CI/runbook work,
the controller's final Fake evaluation, whole-CP4 review and project cleanup are
still pending outside this task. No real model was called or downloaded.

---

# Task 4 fix round 1 — R1 contention isolation and C1 health fields

BASE: `5a79640043770918647c85210e494383166111d1`.
Verification date: 2026-09-12. Branch: `codex/recruitmatch-v2`.
Fix HEAD is the local commit containing this report (resolve with
`git log -1 --format=%H -- .superpowers/sdd/2026-08-24-recruitmatch-v2-cp4-reliability/task-4-fix-1-report.md`);
its exact SHA is also returned in the implementer handoff. No merge or push.

## Findings and changes

R1: confirmed both blocking row locks in `RecoveryRepository._locked` waited
until the real production 500ms lock timeout. The UoW translated that
OperationalError into PersistenceUnavailable; the actual scheduler then returned
early, leaving unrelated selected candidates, maintenance and heartbeat undone.
The same defect occurred after publish failure in `dispatch_failed`.

Minimal fix: both Source and Artifact locks now use
`with_for_update(skip_locked=True)`. Source→Artifact acquisition, every
tenant/type/owner/Artifact/epoch/status/queue-cycle/reservation guard, the DB clock
sample after acquisition, existing fairness order and batch limits are unchanged.
A skipped candidate has no mutation or pacing rewrite. Session closure releases
a Source lock acquired before encountering a held Artifact. The already committed
reservation survives a skipped late error annotation; cooldown still permits
redispatch. No exception catch, schema change or general retry framework was
introduced. Genuine connection failure still translates to PersistenceUnavailable;
a real missing-table programming fault still propagates ProgrammingError.

C1: `HealthService.system` now includes `api:ok`, meaning this local process
is serving the request (not that dependencies/features are all available), and
`minio` derived from the existing sanitized actual app-IAM bucket probe result.
No additional HTTP probe or configuration-based success assertion. Existing
`bucket`, schema/vector diagnostics and heartbeat fields remain; readiness,
authentication, role checks, response HTTP statuses and overall aggregation
are unchanged. A denied real bucket identity maps minio/overall to unavailable
while api remains ok. Missing Worker still yields degraded with readiness200.

## RED and focused GREEN evidence

All commands below ran from
`/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2`.
Only `--env-file .env.example -p recruitmatch-cp4-sep12` was used.
Test services force AI off and empty model key; no Worker/model processing is
constructed in the new tests. Focused development runs used explicit source-file
bind mounts; final acceptance runs below were rebuilt and unmounted.

R1 RED, before changing production locks:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/integration/processing/test_recovery_contention.py:/app/tests/integration/processing/test_recovery_contention.py:ro test-integration pytest tests/integration/processing/test_recovery_contention.py -q --tb=short
```

Exact output excerpts:

```text
FFFFFFFF                                                                 [100%]
    assert same_type[:3] in dispatcher.deliveries
WARNING  app.tasks.beat:beat.py:75 recovery_database_unavailable
8 failed in 5.92s
```

Exit1. All eight combinations failed for the intended missing behavior:
resume/knowledge_document × Source/Artifact held lock × reserve/dispatch_failed.
Actual Scheduler→Scanner→Repository and production SQL connection settings were
used. Broker task publication alone is recorded in test Fakes. Beat heartbeat
uses real Redis with a unique short-lived test namespace. There was no setup or
syntax error in this RED.

R1 GREEN after the two-lock change:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/processing/recovery_repository.py:/app/app/processing/recovery_repository.py:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/integration/processing/test_recovery_contention.py:/app/tests/integration/processing/test_recovery_contention.py:ro test-integration pytest tests/integration/processing/test_recovery_contention.py -q --tb=short
```

```text
........                                                                 [100%]
8 passed in 1.47s
```

Exit0. Assertions establish other same-type and other-type candidates dispatched
and acquired durable reservations; exact maintenance task/expiry/redacted payload
was published; real Beat heartbeat became fresh; the held row's full processing
and dispatch state was unchanged. With Artifact held, a third transaction can
NOWAIT-lock Source, proving the skipped transaction released it. Once a reservation
phase holder releases its lock, the next tick dispatches that previously skipped
candidate without any test rewrite of eligibility/pacing.

Added eight genuine-fault regressions using real PostgreSQL missing-table queries
(connection-local search_path only) and closed-port connections, both Source
types and both lock-helper callers. They pass without adding production exception
handling. Expanded focused command:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/processing/recovery_repository.py:/app/app/processing/recovery_repository.py:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/integration/processing/test_recovery_contention.py:/app/tests/integration/processing/test_recovery_contention.py:ro test-integration pytest tests/integration/processing/test_recovery_contention.py tests/integration/processing/test_recovery.py -q --tb=short
```

Exact summary: `58 passed in 2.01s`, exit0. Existing finite-cohort and
oldest-effective-visit fairness tests remain intact.

C1 RED before adding the fields:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/operations/test_health_and_metrics.py:/app/tests/operations/test_health_and_metrics.py:ro test-unit pytest tests/operations/test_health_and_metrics.py -q -k 'worker_stale or hard_probe_failure' --tb=short
```

Exact output excerpts:

```text
FFFFFF                                                                   [100%]
E   KeyError: 'api'
6 failed, 10 deselected in 1.79s
```

Exit1. Stale-Worker test failed the required field-set assertion; all five hard
dependency cases failed on missing api. C1 GREEN:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/operations/health.py:/app/app/operations/health.py:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/operations/test_health_and_metrics.py:/app/tests/operations/test_health_and_metrics.py:ro test-unit pytest tests/operations/test_health_and_metrics.py -q -k 'worker_stale or hard_probe_failure' --tb=short
```

```text
......                                                                   [100%]
6 passed, 10 deselected in 2.04s
```

Exit0. Existing real operational-health integration tests were also extended to
verify actual successful app-IAM MinIO probe mapping and denied-identity mapping,
not just the unit Fake result.

Test formatting command (exit0: two reformatted, one unchanged):

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests test-unit ruff format tests/integration/processing/test_recovery_contention.py tests/operations/test_health_and_metrics.py tests/integration/test_operational_health.py
```

## Final rebuilt, unmounted gates

No runtime/test-source edits occurred after this build:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 build bootstrap test-integration
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-integration
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit sh -c 'ruff check app tests scripts && ruff format --check app tests scripts && mypy app/models app/retrieval app/services app/ai app/tasks app/operations app/processing && uv lock --check --offline'
```

All exit0, pristine outputs (no warnings or suite failures):

```text
Image recruitmatch-cp4-sep12-app Built
Image recruitmatch-cp4-sep12-app-test Built
285 passed in 27.77s
503 passed in 30.03s
All checks passed!
211 files already formatted
Success: no issues found in 51 source files
Resolved 110 packages in 1ms
```

The integration count increases by16 from487: eight contention cases and eight
genuine-fault cases. No new schema migration/bootstrap needed for this fix.

Final image IDs from
`docker image inspect recruitmatch-cp4-sep12-app:latest recruitmatch-cp4-sep12-app-test:latest --format '{{.RepoTags}} {{.Id}}'`:

- app: `sha256:3a5333ac969174964770dd733479cf22979b4df50e04fea5124e55a76f09ffb9`
- app-test: `sha256:1280596898a2770f2c97ca7f9764b338f8b0fb784655fabd307b066934226c28`

Host/image equality verification, exit0:

```sh
shasum -a 256 app/processing/recovery_repository.py app/operations/health.py tests/integration/processing/test_recovery_contention.py tests/integration/test_operational_health.py tests/operations/test_health_and_metrics.py
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit sha256sum app/processing/recovery_repository.py app/operations/health.py tests/integration/processing/test_recovery_contention.py tests/integration/test_operational_health.py tests/operations/test_health_and_metrics.py
```

Both produced exactly these five file hashes:

```text
2a0427f3887422171b4ce93ab390686961427147bd5feb5261f9bf2db11bfa8d  app/processing/recovery_repository.py
3f3a65e6af79358cfc539423620846e2ec7b29c2fb4419dd0d363da383bf0e27  app/operations/health.py
b7e4c0f814053dd69de3ea19b237ae0e658288d80e28a2035ac59a7326718acf  tests/integration/processing/test_recovery_contention.py
698d833f0f88eb105a11484b652285731bcaa8988232e546d74406cee770a205  tests/integration/test_operational_health.py
7eafa01771dfd193f5bfab348ad371a73e80b8c9d39b01980c68e33cabae8f97  tests/operations/test_health_and_metrics.py
```

`docker compose --env-file .env.example -p recruitmatch-cp4-sep12 ps --all beat`
confirmed `recruitmatch-cp4-sep12-beat-1` remained Exited(0); no lifecycle smoke was
repeated. No ordinary Worker was started. Only synthetic per-test tenant rows and
the exact test heartbeat keys were removed by fixtures; retained project volumes
and controller-owned resources were not cleaned up.

## Owned files, self-review and limits

Owned production: `app/processing/recovery_repository.py`,
`app/operations/health.py`.
Owned tests: `tests/integration/processing/test_recovery_contention.py`,
`tests/integration/test_operational_health.py`,
`tests/operations/test_health_and_metrics.py`.
Owned docs: `docs/operations/recovery-and-health.md`, this standalone fix report,
and the complete appended evidence in primary `task-4-report.md`.
No root brief/review/ledger or untracked `docs/verification/` is staged.

Self-review confirmed the production diff is two lock options and two response
fields plus explanatory comments; no broad exception recovery, schema redesign,
CP5/6 work or predecessor lease/retry changes. Required health fields preserve
additional diagnostics and authentication. Regression assertions exercise real
database transitions and locks, exact external dispatch arguments and real Redis
observations. `git diff --check` is clean; staged whitespace is checked before
commit. Skill workflow used: receiving-code-review, systematic-debugging,
test-driven-development with writing-good-tests, verification-before-completion.

Limits remain explicit: SKIP LOCKED only handles row contention, not DDL/table
locks or genuine database outages. Skipped rows are not backfilled beyond the
bounded selection, so no strict tenant quotas or sustained-contention latency
guarantee is claimed. New tests exercise actual scheduler composition in-process
with recording broker boundaries, not a new real Beat/Worker process lifecycle
smoke or a physical in-flight SIGKILL recovery test. Maintenance publication
continues; the unchanged CP3 Worker task execution remains covered by the full
integration suite. Controller owns independent re-review and final cleanup.
