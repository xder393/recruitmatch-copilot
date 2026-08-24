# CP1 final fix report

Date: 2026-08-24

Branch: `codex/recruitmatch-v2`

Starting commit: `0f9c372`

Implementation commit: `27cd3c1 fix: make bootstrap the sole schema writer`

## Outcome

Both Important findings are resolved. Bootstrap is the only production schema writer, API and worker startup only compose business dependencies, test suites explicitly create their own schema, and the CP2 plan now requires PostgreSQL-only migration/CI evidence plus a complete, runnable production retrieval cutover before deleting the legacy index.

All seven Minor candidates were resolved without a warning ignore or speculative runtime dependency change. The Starlette warning was removed with the compatible test-only `httpx2` transport selected by locked Starlette 1.6.0; runtime continues to use `httpx` for OpenAI.

No pgvector, MinIO, Beat/reliability, or OTel implementation entered CP1. `/api/v1` behavior and response schemas were not changed.

## Important finding 1: bootstrap is the sole schema writer

### Root cause

`app.main.init_recruiting_state()` called `upgrade_database()` during every API lifespan startup. That helper also stamped a recognized unversioned `create_all` schema before upgrading. Separately, `scripts/seed_job_templates.py` called `Base.metadata.create_all()`. The API test suite therefore relied on application startup to repair or create schema rather than declaring schema setup itself.

### RED

Tests were changed first:

- `test_application_startup_does_not_write_schema` starts the application against an empty SQLite database and asserts that startup leaves the database with zero tables.
- `test_domain_event_has_bounded_attributes` mutates both the input dict and the event mapping.
- `test_non_api_compose_targets_disable_the_api_healthcheck` requires bootstrap, worker, test-unit, and test-integration to disable the inherited HTTP probe.

Command:

```bash
docker compose build test-unit >/tmp/cp1-red-build.log && \
docker compose run --rm --no-deps test-unit pytest -q \
  tests/foundation/test_schema_upgrade.py::test_application_startup_does_not_write_schema \
  tests/foundation/test_ports.py::test_domain_event_has_bounded_attributes \
  tests/foundation/test_container_contract.py::test_non_api_compose_targets_disable_the_api_healthcheck
```

Observed:

```text
3 failed, 1 warning in 0.77s
test_application_startup_does_not_write_schema:
  expected [], received 18 tables; Alembic upgrades 20260819_01 through 20260819_10 logged
test_domain_event_has_bounded_attributes:
  expected outcome=success, received outcome=failed
test_non_api_compose_targets_disable_the_api_healthcheck:
  bootstrap section had no disable override
```

The Compose test's first draft split on nested indentation and failed for the test's own parsing bug. It was corrected before accepting RED; the focused rerun then failed specifically on the missing bootstrap healthcheck override.

### Implementation

- Removed `upgrade_database()` from `app/main.py` lifespan initialization.
- Simplified `app/database_migrations.py` to explicit-URL `upgrade head`; removed schema inspection and stamping.
- Made `scripts/bootstrap.py` the production caller of `upgrade_database()` and preserved explicit `DATABASE_URL` over Alembic environment/INI fallback behavior.
- Removed `Base.metadata.create_all()` from `scripts/seed_job_templates.py`.
- Added `tests/support/database.py`; all TestClient suites call its test-only `Base.metadata.create_all()` before application startup.
- Replaced the test that required API repair of an unversioned schema with the no-schema-write regression.
- Updated the standalone seed CLI test to establish its schema explicitly.

Deterministic URL precedence remains covered:

```text
explicit upgrade_database(database_url) > DATABASE_URL environment
direct Alembic DATABASE_URL environment > alembic.ini fallback
```

### GREEN

Focused command:

```bash
docker compose run --rm --no-deps test-unit pytest -q \
  tests/foundation/test_schema_upgrade.py \
  tests/foundation/test_ports.py::test_domain_event_has_bounded_attributes \
  tests/foundation/test_container_contract.py::test_non_api_compose_targets_disable_the_api_healthcheck \
  tests/foundation/test_job_seed.py tests/foundation/test_auth_api.py tests/ai/test_gateway.py
```

Observed before resolving the separately triaged Starlette warning:

```text
16 passed, 1 warning in 2.28s
```

Final full suite after the warning fix:

```bash
docker compose run --rm --no-deps test-unit pytest -q
```

```text
136 passed in 34.41s
```

There was no warnings summary.

### Fresh PostgreSQL bootstrap and live business startup

An isolated project with new named volumes was used:

```bash
docker compose -p recruitmatch-cp1-final-fix up -d --build --wait api worker
```

Observed first bootstrap:

```text
PostgresqlImpl; transactional DDL
upgrades 20260819_01 through 20260819_10
seeded_job_templates=30
bootstrap=exited exit=0
healthcheck={"Test":["NONE"],...}
```

Live checks:

```text
api_uid=10001 status=200 body={"database": "ok", "status": "ready"}
worker_uid=10001
revision=20260819_10
templates=30
```

