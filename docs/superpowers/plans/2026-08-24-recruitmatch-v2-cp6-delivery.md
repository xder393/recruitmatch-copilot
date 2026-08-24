# RecruitMatch v2 Checkpoint 6 Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the reproducible Compose delivery path, security/CI gates, deterministic k6 benchmark, generated performance report and honest GitHub documentation.

**Architecture:** Default Compose performs infrastructure health, one-shot MinIO/bootstrap initialization and non-root application startup from Named Volumes. GitHub Actions reuses the same locked image and real infrastructure integration path. k6 uses Fake AI but real application infrastructure, followed by a PostgreSQL invariant checker.

**Tech Stack:** Docker Compose, uv, GitHub Actions, OSV-Scanner, Bandit, Gitleaks, Trivy, CycloneDX, k6, Markdown.

**Spec:** `docs/superpowers/specs/2026-08-24-recruitmatch-productionization-design.md`

## Global Constraints

- Fresh clone + copied `.env.example` + `docker compose up --build` has no hidden initialization step.
- Application services are read-only/non-root; stateful infrastructure is not mechanically read-only.
- All image tags have versions; target state also pins manifest digests; no `latest`.
- CI smoke does not enforce canonical laptop P95/P99 budgets.
- Real LLM never runs under `LOAD_TEST_MODE=true`.

---

### Task 1: Finalize Compose bootstrap, seed and hardening contracts

**Files:**
- Modify: `docker-compose.yml`
- Modify: `scripts/bootstrap.py`
- Modify: `scripts/seed_job_templates.py`
- Modify: `.env.example`
- Modify: `Dockerfile`
- Test: `tests/delivery/test_compose_contract.py`
- Test: `tests/delivery/test_seed_contract.py`

**Interfaces:**
- Produces: one-shot `minio-init`, one-shot `bootstrap`, idempotent system seed, optional demo seed.
- Consumes: application image and all default infrastructure services.

- [ ] **Step 1: Write Compose and seed contract tests**

```python
def test_default_compose_uses_named_volumes_and_hardened_app_services(compose):
    assert "./data:/app/data" not in compose.raw
    for name in ("api", "worker", "beat", "bootstrap"):
        assert compose.service(name).user == "10001:10001"
        assert compose.service(name).read_only is True


def test_demo_seed_requires_explicit_flag(settings):
    assert settings.demo_seed_enabled is False
```

- [ ] **Step 2: Run and confirm existing Compose violates the contract**

Run: `docker compose run --rm test-unit pytest tests/delivery/test_compose_contract.py tests/delivery/test_seed_contract.py -q`

Expected: FAIL for bind-mounted data, missing services and seed flag.

- [ ] **Step 3: Implement default Compose topology**

Add all 12 default services and Named Volumes from Baseline v2, plus profile-scoped `test-unit`, `test-integration`, `test-ai-regression` and `test-telemetry` runners. `api/worker/beat` depend on both one-shot services with `service_completed_successfully`. Apply `read_only`, `/tmp` tmpfs, `cap_drop: ALL`, `no-new-privileges` to application services only. Bootstrap runs Alembic, idempotent reference seed and conditional demo seed; demo password comes from `.env`. Pin every third-party image to an explicit version and manifest digest, including PostgreSQL/pgvector, Redis, MinIO/MC, Collector, Prometheus, Tempo and Grafana.

- [ ] **Step 4: Recreate and verify idempotent bootstrap**

Run:

```bash
docker compose down -v
cp .env.example .env
docker compose up --build --wait
docker compose run --rm bootstrap
docker compose ps
test "$(docker compose exec -T api id -u)" = "10001"
test "$(docker compose exec -T worker id -u)" = "10001"
test "$(docker compose exec -T beat id -u)" = "10001"
```

Expected: startup and second bootstrap exit 0; app UIDs are 10001.

- [ ] **Step 5: Commit Compose runtime**

```bash
git add docker-compose.yml Dockerfile .env.example scripts/bootstrap.py scripts/seed_job_templates.py tests/delivery
git commit -m "ops: finalize hardened Compose bootstrap path"
```

