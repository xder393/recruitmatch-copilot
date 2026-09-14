"""Worker-only CP3 Artifact reconciliation; no embedding or parser composition."""

from app.artifacts.s3 import S3Settings, S3ArtifactStore
from app.config import Settings
from app.database import create_engine_and_session
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.services.artifact_reconciliation import ArtifactReconciliationService
from app.tasks.dispatcher import CeleryTaskDispatcher
from app.observability.adapters import ObservedArtifactStore


def run_artifact_maintenance():
    settings = Settings.load()
    engine, sessions = create_engine_and_session(settings.database_url)
    client = S3Settings.from_env().client()
    try:
        store = ObservedArtifactStore(S3ArtifactStore(client))
        return ArtifactReconciliationService(
            SqlAlchemyUnitOfWorkFactory(sessions), store, store, CeleryTaskDispatcher()
        ).run_once(batch_size=25)
    finally:
        client.close()
        engine.dispose()
