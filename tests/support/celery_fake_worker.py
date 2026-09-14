"""Synthetic Compose smoke entrypoint: force deterministic embedding and disabled AI."""

import os

from app.tasks import celery_app as module
from tests.support.application import DeterministicEmbeddingAdapter


def main():
    import sys

    settings = module.Settings.load()
    if settings.ai_enabled or settings.api_key or os.environ.get("HF_HUB_OFFLINE") != "1":
        raise RuntimeError("synthetic_worker_requires_disabled_models")
    original = module.build_worker_dependencies

    def fake_dependencies(settings):
        return original(settings, embedder=DeterministicEmbeddingAdapter())

    module.build_worker_dependencies = fake_dependencies
    module.celery_app.worker_main(["worker", "--pool=solo", "--concurrency=1", "--loglevel=WARNING", *sys.argv[1:]])


if __name__ == "__main__":
    main()
