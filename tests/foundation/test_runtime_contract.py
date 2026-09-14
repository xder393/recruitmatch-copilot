from pathlib import Path
import tomllib


def test_python_and_dependency_authority_are_frozen():
    root = Path(__file__).parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert (root / ".python-version").read_text().strip() == "3.12"
    assert project["project"]["requires-python"] == ">=3.12,<3.13"
    assert {"dev", "eval", "load"} <= set(project["dependency-groups"])
    assert (root / "uv.lock").is_file()
    assert not (root / "requirements.txt").exists()


def test_pytest_loads_the_shared_repository_configuration(pytestconfig):
    root = Path(__file__).resolve().parents[2]
    assert pytestconfig.inipath == root / "pytest.ini", "test_image_configuration_differs_from_checkout"
