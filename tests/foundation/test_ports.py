from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.artifacts.ports import ArtifactLocation
from app.observability.events import DomainEvent
from app.services.knowledge_processing import KnowledgeProcessingService
from app.tasks.dispatcher import (
    CeleryTaskDispatcher,
    InlineTaskDispatcher,
    TaskDispatcher,
    build_task_dispatcher,
    configure_task_dispatcher,
)


def test_artifact_location_is_structured_and_immutable():
    location = ArtifactLocation("t1", "resumes", "r1", "a1")
    assert location.namespace == "resumes"
    try:
        location.artifact_id = "changed"
    except Exception:
        pass
    assert location.artifact_id == "a1"


def test_domain_event_has_bounded_attributes():
    attributes = {"outcome": "success"}
    event = DomainEvent("matching.completed", attributes)
    attributes["outcome"] = "failed"

    assert event.name == "matching.completed"
    assert event.attributes == {"outcome": "success"}
    with pytest.raises(TypeError):
        event.attributes["outcome"] = "failed"  # type: ignore[index]


def test_application_services_do_not_import_sqlalchemy():
    service_text = "\n".join(path.read_text() for path in Path("app/services").glob("*.py"))
    assert "from sqlalchemy" not in service_text
    assert "import sqlalchemy" not in service_text


def test_pytest_entrypoint_imports_application_from_project_root():
    result = subprocess.run(
        [sys.executable.rsplit("/", 1)[0] + "/pytest", "tests/foundation/pytest_import_probe.py", "-q"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_contextmanager_uow_factory_is_preserved():
    class FakeUnitOfWork:
        def commit(self):
            return None

        def rollback(self):
            return None

    class FakeFactory:
        def __init__(self):
            self.uow = FakeUnitOfWork()

        def __call__(self):
            from contextlib import nullcontext

            return nullcontext(self.uow)

    factory = FakeFactory()
    service = KnowledgeProcessingService(factory, artifact_store=object(), source_indexer=object())

    assert service.uow_factory is factory
    with service.uow_factory() as uow:
        assert uow is factory.uow


def test_production_dispatchers_conform_to_the_full_protocol():
    class Processor:
        def process(self, tenant_id, owner_id):
            return None

    inline = build_task_dispatcher("inline", Processor(), Processor())
    celery = build_task_dispatcher("celery", Processor(), Processor())

    assert isinstance(inline, InlineTaskDispatcher)
    assert isinstance(celery, CeleryTaskDispatcher)
    assert isinstance(inline, TaskDispatcher)
    assert isinstance(celery, TaskDispatcher)

    app = SimpleNamespace(state=SimpleNamespace())
    configured = configure_task_dispatcher(app, "inline", Processor(), Processor())
    assert app.state.task_dispatcher is configured
    assert app.state.knowledge_dispatcher is configured
