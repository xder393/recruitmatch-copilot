# RecruitMatch Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the tenant, authentication, role policy, database, job template, immutable job version, and protected API foundation for RecruitMatch Copilot.

**Architecture:** Extend the existing FastAPI modular monolith with SQLAlchemy repositories and tenant-scoped services. Keep legacy RAG routes operational while new recruiting APIs live under `/api/v1`; use PostgreSQL in Docker and SQLite in deterministic tests.

**Tech Stack:** Python 3.9+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PyJWT, argon2-cffi, PostgreSQL 16, pytest

**Spec:** `docs/superpowers/specs/2026-08-19-recruitmatch-copilot-design.md`

## Global Constraints

- Sensitive attributes never enter matching inputs.
- Tenant identity comes from the authenticated token, never a caller-provided tenant header or body field.
- Historical match data will reference immutable job versions; editing a job always creates a new version.
- New APIs use `/api/v1`; existing RAG APIs remain available until later migration tasks remove them.
- Tests use SQLite temporary databases and do not require an LLM key or external service.
- Passwords use Argon2 and access tokens expire after 30 minutes by default.

---

## File Map

- `app/database.py`: engine, session factory, declarative base, test reset hook.
- `app/domain/enums.py`: shared roles and lifecycle states.
- `app/models/identity.py`: tenant and user persistence models.
- `app/models/jobs.py`: system template, enterprise job, immutable version models.
- `app/security/passwords.py`: Argon2 hashing and verification.
- `app/security/tokens.py`: JWT issue/decode contracts.
- `app/repositories/identity.py`: tenant-scoped identity reads and writes.
- `app/repositories/jobs.py`: tenant-scoped job and version reads and writes.
- `app/services/auth.py`: bootstrap/login business rules.
- `app/services/jobs.py`: template copy, job editing, publishing and versioning rules.
- `app/api/v1/deps.py`: DB session, authenticated principal and role dependencies.
- `app/api/v1/auth.py`: bootstrap/login/current-user endpoints.
- `app/api/v1/jobs.py`: job template and enterprise job endpoints.
- `app/api/v1/schemas.py`: v1 request/response contracts.
- `app/api/v1/router.py`: v1 router composition.
- `alembic/`: production schema migrations.
- `tests/foundation/`: unit and integration coverage for this phase.

### Task 1: Database and domain persistence

**Files:**
- Create: `app/database.py`
- Create: `app/domain/__init__.py`
- Create: `app/domain/enums.py`
- Create: `app/models/__init__.py`
- Create: `app/models/identity.py`
- Create: `app/models/jobs.py`
- Modify: `app/config.py`
- Modify: `requirements.txt`
- Test: `tests/foundation/test_models.py`

**Interfaces:**
- Produces: `Base`, `create_engine_and_session(database_url)`, `Tenant`, `User`, `JobTemplate`, `Job`, `JobVersion`, `Role`, `JobStatus`.
- Consumes: no recruiting interfaces.

- [ ] **Step 1: Add a failing model persistence test**

```python
def test_job_versions_are_separate_rows(db_session):
    tenant = Tenant(name="Acme")
    job = Job(tenant=tenant, title="AI Engineer", status=JobStatus.DRAFT)
    job.versions.extend([
        JobVersion(version=1, jd_text="v1", profile={"skills": ["Python"]}),
        JobVersion(version=2, jd_text="v2", profile={"skills": ["Python", "RAG"]}),
    ])
    db_session.add(job)
    db_session.commit()
    assert [item.version for item in job.versions] == [1, 2]
```

- [ ] **Step 2: Verify the test fails**

Run: `python -m pytest tests/foundation/test_models.py -v`
Expected: FAIL because `app.models` and the SQLAlchemy fixture do not exist.

- [ ] **Step 3: Implement configuration and SQLAlchemy models**

Add `database_url: str = "sqlite:///data/recruitmatch.db"` to `Settings`, read `DATABASE_URL`, and define string UUID primary keys plus UTC timestamps. `JobVersion` must have `UniqueConstraint("job_id", "version")`, JSON `profile`, `created_by`, and no update service. Define:

