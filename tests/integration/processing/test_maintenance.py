"""The actual Worker maintenance entrypoint reuses CP3 tombstone cleanup."""

import hashlib
from io import BytesIO

import pytest
from sqlalchemy.orm import Session

from app.artifacts.s3 import S3Settings, S3ArtifactStore
from app.artifacts.ports import ArtifactLocation, ArtifactMissing
from app.domain.artifacts import ArtifactStatus
from app.domain.enums import ResumeStatus
from app.models.artifacts import Artifact
from tests.integration.processing.test_leases import source as source


def test_worker_maintenance_cleans_retained_exact_tombstone_without_processing(postgres_engine, source, monkeypatch):
    from app.tasks import celery_app

    def forbidden(*args, **kwargs):
        raise AssertionError("maintenance must not construct processors or embeddings")

    monkeypatch.setattr(celery_app, "build_worker_dependencies", forbidden)
    location = None
    client = S3Settings.from_env().client()
    store = S3ArtifactStore(client)
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        row.lifecycle_status = "deleted"
        row.status = ResumeStatus.DELETED if source[1] == "resume" else "deleted"
        artifact = session.get(Artifact, row.artifact_id)
        artifact.sha256, artifact.status = None, ArtifactStatus.DELETED
        location = ArtifactLocation(
            source[0], "resumes" if source[1] == "resume" else "knowledge", source[2], artifact.id
        )
        session.commit()
    payload = b"synthetic late Put"
    try:
        # Repeated late puts prove a previous successful missing observation cannot retire this tombstone.
        for _ in range(2):
            store.put(location, BytesIO(payload), len(payload), hashlib.sha256(payload).hexdigest())
            for _ in range(10):
                celery_app.reconcile_artifacts_task.run()
                try:
                    store.inspect(location)
                except ArtifactMissing:
                    break
            with pytest.raises(ArtifactMissing):
                store.inspect(location)
        with Session(postgres_engine) as session:
            artifact = session.get(Artifact, location.artifact_id)
            assert artifact.status == ArtifactStatus.DELETED and artifact.error_code is None
    finally:
        store.delete(location)
        client.close()
