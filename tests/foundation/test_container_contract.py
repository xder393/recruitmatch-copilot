import re
from pathlib import Path


def test_runtime_image_declares_non_root_user():
    dockerfile = Path("Dockerfile").read_text()
    assert "FROM " in dockerfile and " AS builder" in dockerfile
    assert "USER recruitmatch" in dockerfile
    assert "requirements.txt" not in dockerfile


def test_non_api_compose_targets_disable_the_api_healthcheck():
    compose = Path("docker-compose.yml").read_text()
    service_names = ("bootstrap", "worker", "test-unit", "test-integration")

    for service_name in service_names:
        service = re.search(rf"(?ms)^  {service_name}:\n(?P<body>.*?)(?=^  \S|\Z)", compose)
        assert service is not None
        assert "healthcheck:\n      disable: true" in service.group("body")


def test_compose_uses_a_versioned_non_root_hf_cache_volume():
    compose = Path("docker-compose.yml").read_text()
    mount = "- hf-cache-v2:/home/recruitmatch/.cache/huggingface"
    assert compose.count(mount) == 2
    assert "\n  hf-cache-v2:\n" in compose
    assert "- hf-cache:/home/recruitmatch/.cache/huggingface" not in compose


def test_image_excludes_removed_legacy_namespaces():
    for namespace in ("agents", "rag", "storage", "tools", "embeddings"):
        assert not Path("app", namespace).exists()