### Task 2: Add supply-chain and static security gates

**Files:**
- Create: `security/.osv-scanner.toml`
- Create: `security/.trivyignore.yaml`
- Create: `security/exceptions.md`
- Create: `scripts/check_security_exceptions.py`
- Modify: `docker-compose.yml`
- Modify: `pyproject.toml`
- Test: `tests/delivery/test_security_policy.py`

**Interfaces:**
- Produces: expiring exception validation, dependency/SAST/secret/image scans and CycloneDX SBOM.
- Consumes: `uv.lock` and final runtime image.

- [ ] **Step 1: Write expiration-policy tests**

```python
def test_expired_security_exception_fails(tmp_path):
    path = tmp_path / "exceptions.md"
    path.write_text("CVE-1 | impact | reason | repository maintainer | 2000-01-01")
    assert check_exceptions(path, today=date(2026, 8, 24)) == 1
```

- [ ] **Step 2: Run and confirm policy checker is absent**

Run: `docker compose run --rm test-unit pytest tests/delivery/test_security_policy.py -q`

Expected: FAIL importing `check_exceptions`.

- [ ] **Step 3: Implement strict exception format and scan commands**

Require vulnerability ID, impact, reason, Owner and ISO expiry. Configure OSV for locked dependencies, Bandit for `app`, Gitleaks for history/current tree, and Trivy for High/Critical OS/package findings. Export CycloneDX from the lock. Add digest-pinned Compose profile services `security-osv`, `security-bandit`, `security-gitleaks`, `security-trivy` and `sbom`; each mounts only the minimum source/image/socket material required and is the local/CI execution contract.

- [ ] **Step 4: Run all local security gates**

Run:

```bash
docker compose run --rm test-unit pytest tests/delivery/test_security_policy.py -q
docker build -t recruitmatch:security .
docker compose --profile security run --rm security-bandit
docker compose --profile security run --rm security-osv
docker compose --profile security run --rm security-gitleaks
docker compose --profile security run --rm security-trivy recruitmatch:security
docker compose --profile security run --rm sbom
```

Expected: commands exit 0 or a real finding is fixed/documented with unexpired narrow exception.

- [ ] **Step 5: Commit security gates**

```bash
git add security scripts/check_security_exceptions.py docker-compose.yml pyproject.toml uv.lock tests/delivery/test_security_policy.py
git commit -m "security: gate dependencies code secrets and image"
```

### Task 3: Replace CI with required reproducible jobs

**Files:**
- Modify: `.github/workflows/ci.yml`
- Test: `tests/delivery/test_ci_contract.py`

**Interfaces:**
- Produces: validate, unit, integration, ai-regression, telemetry-e2e, security, image and performance-smoke jobs.
- Consumes: Compose test runners and scan commands from prior tasks.

- [ ] **Step 1: Write workflow policy tests**

```python
def test_actions_are_sha_pinned_and_permissions_are_read_only(workflow):
    assert workflow["permissions"] == {"contents": "read"}
    for use in workflow.all_uses():
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", use)


def test_artifacts_expire(workflow):
    assert all(days in {7, 14} for days in workflow.artifact_retention_days())
```

- [ ] **Step 2: Run and confirm current workflow fails policy**

Run: `docker compose run --rm test-unit pytest tests/delivery/test_ci_contract.py -q`

Expected: FAIL because Actions use tags, jobs are incomplete and retention is absent.

- [ ] **Step 3: Implement jobs and failure artifacts**

Pin every `uses:` to a full SHA with a version comment. Set workflow `permissions: contents: read`, concurrency cancellation, Compose-backed integration/telemetry, deterministic AI regression, security/image gates and 60-second invariant smoke. The validate job runs Ruff plus `mypy app/artifacts app/retrieval app/processing app/observability`; it must not use global `ignore_errors`. Upload reports/SBOM/logs with 7 or 14 day retention.

- [ ] **Step 4: Validate workflow structure locally**