```python
class Role(str, Enum):
    ADMIN = "admin"
    RECRUITER = "recruiter"
    LEAD = "lead"

class JobStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"

def create_engine_and_session(database_url: str) -> tuple[Engine, sessionmaker]: ...
```

Add exact runtime dependencies: `sqlalchemy>=2.0,<3`, `alembic>=1.13,<2`, `pyjwt>=2.8,<3`, `argon2-cffi>=23.1,<26`.

- [ ] **Step 4: Verify persistence passes**

Run: `python -m pytest tests/foundation/test_models.py -v`
Expected: PASS with two immutable version rows.

- [ ] **Step 5: Commit database foundation**

```bash
git add app/config.py app/database.py app/domain app/models requirements.txt tests/foundation
git commit -m "feat: add recruiting persistence foundation"
```

### Task 2: Passwords, tokens, authentication, and tenant bootstrap

**Files:**
- Create: `app/security/__init__.py`
- Create: `app/security/passwords.py`
- Create: `app/security/tokens.py`
- Create: `app/repositories/__init__.py`
- Create: `app/repositories/identity.py`
- Create: `app/services/auth.py`
- Test: `tests/foundation/test_auth_service.py`

**Interfaces:**
- Consumes: `Tenant`, `User`, `Role` and a SQLAlchemy `Session`.
- Produces: `Principal(user_id, tenant_id, role)`, `AuthService.bootstrap()`, `AuthService.login()`, `issue_access_token()`, `decode_access_token()`.

- [ ] **Step 1: Write failing authentication tests**

```python
def test_bootstrap_hashes_password_and_login_returns_principal(db_session, token_settings):
    service = AuthService(db_session, token_settings)
    user = service.bootstrap("Acme", "admin@acme.test", "correct horse battery staple")
    assert user.password_hash != "correct horse battery staple"
    token = service.login("admin@acme.test", "correct horse battery staple")
    principal = decode_access_token(token, token_settings)
    assert principal.tenant_id == user.tenant_id
    assert principal.role is Role.ADMIN

def test_login_rejects_wrong_password(db_session, token_settings):
    service = AuthService(db_session, token_settings)
    service.bootstrap("Acme", "admin@acme.test", "correct horse battery staple")
    with pytest.raises(AuthenticationError):
        service.login("admin@acme.test", "wrong password")
```

- [ ] **Step 2: Verify authentication tests fail**

Run: `python -m pytest tests/foundation/test_auth_service.py -v`
Expected: FAIL because security and auth service modules do not exist.

- [ ] **Step 3: Implement authentication contracts**

Use `argon2.PasswordHasher`. JWT claims are exactly `sub`, `tenant_id`, `role`, `iat`, `exp`, `jti`; validate algorithm `HS256`, issuer `recruitmatch`, and audience `recruitmatch-api`. Normalize emails with `strip().lower()`. `bootstrap` creates one tenant and its admin in a transaction, rejecting duplicate email. `login` returns only a token string and uses one generic error for unknown email or invalid password.

- [ ] **Step 4: Verify authentication passes**

Run: `python -m pytest tests/foundation/test_auth_service.py -v`
Expected: PASS, including invalid-password rejection.

- [ ] **Step 5: Commit authentication service**

```bash
git add app/security app/repositories app/services/auth.py tests/foundation/test_auth_service.py
git commit -m "feat: add tenant authentication service"
```

### Task 3: Protected v1 authentication API and role policy

**Files:**
- Create: `app/api/v1/__init__.py`
- Create: `app/api/v1/schemas.py`
- Create: `app/api/v1/deps.py`
- Create: `app/api/v1/auth.py`
- Create: `app/api/v1/router.py`
- Modify: `app/main.py`
- Test: `tests/foundation/test_auth_api.py`

**Interfaces:**
- Consumes: `AuthService`, `decode_access_token`, global `SessionFactory`.
- Produces: `POST /api/v1/auth/bootstrap`, `POST /api/v1/auth/login`, `GET /api/v1/auth/me`, `require_roles(*roles)`.

