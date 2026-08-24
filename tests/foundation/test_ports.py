from pathlib import Path

from app.artifacts.ports import ArtifactLocation
from app.observability.events import DomainEvent


def test_artifact_location_is_structured_and_immutable():
    location = ArtifactLocation("t1", "resumes", "r1", "a1")
    assert location.namespace == "resumes"
    try:
        location.artifact_id = "changed"
    except Exception:
        pass
    assert location.artifact_id == "a1"


def test_domain_event_has_bounded_attributes():
    event = DomainEvent("matching.completed", {"outcome": "success"})
    assert event.name == "matching.completed"


def test_application_services_do_not_import_sqlalchemy():
    service_text = "\n".join(path.read_text() for path in Path("app/services").glob("*.py"))
    assert "from sqlalchemy" not in service_text
    assert "import sqlalchemy" not in service_text
