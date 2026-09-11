"""Privacy redaction must commit even when object cleanup is unavailable."""

import pytest
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.ports import ArtifactAccessDenied, ArtifactStorageFailure
from app.core.exceptions import AppError
from app.domain.artifacts import ArtifactStatus
from app.models.artifacts import Artifact
from app.models.resumes import Resume
from app.models.knowledge import KnowledgeDocument
from app.models.jobs import Job, JobVersion
from app.models.matching import MatchRun, MatchResult, Feedback
from app.models.retrieval import RecruitingChunk
from app.domain.enums import FeedbackAction, MatchStatus, ResumeStatus, JobStatus
from app.services.feedback import FeedbackService
from app.services.matching import MatchingService
from app.matching.engine import MatchingEngine
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from app.services.resumes import ResumeService
from app.services.knowledge_documents import KnowledgeDocumentService
from tests.artifacts.test_upload_saga import Dispatcher, PendingStore, upload, knowledge_upload


@pytest.mark.parametrize("error", [ArtifactStorageFailure, ArtifactAccessDenied, TimeoutError])
def test_resume_database_pii_is_null_before_failed_object_cleanup(postgres_engine, principal, error):
    class FailingCleanup(PendingStore):
        def delete(self, location):
            with Session(postgres_engine) as check:
                owner = check.get(Resume, location.owner_id)
                assert owner.original_filename is None
                assert owner.sha256 is None
                assert owner.profile == {} and owner.extracted_text is None
                assert owner.lifecycle_status == "deleted"
                assert check.get(Artifact, location.artifact_id).sha256 is None
            raise error()

    store, dispatcher = FailingCleanup(postgres_engine), Dispatcher(postgres_engine)
    owner_id, artifact_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    with Session(postgres_engine) as session:
        owner = session.get(Resume, owner_id)
        owner.profile = {"name": "PRIVATE_SENTINEL"}
        owner.extracted_text = "PRIVATE_SENTINEL"
        session.commit()
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        service = ResumeService(uow.resumes, store, dispatcher, uow=uow)
        with pytest.raises(AppError) as pending:
            service.delete(principal, owner_id)
        assert pending.value.code == "artifact_cleanup_pending"
        assert pending.value.status_code == 503
    with Session(postgres_engine) as session:
        owner = session.get(Resume, owner_id)
        assert owner.original_filename is None
        assert owner.sha256 is None
        assert owner.lifecycle_status == "deleted"
        assert owner.status.value != "deleted"
        assert session.get(Artifact, artifact_id).status == ArtifactStatus.CLEANUP_FAILED


def test_knowledge_privacy_delete_is_separate_from_deactivation_and_retryable(postgres_engine, principal):
    class Unavailable(PendingStore):
        broken = True

        def delete(self, location):
            if self.broken:
                raise ArtifactAccessDenied()
            super().delete(location)

    store, dispatcher = Unavailable(postgres_engine), Dispatcher(postgres_engine)
    owner_id, artifact_id, *_ = knowledge_upload(postgres_engine, principal, store, dispatcher)
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        service = KnowledgeDocumentService(uow.knowledge, store, dispatcher, uow=uow)
        service.deactivate(principal, owner_id)
        assert service.get(principal, owner_id).original_filename == "policy.txt"
        assert callable(getattr(service, "delete", None)), "Knowledge needs authorized privacy deletion"
        with pytest.raises(AppError) as confirmation:
            service.delete(principal, owner_id)
        assert confirmation.value.code == "knowledge_privacy_confirmation_required"
        assert service.get(principal, owner_id).checksum is not None
        with pytest.raises(AppError) as failure:
            service.delete(principal, owner_id, confirm_tenant_history_redaction=True)
        assert failure.value.code == "artifact_cleanup_pending"
        assert service.list(principal) == []
        with pytest.raises(AppError) as hidden:
            service.get(principal, owner_id)
        assert hidden.value.status_code == 404
    with Session(postgres_engine) as session:
        owner = session.get(KnowledgeDocument, owner_id)
        assert owner.original_filename is None and owner.checksum is None
        assert owner.lifecycle_status == "deleted"
        assert session.get(Artifact, artifact_id).status == ArtifactStatus.CLEANUP_FAILED
    store.broken = False
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        service = KnowledgeDocumentService(uow.knowledge, store, dispatcher, uow=uow)
        service.delete(principal, owner_id, confirm_tenant_history_redaction=True)
        service.delete(principal, owner_id, confirm_tenant_history_redaction=True)
        assert session.get(Artifact, artifact_id).status == ArtifactStatus.DELETED
    replacement = knowledge_upload(postgres_engine, principal, store, dispatcher)
    assert replacement[0] != owner_id and replacement[1] != artifact_id


