from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.ai.contracts import ModelRequest
from app.ai.gateway import ModelGatewayError, OpenAICompatibleGateway
from app.config import Settings
from tests.ai.fakes import FakeStructuredModel


class Answer(BaseModel):
    value: str


class StubCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=outcome))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )


def _client(completions):
    return SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=completions)))


def _request():
    return ModelRequest(
        operation="answer",
        prompt_version="answer-v1",
        system="Use facts only",
        user="question",
        schema=Answer,
    )


def test_fake_model_returns_schema_validated_value():
    fake = FakeStructuredModel({"answer": {"value": "ok"}})
    response = fake.generate(_request())
    assert response.value == Answer(value="ok")
    assert response.model == "fake-model"


def test_gateway_returns_usage_and_cost_from_parsed_response():
    completions = StubCompletions([Answer(value="ok")])
    settings = Settings(
        api_key="test",
        ai_enabled=True,
        model_input_cost_per_million=2.0,
        model_output_cost_per_million=8.0,
    )
    response = OpenAICompatibleGateway(settings, client=_client(completions)).generate(_request())
    assert response.value == Answer(value="ok")
    assert response.input_tokens == 100
    assert response.output_tokens == 20
    assert response.estimated_cost == pytest.approx(0.00036)


def test_gateway_retries_timeout_once_then_succeeds():
    completions = StubCompletions([TimeoutError("slow"), Answer(value="ok")])
    settings = Settings(api_key="test", ai_enabled=True, model_max_retries=1)
    response = OpenAICompatibleGateway(settings, client=_client(completions)).generate(_request())
    assert response.value.value == "ok"
    assert completions.calls == 2


def test_gateway_raises_normalized_timeout_after_retry_budget():
    completions = StubCompletions([TimeoutError("slow"), TimeoutError("still slow")])
    settings = Settings(api_key="test", ai_enabled=True, model_max_retries=1)
    with pytest.raises(ModelGatewayError) as raised:
        OpenAICompatibleGateway(settings, client=_client(completions)).generate(_request())
    assert raised.value.code == "timeout"
    assert raised.value.retryable is True
    assert completions.calls == 2


def test_gateway_normalizes_invalid_parsed_response():
    completions = StubCompletions([None])
    settings = Settings(api_key="test", ai_enabled=True)
    with pytest.raises(ModelGatewayError) as raised:
        OpenAICompatibleGateway(settings, client=_client(completions)).generate(_request())
    assert raised.value.code == "invalid_output"
    assert raised.value.retryable is False
