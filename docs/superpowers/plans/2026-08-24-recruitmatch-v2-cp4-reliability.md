# RecruitMatch v2 Checkpoint 4 Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Celery at-least-once delivery safe through PostgreSQL leases, monotonic fencing, duplicate ACK, bounded retry and Beat recovery.

**Architecture:** Source processing status remains the single task-state authority. Workers claim rows transactionally and carry a lease epoch through renewal and finalization. Beat scans PostgreSQL and dispatches recoverable QUEUED work without doing heavy work or setting RUNNING.

**Tech Stack:** PostgreSQL, SQLAlchemy, Alembic, Celery, Redis, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-24-recruitmatch-productionization-design.md`

## Global Constraints

- `RUNNING + valid lease` duplicate delivery returns and ACKs; it never retries or increments attempts.
- Every renewal/final write checks tenant, source ID and epoch.
- `soft_time_limit < hard_time_limit < visibility_timeout` and long delays use `next_retry_at`.
- Redis has no Result Backend and no business task status.
- Worker/Beat failure degrades workflow health but does not alone fail API readiness.

---

### Task 1: Add processing lease fields and atomic lease repository

**Files:**
- Create: `app/processing/__init__.py`
- Create: `app/processing/leases.py`
- Create: `app/processing/outcomes.py`
- Create: `alembic/versions/20260824_13_processing_leases.py`
- Modify: `app/models/resumes.py`
- Modify: `app/models/knowledge.py`
- Test: `tests/processing/test_leases.py`

**Interfaces:**
- Produces: `ClaimDisposition`, `ProcessDisposition`, `ClaimedLease`, `LeaseRepository.claim/renew/finalize/fail`.
- Consumes: Source single processing-status field.

- [ ] **Step 1: Write claim, duplicate and stale-finalize tests**

```python
def test_live_duplicate_is_noop(repo, running_source):
    result = repo.claim(running_source.tenant_id, running_source.id, "worker-b")
    assert result.disposition is ClaimDisposition.DUPLICATE_ACTIVE
    assert running_source.reload().processing_attempts == 1


def test_stale_epoch_cannot_finalize(repo, taken_over_source):
    assert repo.finalize(taken_over_source.id, taken_over_source.old_epoch, "SUCCEEDED") is False
```

- [ ] **Step 2: Run and confirm `updated_at` lease fails fencing**

Run: `docker compose run --rm test-integration pytest tests/processing/test_leases.py -q`

Expected: FAIL because epoch/owner/expiry/attempt fields and repository are absent.

- [ ] **Step 3: Implement transactional claim and guarded updates**

```python
@dataclass(frozen=True)
class ClaimedLease:
    tenant_id: str
    source_id: str
    epoch: int
    expires_at: datetime

class ClaimDisposition(Enum):
    CLAIMED = "claimed"
    DUPLICATE_ACTIVE = "duplicate_active"
    TERMINAL = "terminal"

class ProcessDisposition(Enum):
    COMPLETED = "completed"
    DUPLICATE_ACTIVE = "duplicate_active"
    TERMINAL = "terminal"
    RETRY_SHORT = "retry_short"
```

`claim()` locks the source row, increments attempts and epoch only for eligible QUEUED/expired work, and returns DUPLICATE_ACTIVE for a live Lease. `renew()` and final methods use an epoch predicate and return `False` on zero affected rows.

- [ ] **Step 4: Run lease repository and migration tests**

Run: `docker compose run --rm test-integration pytest tests/processing/test_leases.py -q`

Expected: all state, epoch and attempt assertions pass.

- [ ] **Step 5: Commit leases**

```bash
git add app/processing app/models/resumes.py app/models/knowledge.py alembic/versions/20260824_13_processing_leases.py tests/processing/test_leases.py
git commit -m "feat: fence asynchronous processing with database leases"
```

### Task 2: Make processors renew and finalize through the fencing token

**Files:**
- Modify: `app/services/resume_processing.py`
- Modify: `app/services/knowledge_processing.py`
- Modify: `app/retrieval/generations.py`
- Create: `app/processing/renewal.py`
- Test: `tests/processing/test_fencing_race.py`

**Interfaces:**
- Consumes: `ClaimedLease`, `LeaseRepository`, staged inactive generation.
- Produces: `LeaseRenewer` and stale-worker-safe processing.

- [ ] **Step 1: Write the Worker A/Worker B race test**

```python
def test_old_worker_cannot_overwrite_takeover(harness):
    lease_a = harness.claim("worker-a")
    harness.expire(lease_a)
    lease_b = harness.claim("worker-b")
    assert lease_b.epoch == lease_a.epoch + 1
    assert harness.finalize(lease_a, "SUCCEEDED") is False
    assert harness.source_status() == "RUNNING"
