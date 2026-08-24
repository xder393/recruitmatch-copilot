"""OpenAI-compatible structured output gateway."""

from __future__ import annotations

import time
from typing import Any, Protocol, cast

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)

from app.ai.contracts import ModelRequest, ModelResponse, T
from app.config import Settings


class StructuredCompletionsClient(Protocol):
    def parse(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        response_format: type[T],
    ) -> Any: ...


class ChatClient(Protocol):
    completions: StructuredCompletionsClient


class BetaClient(Protocol):
    chat: ChatClient


class OpenAIClient(Protocol):
    beta: BetaClient


class ModelGatewayError(Exception):
    def __init__(self, code: str, retryable: bool, attempts: int = 1):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.attempts = attempts


class OpenAICompatibleGateway:
    def __init__(self, settings: Settings, client: OpenAIClient | None = None):
        self.settings = settings
        self.client = client or cast(
            OpenAIClient,
            OpenAI(
                api_key=settings.api_key,
                base_url=settings.base_url,
                timeout=settings.model_timeout_seconds,
                max_retries=0,
            ),
        )

    def generate(self, request: ModelRequest[T]) -> ModelResponse[T]:
        attempts = self.settings.model_max_retries + 1
        error: ModelGatewayError
        cause: BaseException
        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                completion = self.client.beta.chat.completions.parse(
                    model=self.settings.chat_model,
                    messages=[
                        {"role": "system", "content": request.system},
                        {"role": "user", "content": request.user},
                    ],
                    temperature=self.settings.llm_temperature,
                    response_format=request.schema,
                )
                parsed = completion.choices[0].message.parsed
                if parsed is None:
                    raise ModelGatewayError("invalid_output", retryable=False)
                usage = completion.usage
                input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                cost = (
                    input_tokens * self.settings.model_input_cost_per_million
                    + output_tokens * self.settings.model_output_cost_per_million
                ) / 1_000_000
                return ModelResponse(
                    value=request.schema.model_validate(parsed),
                    provider=self.settings.model_provider,
                    model=self.settings.chat_model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    estimated_cost=cost,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    attempts=attempt + 1,
                )
            except ModelGatewayError as exc:
                exc.attempts = attempt + 1
                raise
            except AuthenticationError as exc:
                raise ModelGatewayError("authentication_failed", retryable=False) from exc
            except RateLimitError as exc:
                error = ModelGatewayError("rate_limited", retryable=True)
                cause = exc
            except APITimeoutError as exc:
                error = ModelGatewayError("timeout", retryable=True)
                cause = exc
            except APIConnectionError as exc:
                error = ModelGatewayError("transport_error", retryable=True)
                cause = exc
            except APIStatusError as exc:
                retryable = exc.status_code >= 500 or exc.status_code in {408, 429}
                error = ModelGatewayError("provider_unavailable" if retryable else "provider_rejected", retryable)
                cause = exc
            except (LengthFinishReasonError, ContentFilterFinishReasonError) as exc:
                raise ModelGatewayError("invalid_output", retryable=False) from exc
            except TimeoutError as exc:
                error = ModelGatewayError("timeout", retryable=True)
                cause = exc
            except OpenAIError as exc:
                raise ModelGatewayError("provider_error", retryable=False) from exc
            except (ValueError, TypeError, IndexError, AttributeError) as exc:
                raise ModelGatewayError("invalid_output", retryable=False) from exc
            if attempt + 1 >= attempts:
                error.attempts = attempt + 1
                raise error from cause
        raise RuntimeError("unreachable")
