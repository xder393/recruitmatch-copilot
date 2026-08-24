# RecruitMatch v2 Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the frozen RecruitMatch Architecture Baseline v2 through six independently reviewable checkpoints.

**Architecture:** Keep the modular monolith and one application image, but replace legacy local RAG/storage with PostgreSQL/pgvector, MinIO, fenced Celery processing, and a single OTLP observability pipeline. Each checkpoint ends in working software and a commit gate; later checkpoints consume only interfaces frozen by earlier ones.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16 + pgvector 0.8+, Redis 7, Celery 5, MinIO/S3, OpenTelemetry Collector, Prometheus, Tempo, Grafana, Docker Compose, uv, pytest, k6.

**Spec:** `docs/superpowers/specs/2026-08-24-recruitmatch-productionization-design.md`

## Global Constraints

- Single-host Docker Compose is the only complete runtime path; host Python is never required.
- PostgreSQL is the only business-state authority; Redis is broker plus short-lived Worker/Beat heartbeat only.
- API, Worker, Beat, and Bootstrap use one immutable non-root image with UID/GID 10001.
- Tenant/authorization predicates and vector ordering live in the same SQL; Python-side tenant filtering is forbidden.
- All visible async finalization and generation activation require `processing_lease_epoch` fencing.
- LLM, Embedding, and Telemetry are soft dependencies; deterministic core behavior remains available.
- CI and concurrent load tests use deterministic Fake Model and normalized 512-dimensional Fake Embedding.
- SQLite is limited to unit and adapter-fake tests; migrations and infrastructure integration use PostgreSQL/pgvector.
- No checkpoint may claim production SLA, capacity, accuracy, users, or business outcomes without generated evidence.
- A core invariant change requires an ADR before implementation continues.

---

## Dependency Graph

```text
Checkpoint 1: Foundation, dependency lock, ports, legacy removal
        |
        v
Checkpoint 2: PostgreSQL/pgvector schema and retrieval
        |
        v
Checkpoint 3: Artifact entity and MinIO adapter
        |
        v
Checkpoint 4: Fenced Celery processing, Beat, health
        |
        v
Checkpoint 5: OTel, Prometheus, Tempo, Grafana
        |
        v
Checkpoint 6: hardened Compose, CI, security, load report, docs
```

Checkpoints execute in order. Within a checkpoint, tasks execute in file order and each task gets its own commit.

## Checkpoint Index

| Checkpoint | Plan | Independent acceptance gate |
| --- | --- | --- |
| 1 | `docs/superpowers/plans/2026-08-24-recruitmatch-v2-cp1-foundation.md` | Locked Python 3.12 image runs non-root; legacy production imports/routes are gone; unit suite passes |
| 2 | `docs/superpowers/plans/2026-08-24-recruitmatch-v2-cp2-pgvector.md` | Clean PostgreSQL migration, tenant-safe pgvector search, atomic generation and citation tests pass |
| 3 | `docs/superpowers/plans/2026-08-24-recruitmatch-v2-cp3-artifacts.md` | Concurrent idempotent upload, bounded S3 reads, privacy delete and reconciliation pass against MinIO |
| 4 | `docs/superpowers/plans/2026-08-24-recruitmatch-v2-cp4-reliability.md` | Fencing race, duplicate ACK, Beat recovery and layered health tests pass |
| 5 | `docs/superpowers/plans/2026-08-24-recruitmatch-v2-cp5-observability.md` | Prometheus metric and Tempo API→Worker trace are queryable; PII and Collector-outage tests pass |
| 6 | `docs/superpowers/plans/2026-08-24-recruitmatch-v2-cp6-delivery.md` | Fresh-clone Compose, security gates, CI, k6 invariant checker and generated report pass |

## Frozen-Spec Traceability

| Baseline v2 section | Owning checkpoint | Acceptance evidence |
| --- | --- | --- |
| 3 Overall architecture and Compose boundary | CP1, CP6 | Port/import tests; fresh-clone default topology |
| 4 Recruiting domain and legacy RAG removal | CP1 | Legacy scan plus retained recruiting suites |
| 5 PostgreSQL/pgvector retrieval | CP2 | Migration, authorization, generation and citation tests |
| 6 MinIO/S3 Artifact lifecycle | CP3 | MinIO saga, bounded read, deletion and reconciliation tests |
| 7 Celery, Lease and recovery | CP4 | Fencing race, duplicate ACK and Beat recovery tests |
| 8 Layered health | CP4 | Live/ready/system degradation tests |
| 9 OTel/Prometheus/Tempo/Grafana | CP5 | Privacy tests and real backend telemetry E2E |
| 10 Docker, dependencies and supply chain | CP1, CP6 | Frozen lock, non-root image, scans and SBOM |
| 11 Test matrix and GitHub Actions | CP6 | Workflow contract plus all Compose test runners |
| 12 Performance test and report | CP6 | k6 A/B/C runs, invariants and generated report |
| 13 Operations and documentation | CP6 | Compose-only runbook and black-box fresh clone |
| 14 Implementation boundary and acceptance | All | Per-checkpoint gates plus final gate |

## Global Checkpoint Protocol

- [ ] **Before each checkpoint: verify branch and clean working tree**

Run:

```bash
git branch --show-current
git status --short
```

Expected: intended `codex/*` implementation branch and no unrelated changes. Do not implement directly on `main`.

- [ ] **Read only the frozen spec, roadmap, current checkpoint, and files named by its first task**

Expected: no reliance on stale legacy plans. If the checkpoint conflicts with Baseline v2, stop and write an ADR proposal.

- [ ] **Execute every task red-green-refactor with one commit per task**

Expected: the named test fails for the expected reason before implementation, then passes after the minimal change.

- [ ] **Run the checkpoint gate**

Run the exact gate command in the checkpoint plan. Capture output before claiming completion.

- [ ] **Review the diff and commit range**

Run:

```bash
git diff --check
git status --short
git log --oneline --decorate -12
```

Expected: no whitespace errors, no uncommitted checkpoint work, and small task-oriented commits.

## Final Gate

After Checkpoint 6:

```bash
cp .env.example .env
docker compose down -v
docker compose up --build --wait
docker compose run --rm test-integration
docker compose run --rm test-ai-regression
docker compose run --rm test-telemetry
docker compose --profile loadtest run --rm k6-smoke
docker compose --profile loadtest run --rm invariant-checker
```

Expected:

- all default services healthy or successfully completed;
- integration, AI regression, telemetry and smoke suites exit 0;
- invariant checker reports all five invariant counts as zero;
- `docker compose exec api id -u`, Worker, Beat and Bootstrap image checks return `10001`;
- `rg` legacy gate over production paths returns no match;
- GitHub Actions for the target `main` commit is green before the README badge is described as passing.
