"""Write-only privacy-safe model trace repository."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.ai.contracts import ModelRequest, ModelResponse
from app.ai.gateway import ModelGatewayError
from app.models.operations import ModelTrace


def _fingerprint(request: ModelRequest, source_ids: list[str]) -> str:
    payload = {
        "operation": request.operation,
        "prompt_version": request.prompt_version,
        "source_ids": sorted(source_ids),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ModelTraceWriter:
    def __init__(self, session: Session):
        self.session = session

    def succeeded(
        self,
        tenant_id: str,
        business_type: str,
        business_id: str,
        source_ids: list[str],
        request: ModelRequest,
        response: ModelResponse,
        fallback_reason: str | None = None,
    ) -> ModelTrace:
        trace = ModelTrace(
            tenant_id=tenant_id,
            business_type=business_type,
            business_id=business_id,
            operation=request.operation,
            request_fingerprint=_fingerprint(request, source_ids),
            provider=response.provider,
            model=response.model,
            prompt_version=request.prompt_version,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_cost=response.estimated_cost,
            latency_ms=response.latency_ms,
            status="rejected" if fallback_reason else "succeeded",
            fallback_reason=fallback_reason,
            attempt_count=response.attempts,
        )
        self.session.add(trace)
        return trace

    def failed(
        self,
        tenant_id: str,
        business_type: str,
        business_id: str,
        source_ids: list[str],
        request: ModelRequest,
        error: ModelGatewayError,
        latency_ms: float,
    ) -> ModelTrace:
        trace = ModelTrace(
            tenant_id=tenant_id,
            business_type=business_type,
            business_id=business_id,
            operation=request.operation,
            request_fingerprint=_fingerprint(request, source_ids),
            provider="unknown",
            model="unknown",
            prompt_version=request.prompt_version,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=0.0,
            latency_ms=latency_ms,
            status="failed",
            error_code=error.code[:100],
            fallback_reason=error.code[:100],
            attempt_count=error.attempts,
        )
        self.session.add(trace)
        return trace