Second bootstrap against the same PostgreSQL volume:

```bash
docker compose -p recruitmatch-cp1-final-fix run --rm bootstrap
```

```text
PostgresqlImpl; transactional DDL
seeded_job_templates=0
```

API and worker logs were scanned for `alembic`, `running upgrade`, and `create table`:

```text
business_startup_schema_logs=clean
```

The isolated project's containers, network, and only its four named volumes were removed afterward with `docker compose -p recruitmatch-cp1-final-fix down -v`.

## Important finding 2: CP2 plan corrected before implementation

Only the plan was changed; no CP2 code was implemented.

### Task 1 changes

- Migration evidence now runs only against real PostgreSQL 16 with pgvector 0.8+.
- The new pgvector migration must reject non-PostgreSQL dialects rather than no-op/stamp them.
- SQLite unit tests retain ORM/fake schema setup and never execute the pgvector revision.
- Existing SQLite `alembic upgrade head` CI evidence is explicitly removed.
- CI is split into a static/unit lane with no bootstrap and a PostgreSQL integration lane with real bootstrap/migration evidence.
- URL precedence and bootstrap idempotency evidence move to PostgreSQL integration; application-startup-no-schema-write remains a unit contract.
- Task 1 ends in a runnable schema/CI commit.

### Tasks 2–4 changes

- Task 2 implements the port, fake, and PostgreSQL adapter while explicitly retaining `app/knowledge/index.py` and production composition.
- Task 3 implements an independently runnable atomic generation writer and still retains the legacy adapter and consumers.
- Task 4 is the single production cutover boundary. It explicitly schedules:
  - `app/main.py`;
  - `app/tasks/celery_app.py`;
  - `app/ai/semantic_matching.py`;
  - `app/services/matching.py`;
  - `app/services/resume_processing.py`;
  - `app/services/jobs.py`;
  - `app/services/source_index_backfill.py`;
  - `app/services/knowledge_processing.py`;
  - citation/explanation modules;
  - relevant API composition;
  - `scripts/evaluate_ai_pipeline.py` and all affected fakes/tests.
- Production composition tests build the real FastAPI app and factored Celery dependencies against PostgreSQL, execute rules/hybrid behavior, and import all production roots.
- `app/knowledge/index.py` is deleted last in Task 4, after every producer, reader, adapter injection point, evaluation consumer, and test fake has moved in that same runnable task/commit.
- The Task 4 gate includes production imports, no-legacy-method scan, live API/worker startup, PostgreSQL retrieval tests, and all retained API suites.

## Minor candidate resolutions

### Focused `ModelTraceRepository` signatures

Replaced unrestricted `*args: Any, **kwargs: Any` with the exact `succeeded(...)` and `failed(...)` parameters consumed by the use cases. Provider-independent `ModelRequest`/`ModelResponse` DTOs are typed explicitly. Concrete UoW compatibility passed mypy.

### Defensively immutable `DomainEvent.attributes`

`DomainEvent.__post_init__` copies the supplied mapping and wraps it in `MappingProxyType`. Since allowed values are scalar primitives, this supplies complete defensive immutability without inventing the CP5 allowlist.

Mutation test:

```text
input dict mutation does not change the event
event.attributes assignment raises TypeError
```

### Removed unused knowledge task module

`app/tasks/knowledge_tasks.py` had no production, script, or test import and was deleted. Final scan:

```text
knowledge_tasks_import_scan=clean
```

### Disabled inherited HTTP healthchecks

Bootstrap, worker, test-unit, and test-integration now declare `healthcheck.disable: true`. Normalized Compose JSON showed:

```text
api              explicit HTTP healthcheck
bootstrap        {'disable': True}
worker           {'disable': True}
test-unit        {'disable': True}
test-integration {'disable': True}
```

Docker inspection of the completed bootstrap container reported `"Test":["NONE"]`.

### Removed global Ruff E402 suppression

Removed `E402` from global Ruff ignores and retained only targeted `# noqa: E402` annotations on scripts/models that intentionally establish import order. Final Ruff output was clean.

### Structural OpenAI client injection

`OpenAICompatibleGateway` now accepts an `OpenAIClient` structural Protocol describing only the used `beta.chat.completions.parse(...)` surface. The concrete generated SDK is cast at the creation boundary; injected fakes are no longer typed as the full SDK class. Focused gateway tests and mypy passed.

### Starlette/TestClient warning resolved with proven locked compatibility

Baseline versions and warning:

```text
FastAPI 0.141.1
Starlette 1.6.0
httpx 0.28.1
StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
```

Inspection of locked Starlette showed it imports `httpx2` first and warns only when falling back to `httpx`. Dependency resolution showed current Python 3.12 selects `httpx2==2.12.0`; the incompatible placeholder `httpx2==0.0.0` requires Python 3.14. The bounded test-only dependency is therefore `httpx2>=2.12,<3`.

Warning-as-error proof:

