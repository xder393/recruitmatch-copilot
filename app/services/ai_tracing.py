"""Commit privacy-safe model operation traces outside business transactions."""
from __future__ import annotations

from app.repositories.model_traces import ModelTraceWriter


class AITraceSink:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def succeeded(
        self,
        tenant_id,
        business_type,
        business_id,
        source_ids,
        request,
        response,
        fallback_reason=None,
    ):
        with self.session_factory() as session:
            trace = ModelTraceWriter(session).succeeded(
                tenant_id,
                business_type,
                business_id,
                source_ids,
                request,
                response,
                fallback_reason=fallback_reason,
            )
            session.commit()
            return trace.id

    def failed(self, tenant_id, business_type, business_id, source_ids, request, error, latency_ms):
        with self.session_factory() as session:
            trace = ModelTraceWriter(session).failed(
                tenant_id,
                business_type,
                business_id,
                source_ids,
                request,
                error,
                latency_ms,
            )
            session.commit()
            return trace.id