def seed_result(engine, principal, owner_id, sentinel="PRIVATE_SENTINEL"):
    with Session(engine) as session:
        job = Job(tenant_id=principal.tenant_id, title="Synthetic job", status=JobStatus.ACTIVE)
        version = JobVersion(version=1, jd_text="Python", profile={})
        job.versions.append(version)
        session.add(job)
        session.flush()
        run = MatchRun(tenant_id=principal.tenant_id, resume_id=owner_id, status=MatchStatus.SUCCEEDED)
        result = MatchResult(
            job_version_id=version.id,
            rank=1,
            total_score=70,
            summary=sentinel,
            evidence=[sentinel],
            citations=[],
            dimension_scores={"note": sentinel},
            matched_items=[sentinel],
            missing_items=[sentinel],
            uncertain_items=[sentinel],
            risk_flags=[sentinel],
            grounded_explanation={"claim": sentinel},
            interview_questions=[sentinel],
        )
        run.results.append(result)
        session.add(run)
        session.flush()
        feedback = Feedback(
            tenant_id=principal.tenant_id,
            match_result_id=result.id,
            user_id=principal.user_id,
            action=FeedbackAction.CONFIRM,
            reason=sentinel,
        )
        session.add(feedback)
        session.commit()
        return result.id, feedback.id


@pytest.mark.parametrize("kind", ["resume", "knowledge_document"])
def test_all_generations_result_content_and_feedback_are_scrubbed_and_cannot_be_readded(
    postgres_engine, principal, kind
):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    resume_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    owner_id = resume_id
    if kind == "knowledge_document":
        owner_id, *_ = knowledge_upload(postgres_engine, principal, store, dispatcher)
    result_id, feedback_id = seed_result(postgres_engine, principal, resume_id)
    citation_ids = set()
    with Session(postgres_engine) as session:
        source = session.get(Resume if kind == "resume" else KnowledgeDocument, owner_id)
        version = source.sha256 if kind == "resume" else source.checksum
        for generation in [1, 2]:
            citation = str(uuid4())
            citation_ids.add(citation)
            session.add(
                RecruitingChunk(
                    tenant_id=principal.tenant_id,
                    source_type=kind,
                    source_id=owner_id,
                    source_version=version,
                    generation=generation,
                    citation_id=citation,
                    content="PRIVATE_SENTINEL",
                    start_offset=0,
                    end_offset=16,
                    embedding=[1.0] + [0.0] * 511,
                    embedding_model="fake",
                    is_active=generation == 2,
                )
            )
        session.commit()
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        if kind == "resume":
            ResumeService(uow.resumes, store, dispatcher, uow=uow).delete(principal, owner_id)
        else:
            KnowledgeDocumentService(uow.knowledge, store, dispatcher, uow=uow).delete(
                principal, owner_id, confirm_tenant_history_redaction=True
            )
    with Session(postgres_engine) as session:
        result = session.get(MatchResult, result_id)
        assert "PRIVATE_SENTINEL" not in repr({c.name: getattr(result, c.name) for c in result.__table__.columns})
        assert session.get(Feedback, feedback_id).reason is None
        assert result.grounding_status == "privacy_redacted"
        assert list(session.scalars(select(RecruitingChunk).where(RecruitingChunk.source_id == owner_id))) == []
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(AppError) as rejected:
            FeedbackService(uow.feedback, uow).submit(principal, result_id, FeedbackAction.CONFIRM, "FRESH_PRIVATE")
        assert rejected.value.status_code == 404
    from app.retrieval.pgvector_index import PgVectorRecruitingIndex
    from sqlalchemy.orm import sessionmaker

    index = PgVectorRecruitingIndex(sessionmaker(postgres_engine))
    assert index.resolve_historical_citations(principal.tenant_id, frozenset(citation_ids)) == []


