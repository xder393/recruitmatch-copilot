from __future__ import annotations

from datetime import datetime, timezone

from app.repositories.knowledge import KnowledgeRepository
from app.repositories.resumes import ResumeRepository
from app.retrieval.ports import SearchScope
from tests.retrieval.test_retrieval_contract import TENANT

pytest_plugins = ("tests.retrieval.test_pgvector_search",)


def test_resume_privacy_scrub_removes_current_and_historical_citations(pg_index):
    """Catches privacy deletion leaving any generation resolvable for audit."""
    scope = SearchScope(
        TENANT,
        frozenset({"resume"}),
        frozenset({("resume", "resume-1", "resume-v1")}),
    )
    before = pg_index.resolve_historical_citations(TENANT, frozenset({"citation-a", "inactive"}))
    assert {item.citation_id for item in before} == {"citation-a", "inactive"}

    with pg_index.session_factory() as session:
        repository = ResumeRepository(session)
        resume = repository.get(TENANT, "resume-1", include_deleted=True)
        repository.scrub_private_data(TENANT, resume, datetime.now(timezone.utc))
        session.commit()

    assert pg_index.resolve_active_citations(scope, frozenset({"citation-a"})) == []
    assert pg_index.resolve_historical_citations(TENANT, frozenset({"citation-a", "inactive"})) == []


def test_knowledge_deactivation_removes_current_but_preserves_authorized_audit(pg_index):
    """Catches inactive knowledge entering new grounding or being erased from allowed audit."""
    scope = SearchScope(
        TENANT,
        frozenset({"knowledge_document"}),
        frozenset({("knowledge_document", "knowledge-authority", "knowledge-authority-v1")}),
    )
    with pg_index.session_factory() as session:
        repository = KnowledgeRepository(session)
        document = repository.get_document(TENANT, "knowledge-authority")
        repository.deactivate(document)
        session.commit()

    assert pg_index.resolve_active_citations(scope, frozenset({"knowledge-authority"})) == []
    historical = pg_index.resolve_historical_citations(TENANT, frozenset({"knowledge-authority"}))
    assert [item.citation_id for item in historical] == ["knowledge-authority"]
