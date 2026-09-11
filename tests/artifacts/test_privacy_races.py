"""Real PostgreSQL worker/generation/FK barriers around privacy deletion."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.identity import User
from app.models.resumes import Resume
from app.models.knowledge import KnowledgeDocument
from app.models.retrieval import RecruitingChunk
from app.models.matching import Feedback
from app.core.exceptions import AppError
from app.domain.enums import FeedbackAction
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork, SqlAlchemyUnitOfWorkFactory
from app.retrieval.generations import GenerationWriter, SourceRef, StagedChunk, SourceNotFoundError
from app.resumes.schemas import ResumeProfile
from app.services.resume_processing import ResumeProcessingService
from app.services.resumes import ResumeService
from app.services.knowledge_documents import KnowledgeDocumentService
from app.services.feedback import FeedbackService
from tests.artifacts.test_privacy_delete import seed_result
from tests.artifacts.test_upload_saga import Dispatcher, PendingStore, upload, knowledge_upload


def test_privacy_guard_emits_no_key_update_on_postgresql(postgres_engine, principal):
    """Catches lock-strength regression at the actual repository SQL boundary."""
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.upper().split()))

    with postgres_engine.connect() as connection:
        event.listen(connection, "before_cursor_execute", capture)
        try:
            with Session(connection) as session:
                SqlAlchemyUnitOfWork(session).identities.lock_privacy_guard(principal.tenant_id)
        finally:
            event.remove(connection, "before_cursor_execute", capture)
    assert len(statements) == 1
    assert "FOR NO KEY UPDATE" in statements[0]


def test_source_holder_can_insert_tenant_fk_while_delete_guard_waits(postgres_engine, principal):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    owner_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    guarded = Event()

    def deleting():
        with Session(postgres_engine) as session:
            session.execute(text("SET LOCAL statement_timeout='3s'"))
            uow = SqlAlchemyUnitOfWork(session)
            original = uow.identities.lock_privacy_guard

            def guard(tenant_id):
                original(tenant_id)
                guarded.set()

            uow.identities.lock_privacy_guard = guard
            ResumeService(uow.resumes, store, dispatcher, uow=uow).delete(principal, owner_id)

    with Session(postgres_engine) as worker:
        worker.execute(text("SET LOCAL statement_timeout='2s'"))
        worker.scalar(select(Resume).where(Resume.id == owner_id).with_for_update())
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(deleting)
            assert guarded.wait(10)
            # Any tenant FK insert needs implicit KEY SHARE. A FOR UPDATE guard
            # here deadlocks with the Source lock already held by this worker.
            user_id = str(uuid4())
            worker.add(
                User(
                    id=user_id,
                    tenant_id=principal.tenant_id,
                    email=user_id + "@test.invalid",
                    password_hash="synthetic",
                )
            )
            try:
                worker.flush()
                assert not future.done()
            finally:
                worker.commit()
            future.result(timeout=10)
    with Session(postgres_engine) as session:
        assert session.get(Resume, owner_id).lifecycle_status == "deleted"


def test_processing_in_flight_cannot_restore_profile_text_after_delete(postgres_engine, principal):
    entered, release = Event(), Event()
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    owner_id, *_ = upload(postgres_engine, principal, store, dispatcher)

    class PausedParser:
        def parse(self, value):
            entered.set()
            assert release.wait(10)
            return ResumeProfile()

    processing = ResumeProcessingService(
        SqlAlchemyUnitOfWorkFactory(sessionmaker(postgres_engine)), store, PausedParser()
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(processing.process, principal.tenant_id, owner_id)
        assert entered.wait(10)
        try:
            with Session(postgres_engine) as session:
                uow = SqlAlchemyUnitOfWork(session)
                ResumeService(uow.resumes, store, dispatcher, uow=uow).delete(principal, owner_id)
        finally:
            release.set()
        assert future.result(timeout=10)
    with Session(postgres_engine) as session:
        owner = session.get(Resume, owner_id)
        assert owner.lifecycle_status == "deleted" and owner.profile == {} and owner.extracted_text is None


@pytest.mark.parametrize("kind", ["resume", "knowledge_document"])
@pytest.mark.parametrize("delete_first", [False, True])
def test_generation_activation_and_privacy_delete_cannot_resurrect_chunks(
    postgres_engine, principal, kind, delete_first
):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    owner_id, *_ = (upload if kind == "resume" else knowledge_upload)(postgres_engine, principal, store, dispatcher)
    with Session(postgres_engine) as session:
        owner = session.get(Resume if kind == "resume" else KnowledgeDocument, owner_id)
        version = owner.sha256 if kind == "resume" else owner.checksum
    reference = SourceRef(principal.tenant_id, kind, owner_id, version)
    entered, release = Event(), Event()

    def checkpoint():
        entered.set()
        assert release.wait(10)

    writer = GenerationWriter(sessionmaker(postgres_engine), activation_checkpoint=checkpoint)
    writer.stage(reference, 1, [StagedChunk(str(uuid4()), "PRIVATE_SENTINEL", 0, 16, [1.0] + [0.0] * 511, "fake")])

    def deleting():
        with Session(postgres_engine) as session:
            uow = SqlAlchemyUnitOfWork(session)
            if kind == "resume":
                ResumeService(uow.resumes, store, dispatcher, uow=uow).delete(principal, owner_id)
            else:
                KnowledgeDocumentService(uow.knowledge, store, dispatcher, uow=uow).delete(
                    principal, owner_id, confirm_tenant_history_redaction=True
                )

    if delete_first:
        deleting()
        with pytest.raises(SourceNotFoundError):
            writer.activate(reference, 1, expected_count=1, embedding_model="fake")
        with pytest.raises(SourceNotFoundError):
            writer.stage(reference, 1, [StagedChunk(str(uuid4()), "NEW_PRIVATE", 0, 11, [1.0] + [0.0] * 511, "fake")])
    else:
        with ThreadPoolExecutor(max_workers=2) as executor:
            activation = executor.submit(writer.activate, reference, 1, expected_count=1, embedding_model="fake")
            assert entered.wait(10)
            deletion = executor.submit(deleting)
            try:
                with pytest.raises(FutureTimeout):
                    deletion.result(timeout=0.3)
            finally:
                release.set()
            activation.result(timeout=10)
            deletion.result(timeout=10)
    with Session(postgres_engine) as session:
        assert list(session.scalars(select(RecruitingChunk).where(RecruitingChunk.source_id == owner_id))) == []
        owner = session.get(Resume if kind == "resume" else KnowledgeDocument, owner_id)
        assert owner.lifecycle_status == "deleted" and owner.search_index_status == "deleted"


@pytest.mark.parametrize("delete_first", [False, True])
def test_feedback_and_privacy_delete_serialize_without_retaining_new_reason(postgres_engine, principal, delete_first):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    resume_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    document_id, *_ = knowledge_upload(postgres_engine, principal, store, dispatcher)
    result_id, _ = seed_result(postgres_engine, principal, resume_id)
    entered, release = Event(), Event()

    def feedback():
        with Session(postgres_engine) as session:
            uow = SqlAlchemyUnitOfWork(session)
            original = uow.feedback.add
            if not delete_first:

                def paused(item):
                    original(item)
                    entered.set()
                    assert release.wait(10)

                uow.feedback.add = paused
            try:
                FeedbackService(uow.feedback, uow).submit(principal, result_id, FeedbackAction.CONFIRM, "LATE_PRIVATE")
            except AppError as error:
                return error.status_code
            return 201

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
        first = executor.submit(deleting if delete_first else feedback)
        assert entered.wait(10)
        second = executor.submit(feedback if delete_first else deleting)
        try:
            with pytest.raises(FutureTimeout):
                second.result(timeout=0.3)
        finally:
            release.set()
        first_result, second_result = first.result(timeout=10), second.result(timeout=10)
    assert (second_result if delete_first else first_result) == (404 if delete_first else 201)
    with Session(postgres_engine) as session:
        assert all(
            row.reason is None for row in session.scalars(select(Feedback).where(Feedback.match_result_id == result_id))
        )
