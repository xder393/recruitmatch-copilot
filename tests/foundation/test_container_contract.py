import re
from pathlib import Path
import yaml


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
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    for service in ("api", "worker", "beat"):
        assert "hf-cache-v2:/home/recruitmatch/.cache/huggingface" in compose["services"][service]["volumes"]
    assert "hf-cache-v2" in compose["volumes"]
    assert "hf-cache" not in compose["volumes"]


def test_runtime_services_wait_for_one_shot_initialization():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    required_dependencies = {
        "minio-init": {"condition": "service_completed_successfully"},
        "bootstrap": {"condition": "service_completed_successfully"},
    }

    for service_name in ("api", "worker", "beat"):
        dependencies = compose["services"][service_name]["depends_on"]
        assert {
            dependency: dependencies.get(dependency) for dependency in required_dependencies
        } == required_dependencies


def test_image_excludes_removed_legacy_namespaces():
    for namespace in ("agents", "rag", "storage", "tools", "embeddings"):
        assert not Path("app", namespace).exists()
