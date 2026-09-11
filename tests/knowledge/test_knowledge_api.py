from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from tests.support.application import create_sqlite_test_app
from app.models.knowledge import KnowledgeDocument
from app.models.jobs import JobVersion
from app.models.resumes import Resume
from app.domain.enums import Role
from app.security.tokens import Principal, issue_access_token
from tests.support.database import prepare_test_database


class HashEmbedder:
    model_name = "fake-512-v1"

    def _vector(self, text):
        vector = [0.0] * 512
        vector[sum(text.encode()) % 512] = 1.0
        return vector

    def embed_documents(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)


@pytest.fixture
def knowledge_app(tmp_path):
    settings = Settings(
        api_key="",
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        jwt_secret="a-test-secret-that-is-at-least-32-bytes",
    )
    prepare_test_database(settings.database_url)
    return create_sqlite_test_app(settings, knowledge_embedder=HashEmbedder())


@pytest.fixture
def client(knowledge_app):
    with TestClient(knowledge_app) as test_client:
        yield test_client


def _login(client, tenant, email):
    client.headers.pop("Authorization", None)
    client.post(
        "/api/v1/auth/bootstrap",
        json={"tenant_name": tenant, "email": email, "password": "correct horse battery staple"},
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct horse battery staple"},
    ).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client.get("/api/v1/auth/me").json()["tenant_id"]


def _upload(client, content=b"Python interview policy"):
    return client.post(
        "/api/v1/knowledge-documents",
        data={"document_type": "policy"},
        files={"file": ("policy.txt", content, "text/plain")},
    )


def _search(app, tenant_id, source_type, source_id, source_version, query):
    from app.retrieval import SearchScope

    scope = SearchScope(
        tenant_id,
        frozenset({source_type}),
        frozenset({(source_type, source_id, source_version)}),
    )
    return app.state.retrieval_index.search(
        scope,
        app.state.embedding_adapter.embed_query(query),
        app.state.embedding_adapter.model_name,
        5,
        -1.0,
    )


def test_upload_is_idempotent_and_processed_inline(client):
    _login(client, "Acme", "admin@acme.test")
    first = _upload(client)
    second = _upload(client)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["status"] == "ready"
    assert client.get("/api/v1/knowledge-documents").json()["total"] == 1


def test_failed_knowledge_dispatch_remains_queued_for_recovery(client, knowledge_app):
    class BrokenDispatcher:
        def dispatch_knowledge(self, tenant_id, document_id):
            raise RuntimeError("broker unavailable")

    tenant_id = _login(client, "Acme", "admin@acme.test")
    working = knowledge_app.state.knowledge_dispatcher
    knowledge_app.state.knowledge_dispatcher = BrokenDispatcher()
    failed = _upload(client, b"retryable policy")
    assert failed.status_code == 202
    assert failed.json()["status"] == "uploaded"
    assert failed.json()["error_code"] == "task_dispatch_failed"

    knowledge_app.state.knowledge_dispatcher = working
    retried = _upload(client, b"retryable policy")
    assert retried.status_code == 202
    assert retried.json()["status"] == "uploaded"
    knowledge_app.state.knowledge_processor.process(tenant_id, failed.json()["id"])
    assert client.get(f"/api/v1/knowledge-documents/{failed.json()['id']}").json()["status"] == "ready"


def test_cross_tenant_document_is_hidden(client):
    _login(client, "Acme", "admin@acme.test")
    document_id = _upload(client).json()["id"]
    _login(client, "Globex", "admin@globex.test")
    assert client.get(f"/api/v1/knowledge-documents/{document_id}").status_code == 404


def test_deactivate_excludes_document_chunks_from_search(client, knowledge_app):
    tenant_id = _login(client, "Acme", "admin@acme.test")
    document_id = _upload(client).json()["id"]
    with knowledge_app.state.session_factory() as session:
        checksum = session.get(KnowledgeDocument, document_id).checksum
    assert _search(knowledge_app, tenant_id, "knowledge_document", document_id, checksum, "Python")
    response = client.post(f"/api/v1/knowledge-documents/{document_id}/deactivate")
    assert response.json()["status"] == "inactive"
    assert _search(knowledge_app, tenant_id, "knowledge_document", document_id, checksum, "Python") == []


def test_resume_and_job_versions_are_indexed_for_rag(client, knowledge_app):
    tenant_id = _login(client, "Acme", "admin@acme.test")
    resume = client.post(
        "/api/v1/resumes",
        files={"file": ("resume.txt", b"Python FastAPI", "text/plain")},
    )
    assert resume.status_code == 202
    job = client.post(
        "/api/v1/jobs",
        json={"title": "AI Engineer", "jd_text": "Python RAG", "profile": {}},
    )
    assert job.status_code == 201
    assert job.json()["versions"][0]["search_index_status"] == "ready"
    with knowledge_app.state.session_factory() as session:
        resume_version = session.get(Resume, resume.json()["id"]).sha256
    resume_hits = _search(knowledge_app, tenant_id, "resume", resume.json()["id"], resume_version, "Python")
    version = job.json()["versions"][0]
    job_hits = _search(knowledge_app, tenant_id, "job_version", version["id"], str(version["version"]), "RAG")
    assert resume_hits[0].source_id == resume.json()["id"]
    assert job_hits[0].source_id == job.json()["versions"][0]["id"]


def test_oversized_knowledge_upload_is_rejected(client):
    _login(client, "Acme", "admin@acme.test")
    response = _upload(client, b"x" * (10 * 1024 * 1024 + 2))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_file"


def test_lead_role_cannot_mutate_enterprise_knowledge(client):
    tenant_id = _login(client, "Acme", "admin@acme.test")
    token = issue_access_token(
        Principal(user_id="lead-user", tenant_id=tenant_id, role=Role.LEAD),
        client.app.state.token_settings,
    )
    client.headers["Authorization"] = f"Bearer {token}"

    assert _upload(client).status_code == 403
    assert client.post("/api/v1/knowledge-documents/rebuild-sources").status_code == 403


def test_source_backfill_repairs_existing_resume_and_job_indexes(client, knowledge_app):
    tenant_id = _login(client, "Acme", "admin@acme.test")
    resume = client.post(
        "/api/v1/resumes",
        files={"file": ("resume.txt", b"Python FastAPI", "text/plain")},
    ).json()
    job = client.post(
        "/api/v1/jobs",
        json={"title": "AI Engineer", "jd_text": "Python RAG", "profile": {}},
    ).json()
    with knowledge_app.state.session_factory() as session:
        stored_resume = session.get(Resume, resume["id"])
        stored_job = session.get(JobVersion, job["versions"][0]["id"])
        stored_resume.search_index_status = "pending"
        stored_job.search_index_status = "pending"
        session.commit()

    response = client.post("/api/v1/knowledge-documents/rebuild-sources")
    assert response.status_code == 200
    assert response.json() == {"resumes_indexed": 1, "job_versions_indexed": 1, "failed": 0}
    with knowledge_app.state.session_factory() as session:
        resume_version = session.get(Resume, resume["id"]).sha256
    assert _search(knowledge_app, tenant_id, "resume", resume["id"], resume_version, "Python")
    version = job["versions"][0]
    assert _search(knowledge_app, tenant_id, "job_version", version["id"], str(version["version"]), "RAG")
    with knowledge_app.state.session_factory() as session:
        assert session.get(Resume, resume["id"]).search_index_status == "ready"
        assert session.get(JobVersion, job["versions"][0]["id"]).search_index_status == "ready"
