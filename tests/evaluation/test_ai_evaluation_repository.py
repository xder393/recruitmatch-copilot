from __future__ import annotations

from app.database import Base, create_engine_and_session
from app.models.evaluation import AIEvaluationCase, AIEvaluationRun
from app.models.identity import Tenant
from app.repositories.evaluation import AIEvaluationRepository


def _run(tenant_id, algorithm):
    return AIEvaluationRun(
        tenant_id=tenant_id,
        dataset_version="tenant-safe-v1",
        algorithm_version=algorithm,
        model_version="fake",
        prompt_version="v1",
        embedding_version="fake",
        case_count=1,
        metrics={"top3_recall": 1},
        cases=[
            AIEvaluationCase(
                case_key="case-1",
                passed=False,
                failure_categories=["grounding_rejected"],
            )
        ],
    )


def test_evaluation_repository_requires_and_filters_tenant(tmp_path):
    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'evaluations.db'}")
    Base.metadata.create_all(engine)
    with factory() as session:
        acme, globex = Tenant(name="Acme"), Tenant(name="Globex")
        session.add_all([acme, globex])
        session.commit()
        repository = AIEvaluationRepository(session)
        repository.add(_run(acme.id, "hybrid-v1"))
        repository.add(_run(globex.id, "rules-v1"))
        runs = repository.list_for_tenant(acme.id)
        assert [run.algorithm_version for run in runs] == ["hybrid-v1"]
        assert runs[0].cases[0].failure_categories == ["grounding_rejected"]


def test_synthetic_cli_result_can_persist_safe_tenant_records(tmp_path):
    import json
    from pathlib import Path
    from scripts.evaluate_ai_pipeline import evaluate, persist_result

    database_url = f"sqlite:///{tmp_path / 'persist.db'}"
    engine, factory = create_engine_and_session(database_url)
    Base.metadata.create_all(engine)
    with factory() as session:
        tenant = Tenant(name="Acme")
        session.add(tenant)
        session.commit()
        tenant_id = tenant.id
    cases = json.loads(Path("evaluation/recruitmatch-ai-v1.json").read_text(encoding="utf-8"))[:2]
    result = evaluate(cases, "hybrid-v1")
    run_id = persist_result(result, database_url, tenant_id)
    with factory() as session:
        runs = AIEvaluationRepository(session).list_for_tenant(tenant_id)
        assert runs[0].id == run_id
        assert len(runs[0].cases) == 2
        assert not hasattr(runs[0].cases[0], "resume_text")