- [ ] **Step 1: Write failing API tests**

```python
def test_login_and_me(v1_client):
    v1_client.post("/api/v1/auth/bootstrap", json={
        "tenant_name": "Acme", "email": "admin@acme.test", "password": "correct horse battery staple"
    })
    login = v1_client.post("/api/v1/auth/login", json={
        "email": "admin@acme.test", "password": "correct horse battery staple"
    })
    token = login.json()["access_token"]
    me = v1_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["tenant_name"] == "Acme"

def test_me_rejects_missing_token(v1_client):
    assert v1_client.get("/api/v1/auth/me").status_code == 401
```

- [ ] **Step 2: Verify v1 API tests fail**

Run: `python -m pytest tests/foundation/test_auth_api.py -v`
Expected: FAIL with 404 for the absent v1 endpoints.

- [ ] **Step 3: Implement schemas, dependencies, and routes**

Define `BootstrapRequest`, `LoginRequest`, `TokenResponse`, and `CurrentUserResponse`. Password fields require 12 to 128 characters. Use `HTTPBearer(auto_error=False)`. Authentication failures return `{"ok": false, "error": {"code": "unauthorized", ...}}` through the existing error system. `require_roles` compares enum values and returns 403 without leaking resource existence.

- [ ] **Step 4: Verify v1 API tests pass**

Run: `python -m pytest tests/foundation/test_auth_api.py -v`
Expected: PASS for authorized and unauthorized paths.

- [ ] **Step 5: Commit protected v1 API**

```bash
git add app/api/v1 app/main.py tests/foundation/test_auth_api.py
git commit -m "feat: expose protected recruiting API"
```

### Task 4: Immutable tenant-scoped job catalog

**Files:**
- Create: `app/repositories/jobs.py`
- Create: `app/services/jobs.py`
- Test: `tests/foundation/test_job_service.py`
- Test: `tests/foundation/test_tenant_isolation.py`

**Interfaces:**
- Consumes: `JobTemplate`, `Job`, `JobVersion`, `JobStatus`, `Principal`.
- Produces: `JobService.list_templates()`, `create_job()`, `update_job()`, `activate_job()`, `deactivate_job()`, `get_job()`, `list_jobs()`.

- [ ] **Step 1: Write failing versioning and isolation tests**

```python
def test_updating_job_creates_immutable_version(job_service, admin_principal):
    job = job_service.create_job(admin_principal, title="AI Engineer", jd_text="Python")
    updated = job_service.update_job(admin_principal, job.id, jd_text="Python and RAG")
    assert updated.current_version == 2
    assert [v.jd_text for v in updated.versions] == ["Python", "Python and RAG"]

def test_other_tenant_cannot_read_job(service_for_two_tenants, principals):
    job = service_for_two_tenants.create_job(principals.acme, title="Private", jd_text="secret")
    with pytest.raises(ResourceNotFoundError):
        service_for_two_tenants.get_job(principals.globex, job.id)
```

- [ ] **Step 2: Verify job tests fail**

Run: `python -m pytest tests/foundation/test_job_service.py tests/foundation/test_tenant_isolation.py -v`
Expected: FAIL because the repository and job service do not exist.

- [ ] **Step 3: Implement tenant-scoped repository and service**

Every repository method requires `tenant_id`. `create_job` creates version 1 in the same transaction. `update_job` appends `current_version + 1`; it never updates an existing `JobVersion`. Only `ADMIN` and `RECRUITER` may mutate jobs; `LEAD` can read. `activate_job` requires a non-empty JD and profile keys `job_family`, `level`, `required_skills`, `preferred_skills`, and `weights`. Other-tenant IDs resolve as not found.

- [ ] **Step 4: Verify versioning and isolation pass**

Run: `python -m pytest tests/foundation/test_job_service.py tests/foundation/test_tenant_isolation.py -v`
Expected: PASS, including immutable versions and hidden cross-tenant resources.

