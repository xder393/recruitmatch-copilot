"""Processor failure classes retain PostgreSQL state and bounded recovery."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError, ProgrammingError

from tests.integration.processing import test_processors
from tests.integration.processing.test_processors import processor

processing_source = test_processors.processing_source


def test_transient_storage_failure_durably_retries_and_then_exhausts(processing_source, monkeypatch):
    factory, store, source, model = processing_source

    def unavailable(*args):
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(store, "read_bounded", unavailable)
    service = processor(processing_source)
    for attempt in range(1, 6):
        assert service.process(source.tenant_id, source.source_id).value == (
            "retry_short" if attempt <= 2 else "completed"
        )
        with factory() as session:
            row = session.get(model, source.source_id)
            assert row.processing_attempts == attempt and row.processing_lease_epoch == attempt
            assert row.error_code == "storage_unavailable" and row.error_message is None
            assert row.processing_lease_owner is None
            if attempt == 5:
                assert row.next_retry_at is None
                break
            assert row.next_retry_at > datetime.now(timezone.utc)
            row.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()
            if attempt > 2:
                from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork

                assert SqlAlchemyUnitOfWork(session).leases.requeue_due(
                    source.tenant_id,
                    source.source_type,
                    source.source_id,
                    expected_epoch=attempt,
                    expected_artifact_id=row.artifact_id,
                    max_attempts=5,
                )
                session.commit()
    assert service.process(source.tenant_id, source.source_id).value == "terminal"


@pytest.mark.parametrize("failure_type", [OperationalError, ProgrammingError, "disconnect"])
def test_database_stage_failure_never_publishes_soft_fallback(processing_source, postgres_engine, failure_type):
    factory, _, source, model = processing_source

    def fail_query(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT recruiting_chunks"):
            if failure_type == "disconnect":
                connection.connection.driver_connection.close()
                return
            raise failure_type("synthetic query", {}, RuntimeError("private DB detail"))

    event.listen(postgres_engine, "before_cursor_execute", fail_query)
    try:
        with pytest.raises(Exception) as failure:
            processor(processing_source).process(source.tenant_id, source.source_id)
        if failure_type is OperationalError or failure_type == "disconnect":
            assert type(failure.value).__name__ == "PersistenceUnavailable"
            assert isinstance(failure.value.__cause__, OperationalError)
        else:
            assert isinstance(failure.value, ProgrammingError)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail_query)
    with factory() as session:
        row = session.get(model, source.source_id)
        assert row.status.value == "running" if source.source_type == "resume" else row.status == "processing"
        assert row.processing_lease_owner is not None and row.next_retry_at is None
        assert row.error_code is None and row.search_index_error_code is None
        assert row.active_index_generation == 0
        if source.source_type == "resume":
            assert row.profile == {} and row.extracted_text is None


def test_soft_time_limit_retries_with_current_lease(processing_source, monkeypatch):
    from billiard.exceptions import SoftTimeLimitExceeded

    factory, _, source, model = processing_source
    service = processor(processing_source)
    assert hasattr(service, "timeout_errors")
    service.timeout_errors = (SoftTimeLimitExceeded,)

    def timeout(*args):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(service.source_indexer.embedder, "embed_documents", timeout)
    assert service.process(source.tenant_id, source.source_id).value == "retry_short"
    with factory() as session:
        row = session.get(model, source.source_id)
        assert row.error_code == "processing_timeout" and row.next_retry_at is not None
        assert row.processing_lease_owner is None and row.active_index_generation == 0


def test_database_loss_while_writing_retry_leaves_committed_lease(processing_source, postgres_engine, monkeypatch):
    from app.repositories.ports import PersistenceUnavailable

    factory, store, source, model = processing_source

    def storage_unavailable(*args):
        raise OSError("synthetic storage outage")

    def disconnect_retry_write(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE ") and parameters.get("error_code") == "storage_unavailable":
            connection.connection.driver_connection.close()

    monkeypatch.setattr(store, "read_bounded", storage_unavailable)
    event.listen(postgres_engine, "before_cursor_execute", disconnect_retry_write)
    try:
        with pytest.raises(PersistenceUnavailable) as failure:
            processor(processing_source).process(source.tenant_id, source.source_id)
        assert isinstance(failure.value.__cause__, OperationalError)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", disconnect_retry_write)
    with factory() as session:
        row = session.get(model, source.source_id)
        assert row.error_code is None and row.next_retry_at is None
        assert row.processing_attempts == 1 and row.processing_lease_epoch == 1
        assert row.processing_lease_owner is not None
        assert row.status.value == "running" if source.source_type == "resume" else row.status == "processing"


def test_processor_claim_cap_is_committed_on_terminal_return(processing_source):
    from app.domain.enums import ResumeStatus

    factory, _, source, model = processing_source
    with factory() as session:
        row = session.get(model, source.source_id)
        row.processing_attempts = 5
        row.processing_lease_epoch = 5
        row.status = ResumeStatus.RUNNING if source.source_type == "resume" else "processing"
        row.processing_lease_owner = "dead-worker"
        row.processing_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    assert processor(processing_source).process(source.tenant_id, source.source_id).value == "terminal"
    with factory() as session:
        row = session.get(model, source.source_id)
        assert row.error_code == "processing_attempts_exhausted"
        assert row.processing_attempts == 5 and row.processing_lease_epoch == 5
        assert row.status == (ResumeStatus.FAILED if source.source_type == "resume" else "failed")
        assert row.processing_lease_owner is None


def test_short_publish_failure_preserves_database_due_queue(processing_source, monkeypatch):
    from types import SimpleNamespace
    from app.tasks import celery_app as tasks

    factory, store, source, model = processing_source

    def fail(*args, **kwargs):
        raise OSError("synthetic infrastructure outage")

    monkeypatch.setattr(store, "read_bounded", fail)
    service = processor(processing_source)
    monkeypatch.setattr(
        tasks,
        "build_worker_dependencies",
        lambda _: SimpleNamespace(resume_processor=service, knowledge_processor=service),
    )
    task = tasks.process_resume_task if source.source_type == "resume" else tasks.process_knowledge_task
    monkeypatch.setattr(task, "apply_async", fail)
    task.push_request(
        called_directly=False,
        is_eager=False,
        retries=0,
        args=(source.tenant_id, source.source_id),
        kwargs={},
        id="synthetic-task",
    )
    try:
        assert task.run(source.tenant_id, source.source_id) is None
    finally:
        task.pop_request()
    with factory() as session:
        row = session.get(model, source.source_id)
        assert row.next_retry_at > datetime.now(timezone.utc)
        assert row.error_code == "storage_unavailable" and row.processing_attempts == 1
        assert row.processing_lease_owner is None
    assert service.process(source.tenant_id, source.source_id).value == "deferred"


@pytest.mark.parametrize("processing_source", ["knowledge_document"], indirect=True)
def test_lost_retry_fence_rolls_back_preceding_index_error(processing_source):
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
    from tests.support.application import DeterministicEmbeddingAdapter

    factory, _, source, model = processing_source

    class UnavailableEmbedder(DeterministicEmbeddingAdapter):
        def embed_documents(self, texts):
            raise RuntimeError("synthetic embedding outage")

    service = processor(processing_source, embedder=UnavailableEmbedder())

    def uows():
        session = factory()
        uow = SqlAlchemyUnitOfWork(session, owns_session=True)
        real_retry = uow.leases.schedule_retry

        def expire_before_retry(*args, **kwargs):
            session.get(model, source.source_id).processing_lease_expires_at = datetime.now(timezone.utc) - timedelta(
                seconds=1
            )
            session.flush()
            return real_retry(*args, **kwargs)

        uow.leases.schedule_retry = expire_before_retry
        return uow

    service.uow_factory = uows
    assert service.process(source.tenant_id, source.source_id).value == "lease_lost"
    with factory() as session:
        row = session.get(model, source.source_id)
        assert row.search_index_error_code is None and row.error_code is None
        assert row.next_retry_at is None
