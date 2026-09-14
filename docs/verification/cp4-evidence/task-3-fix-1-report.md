## Task 3 fix round 1 report

Status: DONE. Fix base: `bf2a7c88daf1f61dfe32653f81b2fb7fe9ea5498`.
Worktree: `/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2`.
Branch: `codex/recruitmatch-v2`. Addresses T3-I1 and T3-M1 only.
Read the complete independent review and fix brief and verified both findings
against the exact production blocks before editing. Used receiving-code-review,
test-driven-development and verification-before-completion skills. Worked alone;
no new agents, external review, merge, push, scheduler, health work or cleanup.

### Changes and transaction contract

T3-I1: added `finish_failed_attempt(uow, lease, code, policy) -> ProcessDisposition`
to the existing `app/processing/retry.py` module. This is the one shared decision
for transient retry versus permanent failure, timedelta conversion, lease-loss
rollback and commit. It accepts the caller's existing RecruitingUnitOfWork; it
does not open any UoW, Session, context manager or separate transaction. A lost
fence calls rollback on that full caller UoW and returns LEASE_LOST. Otherwise it
commits the caller UoW after the guarded failure/retry operation and returns its
existing disposition. Repository errors still propagate through the caller's
original context manager, preserving the existing SQL error boundary.

Both processors now call the helper from their existing _mark_failed UoW context.
Knowledge still performs its preceding index-error write inside leases.owned on
that exact UoW before invoking the helper. The independent retry-fence check and
full rollback therefore retain the earlier race fix; there is no independently
committed index-error or second failure transaction. A TYPE_CHECKING-only port
import avoids adding a runtime cycle to the existing processing/SQL adapter graph.

T3-M1: both artifact-read try blocks re-raise self.timeout_errors before the
catch-all storage mapping. The existing outer timeout handler now records the
correct processing_timeout through the same fenced retry policy. Storage/network
errors remain storage_unavailable; checksum and length mismatches keep their
existing permanent classification. No change to attempts, epoch, delay settings,
ordinary FAILED ACK, durable index-only inference or Celery delivery configuration.

### RED and focused GREEN

RED command against the predecessor image with only the new test mounted:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-integration pytest tests/integration/processing/test_retry_processors.py -q -k artifact_read_timeout
```

Exit 1: **2 failed, 17 deselected in 1.22s**. Both Resume and Knowledge failed on
`assert row.error_code == "processing_timeout"`, observing
`storage_unavailable`. The injected exception is the actual billiard
SoftTimeLimitExceeded from read_bounded. The tests also check the durable future
retry, first attempt/epoch, cleared owner and no active index generation.

After the helper extraction and catch-order change, focused GREEN:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app:/app/app:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-integration pytest tests/integration/processing/test_retry_processors.py tests/integration/processing/test_retry.py tests/processing/test_celery_delivery.py -q
```

Exit 0: **71 passed in 1.49s**. This includes both new artifact-read timeout
cases, full short/long/exhausted storage cycles for both Sources, real PostgreSQL
retry-write connection loss, committed claim-cap returns, the Knowledge
lost-retry-fence full rollback, due requeue races, and actual Celery tracing/
publish-failure behavior. Focused Ruff check and format check also exited 0;
four files were already formatted.

### Final rebuilt, unmounted verification

Ran these exact commands from the worktree, with no application/test source mounts:

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 build bootstrap test-unit
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-integration
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit sh -c 'ruff check app tests scripts && ruff format --check app tests scripts && mypy app/models app/retrieval app/services app/ai app/tasks && mypy app/processing app/repositories/ports.py app/repositories/unit_of_work.py app/repositories/sqlalchemy_unit_of_work.py && uv lock --check --offline'
```

All exited 0:

- Build: rebuilt runtime and test images from the final code/test tree.
- Unit: **268 passed in 31.56s**.
- Integration/retrieval/artifacts: **434 passed in 36.04s** (+2 from fix base).
- Ruff check: all checks passed. Format: 195 files already formatted.
- Actual CI mypy targets: success, 39 source files.
- Additional processing and affected repository/UoW annotations: success, 8 files.
- Offline lock: resolved 110 packages in 3ms, unchanged lock.
- git diff --check: exit 0; repeated before commit.

Image evidence from
`docker image inspect recruitmatch-cp4-sep12-app:latest recruitmatch-cp4-sep12-app-test:latest --format '{{.RepoTags}} {{.Id}}'`
(exit 0):

- Runtime: `sha256:09a6971a3f4c2b6eaa009f9305d6b7be515e6512120c09f53841c8b9bf304f42`.
- Test: `sha256:b82f3d46f973007b77bf8c657cae348b569bbc6ccbd677eb6b8569103b5479bd`.

### Self-review, files and remaining boundaries

Read the complete four-file implementation/test diff after focused GREEN.
Confirmed the duplicated policy block is removed from both processors, the helper
uses only the supplied UoW, Knowledge's guard precedes it, and lost ownership rolls
back all staged writes. No new issue or known correctness concern remains.

Changed exactly app/processing/retry.py, app/services/resume_processing.py,
app/services/knowledge_processing.py, tests/integration/processing/test_retry_processors.py,
and the two own report files. This full report is both appended to task-3-report.md
and saved as task-3-fix-1-report.md at controller request. No controller ledger,
brief or review file is staged.

All execution used the owned synthetic Compose project and AI-disabled test
services. No real .env, real model, model download, host venv, default project or
user data was used. Task 4, independent fix review and final Fake evaluation/golden
remain controller-owned. This scoped fix does not claim full CP4 completion.