- [ ] **Step 5: Commit job domain**

```bash
git add app/repositories/jobs.py app/services/jobs.py tests/foundation/test_job_service.py tests/foundation/test_tenant_isolation.py
git commit -m "feat: add tenant scoped job versioning"
```

### Task 5: Job APIs, seed templates, migration, and foundation verification

**Files:**
- Create: `app/api/v1/jobs.py`
- Modify: `app/api/v1/router.py`
- Modify: `app/api/v1/schemas.py`
- Create: `app/seeds/job_templates.json`
- Create: `scripts/seed_job_templates.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/20260819_01_foundation.py`
- Modify: `docker-compose.yml`
- Create: `.github/workflows/ci.yml`
- Test: `tests/foundation/test_jobs_api.py`

**Interfaces:**
- Consumes: `JobService`, `require_roles`, SQLAlchemy models.
- Produces: template list, job CRUD/version/activate/deactivate APIs, initial schema migration, ten seeded technical job families.

- [ ] **Step 1: Write failing job API test**

```python
def test_create_update_and_list_job(authenticated_client):
    created = authenticated_client.post("/api/v1/jobs", json={
        "title": "AI 应用开发工程师",
        "jd_text": "负责 RAG 应用，要求 Python 与 FastAPI",
        "profile": {
            "job_family": "ai_application", "level": "mid",
            "required_skills": ["Python", "FastAPI"], "preferred_skills": ["RAG"],
            "weights": {"skills": 0.5, "experience": 0.3, "projects": 0.2}
        }
    })
    assert created.status_code == 201
    job_id = created.json()["id"]
    updated = authenticated_client.put(f"/api/v1/jobs/{job_id}", json={"jd_text": "新增 Agent 要求"})
    assert updated.json()["current_version"] == 2
    assert authenticated_client.get("/api/v1/jobs").json()["total"] == 1
```

- [ ] **Step 2: Verify job API test fails**

Run: `python -m pytest tests/foundation/test_jobs_api.py -v`
Expected: FAIL with 404 for absent job routes.

- [ ] **Step 3: Implement job APIs and seed data**

Expose `GET /job-templates`, `POST/GET /jobs`, `GET/PUT /jobs/{id}`, `POST /jobs/{id}/activate`, and `POST /jobs/{id}/deactivate`. Seed these exact families with junior/mid/senior variants: AI application, LLM algorithm, machine learning, data engineering, data analysis, backend, frontend, full stack, test development, and DevOps/SRE. The seed script upserts by `slug` and never overwrites an enterprise job.

- [ ] **Step 4: Add initial migration, PostgreSQL service, and CI**

The migration creates identity and job tables plus tenant and status indexes. Compose adds `postgres:16-alpine` with a health check and configures the API `DATABASE_URL`. CI uses Python 3.11 and runs:

```yaml
- run: pip install -r requirements.txt
- run: python -m ruff check app tests scripts
- run: python -m pytest -q
```

- [ ] **Step 5: Run complete foundation verification**

Run: `python -m pytest tests/foundation -q && python -m pytest tests -q && python -m ruff check app tests scripts`
Expected: all foundation and legacy tests pass; Ruff exits 0.

- [ ] **Step 6: Commit foundation delivery**

```bash
git add app/api/v1 app/seeds scripts/seed_job_templates.py alembic.ini alembic docker-compose.yml .github tests/foundation
git commit -m "feat: deliver recruiting job catalog foundation"
```

## Plan Self-Review

- Spec coverage for this phase: tenant identity, JWT/RBAC, job templates, enterprise job lifecycle, immutable versions, migration, PostgreSQL configuration, tenant isolation tests, and CI are assigned to Tasks 1–5.
- Deferred by explicit phase boundary: resume artifacts, background jobs, matching, feedback, evaluation, observability dashboard, and performance tests belong to later plans.
- Interfaces use `Principal(user_id, tenant_id, role)` consistently; all protected repository operations require `tenant_id` derived from that principal.
- The plan contains no incomplete implementation markers or undefined cross-task service names.
