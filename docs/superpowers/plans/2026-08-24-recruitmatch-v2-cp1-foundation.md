# RecruitMatch v2 Checkpoint 1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the locked Python 3.12/non-root application foundation, stable ports, and a recruiting-only codebase with legacy RAG removed.

**Architecture:** `pyproject.toml` and `uv.lock` become the dependency authority. Domain/application code consumes focused Protocols and a DomainEventRecorder; production adapters remain outside the domain. The existing recruiting API remains, while generic chat/agent/conversation code and configuration are removed.

**Tech Stack:** Python 3.12, uv, FastAPI, Pydantic, SQLAlchemy, pytest, Ruff, mypy, multi-stage Docker.

**Spec:** `docs/superpowers/specs/2026-08-24-recruitmatch-productionization-design.md`

## Global Constraints

- Do not change recruiting `/api/v1` response schemas in this checkpoint.
- Do not add PostgreSQL Vector, MinIO, Celery reliability, or OTel implementation yet; define their stable ports only.
- `app/domain` and application services must not import MinIO, pgvector, Celery, OpenTelemetry, or Prometheus SDKs.
- Every legacy utility retained must move into a recruiting namespace before the legacy namespace is deleted.

---

### Task 1: Make `pyproject.toml` and `uv.lock` the dependency authority

**Files:**
- Create: `.python-version`
- Create: `uv.lock`
- Modify: `pyproject.toml`
- Modify: `Dockerfile`
- Modify: `.github/workflows/ci.yml`
- Delete: `requirements.txt`
- Test: `tests/foundation/test_runtime_contract.py`

**Interfaces:**
- Produces: Python `3.12`, dependency groups `dev`, `eval`, `load`, and a frozen cross-platform lock.
- Consumes: none.

- [ ] **Step 1: Write the failing runtime metadata test**

```python
from pathlib import Path
import tomllib


def test_python_and_dependency_authority_are_frozen():
    root = Path(__file__).parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert (root / ".python-version").read_text().strip() == "3.12"
    assert project["project"]["requires-python"] == ">=3.12,<3.13"
    assert {"dev", "eval", "load"} <= set(project["dependency-groups"])
    assert (root / "uv.lock").is_file()
    assert not (root / "requirements.txt").exists()
```

- [ ] **Step 2: Run the test and confirm the authority is still split**

Run: `docker compose run --rm recruitmatch-api python -m pytest tests/foundation/test_runtime_contract.py -q`

Expected: FAIL because `.python-version`, `uv.lock`, and dependency groups do not exist and `requirements.txt` still exists.

- [ ] **Step 3: Move dependencies into project metadata and lock them**

Use these groups in `pyproject.toml`:

```toml
[project]
name = "recruitmatch-copilot"
version = "0.2.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.110",
  "uvicorn[standard]>=0.29",
  "python-multipart>=0.0.9",
  "pydantic>=2,<3",
  "sqlalchemy>=2.0,<3",
  "alembic>=1.13,<2",
  "psycopg[binary]>=3.2,<4",
  "pyjwt>=2.8,<3",
  "argon2-cffi>=23.1,<26",
  "celery[redis]>=5.4,<6",
  "openai>=1.30",
  "sentence-transformers>=2.5",
  "pypdf>=4.0",
  "python-docx>=1.1,<2",
  "python-dotenv>=1.0",
]

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27,<1", "ruff>=0.6,<1", "mypy>=1.11", "types-pyjwt"]
eval = []
load = []
```

Use uv `0.12.5` consistently in Docker and CI. Generate the first lock without requiring host Python or uv:

```bash
docker run --rm -v "$PWD:/workspace" -w /workspace ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie-slim uv lock
docker run --rm -v "$PWD:/workspace" -w /workspace ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie-slim uv sync --frozen --all-groups
docker run --rm -v "$PWD:/workspace" -w /workspace ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie-slim uv run python -c "import fastapi, sqlalchemy, celery, sentence_transformers, psycopg"
```

