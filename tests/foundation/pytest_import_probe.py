from app.artifacts.ports import ArtifactLocation


def test_application_import_is_available_to_pytest_entrypoint():
    assert ArtifactLocation("t", "resumes", "r", "a").tenant_id == "t"