Run:

```bash
docker compose run --rm test-unit pytest tests/delivery/test_ci_contract.py -q
docker compose config --quiet
```

Expected: workflow policy and Compose config tests pass.

- [ ] **Step 5: Commit CI**

```bash
git add .github/workflows/ci.yml tests/delivery/test_ci_contract.py
git commit -m "ci: enforce RecruitMatch v2 delivery gates"
```

### Task 4: Add deterministic k6 scenarios and invariant checker

**Files:**
- Create: `load/k6/scenarios.js`
- Create: `load/k6/config.js`
- Create: `load/fixtures/resume.txt`
- Create: `app/testing/fake_embedding.py`
- Create: `scripts/check_benchmark_invariants.py`
- Modify: `app/config.py`
- Modify: `docker-compose.yml`
- Test: `tests/load/test_fake_embedding.py`
- Test: `tests/load/test_invariant_checker.py`

**Interfaces:**
- Produces: `k6-smoke`, `k6-benchmark`, `invariant-checker` Compose services.
- Consumes: real API/DB/Redis/Celery/MinIO and Fake Model/Embedding.

- [ ] **Step 1: Write Fake Embedding and hard-protection tests**

```python
def test_fake_embedding_is_normalized_deterministic_and_distinct():
    embedder = HashEmbedding(512)
    assert embedder.embed_query("a") == embedder.embed_query("a")
    assert embedder.embed_query("a") != embedder.embed_query("b")
    assert math.isclose(sum(x*x for x in embedder.embed_query("a")), 1.0, rel_tol=1e-6)


def test_load_mode_rejects_real_provider(monkeypatch):
    monkeypatch.setenv("LOAD_TEST_MODE", "true")
    monkeypatch.setenv("MODEL_PROVIDER", "openai-compatible")
    with pytest.raises(ConfigError):
        Settings.load()
```

- [ ] **Step 2: Run and confirm load harness is absent**

Run: `docker compose run --rm test-unit pytest tests/load -q`

Expected: FAIL importing `HashEmbedding`/invariant checker.

- [ ] **Step 3: Implement operation-tagged scenarios and bounded polling**

Tag `auth.login`, `jobs.list/search/detail`, `resume.accept`, `match.create/read`, `citation.read`; use bounded poll interval/timeout; measure `resume.processing.e2e` and `matching.e2e`. Smoke validates completion/error/drain/invariants only. Canonical run uses 2-minute warmup, 10-minute steady and 10→25→50 VUs.

Invariant checker queries PostgreSQL and exits nonzero unless expired/no-Lease RUNNING, multiple active generations, cleanup pending, duplicate side effects and fencing overwrite counts are all zero.

- [ ] **Step 4: Run Smoke and invariant gate**

Run:

```bash
docker compose run --rm test-unit pytest tests/load -q
docker compose --profile loadtest run --rm k6-smoke
docker compose --profile loadtest run --rm invariant-checker
```

Expected: all commands exit 0; no real provider is called.

- [ ] **Step 5: Commit load harness**

```bash
git add load app/testing app/config.py scripts/check_benchmark_invariants.py docker-compose.yml tests/load
git commit -m "perf: add deterministic Compose load harness"
```

### Task 5: Generate canonical performance evidence

**Files:**
- Create: `scripts/render_performance_report.py`
- Create: `docs/performance/latest.md`
- Create: `docs/performance/latest.json`
- Modify: `docker-compose.yml`
- Test: `tests/load/test_performance_report.py`

**Interfaces:**
- Produces: machine-generated Markdown/JSON tied to Git SHA and hardware.
- Consumes: k6 summary, environment facts, telemetry A/B/C experiments and invariant output.

- [ ] **Step 1: Write required-field report test**

```python
def test_report_contains_environment_operations_and_invariants(report):
    assert {"git_sha", "cpu", "cores", "ram_bytes", "os", "architecture",
            "docker_version", "worker_replicas", "operations", "invariants"} <= report.keys()
```

