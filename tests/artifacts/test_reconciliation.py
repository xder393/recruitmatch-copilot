"""Bounded administrative storage recovery with durable, fair sweeps."""

from datetime import datetime, timedelta, timezone
from dataclasses import asdict
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.artifacts.ports import ArtifactLocation, ArtifactMissing, ArtifactStorageFailure
from app.domain.artifacts import ArtifactStatus
from app.domain.enums import ResumeStatus
from app.core.exceptions import AppError
from app.models.artifacts import Artifact
from app.models.resumes import Resume
from app.models.knowledge import KnowledgeDocument
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork, SqlAlchemyUnitOfWorkFactory
from app.services.resumes import ResumeService
from tests.artifacts.test_upload_saga import Dispatcher, PendingStore, upload, knowledge_upload


def reconciler(engine, store, dispatcher, **kwargs):
    from app.services.artifact_reconciliation import ArtifactReconciliationService

    return ArtifactReconciliationService(
        SqlAlchemyUnitOfWorkFactory(sessionmaker(engine)), store, store, dispatcher, **kwargs
    )


def pending(engine, principal, store, dispatcher, *, old=True):
    owner_id, artifact_id, *_ = upload(engine, principal, store, dispatcher)
    with Session(engine) as session:
        artifact = session.get(Artifact, artifact_id)
        artifact.status = ArtifactStatus.PENDING
        artifact.created_at = datetime.now(timezone.utc) - timedelta(minutes=10) if old else datetime.now(timezone.utc)
        session.commit()
    dispatcher.calls.clear()
    return owner_id, artifact_id, store.puts[-1]


def test_valid_stale_pending_repairs_and_commits_before_dispatch(postgres_engine, principal):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    owner_id, artifact_id, _ = pending(postgres_engine, principal, store, dispatcher)
    report = reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1)
    assert report.repaired == 1
    assert dispatcher.calls == [(principal.tenant_id, owner_id)]
    with Session(postgres_engine) as session:
        assert session.get(Artifact, artifact_id).status == ArtifactStatus.AVAILABLE
        assert session.get(Resume, owner_id).status == ResumeStatus.QUEUED


def test_active_put_grace_is_not_inspected_or_repaired(postgres_engine, principal):
    class InspectForbidden(PendingStore):
        def inspect(self, location):
            raise AssertionError("active Put must not be inspected")

    store, dispatcher = InspectForbidden(postgres_engine), Dispatcher(postgres_engine)
    _, artifact_id, _ = pending(postgres_engine, principal, store, dispatcher, old=False)
    report = reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1)
    assert report.inspected == 0 and report.repaired == 0
    with Session(postgres_engine) as session:
        assert session.get(Artifact, artifact_id).status == ArtifactStatus.PENDING


@pytest.mark.parametrize("invalid", ["missing", "checksum", "size"])
def test_invalid_stale_pending_fails_and_cleans_without_dispatch(postgres_engine, principal, invalid):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    owner_id, artifact_id, location = pending(postgres_engine, principal, store, dispatcher)
    if invalid == "missing":
        store.delete(location)
    else:
        store._objects[location] = b"Golang developer" if invalid == "checksum" else b"short"
    report = reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1)
    assert report.invalid == 1
    with Session(postgres_engine) as session:
        assert session.get(Resume, owner_id).status == ResumeStatus.FAILED
        assert session.get(Artifact, artifact_id).status == ArtifactStatus.DELETED
    assert dispatcher.calls == []
    with pytest.raises(ArtifactMissing):
        store.inspect(location)