Then remove `requirements.txt`, set Ruff target to `py312`, change CI installation to `uv sync --frozen --all-groups`, and make the current Docker stage install all groups temporarily so Tasks 2–3 remain executable inside Compose. Task 4 separates the final runtime `--no-dev` target from the test target.

- [ ] **Step 4: Verify lock, imports, tests and formatting**

Run:

```bash
docker run --rm -v "$PWD:/workspace" -w /workspace ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie-slim uv lock --check
docker compose build recruitmatch-api
docker compose run --rm recruitmatch-api python -m pytest tests/foundation/test_runtime_contract.py tests/test_config.py -q
docker compose run --rm recruitmatch-api ruff check app tests scripts
docker compose run --rm recruitmatch-api ruff format --check app tests scripts
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit dependency authority**

```bash
git add .python-version pyproject.toml uv.lock Dockerfile .github/workflows/ci.yml tests/foundation/test_runtime_contract.py requirements.txt
git commit -m "build: lock Python 3.12 dependencies with uv"
```

### Task 2: Define infrastructure-independent application ports

**Files:**
- Create: `app/artifacts/__init__.py`
- Create: `app/artifacts/ports.py`
- Create: `app/retrieval/__init__.py`
- Create: `app/retrieval/ports.py`
- Create: `app/observability/__init__.py`
- Create: `app/observability/events.py`
- Create: `app/repositories/ports.py`
- Create: `app/repositories/unit_of_work.py`
- Modify: `app/api/v1/deps.py`
- Modify: `app/services/auth.py`
- Modify: `app/services/feedback.py`
- Modify: `app/services/ingestion.py`
- Modify: `app/services/jobs.py`
- Modify: `app/services/knowledge_documents.py`
- Modify: `app/services/knowledge_processing.py`
- Modify: `app/services/matching.py`
- Modify: `app/services/resume_processing.py`
- Modify: `app/services/resumes.py`
- Modify: `app/services/source_index_backfill.py`
- Modify: `app/tasks/dispatcher.py`
- Test: `tests/foundation/test_ports.py`

**Interfaces:**
- Produces: `ArtifactLocation`, `ArtifactStore`, `RecruitingVectorIndex`, `RetrievedChunk`, focused Repository Protocols, `UnitOfWork`, `DomainEvent`, `DomainEventRecorder`, `TaskDispatcher`.
- Consumes: standard library and recruiting DTOs only.

- [ ] **Step 1: Write import and signature tests**

```python
from app.artifacts.ports import ArtifactLocation
from app.observability.events import DomainEvent


def test_artifact_location_is_structured_and_immutable():
    location = ArtifactLocation("t1", "resumes", "r1", "a1")
    assert location.namespace == "resumes"
    try:
        location.artifact_id = "changed"
    except Exception:
        pass
    assert location.artifact_id == "a1"


def test_domain_event_has_bounded_attributes():
    event = DomainEvent("matching.completed", {"outcome": "success"})
    assert event.name == "matching.completed"


def test_application_services_do_not_import_sqlalchemy():
    service_text = "\n".join(path.read_text() for path in Path("app/services").glob("*.py"))
    assert "from sqlalchemy" not in service_text
    assert "import sqlalchemy" not in service_text
```

- [ ] **Step 2: Run and confirm missing modules**

Run: `docker compose run --rm recruitmatch-api pytest tests/foundation/test_ports.py -q`

Expected: FAIL with `ModuleNotFoundError: app.artifacts`.

- [ ] **Step 3: Add the exact contracts**

```python
# app/artifacts/ports.py
from dataclasses import dataclass
from typing import BinaryIO, Protocol

@dataclass(frozen=True)
class ArtifactLocation:
    tenant_id: str
    namespace: str
    owner_id: str
    artifact_id: str

class ArtifactStore(Protocol):
    def put(self, location: ArtifactLocation, stream: BinaryIO, size_bytes: int, sha256: str) -> None:
        raise NotImplementedError
    def read_bounded(self, location: ArtifactLocation, max_bytes: int) -> bytes:
        raise NotImplementedError
    def delete(self, location: ArtifactLocation) -> None:
        raise NotImplementedError

