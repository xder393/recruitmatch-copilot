"""Commit privacy-safe model operation traces outside business transactions."""

from __future__ import annotations

from app.repositories.unit_of_work import UnitOfWorkFactory, unit_of_work_factory


class AITraceSink:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = unit_of_work_factory(uow_factory)

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
        with self.uow_factory() as uow:
            trace = uow.model_traces.succeeded(
                tenant_id,
                business_type,
                business_id,
                source_ids,
                request,
                response,
                fallback_reason=fallback_reason,
            )
            uow.commit()
            return trace.id

    def failed(self, tenant_id, business_type, business_id, source_ids, request, error, latency_ms):
        with self.uow_factory() as uow:
            trace = uow.model_traces.failed(
                tenant_id,
                business_type,
                business_id,
                source_ids,
                request,
                error,
                latency_ms,
            )
            uow.commit()
            return trace.id
