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