@pytest.mark.parametrize("kind", ["resume", "knowledge", "inactive_knowledge"])
@pytest.mark.parametrize("invalid", ["missing", "checksum", "size"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_invalid_repair_releases_only_old_identity_for_reupload(
    postgres_engine, principal, kind, invalid, cleanup_fails
):
    """Catches Source uniqueness blocking a new upload after invalid Artifact cleanup."""

    class RetryStore(PendingStore):
        broken = False

        def delete(self, location):
            if self.broken:
                raise ArtifactStorageFailure()
            super().delete(location)

    store, dispatcher = RetryStore(postgres_engine), Dispatcher(postgres_engine)
    uploading = upload if kind == "resume" else knowledge_upload
    model = Resume if kind == "resume" else KnowledgeDocument
    checksum_field = "sha256" if kind == "resume" else "checksum"
    terminal_status = "inactive" if kind == "inactive_knowledge" else "failed"
    owner_id, artifact_id, *_ = uploading(postgres_engine, principal, store, dispatcher)
    location = store.puts[-1]
    content = store._objects[location]
    with Session(postgres_engine) as session:
        artifact = session.get(Artifact, artifact_id)
        artifact.status = ArtifactStatus.PENDING
        artifact.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        if kind == "inactive_knowledge":
            session.get(model, owner_id).status = "inactive"
        session.commit()
    if invalid == "missing":
        store.delete(location)
    else:
        store._objects[location] = b"X" * len(content) if invalid == "checksum" else b"short"
    store.broken = cleanup_fails
    dispatcher.calls.clear()
    report = reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1)
    assert report.invalid == 1 and report.cleanup_failures == int(cleanup_fails)
    assert dispatcher.calls == []
    # Exercise the original uncaught uniqueness failure before checking internal state.
    new_owner, new_artifact, created, *_ = uploading(postgres_engine, principal, store, dispatcher)
    assert created and new_owner != owner_id and new_artifact != artifact_id
    assert uploading(postgres_engine, principal, store, dispatcher)[0:3] == (new_owner, new_artifact, False)
    assert len(store.puts) == 2
    with Session(postgres_engine) as session:
        assert getattr(session.get(model, owner_id), checksum_field) is None
        assert session.get(model, owner_id).status == terminal_status
        assert session.get(Artifact, artifact_id).sha256 is None
        assert session.get(Artifact, new_artifact).status == ArtifactStatus.AVAILABLE
    # A late old Put and retry must delete only the original exact location.
    store._objects[location] = b"LATE_OLD_PRIVATE"
    store.broken = False
    for _ in range(4):
        reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1)
    assert location not in store._objects
    assert store._objects[store.puts[-1]] == content
    assert dispatcher.calls == [(principal.tenant_id, new_owner)]
    with Session(postgres_engine) as session:
        assert session.get(model, owner_id).status == terminal_status
        assert session.get(Artifact, artifact_id).status == ArtifactStatus.DELETED


def test_mixed_lanes_reserve_independent_pages_and_resume_in_fresh_services(postgres_engine, principal):
    """Catches one busy lane consuming the budget of pending repair or cleanup."""
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    lanes = {"pending": [], "cleanup": [], "deleted": []}
    for lane, count in [("pending", 3), ("cleanup", 1), ("deleted", 1)]:
        for index in range(count):
            with Session(postgres_engine) as session:
                uow = SqlAlchemyUnitOfWork(session)
                owner, _ = ResumeService(uow.resumes, store, dispatcher, uow=uow).upload(
                    principal, "lane.txt", "text/plain", f"{lane}-{index}".encode()
                )
                location = store.puts[-1]
                lanes[lane].append(location)
                artifact = session.get(Artifact, location.artifact_id)
                if lane == "pending":
                    artifact.status = ArtifactStatus.PENDING
                    artifact.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
                else:
                    ResumeService(uow.resumes, store, dispatcher, uow=uow).delete(principal, owner.id)
                    if lane == "cleanup":
                        artifact.status = ArtifactStatus.CLEANUP_PENDING
                    store._objects[location] = b"LATE"
                session.commit()
    reserved_pending = []
    for page in range(2):
        with Session(postgres_engine) as session:
            uow = SqlAlchemyUnitOfWork(session)
            batch = uow.artifacts.reserve_reconciliation_batch(
                datetime.now(timezone.utc) - timedelta(minutes=5), limit=1
            )
            uow.commit()  # Crash after durable reservation; no work performed.
        assert len(batch.pending) == 1
        reserved_pending.append(batch.pending[0].location)
        assert len(batch.cleanup) == (1 if page == 0 else 0)
        assert len(batch.deleted) == (1 if page == 0 else 0)
    assert len(set(reserved_pending)) == 2
    dispatcher.calls.clear()
    first = reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1)
    assert first.selected == 3 and first.repaired == 1 and first.cleaned == 2
    assert dispatcher.calls[0][1] not in {item.owner_id for item in reserved_pending}
    reports = [reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1) for _ in range(6)]
    assert all(item.selected <= 3 and item.partial for item in reports)
    assert {owner_id for _, owner_id in dispatcher.calls} == {loc.owner_id for loc in lanes["pending"]}
    assert all(loc not in store._objects for lane in ["cleanup", "deleted"] for loc in lanes[lane])


