"""Real PostgreSQL contracts for upload arbitration, privacy and guarded state."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.artifacts.ports import ArtifactLocation
from app.domain.artifacts import ArtifactErrorCode, ArtifactNotFoundError, ArtifactStatus, ArtifactTransitionError
from app.models.artifacts import Artifact
from app.models.identity import Tenant
from app.models.knowledge import KnowledgeDocument
from app.models.resumes import Resume
from app.repositories.artifacts import ArtifactRepository
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork


@pytest.fixture
def tenants(postgres_engine):
    ids = [str(uuid4()), str(uuid4())]
    with Session(postgres_engine) as session:
        session.add_all([Tenant(id=id_, name="artifact-test") for id_ in ids])
        session.commit()
    yield ids
    with Session(postgres_engine) as session:
        session.execute(delete(Tenant).where(Tenant.id.in_(ids)))
        session.commit()


def claim(repo, tenant, owner="owner-1", sha="a" * 64, owner_type="resume"):
    return repo.claim_upload(tenant, owner_type, owner, sha, "text/plain", 10)


def transition(repo, method, *args, owner_type="resume", owner_id="owner-1"):
    return getattr(repo, method)(*args, owner_type=owner_type, owner_id=owner_id)


@pytest.mark.parametrize("state", ["PENDING", "AVAILABLE", "FAILED"])
def test_active_checksum_returns_winning_owner_and_respects_scope(postgres_engine, tenants, state):
    with Session(postgres_engine) as session:
        repo = SqlAlchemyUnitOfWork(session).artifacts
        first = claim(repo, tenants[0])
        if state == "AVAILABLE":
            transition(repo, "mark_available", tenants[0], first.id)
        elif state == "FAILED":
            transition(repo, "mark_failed", tenants[0], first.id, ArtifactErrorCode.OBJECT_NOT_FOUND)
        again = claim(repo, tenants[0], owner="different-proposal")
        assert (again.id, again.owner_id) == (first.id, "owner-1")
        assert claim(repo, tenants[1]).id != first.id
        assert claim(repo, tenants[0], owner_type="knowledge_document").id != first.id


def test_distinct_owner_concurrent_claim_preserves_callers_work(postgres_engine, tenants):
    barrier = Barrier(2)

    def worker(owner):
        with Session(postgres_engine) as session:
            unrelated = Tenant(id=str(uuid4()), name=owner)
            session.add(unrelated)
            barrier.wait(timeout=10)
            artifact = claim(ArtifactRepository(session), tenants[0], owner)
            result = artifact.id, artifact.owner_id, unrelated.id
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, ["proposal-a", "proposal-b"]))
    try:
        assert results[0][:2] == results[1][:2]
        assert results[0][1] in {"proposal-a", "proposal-b"}
        with Session(postgres_engine) as session:
            assert len(list(session.scalars(select(Tenant).where(Tenant.id.in_([r[2] for r in results]))))) == 2
            assert len(list(session.scalars(select(Artifact).where(Artifact.tenant_id == tenants[0])))) == 1
    finally:
        with Session(postgres_engine) as session:
            session.execute(delete(Tenant).where(Tenant.id.in_([r[2] for r in results])))
            session.commit()


def test_claim_and_transition_do_not_commit_caller_transaction(postgres_engine, tenants):
    with Session(postgres_engine) as session:
        repo = ArtifactRepository(session)
        artifact = claim(repo, tenants[0])
        transition(repo, "mark_available", tenants[0], artifact.id)
        artifact_id = artifact.id
        session.rollback()
    with Session(postgres_engine) as session:
        assert session.get(Artifact, artifact_id) is None


def test_deleted_tombstones_remain_pageable_after_success_and_late_error(postgres_engine, tenants):
    with Session(postgres_engine) as session:
        repo = ArtifactRepository(session)
        artifact = claim(repo, tenants[0])
        transition(repo, "mark_failed", tenants[0], artifact.id, ArtifactErrorCode.STORAGE_UNAVAILABLE)
        transition(repo, "mark_cleanup_pending", tenants[0], artifact.id)
        transition(repo, "mark_deleted", tenants[0], artifact.id)
        repo.record_deleted_cleanup_result(
            tenants[0], artifact.id, ArtifactErrorCode.ACCESS_DENIED, owner_type="resume", owner_id="owner-1"
        )
        session.commit()
        page = repo.list_deleted_tombstones(tenants[0], limit=1)
        assert [entry.location.artifact_id for entry in page] == [artifact.id]
        assert page[0].error_code == ArtifactErrorCode.ACCESS_DENIED
        assert repo.list_deleted_tombstones(tenants[0], after_id=artifact.id, limit=1) == []
        repo.record_deleted_cleanup_result(tenants[0], artifact.id, None, owner_type="resume", owner_id="owner-1")
        session.commit()
        assert len(repo.list_deleted_tombstones(tenants[0], limit=1)) == 1
        assert artifact.status == ArtifactStatus.DELETED and artifact.sha256 is None
        assert repo.list_deleted_tombstones(tenants[1], limit=1) == []


def test_tombstone_error_update_cannot_change_active_artifact(postgres_engine, tenants):
    with Session(postgres_engine) as session:
        repo = ArtifactRepository(session)
        artifact = claim(repo, tenants[0])
        with pytest.raises(ArtifactTransitionError):
            repo.record_deleted_cleanup_result(
                tenants[0], artifact.id, ArtifactErrorCode.ACCESS_DENIED, owner_type="resume", owner_id="owner-1"
            )


def test_tombstone_error_guard_uses_persisted_state_without_autoflush(postgres_engine, tenants):
    with Session(postgres_engine, expire_on_commit=False) as session:
        repo = ArtifactRepository(session)
        artifact = claim(repo, tenants[0])
        session.commit()
        artifact.status, artifact.sha256 = ArtifactStatus.DELETED, None
        with pytest.raises(ArtifactTransitionError):
            repo.record_deleted_cleanup_result(
                tenants[0], artifact.id, ArtifactErrorCode.ACCESS_DENIED, owner_type="resume", owner_id="owner-1"
            )
        session.rollback()


@pytest.mark.parametrize("initial", ["available", "failed"])
def test_cleanup_redacts_checksum_before_storage_success_and_allows_retry(postgres_engine, tenants, initial):
    with Session(postgres_engine) as session:
        repo = ArtifactRepository(session)
        artifact = claim(repo, tenants[0])
        if initial == "available":
            transition(repo, "mark_available", tenants[0], artifact.id)
        else:
            transition(repo, "mark_failed", tenants[0], artifact.id, ArtifactErrorCode.CHECKSUM_MISMATCH)
        transition(repo, "mark_cleanup_pending", tenants[0], artifact.id)
        session.commit()
        session.expire_all()
        assert artifact.sha256 is None
        assert artifact.status == ArtifactStatus.CLEANUP_PENDING
        assert artifact.error_code is None
        transition(repo, "mark_cleanup_failed", tenants[0], artifact.id, ArtifactErrorCode.STORAGE_UNAVAILABLE)
        session.commit()
        assert artifact.status == ArtifactStatus.CLEANUP_FAILED
        assert artifact.sha256 is None
        transition(repo, "mark_cleanup_pending", tenants[0], artifact.id)
        assert artifact.error_code is None
        transition(repo, "mark_deleted", tenants[0], artifact.id)
        transition(repo, "mark_deleted", tenants[0], artifact.id)
        session.commit()
        second = claim(repo, tenants[0], owner="replacement")
        assert second.id != artifact.id
        assert second.status == ArtifactStatus.PENDING
        assert artifact.status == ArtifactStatus.DELETED


@pytest.mark.parametrize("target", ["mark_cleanup_pending", "mark_deleted", "mark_cleanup_failed"])
def test_pending_cannot_skip_upload_failure_path(postgres_engine, tenants, target):
    with Session(postgres_engine) as session:
        repo = ArtifactRepository(session)
        artifact = claim(repo, tenants[0])
        args = [tenants[0], artifact.id]
        if target == "mark_cleanup_failed":
            args.append(ArtifactErrorCode.STORAGE_UNAVAILABLE)
        with pytest.raises(ArtifactTransitionError):
            transition(repo, target, *args)
        session.refresh(artifact)
        assert artifact.status == ArtifactStatus.PENDING
        assert artifact.sha256 == "a" * 64


def test_stale_identity_map_cannot_resurrect_cleaned_artifact(postgres_engine, tenants):
    with Session(postgres_engine, expire_on_commit=False) as stale:
        repo = ArtifactRepository(stale)
        artifact = claim(repo, tenants[0])
        transition(repo, "mark_available", tenants[0], artifact.id)
        stale.commit()
        with Session(postgres_engine) as current:
            current_repo = ArtifactRepository(current)
            transition(current_repo, "mark_cleanup_pending", tenants[0], artifact.id)
            transition(current_repo, "mark_deleted", tenants[0], artifact.id)
            current.commit()
        assert artifact.status == ArtifactStatus.AVAILABLE
        with pytest.raises(ArtifactTransitionError):
            transition(repo, "mark_available", tenants[0], artifact.id)
        stale.refresh(artifact)
        assert artifact.status == ArtifactStatus.DELETED
        assert artifact.sha256 is None


def test_location_and_transitions_are_tenant_and_owner_qualified(postgres_engine, tenants):
    with Session(postgres_engine) as session:
        repo = ArtifactRepository(session)
        artifact = claim(repo, tenants[0])
        location = repo.resolve_location(tenants[0], "resume", "owner-1", artifact.id)
        assert location == ArtifactLocation(tenants[0], "resumes", "owner-1", artifact.id)
        with pytest.raises(FrozenInstanceError):
            location.owner_id = "other"
        for tenant, owner_type, owner in [
            (tenants[1], "resume", "owner-1"),
            (tenants[0], "knowledge_document", "owner-1"),
            (tenants[0], "resume", "other"),
        ]:
            with pytest.raises(ArtifactNotFoundError):
                repo.resolve_location(tenant, owner_type, owner, artifact.id)
            with pytest.raises(ArtifactNotFoundError):
                transition(repo, "mark_available", tenant, artifact.id, owner_type=owner_type, owner_id=owner)
        with pytest.raises(ArtifactNotFoundError):
            transition(repo, "mark_available", tenants[1], artifact.id)
        with pytest.raises(ArtifactNotFoundError):
            transition(repo, "mark_available", tenants[0], "missing")
        session.refresh(artifact)
        assert artifact.status == ArtifactStatus.PENDING


def test_free_text_failure_code_is_rejected_without_persistence(postgres_engine, tenants):
    with Session(postgres_engine) as session:
        repo = ArtifactRepository(session)
        artifact = claim(repo, tenants[0])
        with pytest.raises(ValueError):
            transition(repo, "mark_failed", tenants[0], artifact.id, "private exception text")
        session.refresh(artifact)
        assert artifact.error_code is None
        assert artifact.status == ArtifactStatus.PENDING


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "QUEUED"),
        ("error_code", "private error"),
        ("owner_type", "job_version"),
        ("size_bytes", -1),
        ("sha256", "short"),
        ("sha256", None),
    ],
)
def test_database_rejects_invalid_artifact_state_even_without_orm(postgres_engine, tenants, field, value):
    with Session(postgres_engine) as session:
        artifact = claim(ArtifactRepository(session), tenants[0])
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                text(f"UPDATE artifacts SET {field}=:value WHERE id=:id"), {"value": value, "id": artifact.id}
            )


@pytest.mark.parametrize(
    "state,allowed",
    [
        ("PENDING", {"AVAILABLE", "FAILED"}),
        ("AVAILABLE", {"AVAILABLE", "CLEANUP_PENDING"}),
        ("FAILED", {"FAILED", "CLEANUP_PENDING"}),
        ("CLEANUP_PENDING", {"CLEANUP_PENDING", "CLEANUP_FAILED", "DELETED"}),
        ("CLEANUP_FAILED", {"CLEANUP_FAILED", "CLEANUP_PENDING"}),
        ("DELETED", {"DELETED"}),
    ],
)
@pytest.mark.parametrize("target", ["AVAILABLE", "FAILED", "CLEANUP_PENDING", "CLEANUP_FAILED", "DELETED"])
def test_persisted_transition_matrix(postgres_engine, tenants, state, allowed, target):
    with Session(postgres_engine) as session:
        artifact = Artifact(
            tenant_id=tenants[0],
            owner_type="resume",
            owner_id="owner-1",
            sha256="a" * 64 if state in {"PENDING", "AVAILABLE", "FAILED"} else None,
            media_type="text/plain",
            size_bytes=10,
            status=ArtifactStatus(state),
        )
        session.add(artifact)
        session.commit()
        repo = ArtifactRepository(session)
        args = [tenants[0], artifact.id]
        if target in {"FAILED", "CLEANUP_FAILED"}:
            args.append(ArtifactErrorCode.STORAGE_UNAVAILABLE)
        if target in allowed:
            transition(repo, "mark_" + target.lower(), *args)
            session.flush()
            session.refresh(artifact)
            assert artifact.status.value == target
        else:
            with pytest.raises(ArtifactTransitionError):
                transition(repo, "mark_" + target.lower(), *args)
            session.refresh(artifact)
            assert artifact.status.value == state


def test_dirty_stale_status_is_not_flushed_before_guard(postgres_engine, tenants):
    with Session(postgres_engine, expire_on_commit=False) as session:
        repo = ArtifactRepository(session)
        artifact = claim(repo, tenants[0])
        transition(repo, "mark_failed", tenants[0], artifact.id, ArtifactErrorCode.OBJECT_NOT_FOUND)
        session.commit()
        artifact.status = ArtifactStatus.PENDING
        with pytest.raises(ArtifactTransitionError):
            transition(repo, "mark_available", tenants[0], artifact.id)
        session.refresh(artifact)
        assert artifact.status == ArtifactStatus.FAILED


@pytest.mark.parametrize(
    "field,value", [("sha256", "wrong"), ("owner_type", "job_version"), ("owner_id", ""), ("size_bytes", 10485761)]
)
def test_claim_rejects_invalid_metadata_without_partial_write(postgres_engine, tenants, field, value):
    with Session(postgres_engine) as session:
        params = dict(
            tenant_id=tenants[0],
            owner_type="resume",
            owner_id="owner-1",
            sha256="a" * 64,
            media_type="text/plain",
            size_bytes=10,
        )
        params[field] = value
        with pytest.raises(ValueError):
            ArtifactRepository(session).claim_upload(**params)
        assert list(session.scalars(select(Artifact).where(Artifact.tenant_id == tenants[0]))) == []


def test_schema_has_new_head_and_nullable_source_anchors(postgres_engine):
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260911_15"
        for table in ("resumes", "knowledge_documents"):
            assert (
                connection.scalar(
                    text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=:table AND column_name='artifact_id'"
                    ),
                    {"table": table},
                )
                == "YES"
            )


@pytest.mark.parametrize("source_type", ["resume", "knowledge_document"])
def test_sources_can_use_new_anchor_alone_and_reject_other_owner(postgres_engine, tenants, source_type):
    with Session(postgres_engine) as session:
        artifact = claim(ArtifactRepository(session), tenants[0], owner_type=source_type)
        fields = dict(
            id="owner-1",
            tenant_id=tenants[0],
            artifact_id=artifact.id,
            original_filename="example.txt",
            media_type="text/plain",
            size_bytes=10,
        )
        if source_type == "resume":
            source = Resume(**fields, sha256="a" * 64)
        else:
            source = KnowledgeDocument(**fields, checksum="a" * 64, document_type="policy", status="queued")
        session.add(source)
        session.flush()
        assert source.artifact_id == artifact.id
        if source_type == "resume":
            assert source.extracted_text is None
        with pytest.raises(IntegrityError), session.begin_nested():
            source.artifact_id = claim(ArtifactRepository(session), tenants[1], owner_type=source_type).id
            session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            source.artifact_id = claim(ArtifactRepository(session), tenants[0], "wrong-owner", "b" * 64, source_type).id
            session.flush()


@pytest.mark.parametrize("source_type", ["resume", "knowledge_document"])
def test_source_anchor_rejects_other_artifact_owner_type(postgres_engine, tenants, source_type):
    with Session(postgres_engine) as session:
        other_type = "knowledge_document" if source_type == "resume" else "resume"
        artifact = claim(ArtifactRepository(session), tenants[0], owner_type=other_type)
        fields = dict(
            id="owner-1",
            tenant_id=tenants[0],
            artifact_id=artifact.id,
            original_filename="example.txt",
            media_type="text/plain",
            size_bytes=10,
        )
        if source_type == "resume":
            source = Resume(**fields, sha256="a" * 64)
        else:
            source = KnowledgeDocument(**fields, checksum="a" * 64, document_type="policy", status="queued")
        session.add(source)
        with pytest.raises(IntegrityError):
            session.flush()