def test_knowledge_cleanup_retry_preserves_fresh_result_and_feedback(postgres_engine, principal):
    class FailedStore(PendingStore):
        broken = True

        def delete(self, location):
            if self.broken:
                raise ArtifactStorageFailure()
            super().delete(location)

    store, dispatcher = FailedStore(postgres_engine), Dispatcher(postgres_engine)
    resume_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    document_id, *_ = knowledge_upload(postgres_engine, principal, store, dispatcher)
    old_result, old_feedback = seed_result(postgres_engine, principal, resume_id)
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(AppError):
            KnowledgeDocumentService(uow.knowledge, store, dispatcher, uow=uow).delete(
                principal, document_id, confirm_tenant_history_redaction=True
            )
    new_result, new_feedback = seed_result(postgres_engine, principal, resume_id, "FRESH_AFTER_DELETION")
    store.broken = False
    for _ in range(2):
        with Session(postgres_engine) as session:
            uow = SqlAlchemyUnitOfWork(session)
            KnowledgeDocumentService(uow.knowledge, store, dispatcher, uow=uow).delete(
                principal, document_id, confirm_tenant_history_redaction=True
            )
    with Session(postgres_engine) as session:
        assert session.get(MatchResult, old_result).summary is None
        assert session.get(Feedback, old_feedback).reason is None
        assert session.get(MatchResult, new_result).summary == "FRESH_AFTER_DELETION"
        assert session.get(Feedback, new_feedback).reason == "FRESH_AFTER_DELETION"