```bash
docker compose run --rm --no-deps test-unit pytest -q \
  -W error::starlette.exceptions.StarletteDeprecationWarning \
  tests/foundation/test_schema_upgrade.py::test_application_startup_does_not_write_schema \
  tests/foundation/test_auth_api.py tests/ai/test_gateway.py
```

```text
10 passed in 1.11s
```

The full 136-test Compose suite also emitted no warning. Runtime remains unchanged by this test dependency:

```text
runtime_httpx2=None
runtime_httpx=True
```

No warning filters or ignores were added.

## Final verification evidence

### Lock, lint, format, and strict affected-surface mypy

```bash
docker compose run --rm --no-deps test-unit uv lock --check
docker compose run --rm --no-deps test-unit ruff check app tests scripts
docker compose run --rm --no-deps test-unit ruff format --check app tests scripts
docker compose run --rm --no-deps test-unit mypy \
  app/artifacts app/retrieval app/observability app/tasks/dispatcher.py \
  app/ai/gateway.py app/repositories/ports.py
```

```text
Resolved 104 packages in 3ms
All checks passed!
147 files already formatted
Success: no issues found in 9 source files
```

Concrete protocol/UoW check:

```bash
docker compose run --rm --no-deps test-unit mypy \
  app/ai/gateway.py app/repositories/ports.py \
  app/repositories/sqlalchemy_unit_of_work.py app/services/ai_tracing.py
```

```text
Success: no issues found in 4 source files
```

### CP1 tests

```bash
docker compose run --rm --no-deps test-unit pytest \
  tests/foundation tests/resumes tests/knowledge tests/matching tests/ai tests/web -q
```

```text
118 passed in 29.24s
```

Complete Compose test target:

```text
136 passed in 34.41s
```

### Runtime image, UID, imports, and scans

```bash
docker build -t recruitmatch:cp1-final-fix .
docker run --rm recruitmatch:cp1-final-fix id -u
docker run --rm recruitmatch:cp1-final-fix \
  python -c "import app.main, app.tasks.celery_app; print('runtime_imports=ok')"
```

```text
build exit 0
10001
runtime_imports=ok
```

Scans:

```text
legacy_scan=clean
business_schema_writer_scan=clean
knowledge_tasks_import_scan=clean
git diff --check: no output, exit 0
```

The business schema scan covers `app/main.py`, `app/tasks`, `app/services`, and `scripts/seed_job_templates.py` for `upgrade_database`, `Base.metadata.create_all`, and Alembic upgrade/stamp calls. Only `scripts/bootstrap.py` reaches the migration helper in production.

## Files and commits

Implementation commit:

```text
27cd3c1 fix: make bootstrap the sole schema writer
```

Production/runtime changes:

- `app/main.py`
- `app/database_migrations.py`
- `scripts/bootstrap.py`
- `scripts/seed_job_templates.py`
- `docker-compose.yml`
- `app/observability/events.py`
- `app/repositories/ports.py`
- `app/ai/gateway.py`
- deleted `app/tasks/knowledge_tasks.py`
- `pyproject.toml`
- `uv.lock`

Test changes:

- new `tests/support/__init__.py`
- new `tests/support/database.py`
- API/TestClient suites under `tests/ai`, `foundation`, `knowledge`, `matching`, `operations`, `resumes`, and `web`
- `tests/foundation/test_schema_upgrade.py`
- `tests/foundation/test_container_contract.py`
- `tests/foundation/test_ports.py`
- `tests/foundation/test_job_seed.py`

Plan change:

- `docs/superpowers/plans/2026-08-24-recruitmatch-v2-cp2-pgvector.md`

This report is committed as a separate documentation-only follow-up after the implementation hash was known.

## Self-review

- Re-read every Important and Minor item in `final-review-findings.md` and mapped it to code, tests, plan text, or explicit runtime evidence.
- Verified the test-first regressions failed for the intended missing behavior before editing production.
- Confirmed every TestClient use either explicitly calls `prepare_test_database()` or is the deliberate empty-schema no-write regression.
- Confirmed business startup has no migration or `create_all` path; direct model tests retain test-only schema creation.
- Parsed normalized Compose output and inspected the real bootstrap container healthcheck rather than relying only on YAML text.
- Verified the Starlette change with its installed source, dependency resolution, warning-as-error tests, full Compose tests, and a runtime-image negative check.
- Reviewed the full staged diff and ran `git diff --check` before the implementation commit.
- Reviewed CP2 Tasks 1–4 for PostgreSQL-only migration evidence, SQLite isolation, explicit composition roots, complete old-consumer inventory, and deletion ordering.
- Confirmed no `/api/v1` route/schema implementation changed and no CP2+ infrastructure code was added.
- Used only an isolated Compose project for destructive volume cleanup; existing default-project volumes were not removed or mutated.

## Unresolved findings and residual concerns

No Important or Minor review finding remains unresolved.

CP2 remains a plan only, as required. Its pgvector schema, PostgreSQL adapter, generation writer, and coordinated production consumer cutover must be implemented and verified in the runnable boundaries now documented; none is claimed as CP1 functionality.
