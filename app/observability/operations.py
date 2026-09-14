"""Beat-owned PostgreSQL/Redis aggregates; callbacks never perform probes."""

import logging

from opentelemetry.instrumentation.utils import suppress_instrumentation
from sqlalchemy import func, select, text

from app.domain.artifacts import ArtifactStatus
from app.domain.enums import ResumeStatus
from app.models.artifacts import Artifact
from app.models.knowledge import KnowledgeDocument
from app.models.resumes import Resume
from app.observability.events import DomainEvent
from app.observability.otel import OtelDomainEventRecorder


_POSTGRES = frozenset({"queue.depth", "queue.oldest_age", "artifact.cleanup_pending"})
_HEARTBEATS = frozenset({"worker.live", "worker.oldest_heartbeat_age", "beat.tick_age"})
logger = logging.getLogger(__name__)


class OperationalMetrics:
    def __init__(self, runtime, session_factory, heartbeats):
        self.runtime, self.session_factory, self.heartbeats = runtime, session_factory, heartbeats

    def poll(self):
        recorder = self.runtime.recorder
        if not isinstance(recorder, OtelDomainEventRecorder) or self.runtime._closed:
            return
        with suppress_instrumentation():
            postgres = []
            try:
                with self.session_factory() as session:
                    session.execute(text("SET LOCAL statement_timeout = '1000ms'"))
                    session.execute(text("SET LOCAL lock_timeout = '500ms'"))
                    for model, kind, queued in (
                        (Resume, "resume", ResumeStatus.QUEUED),
                        (KnowledgeDocument, "knowledge_document", "uploaded"),
                    ):
                        count, age = session.execute(
                            select(
                                func.count(model.id),
                                func.coalesce(
                                    func.extract("epoch", func.clock_timestamp() - func.min(model.queued_at)), 0
                                ),
                            ).where(model.lifecycle_status == "active", model.status == queued)
                        ).one()
                        postgres.extend(
                            [
                                DomainEvent("queue.depth", {"source.type": kind}, count),
                                DomainEvent("queue.oldest_age", {"source.type": kind}, max(0, float(age))),
                            ]
                        )
                    pending = session.scalar(
                        select(func.count(Artifact.id)).where(Artifact.status == ArtifactStatus.CLEANUP_PENDING)
                    )
                    postgres.append(DomainEvent("artifact.cleanup_pending", {}, pending))
            except Exception:
                postgres = []
                logger.warning("telemetry_probe_unavailable")
            recorder.replace_gauges(_POSTGRES, postgres)
            heartbeat = []
            try:
                snapshot = self.heartbeats.snapshot()
                for name, field in (
                    ("worker.live", "worker_count"),
                    ("worker.oldest_heartbeat_age", "worker_oldest_heartbeat_age_seconds"),
                    ("beat.tick_age", "beat_heartbeat_age_seconds"),
                ):
                    value = snapshot.get(field)
                    if value is not None:
                        heartbeat.append(DomainEvent(name, {}, value))
            except Exception:
                logger.warning("telemetry_probe_unavailable")
            recorder.replace_gauges(_HEARTBEATS, heartbeat)
