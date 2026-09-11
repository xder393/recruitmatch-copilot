"""Upload transaction ordering and concurrency against real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.artifacts.ports import ArtifactChecksumMismatch, ArtifactLocation, ArtifactMissing, ArtifactStorageFailure
from app.artifacts.ports import ArtifactAccessDenied, ArtifactLengthMismatch, ArtifactTooLarge, ArtifactInvalidLocation
from app.core.exceptions import AppError
from app.domain.artifacts import ArtifactStatus
from app.domain.enums import ResumeStatus, Role
from app.models.artifacts import Artifact
from app.models.identity import Tenant, User
from app.models.resumes import Resume
from app.models.knowledge import KnowledgeDocument
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.security.tokens import Principal
from app.services.resumes import ResumeService
from app.services.knowledge_documents import KnowledgeDocumentService
from app.services.knowledge_processing import KnowledgeProcessingService
from app.services.resume_processing import ResumeProcessingService
from app.resumes.parser import HeuristicResumeParser
from sqlalchemy.orm import sessionmaker
from tests.fakes.artifacts import FakeArtifactStore


@pytest.fixture
def principal(postgres_engine):
    tenant_id, user_id = str(uuid4()), str(uuid4())
    with Session(postgres_engine) as session:
        session.add(Tenant(id=tenant_id, name="synthetic-upload"))
        session.flush()
        session.add(User(id=user_id, tenant_id=tenant_id, email=user_id + "@test.invalid", password_hash="synthetic"))
        session.commit()
    yield Principal(user_id, tenant_id, Role.ADMIN)
    with Session(postgres_engine) as session:
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()


class Dispatcher:
    def __init__(self, engine, *, broken=False):
        self.engine, self.broken, self.calls = engine, broken, []

    def dispatch_resume(self, tenant_id, owner_id):
        with Session(self.engine) as session:
            owner = session.get(Resume, owner_id)
            assert owner.tenant_id == tenant_id
            assert owner.status == ResumeStatus.QUEUED
            artifact = session.get(Artifact, owner.artifact_id)
            assert artifact is not None and artifact.status == ArtifactStatus.AVAILABLE
        self.calls.append((tenant_id, owner_id))
        if self.broken:
            raise RuntimeError("synthetic broker failure")

    def dispatch_knowledge(self, tenant_id, owner_id):
        with Session(self.engine) as session:
            owner = session.get(KnowledgeDocument, owner_id)
            assert owner.tenant_id == tenant_id and owner.status == "uploaded"
            assert session.get(Artifact, owner.artifact_id).status == ArtifactStatus.AVAILABLE
        self.calls.append((tenant_id, owner_id))
        if self.broken:
            raise RuntimeError("synthetic broker failure")


class PendingStore(FakeArtifactStore):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.puts = []

    def put(self, *args):
        assert isinstance(args[0], ArtifactLocation), "upload must use persistent typed ArtifactLocation"
        location = args[0]
        with Session(self.engine) as session:
            artifact = session.get(Artifact, location.artifact_id)
            owner = session.get(Resume if location.namespace == "resumes" else KnowledgeDocument, location.owner_id)
            assert artifact is not None and artifact.status == ArtifactStatus.PENDING
            assert owner is not None and owner.artifact_id == artifact.id
        self.puts.append(location)
        super().put(*args)


def upload(engine, principal, store, dispatcher):
    with Session(engine, expire_on_commit=False) as session:
        uow = SqlAlchemyUnitOfWork(session)
        owner, created = ResumeService(uow.resumes, store, dispatcher, uow=uow).upload(
            principal, "resume.txt", "text/plain", b"Python developer"
        )
        return owner.id, owner.artifact_id, created, owner.status, owner.error_code


def test_pending_is_durable_before_put_and_payload_contains_only_tenant_owner(postgres_engine, principal):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    result = upload(postgres_engine, principal, store, dispatcher)
    assert result[2:] == (True, ResumeStatus.QUEUED, None)
    assert dispatcher.calls == [(principal.tenant_id, result[0])]
    assert len(store.puts) == 1


def test_distinct_concurrent_proposals_create_one_owner_and_one_put(postgres_engine, principal):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    barrier = Barrier(2)

    def run(_):
        barrier.wait(timeout=10)
        return upload(postgres_engine, principal, store, dispatcher)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(run, range(2)))
    assert first[:2] == second[:2]
    assert sorted([first[2], second[2]]) == [False, True]
    assert len(store.puts) == 1
    assert dispatcher.calls == [(principal.tenant_id, first[0])]
    with Session(postgres_engine) as session:
        assert len(list(session.scalars(select(Resume).where(Resume.tenant_id == principal.tenant_id)))) == 1


def test_dispatch_failure_preserves_recoverable_queued_source(postgres_engine, principal):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine, broken=True)
    result = upload(postgres_engine, principal, store, dispatcher)
    assert result[3:] == (ResumeStatus.QUEUED, "task_dispatch_failed")
    assert upload(postgres_engine, principal, store, dispatcher)[2] is False
    assert len(store.puts) == 1


@pytest.mark.parametrize(
    "error_type,expected_code",
    [
        (ArtifactChecksumMismatch, "checksum_mismatch"),
        (ArtifactMissing, "object_not_found"),
        (ArtifactAccessDenied, "access_denied"),
        (ArtifactLengthMismatch, "size_mismatch"),
        (ArtifactTooLarge, "size_exceeded"),
        (ArtifactInvalidLocation, "storage_unavailable"),
        (ArtifactStorageFailure, "storage_unavailable"),
    ],
)
def test_failed_put_is_durable_and_duplicate_does_not_retry(postgres_engine, principal, error_type, expected_code):
    class RejectStore(PendingStore):
        def put(self, *args):
            super().put(*args)
            raise error_type()

    store, dispatcher = RejectStore(postgres_engine), Dispatcher(postgres_engine)
    with pytest.raises(AppError) as failure:
        upload(postgres_engine, principal, store, dispatcher)
    assert failure.value.code == expected_code
    with Session(postgres_engine) as session:
        artifact = session.scalar(select(Artifact).where(Artifact.tenant_id == principal.tenant_id))
        assert artifact.status == ArtifactStatus.FAILED
        assert artifact.error_code.value == expected_code
    again = upload(postgres_engine, principal, store, dispatcher)
    assert again[2:] == (False, ResumeStatus.FAILED, expected_code)
    assert len(store.puts) == 1
    assert dispatcher.calls == []


def knowledge_upload(engine, principal, store, dispatcher, document_type="policy"):
    with Session(engine, expire_on_commit=False) as session:
        uow = SqlAlchemyUnitOfWork(session)
        document, created = KnowledgeDocumentService(uow.knowledge, store, dispatcher, uow=uow).upload(
            principal, document_type, "policy.txt", "text/plain", b"Hiring policy"
        )
        return document.id, document.artifact_id, created, document.status, document.error_code


def test_knowledge_duplicate_classification_is_preserved(postgres_engine, principal):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    first = knowledge_upload(postgres_engine, principal, store, dispatcher)
    second = knowledge_upload(postgres_engine, principal, store, dispatcher)
    assert first[:2] == second[:2] and second[2] is False
    with pytest.raises(AppError) as failure:
        knowledge_upload(postgres_engine, principal, store, dispatcher, "competency")
    assert failure.value.status_code == 409
    assert failure.value.code == "knowledge_document_type_conflict"
    assert len(store.puts) == 1
    assert dispatcher.calls == [(principal.tenant_id, first[0])]


@pytest.mark.parametrize("crash,late_delete_failure", [(False, False), (True, False), (False, True)])
def test_late_put_cannot_resurrect_deleted_source_and_retains_recovery_anchor(
    postgres_engine,
    principal,
    crash,
    late_delete_failure,
):
    entered, release = Event(), Event()

    class SlowStore(PendingStore):
        def put(self, *args):
            self.location = args[0]
            entered.set()
            assert release.wait(10)
            FakeArtifactStore.put(self, *args)
            if crash:
                raise SystemExit("synthetic process termination")

        def delete(self, location):
            if release.is_set() and late_delete_failure:
                raise ArtifactStorageFailure()
            super().delete(location)

    store, dispatcher = SlowStore(postgres_engine), Dispatcher(postgres_engine)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(upload, postgres_engine, principal, store, dispatcher)
        assert entered.wait(10)
        try:
            location = store.location
            with Session(postgres_engine) as session:
                uow = SqlAlchemyUnitOfWork(session)
                ResumeService(uow.resumes, store, dispatcher, uow=uow).delete(principal, location.owner_id)
            with Session(postgres_engine) as session:
                assert session.get(Artifact, location.artifact_id).status == ArtifactStatus.DELETED
        finally:
            release.set()
        with pytest.raises((AppError, SystemExit)):
            pending.result(timeout=10)
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        tombstone = uow.artifacts.list_deleted_tombstones(principal.tenant_id)[0]
        assert tombstone.location == location
        assert session.get(Resume, location.owner_id).lifecycle_status == "deleted"
        assert session.get(Artifact, location.artifact_id).sha256 is None
        if late_delete_failure:
            assert tombstone.error_code.value == "storage_unavailable"
        # Task 4 will schedule this exact associated-location retry. A fresh
        # process can recover from the persisted tombstone after uploader death.
        if crash or late_delete_failure:
            assert store.inspect(tombstone.location).size_bytes == 16
            FakeArtifactStore.delete(store, tombstone.location)
        with pytest.raises(ArtifactMissing):
            store.inspect(tombstone.location)
        assert dispatcher.calls == []


@pytest.mark.parametrize("artifact_state", ["PENDING", "FAILED", "CLEANUP_PENDING", "DELETED", None])
def test_early_message_never_processes_unavailable_artifact(postgres_engine, principal, artifact_state):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    owner_id, artifact_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    with Session(postgres_engine) as session:
        owner, artifact = session.get(Resume, owner_id), session.get(Artifact, artifact_id)
        if artifact_state is None:
            owner.artifact_id = None
        else:
            artifact.status = ArtifactStatus(artifact_state)
            if artifact_state in {"CLEANUP_PENDING", "DELETED"}:
                artifact.sha256 = None
        session.commit()
    processor = ResumeProcessingService(
        SqlAlchemyUnitOfWorkFactory(sessionmaker(postgres_engine)), store, HeuristicResumeParser()
    )
    processor.process(principal.tenant_id, owner_id)
    with Session(postgres_engine) as session:
        owner = session.get(Resume, owner_id)
        assert owner.status == ResumeStatus.QUEUED
        assert owner.profile == {}


def test_worker_rejects_body_differing_from_trusted_claim(postgres_engine, principal):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    owner_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    # Fake Head and returned body agree, but the database upload digest differs.
    store._objects[store.puts[0]] = b"Golang developer"
    processor = ResumeProcessingService(
        SqlAlchemyUnitOfWorkFactory(sessionmaker(postgres_engine)), store, HeuristicResumeParser()
    )
    processor.process(principal.tenant_id, owner_id)
    with Session(postgres_engine) as session:
        owner = session.get(Resume, owner_id)
        assert owner.status == ResumeStatus.FAILED
        assert owner.error_code == "checksum_mismatch"
        assert owner.profile == {}


@pytest.mark.parametrize("failing_commit", [1, 2])
def test_database_failure_before_and_after_pending_commit_has_recoverable_boundary(
    postgres_engine,
    principal,
    failing_commit,
):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    with pytest.raises(RuntimeError, match="synthetic database interruption"):
        with Session(postgres_engine) as session:
            uow = SqlAlchemyUnitOfWork(session)
            original, count = uow.commit, 0

            def commit():
                nonlocal count
                count += 1
                if count == failing_commit:
                    raise RuntimeError("synthetic database interruption")
                original()

            uow.commit = commit
            ResumeService(uow.resumes, store, dispatcher, uow=uow).upload(
                principal, "resume.txt", "text/plain", b"Python developer"
            )
    with Session(postgres_engine) as session:
        artifacts = list(session.scalars(select(Artifact).where(Artifact.tenant_id == principal.tenant_id)))
        if failing_commit == 1:
            assert artifacts == [] and store.puts == []
        else:
            assert len(artifacts) == 1 and artifacts[0].status == ArtifactStatus.PENDING
            assert store.inspect(store.puts[0]).sha256 == artifacts[0].sha256
        assert dispatcher.calls == []


def test_real_minio_upload_and_worker_read_use_persisted_location(postgres_engine, principal):
    from app.artifacts.s3 import S3ArtifactStore, S3Settings

    store, dispatcher = S3ArtifactStore(S3Settings.from_env().client()), Dispatcher(postgres_engine)
    owner_id, artifact_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    with Session(postgres_engine) as session:
        location = SqlAlchemyUnitOfWork(session).artifacts.resolve_location(
            principal.tenant_id, "resume", owner_id, artifact_id
        )
    try:
        assert store.inspect(location).size_bytes == 16
        processor = ResumeProcessingService(
            SqlAlchemyUnitOfWorkFactory(sessionmaker(postgres_engine)), store, HeuristicResumeParser()
        )
        processor.process(principal.tenant_id, owner_id)
        processor.process(principal.tenant_id, owner_id)
        with Session(postgres_engine) as session:
            owner = session.get(Resume, owner_id)
            assert owner.status == ResumeStatus.SUCCEEDED
            assert owner.extracted_text == "Python developer"
    finally:
        store.delete(location)


def test_late_storage_error_does_not_delete_already_published_live_artifact(postgres_engine, principal):
    class PublishedStore(PendingStore):
        def put(self, *args):
            super().put(*args)
            location = args[0]
            with Session(postgres_engine) as session:
                uow = SqlAlchemyUnitOfWork(session)
                uow.artifacts.mark_available(
                    principal.tenant_id, location.artifact_id, owner_type="resume", owner_id=location.owner_id
                )
                uow.commit()
            raise ArtifactStorageFailure()

    store, dispatcher = PublishedStore(postgres_engine), Dispatcher(postgres_engine)
    with pytest.raises(AppError):
        upload(postgres_engine, principal, store, dispatcher)
    assert store.inspect(store.puts[0]).size_bytes == 16
    with Session(postgres_engine) as session:
        assert session.get(Artifact, store.puts[0].artifact_id).status == ArtifactStatus.AVAILABLE
        assert session.get(Resume, store.puts[0].owner_id).status == ResumeStatus.QUEUED


@pytest.mark.parametrize("available", [False, True])
def test_knowledge_worker_respects_pending_and_trusted_checksum(postgres_engine, principal, available):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    owner_id, artifact_id, *_ = knowledge_upload(postgres_engine, principal, store, dispatcher)
    if available:
        store._objects[store.puts[0]] = b"Other policy!"
    else:
        with Session(postgres_engine) as session:
            session.get(Artifact, artifact_id).status = ArtifactStatus.PENDING
            session.commit()
    processor = KnowledgeProcessingService(
        SqlAlchemyUnitOfWorkFactory(sessionmaker(postgres_engine)), store, source_indexer=None
    )
    processor.process(principal.tenant_id, owner_id)
    with Session(postgres_engine) as session:
        owner = session.get(KnowledgeDocument, owner_id)
        assert owner.status == ("failed" if available else "uploaded")
        assert owner.error_code == ("checksum_mismatch" if available else None)
        assert owner.active_index_generation == 0


def test_concurrent_knowledge_classifications_keep_exactly_one_winner(postgres_engine, principal):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    barrier = Barrier(2)

    def run(kind):
        barrier.wait(timeout=10)
        try:
            return knowledge_upload(postgres_engine, principal, store, dispatcher, kind)
        except AppError as error:
            assert error.code == "knowledge_document_type_conflict" and error.status_code == 409
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, ["policy", "competency"]))
    assert sum(result is not None for result in results) == 1
    assert len(store.puts) == 1
    with Session(postgres_engine) as session:
        assert (
            len(
                list(
                    session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == principal.tenant_id))
                )
            )
            == 1
        )