```

- [ ] **Step 2: Run and prove the current processor overwrites**

Run: `docker compose run --rm test-integration pytest tests/processing/test_fencing_race.py -q`

Expected: FAIL because current finalization checks deletion only.

- [ ] **Step 3: Thread the claimed epoch through all visible writes**

Start a renewer at `lease_seconds / 3`. Stage derived chunks inactive. Extend `GenerationWriter.activate(source, generation)` to `activate(source, generation, expected_epoch)` and require the epoch predicate in the same activation transaction. Before profile, trace, generation activation, SUCCEEDED or FAILED commits, verify epoch. On lost Lease, stop and leave inactive data for reconciliation; do not have the stale Worker delete it.

- [ ] **Step 4: Run race, renewal and generation suites**

Run: `docker compose run --rm test-integration pytest tests/processing/test_fencing_race.py tests/retrieval/test_generation_switch.py -q`

Expected: old epoch affects zero rows and cannot expose output.

- [ ] **Step 5: Commit fenced processors**

```bash
git add app/services/resume_processing.py app/services/knowledge_processing.py app/retrieval/generations.py app/processing/renewal.py tests/processing/test_fencing_race.py
git commit -m "fix: prevent stale workers from committing results"
```

### Task 3: Freeze Celery delivery/time-limit semantics

**Files:**
- Modify: `app/tasks/celery_app.py`
- Modify: `app/tasks/dispatcher.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Test: `tests/processing/test_celery_delivery.py`

**Interfaces:**
- Produces: task entrypoints that ACK duplicates, ignore results and classify bounded retries.
- Consumes: processor `ProcessDisposition` rather than boolean.

- [ ] **Step 1: Write duplicate-no-retry and timeout-order tests**

```python
def test_live_duplicate_returns_without_retry(task_harness):
    result = task_harness.deliver_duplicate()
    assert result == "duplicate_ack"
    assert task_harness.retry_calls == []


def test_timeout_order(settings):
    assert settings.task_soft_time_limit < settings.task_hard_time_limit < settings.redis_visibility_timeout
```

- [ ] **Step 2: Run and confirm current `self.retry(countdown=60)` fails**

Run: `docker compose run --rm test-unit pytest tests/processing/test_celery_delivery.py -q`

Expected: FAIL with an observed retry for the duplicate.

- [ ] **Step 3: Configure Celery and explicit dispositions**

Set `task_acks_late`, `task_reject_on_worker_lost`, prefetch 1, `task_ignore_result=True`, no result backend, soft/hard limits, and Redis visibility timeout. Map DUPLICATE_ACTIVE/TERMINAL to normal return. Use bounded countdown only for short transient failures; persist long retry time in PostgreSQL.

- [ ] **Step 4: Run delivery tests**

Run: `docker compose run --rm test-unit pytest tests/processing/test_celery_delivery.py -q`

Expected: duplicate ACK creates no retry message or attempt increment; timeout relation passes.

- [ ] **Step 5: Commit Celery semantics**

```bash
git add app/tasks/celery_app.py app/tasks/dispatcher.py app/config.py .env.example tests/processing/test_celery_delivery.py
git commit -m "fix: make duplicate Celery delivery a safe ack"
```

### Task 4: Add singleton Beat recovery and layered health

**Files:**
- Create: `app/processing/recovery.py`
- Create: `app/tasks/beat.py`
- Modify: `app/tasks/celery_app.py`
- Modify: `app/api/v1/operations.py`
- Modify: `app/api/v1/deps.py`
- Modify: `docker-compose.yml`
- Test: `tests/processing/test_recovery.py`
- Test: `tests/operations/test_health_and_metrics.py`

**Interfaces:**
- Produces: `RecoveryScanner.run_once(now)`, `/live`, `/ready`, admin `/health/system`.
- Consumes: TaskDispatcher, Artifact/Vector health probes, Worker/Beat heartbeats.

- [ ] **Step 1: Write recovery dispatch-failure and readiness tests**

```python
def test_beat_never_marks_running_before_worker(scanner, expired_source):
    scanner.run_once(now=expired_source.after_expiry)
    assert expired_source.reload().status == "QUEUED"


def test_worker_stale_degrades_system_but_not_readiness(client, admin_headers):
    assert client.get("/api/v1/health/ready").status_code == 200
    body = client.get("/api/v1/health/system", headers=admin_headers).json()
    assert body["worker"] == "stale" and body["overall"] == "degraded"
```

- [ ] **Step 2: Run and confirm Beat/system health are missing**

Run: `docker compose run --rm test-integration pytest tests/processing/test_recovery.py tests/operations/test_health_and_metrics.py -q`

Expected: FAIL for missing recovery and system-health behavior.

- [ ] **Step 3: Implement scanner, Beat schedule and health probes**

Scanner finds stale QUEUED, expired Lease, due `next_retry_at`, cleanup pending and inconsistent generations; it performs guarded transition then dispatches. Dispatch failure leaves recoverable state and stable code. `/ready` checks DB, Alembic Head, pgvector >=0.8, Redis and Bucket; `/health/system` is admin-only and returns bounded statuses without connection details.

- [ ] **Step 4: Run recovery and health suites with Worker stopped**

Run: `docker compose run --rm test-integration pytest tests/processing/test_recovery.py tests/operations/test_health_and_metrics.py -q`

Expected: Beat recovery passes; Worker stale does not turn readiness into 503.

- [ ] **Step 5: Commit recovery and health**

```bash
git add app/processing/recovery.py app/tasks/beat.py app/tasks/celery_app.py app/api/v1/operations.py app/api/v1/deps.py docker-compose.yml tests/processing/test_recovery.py tests/operations/test_health_and_metrics.py
git commit -m "feat: recover expired work with Celery Beat"
```

## Checkpoint 4 Gate

```bash
docker compose up -d postgres redis minio
docker compose run --rm bootstrap
docker compose run --rm test-integration pytest tests/processing tests/operations tests/retrieval/test_generation_switch.py -q
```

Expected: fencing race, renewal, duplicate ACK, bounded timeout, Beat recovery and layered health all pass.