class ArtifactStoreHealthProbe(Protocol):
    def probe(self) -> bool:
        raise NotImplementedError
```

```python
# app/observability/events.py
from dataclasses import dataclass
from typing import Mapping, Protocol

@dataclass(frozen=True)
class DomainEvent:
    name: str
    attributes: Mapping[str, str | int | float | bool]

class DomainEventRecorder(Protocol):
    def record(self, event: DomainEvent) -> None:
        raise NotImplementedError

class NoopDomainEventRecorder:
    def record(self, event: DomainEvent) -> None:
        return None
```

Define retrieval types in Checkpoint 2-facing form and retain dispatcher methods `dispatch_resume()` and `dispatch_knowledge()` with only tenant/source IDs. Add focused Repository Protocols for the methods actually consumed by each recruiting use case plus a `UnitOfWork` Protocol with `commit()`/`rollback()`. Move SQLAlchemy query/transaction details into `app/repositories/*`; inject repositories and the unit of work from `app/api/v1/deps.py` instead of constructing concrete repositories from a Session inside services. Do not create a generic CRUD repository.

- [ ] **Step 4: Run port tests and import-boundary scan**

Run:

```bash
docker compose run --rm recruitmatch-api pytest tests/foundation/test_ports.py -q
! rg "from (minio|pgvector|opentelemetry|prometheus_client)" app/domain app/services
! rg "(^| )import sqlalchemy|from sqlalchemy" app/domain app/services
```

Expected: tests pass and the boundary scan returns no match.

- [ ] **Step 5: Commit ports**

```bash
git add app/artifacts app/retrieval app/observability app/repositories app/api/v1/deps.py app/services app/tasks/dispatcher.py tests/foundation/test_ports.py
git commit -m "refactor: define RecruitMatch infrastructure ports"
```

### Task 3: Remove legacy RAG without losing recruiting utilities

**Files:**
- Modify: `app/main.py`
- Modify: `app/config.py`
- Modify: `app/api/v1/router.py`
- Modify: `web/index.html`
- Delete: `app/agents/`
- Delete: `app/rag/`
- Delete: `app/storage/conversations.py`
- Delete: `app/storage/vector_store.py`
- Delete: `app/api/routes.py`
- Delete: `app/api/schemas.py`
- Delete: `app/api/deps.py`
- Delete: `app/tools/`
- Delete: `tests/test_agent.py`
- Delete: `tests/test_api.py`
- Delete: `tests/test_retriever.py`
- Delete: `tests/test_splitter.py`
- Delete: `tests/test_vector_store.py`
- Test: `tests/foundation/test_legacy_removed.py`

**Interfaces:**
- Consumes: recruiting `/api/v1` application factory and `app/knowledge/chunking.py`.
- Produces: recruiting-only app startup with no legacy feature flag or routes.

- [ ] **Step 1: Write the production-scope legacy gate**

```python
from pathlib import Path


def test_legacy_rag_is_absent_from_production_scope():
    root = Path(__file__).parents[2]
    forbidden = ("LEGACY_RAG", "app.rag", "app.agents", "ConversationStore", "VectorStore")
    files = list((root / "app").rglob("*.py")) + [root / "pyproject.toml", root / ".env.example"]
    text = "\n".join(path.read_text() for path in files if path.exists())
    assert not [token for token in forbidden if token in text]
```

- [ ] **Step 2: Run and confirm legacy references remain**

Run: `docker compose run --rm recruitmatch-api pytest tests/foundation/test_legacy_removed.py -q`

Expected: FAIL listing legacy tokens.

- [ ] **Step 3: Move only proven reusable helpers and delete legacy code**

Keep recruiting chunking in `app/knowledge/chunking.py` and extraction in `app/resumes/extractors.py`; update imports before deleting the legacy directories. Remove `legacy_rag_enabled`, generic data paths, generic router inclusion and the chat UI. Do not add compatibility routes.

- [ ] **Step 4: Verify recruiting behavior and legacy gate**

Run:

```bash
docker compose run --rm recruitmatch-api pytest tests/foundation tests/resumes tests/knowledge tests/matching tests/ai tests/web -q
docker compose run --rm recruitmatch-api pytest tests/foundation/test_legacy_removed.py -q
! rg "LEGACY_RAG|app\\.rag|app\\.agents|VectorStore|conversation" app scripts pyproject.toml .env.example docker-compose.yml
```

Expected: all retained suites pass and the final scan returns no match.

- [ ] **Step 5: Commit legacy removal**

```bash
git add -A app web tests pyproject.toml .env.example docker-compose.yml
git commit -m "refactor: remove legacy generic RAG surface"
```

### Task 4: Build and prove the non-root application image

**Files:**
- Modify: `Dockerfile`
- Create: `.dockerignore`
- Modify: `docker-compose.yml`
- Create: `scripts/bootstrap.py`
- Test: `tests/foundation/test_container_contract.py`

**Interfaces:**
- Produces: one `recruitmatch-app` runtime image plus a `test` build target used by Compose `test-unit` and `test-integration` runners.
- Consumes: `uv.lock`, Python 3.12 metadata from Task 1.

- [ ] **Step 1: Add a static container contract test**

```python
from pathlib import Path


def test_runtime_image_declares_non_root_user():
    dockerfile = Path("Dockerfile").read_text()
    assert "FROM " in dockerfile and " AS builder" in dockerfile
    assert "USER recruitmatch" in dockerfile
    assert "requirements.txt" not in dockerfile
```

- [ ] **Step 2: Run and confirm the root image fails the contract**

Run: `docker compose run --rm recruitmatch-api pytest tests/foundation/test_container_contract.py -q`

Expected: FAIL because the current image has one stage and no `USER`.

- [ ] **Step 3: Implement builder/runtime stages**

The runtime stage must create `recruitmatch` UID/GID 10001, copy the frozen `.venv`, set `PATH=/app/.venv/bin:$PATH`, `PYTHONDONTWRITEBYTECODE=1`, and use `/home/recruitmatch/.cache/huggingface`. Do not copy tests, `.git`, `.env`, local data or build caches. Add a separate `test` target that includes test/development groups and the `tests/` tree; Compose test runners build only this target. Rename application services to `api` and `worker`, add a one-shot non-root `bootstrap` that runs Alembic plus the current idempotent reference seed, and add `test-unit`/`test-integration` profile services from the test target.

- [ ] **Step 4: Build and run runtime assertions**

Run:

```bash
docker build -t recruitmatch:test .
test "$(docker run --rm recruitmatch:test id -u)" = "10001"
docker run --rm recruitmatch:test python -c "import app, fastapi, sqlalchemy, celery"
docker compose run --rm test-unit pytest tests/foundation/test_container_contract.py -q
```

Expected: all commands exit 0 and UID is exactly 10001.

- [ ] **Step 5: Commit image foundation**

```bash
git add Dockerfile .dockerignore docker-compose.yml scripts/bootstrap.py tests/foundation/test_container_contract.py
git commit -m "build: run RecruitMatch from a non-root locked image"
```

## Checkpoint 1 Gate

```bash
docker compose run --rm test-unit uv lock --check
docker compose run --rm test-unit ruff check app tests scripts
docker compose run --rm test-unit ruff format --check app tests scripts
docker compose run --rm test-unit mypy app/artifacts app/retrieval app/observability app/tasks/dispatcher.py
docker compose run --rm test-unit pytest tests/foundation tests/resumes tests/knowledge tests/matching tests/ai tests/web -q
docker build -t recruitmatch:cp1 .
test "$(docker run --rm recruitmatch:cp1 id -u)" = "10001"
! rg "LEGACY_RAG|app\\.rag|app\\.agents|VectorStore|conversation" app scripts pyproject.toml .env.example docker-compose.yml
```

Expected: every command exits 0. Do not start Checkpoint 2 until the lock, import, test, image and legacy gates pass.