def test_permanent_tombstone_revisited_after_missing_observation_and_fresh_instance(postgres_engine, principal):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    owner_id, artifact_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    location = store.puts[-1]
    orphan = ArtifactLocation(principal.tenant_id, "resumes", str(uuid4()), str(uuid4()))
    store._objects[orphan] = b"UNASSOCIATED"
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        ResumeService(uow.resumes, store, dispatcher, uow=uow).delete(principal, owner_id)
    reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1)
    # The original uploader died before compensation and writes after the sweep.
    store._objects[location] = b"LATE_PRIVATE"
    for _ in range(4):
        reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1)
    with pytest.raises(ArtifactMissing):
        store.inspect(location)
    assert store._objects[orphan] == b"UNASSOCIATED"
    with Session(postgres_engine) as session:
        artifact = session.get(Artifact, artifact_id)
        assert artifact.status == ArtifactStatus.DELETED and artifact.sha256 is None


def test_durable_reservation_crash_rotates_tenants_and_pages_then_wraps(postgres_engine, principal):
    from app.models.identity import Tenant, User
    from app.security.tokens import Principal
    from app.domain.enums import Role
    from sqlalchemy import delete

    other = Principal(str(uuid4()), str(uuid4()), Role.ADMIN)
    with Session(postgres_engine) as session:
        session.add(Tenant(id=other.tenant_id, name="synthetic-second-tenant"))
        session.flush()
        session.add(
            User(
                id=other.user_id,
                tenant_id=other.tenant_id,
                email=other.user_id + "@test.invalid",
                password_hash="synthetic",
            )
        )
        session.commit()
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    targets = []
    try:
        for identity in [principal, other]:
            for index in range(3):
                with Session(postgres_engine) as session:
                    uow = SqlAlchemyUnitOfWork(session)
                    service = ResumeService(uow.resumes, store, dispatcher, uow=uow)
                    owner, _ = service.upload(identity, "synthetic.txt", "text/plain", f"private-{index}".encode())
                    targets.append(store.puts[-1])
                    service.delete(identity, owner.id)
        # Simulate two new processes crashing just after committed reservation.
        reserved = []
        for _ in range(2):
            with Session(postgres_engine) as session:
                uow = SqlAlchemyUnitOfWork(session)
                batch = uow.artifacts.reserve_reconciliation_batch(datetime.now(timezone.utc), limit=1)
                uow.commit()
                reserved.extend(item.location for item in batch.deleted)
        assert {loc.tenant_id for loc in reserved} == {principal.tenant_id, other.tenant_id}
        assert len(reserved) == 2
        for location in targets:
            store._objects[location] = b"LATE_PRIVATE"
        unrelated = ArtifactLocation(other.tenant_id, "resumes", str(uuid4()), str(uuid4()))
        store._objects[unrelated] = b"UNRELATED"
        reports = [reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1) for _ in range(12)]
        assert all(report.selected <= 3 and report.partial for report in reports)
        assert all(location not in store._objects for location in targets)
        assert store._objects[unrelated] == b"UNRELATED"
        assert all(loc.tenant_id not in repr(asdict(report)) for report in reports for loc in targets)
    finally:
        with Session(postgres_engine) as session:
            session.execute(delete(Tenant).where(Tenant.id == other.tenant_id))
            session.commit()