@pytest.mark.parametrize("delete_first", [False, True])
def test_knowledge_delete_and_matching_use_one_commit_order_guard(postgres_engine, principal, delete_first):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    resume_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    document_id, *_ = knowledge_upload(postgres_engine, principal, store, dispatcher)
    seed_result(postgres_engine, principal, resume_id)
    with Session(postgres_engine) as session:
        session.get(Resume, resume_id).status = ResumeStatus.SUCCEEDED
        document = session.get(KnowledgeDocument, document_id)
        document.status = "ready"
        document.search_index_status = "ready"
        session.commit()
    entered, release, matching_entered = Event(), Event(), Event()

    class BlockingEngine(MatchingEngine):
        def rank(self, profile, candidates, context, **kwargs):
            matching_entered.set()
            knowledge_ids = {
                item[1] for item in context.search_scope.authorized_sources if item[0] == "knowledge_document"
            }
            assert (document_id in knowledge_ids) is not delete_first
            if not delete_first:
                entered.set()
                assert release.wait(10)
            return super().rank(profile, candidates, **kwargs)

    def matching():
        with Session(postgres_engine) as session:
            uow = SqlAlchemyUnitOfWork(session)
            return (
                MatchingService(uow.matching, hybrid_engine=BlockingEngine(), uow=uow)
                .run(principal, resume_id, mode="hybrid-v1")
                .id
            )

    def deleting():
        with Session(postgres_engine) as session:
            uow = SqlAlchemyUnitOfWork(session)
            original = uow.knowledge.scrub_private_data
            if delete_first:

                def paused(*args):
                    original(*args)
                    entered.set()
                    assert release.wait(10)

                uow.knowledge.scrub_private_data = paused
            KnowledgeDocumentService(uow.knowledge, store, dispatcher, uow=uow).delete(
                principal, document_id, confirm_tenant_history_redaction=True
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(deleting if delete_first else matching)
        assert entered.wait(10)
        second = executor.submit(matching if delete_first else deleting)
        try:
            if delete_first:
                assert not matching_entered.wait(0.3), "matching must wait for committed Knowledge deletion"
            else:
                from concurrent.futures import TimeoutError as FutureTimeout

                with pytest.raises(FutureTimeout):
                    second.result(timeout=0.3)
        finally:
            release.set()
        first_result, second_result = first.result(timeout=10), second.result(timeout=10)
    run_id = second_result if delete_first else first_result
    with Session(postgres_engine) as session:
        results = list(session.scalars(select(MatchResult).where(MatchResult.run_id == run_id)))
        assert results
        if delete_first:
            assert all(item.grounding_status != "privacy_redacted" for item in results)
        else:
            assert all(item.grounding_status == "privacy_redacted" for item in results)


def test_knowledge_delete_api_requires_acknowledgement_and_enforces_scope(postgres_engine, principal):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Settings
    from app.api.v1.deps import get_current_principal
    from app.security.tokens import Principal
    from app.domain.enums import Role

    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    owner_id, artifact_id, *_ = knowledge_upload(postgres_engine, principal, store, dispatcher)
    resume_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    result_id, _ = seed_result(postgres_engine, principal, resume_id)
    with Session(postgres_engine) as session:
        run_id = session.get(MatchResult, result_id).run_id
    settings = Settings(
        database_url=postgres_engine.url.render_as_string(hide_password=False),
        ai_enabled=False,
        task_mode="inline",
        jwt_secret="synthetic-secret-at-least-32-bytes",
    )
    app = create_app(settings, artifact_store=store)
    current = principal
    app.dependency_overrides[get_current_principal] = lambda: current
    path = f"/api/v1/knowledge-documents/{owner_id}"
    with TestClient(app) as client:
        response = client.delete(path)
        assert response.status_code == 409, response.text
        assert "knowledge_privacy_confirmation_required" in response.text
        assert client.get(path).status_code == 200
        current = Principal(principal.user_id, principal.tenant_id, Role.LEAD)
        assert client.delete(path + "?confirm_tenant_history_redaction=true").status_code == 403
        current = Principal(principal.user_id, str(uuid4()), Role.ADMIN)
        assert client.delete(path + "?confirm_tenant_history_redaction=true").status_code == 404
        current = principal
        assert client.delete(path + "?confirm_tenant_history_redaction=true").status_code == 204
        assert client.get(path).status_code == 404
        assert client.get("/api/v1/knowledge-documents").json()["items"] == []
        redacted = client.get(f"/api/v1/match-runs/{run_id}")
        assert redacted.status_code == 200
        assert "PRIVATE_SENTINEL" not in redacted.text
        assert redacted.json()["results"][0]["grounding_status"] == "privacy_redacted"
        assert (
            client.post(
                f"/api/v1/match-results/{result_id}/feedback",
                json={"action": "confirm", "reason": "NEW_PRIVATE_SENTINEL"},
            ).status_code
            == 404
        )
    with Session(postgres_engine) as session:
        assert session.get(Artifact, artifact_id).status == ArtifactStatus.DELETED


def test_real_minio_permission_failure_does_not_retain_resume_pii(postgres_engine, principal):
    import os
    from app.artifacts.s3 import S3ArtifactStore, S3Settings
    from app.artifacts.ports import ArtifactMissing

    store, dispatcher = S3ArtifactStore(S3Settings.from_env().client()), Dispatcher(postgres_engine)
    owner_id, artifact_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    denied = S3ArtifactStore(
        S3Settings(
            os.environ["S3_ENDPOINT_URL"], os.environ["S3_ACCESS_KEY_ID"], "intentionally-invalid-synthetic-secret"
        ).client()
    )
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        location = uow.artifacts.resolve_location(principal.tenant_id, "resume", owner_id, artifact_id)
    try:
        with Session(postgres_engine) as session:
            uow = SqlAlchemyUnitOfWork(session)
            with pytest.raises(AppError) as failure:
                ResumeService(uow.resumes, denied, dispatcher, uow=uow).delete(principal, owner_id)
            assert failure.value.status_code == 503 and failure.value.code == "artifact_cleanup_pending"
        assert store.inspect(location).size_bytes == 16
        with Session(postgres_engine) as session:
            owner, artifact = session.get(Resume, owner_id), session.get(Artifact, artifact_id)
            assert owner.sha256 is None and owner.original_filename is None and owner.lifecycle_status == "deleted"
            assert artifact.status == ArtifactStatus.CLEANUP_FAILED and artifact.error_code.value == "access_denied"
            uow = SqlAlchemyUnitOfWork(session)
            ResumeService(uow.resumes, store, dispatcher, uow=uow).delete(principal, owner_id)
        with pytest.raises(ArtifactMissing):
            store.inspect(location)
    finally:
        store.delete(location)
