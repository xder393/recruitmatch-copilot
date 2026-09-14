"""Test-image-only real API/prefork composition; no provider/network model calls."""

import os
import re
import time
from types import SimpleNamespace

from app.ai.gateway import OpenAICompatibleGateway
from app.config import Settings
from app.resumes.schemas import Evidence, ResumeProfile, SkillEvidence

MODEL_SENTINEL = "CP5_PRIVATE_MODEL_RESPONSE_9dc850"
KEY_SENTINEL = "CP5_PRIVATE_CREDENTIAL_9dc850"


class SyntheticClient:
    def __init__(self):
        self.beta = SimpleNamespace(chat=SimpleNamespace(completions=self))

    def parse(self, *, model, messages, temperature, response_format):
        from app.ai.semantic_matching import SemanticProjectScore
        from app.ai.explanations import GroundedClaim, GroundedModelOutput

        # A bounded delay ensures concurrent uploads exercise both prefork children.
        time.sleep(0.3)
        user = messages[-1]["content"]
        if response_format is ResumeProfile:
            value = ResumeProfile(
                skills=[SkillEvidence(name="Python", evidence=Evidence(start=0, end=6, text="Python"))],
                education_level=MODEL_SENTINEL,
            )
        elif response_format is SemanticProjectScore:
            ids = re.findall(r"\[citation:([^\]]+)\]", user)
            value = SemanticProjectScore(
                score=80, rationale=MODEL_SENTINEL, resume_citation_ids=ids[:1], job_citation_ids=ids[-1:]
            )
        elif response_format is GroundedModelOutput:
            ids = re.findall(r"\[citation:([^\]]+)\]", user)
            value = GroundedModelOutput(summary=GroundedClaim(text=MODEL_SENTINEL, citation_ids=ids[:1]))
        else:
            raise ValueError("unsupported_synthetic_schema")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=value))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
        )


def settings():
    value = Settings.load()
    if (
        os.environ.get("TELEMETRY_E2E") != "1"
        or os.environ.get("HF_HUB_OFFLINE") != "1"
        or os.environ.get("TRANSFORMERS_OFFLINE") != "1"
        or value.api_key != KEY_SENTINEL
        or value.base_url != "http://127.0.0.1:9"
        or not value.ai_enabled
        or value.task_mode != "celery"
    ):
        raise RuntimeError("synthetic_telemetry_prerequisites_required")
    return value


def gateway(value):
    return OpenAICompatibleGateway(value, client=SyntheticClient())


def create_api():
    from app.main import create_app
    from tests.support.application import DeterministicEmbeddingAdapter

    value = settings()
    return create_app(value, structured_model=gateway(value), knowledge_embedder=DeterministicEmbeddingAdapter())


def main():
    from app.tasks import celery_app as module
    from tests.support.application import DeterministicEmbeddingAdapter

    settings()
    original = module.build_worker_dependencies
    module.OpenAICompatibleGateway = gateway

    def dependencies(value):
        return original(value, embedder=DeterministicEmbeddingAdapter())

    module.build_worker_dependencies = dependencies
    module.celery_app.start(["--quiet", "worker", "--pool=prefork", "--concurrency=2", "--loglevel=INFO"])


if __name__ == "__main__":
    main()
