from __future__ import annotations

from time import perf_counter

from app.ai.contracts import ModelRequest, ModelResponse
from app.ai.gateway import ModelGatewayError


class FakeStructuredModel:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def generate(self, request: ModelRequest):
        self.calls.append(request)
        started = perf_counter()
        value = request.schema.model_validate(self.outputs[request.operation])
        return ModelResponse(
            value=value,
            provider="fake",
            model="fake-model",
            input_tokens=12,
            output_tokens=8,
            estimated_cost=0.0,
            latency_ms=(perf_counter() - started) * 1000,
        )


class RaisingModel:
    def __init__(self, code: str):
        self.code = code
        self.calls = []

    def generate(self, request: ModelRequest):
        self.calls.append(request)
        raise ModelGatewayError(self.code, retryable=False)