- [ ] **Step 2: Run and confirm report generator is absent**

Run: `docker compose run --rm test-unit pytest tests/load/test_performance_report.py -q`

Expected: FAIL importing report renderer.

- [ ] **Step 3: Implement A/B/C benchmark orchestration and renderer**

Add a `performance-report` profile service. Run A with OTel 10%, B with OTel off, C with Collector stopped mid-run. Render per-operation P50/P95/P99/RPS/error rate, async E2E, drain, Exact/HNSW results, overhead, outage and all invariants. Do not compute improvement percentage across different hardware fingerprints.

- [ ] **Step 4: Execute canonical benchmark and validate report**

Run:

```bash
docker compose --profile loadtest run --rm k6-benchmark
docker compose --profile loadtest run --rm invariant-checker
docker compose --profile loadtest run --rm performance-report
docker compose run --rm test-unit pytest tests/load/test_performance_report.py -q
```

Expected: commands exit 0 and both report files contain actual run data, not placeholders.

- [ ] **Step 5: Commit generated evidence**

```bash
git add scripts/render_performance_report.py docs/performance/latest.md docs/performance/latest.json docker-compose.yml tests/load/test_performance_report.py
git commit -m "perf: publish reproducible RecruitMatch benchmark"
```

### Task 6: Rewrite runbook and prove fresh-clone startup

**Files:**
- Modify: `README.md`
- Modify: `docs/usage-guide.md`
- Modify: `docs/architecture.md`
- Modify: `docs/github-publish-checklist.md`
- Create: `Makefile`
- Create: `scripts/verify_fresh_clone.sh`
- Test: `tests/delivery/test_documentation_contract.py`

**Interfaces:**
- Produces: Compose-only runbook, honest resume claims and CI badge tied to the current workflow.
- Consumes: all prior checkpoint commands.

- [ ] **Step 1: Write documentation contract tests**

```python
def test_readme_documents_only_compose_runtime():
    text = Path("README.md").read_text()
    assert "docker compose up --build" in text
    assert "python -m uvicorn" not in text
    assert "真实生产 SLA" in text


def test_make_targets_only_wrap_compose():
    lines = Path("Makefile").read_text().splitlines()
    assert not any(" pip " in line or line.lstrip().startswith("python ") for line in lines)
```

- [ ] **Step 2: Run and confirm current documentation is stale**

Run: `docker compose run --rm test-unit pytest tests/delivery/test_documentation_contract.py -q`

Expected: FAIL on old local-Python/local-storage instructions.

- [ ] **Step 3: Document exact operations and create black-box verifier**

Cover startup/reset, Seed modes, AI modes, health semantics, Grafana, tests/scans/load, cleanup/Lease/Beat troubleshooting and privacy deletion. Makefile targets call Docker Compose only. Fresh-clone script clones to a temporary directory, copies `.env.example`, starts empty Volumes, runs smoke checks, then tears down its explicit Compose project.

- [ ] **Step 4: Run documentation and fresh-clone gates**

Run:

```bash
docker compose run --rm test-unit pytest tests/delivery/test_documentation_contract.py -q
./scripts/verify_fresh_clone.sh
```

Expected: both exit 0 with no manual Bucket/database/script step.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md docs/usage-guide.md docs/architecture.md docs/github-publish-checklist.md Makefile scripts/verify_fresh_clone.sh tests/delivery/test_documentation_contract.py
git commit -m "docs: publish the Compose-only RecruitMatch runbook"
```

## Checkpoint 6 Gate

```bash
./scripts/verify_fresh_clone.sh
docker compose run --rm test-integration
docker compose run --rm test-ai-regression
docker compose run --rm test-telemetry
docker compose --profile loadtest run --rm k6-smoke
docker compose --profile loadtest run --rm invariant-checker
docker compose run --rm test-unit pytest tests/delivery tests/load -q
```

Expected: all local gates pass. Push the target commit, wait for every required GitHub Actions job to succeed, then verify the README badge references the same `main` workflow before claiming passing CI.
