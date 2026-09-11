from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migration_head_is_artifact_maintenance_revision():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_current_head() == "20260911_17"