def test_terminal_failure_is_sanitized_and_recovered_on_later_wrap(postgres_engine, principal):
    class SometimesUnavailable(PendingStore):
        broken = False

        def delete(self, location):
            if self.broken:
                raise RuntimeError("SECRET_NAME raw/key/tenant")
            super().delete(location)

    store, dispatcher = SometimesUnavailable(postgres_engine), Dispatcher(postgres_engine)
    owner_id, artifact_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    location = store.puts[-1]
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        ResumeService(uow.resumes, store, dispatcher, uow=uow).delete(principal, owner_id)
    store._objects[location] = b"LATE"
    store.broken = True
    report = reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1)
    assert report.cleanup_failures == 1 and "SECRET" not in repr(report)
    with Session(postgres_engine) as session:
        artifact = session.get(Artifact, artifact_id)
        assert artifact.status == ArtifactStatus.DELETED
        assert artifact.error_code.value == "storage_unavailable"
    store.broken = False
    reports = [reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1) for _ in range(3)]
    assert any(item.known_terminal_failures == 1 for item in reports)
    with Session(postgres_engine) as session:
        assert session.get(Artifact, artifact_id).error_code is None
    assert location not in store._objects


def test_orphan_pages_are_bounded_report_only_and_failed_page_is_not_empty_success(postgres_engine, principal):
    from app.artifacts.s3 import S3ArtifactStore

    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    _, _, *_ = upload(postgres_engine, principal, store, dispatcher)
    location = store.puts[-1]
    orphan = ArtifactLocation(principal.tenant_id, "resumes", str(uuid4()), str(uuid4()))

    class ListingClient:
        fail = False
        calls = []

        def list_objects_v2(self, **kwargs):
            if self.fail:
                raise OSError("SECRET listing failed")
            self.calls.append(kwargs)
            if kwargs.get("ContinuationToken"):
                return {"Contents": [{"Key": "tenants/not/a/valid/key/extra"}], "IsTruncated": False}

            def key(item):
                return f"tenants/{item.tenant_id}/{item.namespace}/{item.owner_id}/{item.artifact_id}"

            return {
                "Contents": [{"Key": key(location)}, {"Key": key(orphan)}],
                "IsTruncated": True,
                "NextContinuationToken": "opaque-SECRET",
            }

        def delete_object(self, **kwargs):
            raise AssertionError("orphan inspection must never delete")

    client = ListingClient()
    lister = S3ArtifactStore(client)
    service = reconciler(postgres_engine, store, dispatcher)
    assert callable(getattr(service, "inspect_orphan_page", None)), "report-only orphan inspection is absent"
    report, cursor = service.inspect_orphan_page(lister, limit=2)
    assert report.observed == 2 and report.unassociated == 1 and report.partial
    assert report.error_code is None and cursor is not None
    assert client.calls[0]["MaxKeys"] == 2
    assert "SECRET" not in repr(report) and principal.tenant_id not in repr(report)
    second, next_cursor = service.inspect_orphan_page(lister, cursor=cursor, limit=2)
    assert second.unrecognized == 1 and next_cursor is None and second.partial
    client.fail = True
    failed, retained_cursor = service.inspect_orphan_page(lister, cursor=cursor, limit=2)
    assert failed.error_code == "storage_unavailable"
    assert failed.observed == 0 and retained_cursor == cursor


