from __future__ import annotations

import importlib
import importlib.util


def test_production_entrypoints_import_after_legacy_index_removal():
    """Catches a stranded API, worker, matching, or evaluation import after cutover."""
    for module in (
        "app.main",
        "app.tasks.celery_app",
        "app.ai.semantic_matching",
        "app.services.matching",
        "scripts.evaluate_ai_pipeline",
    ):
        importlib.import_module(module)

    legacy_module = ".".join(("app", "knowledge", "index"))
    assert importlib.util.find_spec(legacy_module) is None