def test_cleanup_retry_commits_pending_before_object_io(postgres_engine, principal):
    class RetryStore(PendingStore):
        fail = True

        def delete(self, location):
            if self.fail:
                raise ArtifactStorageFailure()
            with Session(postgres_engine) as session:
                assert session.get(Artifact, location.artifact_id).status == ArtifactStatus.CLEANUP_PENDING
            super().delete(location)

    store, dispatcher = RetryStore(postgres_engine), Dispatcher(postgres_engine)
    owner_id, artifact_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(AppError):
            ResumeService(uow.resumes, store, dispatcher, uow=uow).delete(principal, owner_id)
    store.fail = False
    report = reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1)
    assert report.cleaned == 1 and report.cleanup_failures == 0
    with Session(postgres_engine) as session:
        assert session.get(Artifact, artifact_id).status == ArtifactStatus.DELETED


def test_cleanup_wins_after_reconciliation_head_and_before_publication(postgres_engine, principal):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    inspected, release = Event(), Event()

    class SlowHead(PendingStore):
        def inspect(self, location):
            info = super().inspect(location)
            inspected.set()
            assert release.wait(10)
            return info

    store, dispatcher = SlowHead(postgres_engine), Dispatcher(postgres_engine)
    owner_id, artifact_id, _ = pending(postgres_engine, principal, store, dispatcher)
    with ThreadPoolExecutor(max_workers=1) as executor:
        recovering = executor.submit(lambda: reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1))
        assert inspected.wait(10)
        try:
            with Session(postgres_engine) as session:
                uow = SqlAlchemyUnitOfWork(session)
                ResumeService(uow.resumes, store, dispatcher, uow=uow).delete(principal, owner_id)
        finally:
            release.set()
        report = recovering.result(timeout=10)
    assert report.repaired == 0 and report.skipped == 1
    assert dispatcher.calls == []
    with Session(postgres_engine) as session:
        assert session.get(Artifact, artifact_id).status == ArtifactStatus.DELETED
        assert session.get(Resume, owner_id).sha256 is None


def test_dispatch_failure_after_repair_leaves_durable_queue(postgres_engine, principal):
    store, dispatcher = PendingStore(postgres_engine), Dispatcher(postgres_engine)
    owner_id, artifact_id, _ = pending(postgres_engine, principal, store, dispatcher)
    dispatcher.broken = True
    report = reconciler(postgres_engine, store, dispatcher).run_once(batch_size=1)
    assert report.repaired == 1 and report.dispatch_failures == 1
    with Session(postgres_engine) as session:
        assert session.get(Artifact, artifact_id).status == ArtifactStatus.AVAILABLE
        assert session.get(Resume, owner_id).status == ResumeStatus.QUEUED


def test_real_minio_repair_privacy_delete_and_paginated_orphan_observation(postgres_engine, principal):
    from app.artifacts.s3 import S3ArtifactStore, S3Settings

    store, dispatcher = S3ArtifactStore(S3Settings.from_env().client()), Dispatcher(postgres_engine)
    owner_id, artifact_id, *_ = upload(postgres_engine, principal, store, dispatcher)
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        location = uow.artifacts.resolve_location(principal.tenant_id, "resume", owner_id, artifact_id)
        row = session.get(Artifact, artifact_id)
        row.status = ArtifactStatus.PENDING
        row.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        session.commit()
    try:
        service = reconciler(postgres_engine, store, dispatcher)
        assert service.run_once(batch_size=1).repaired == 1
        report, _ = service.inspect_orphan_page(store, limit=1)
        assert report.partial and report.observed <= 1 and report.error_code is None
        with Session(postgres_engine) as session:
            uow = SqlAlchemyUnitOfWork(session)
            ResumeService(uow.resumes, store, dispatcher, uow=uow).delete(principal, owner_id)
        with pytest.raises(ArtifactMissing):
            store.inspect(location)
    finally:
        store.delete(location)
