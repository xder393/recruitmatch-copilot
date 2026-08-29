"""Shared retrieval-port contract, exercised first by the deterministic fake."""

from __future__ import annotations

import math

import pytest

from app.retrieval import SearchScope
from tests.fakes.retrieval import FakeRecruitingChunk, FakeRecruitingVectorIndex


MODEL = "bge-small-zh-v1.5@512"
TENANT = "tenant-a"


def unit_vector(x: float = 1.0, y: float = 0.0) -> list[float]:
    return [x, y, *([0.0] * 510)]


def authorized_scope() -> SearchScope:
    return SearchScope(
        tenant_id=TENANT,
        source_types=frozenset({"resume", "job_version"}),
        authorized_sources=frozenset(
            {
                ("resume", "resume-1", "resume-v1"),
                ("resume", "resume-pending", "resume-pending-v1"),
                ("resume", "resume-failed", "resume-failed-v1"),
                ("resume", "resume-deleted", "resume-deleted-v1"),
                ("job_version", "job-version-1", "1"),
            }
        ),
    )


def contract_rows() -> list[FakeRecruitingChunk]:
    common = {
        "tenant_id": TENANT,
        "source_type": "resume",
        "source_id": "resume-1",
        "source_version": "resume-v1",
        "generation": 2,
        "active_source_generation": 2,
        "embedding_model": MODEL,
        "is_active": True,
        "content": "authorized evidence",
    }
    rows = [
        FakeRecruitingChunk(id="chunk-b", citation_id="citation-b", embedding=unit_vector(), **common),
        FakeRecruitingChunk(id="chunk-a", citation_id="citation-a", embedding=unit_vector(), **common),
        FakeRecruitingChunk(
            id="other-tenant",
            tenant_id="tenant-b",
            citation_id="other-tenant",
            source_type="resume",
            source_id="resume-1",
            source_version="resume-v1",
            generation=2,
            active_source_generation=2,
            embedding=unit_vector(),
            embedding_model=MODEL,
            is_active=True,
            content="closer but cross tenant",
        ),
        FakeRecruitingChunk(
            id="unauthorized",
            citation_id="unauthorized",
            source_id="resume-2",
            embedding=unit_vector(),
            **{key: value for key, value in common.items() if key != "source_id"},
        ),
        FakeRecruitingChunk(
            id="inactive",
            citation_id="inactive",
            embedding=unit_vector(),
            is_active=False,
            **{key: value for key, value in common.items() if key != "is_active"},
        ),
        FakeRecruitingChunk(
            id="stale-authority",
            citation_id="stale-authority",
            embedding=unit_vector(),
            generation=1,
            **{key: value for key, value in common.items() if key != "generation"},
        ),
        FakeRecruitingChunk(
            id="wrong-model",
            citation_id="wrong-model",
            embedding=unit_vector(),
            embedding_model="other-model@512",
            **{key: value for key, value in common.items() if key != "embedding_model"},
        ),
        FakeRecruitingChunk(
            id="pending-source",
            citation_id="pending-source",
            source_id="resume-pending",
            source_version="resume-pending-v1",
            embedding=unit_vector(),
            source_search_index_status="pending",
            **{key: value for key, value in common.items() if key not in {"source_id", "source_version"}},
        ),
        FakeRecruitingChunk(
            id="failed-source",
            citation_id="failed-source",
            source_id="resume-failed",
            source_version="resume-failed-v1",
            embedding=unit_vector(),
            source_search_index_status="failed",
            **{key: value for key, value in common.items() if key not in {"source_id", "source_version"}},
        ),
        FakeRecruitingChunk(
            id="deleted-source",
            citation_id="deleted-source",
            source_id="resume-deleted",
            source_version="resume-deleted-v1",
            embedding=unit_vector(),
            source_search_index_status="deleted",
            **{key: value for key, value in common.items() if key not in {"source_id", "source_version"}},
        ),
        FakeRecruitingChunk(
            id="inactive-source",
            citation_id="inactive-source",
            source_id="resume-inactive",
            source_version="resume-inactive-v1",
            embedding=unit_vector(),
            source_search_index_status="inactive",
            **{key: value for key, value in common.items() if key not in {"source_id", "source_version"}},
        ),
        FakeRecruitingChunk(
            id="empty-content",
            citation_id="empty-content",
            embedding=unit_vector(),
            content="",
            **{key: value for key, value in common.items() if key != "content"},
        ),
    ]
    return rows


class RetrievalContract:
    """Behavior shared by the fake and real PostgreSQL adapter."""

    index: object

    def test_search_keeps_every_security_predicate_with_vector_ordering(self) -> None:
        hits = self.index.search(authorized_scope(), unit_vector(), MODEL, top_k=10, min_score=0.0)

        assert [(hit.id, hit.citation_id, hit.score) for hit in hits] == [
            ("chunk-a", "citation-a", 1.0),
            ("chunk-b", "citation-b", 1.0),
        ]

    def test_search_applies_score_then_top_k_without_broadening_scope(self) -> None:
        hits = self.index.search(authorized_scope(), unit_vector(), MODEL, top_k=1, min_score=0.9)

        assert [hit.id for hit in hits] == ["chunk-a"]

    def test_empty_authorization_is_empty(self) -> None:
        scope = SearchScope(TENANT, frozenset({"resume"}), frozenset())

        assert self.index.search(scope, unit_vector(), MODEL, top_k=5, min_score=-1.0) == []
        assert self.index.resolve_active_citations(scope, frozenset({"citation-a"})) == []

    @pytest.mark.parametrize(
        ("embedding", "message"),
        [
            ([1.0], "exactly 512"),
            (unit_vector(2.0), "L2-normalized"),
            (unit_vector(float("nan")), "finite"),
            (unit_vector(float("inf")), "finite"),
        ],
    )
    def test_search_rejects_invalid_embedding(self, embedding: list[float], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            self.index.search(authorized_scope(), embedding, MODEL, top_k=5, min_score=0.0)

    @pytest.mark.parametrize(
        ("scope", "model", "top_k", "min_score", "message"),
        [
            (SearchScope("", frozenset({"resume"}), frozenset()), MODEL, 1, 0.0, "tenant"),
            (SearchScope(TENANT, frozenset(), frozenset()), MODEL, 1, 0.0, "source type"),
            (SearchScope(TENANT, frozenset({"resume"}), frozenset()), "", 1, 0.0, "model"),
            (authorized_scope(), MODEL, 0, 0.0, "top_k"),
            (authorized_scope(), MODEL, 1.5, 0.0, "top_k"),
            (authorized_scope(), MODEL, 1, 1.01, "min_score"),
            (authorized_scope(), MODEL, 1, math.nan, "min_score"),
        ],
    )
    def test_search_rejects_invalid_request(
        self,
        scope: SearchScope,
        model: str,
        top_k: int,
        min_score: float,
        message: str,
    ) -> None:
        with pytest.raises(ValueError, match=message):
            self.index.search(scope, unit_vector(), model, top_k=top_k, min_score=min_score)

    def test_citation_operations_reject_blank_identifiers(self) -> None:
        with pytest.raises(ValueError, match="citation"):
            self.index.resolve_active_citations(authorized_scope(), frozenset({""}))
        with pytest.raises(ValueError, match="tenant"):
            self.index.resolve_historical_citations("", frozenset({"citation-a"}))
        with pytest.raises(ValueError, match="citation"):
            self.index.resolve_historical_citations(TENANT, frozenset({""}))

    def test_citations_separate_current_and_historical_evidence(self) -> None:
        active = self.index.resolve_active_citations(
            authorized_scope(),
            frozenset({"citation-b", "citation-a", "inactive", "stale-authority", "unknown"}),
        )
        historical = self.index.resolve_historical_citations(
            TENANT,
            frozenset(
                {
                    "citation-b",
                    "citation-a",
                    "inactive",
                    "stale-authority",
                    "pending-source",
                    "failed-source",
                    "inactive-source",
                    "unknown",
                }
            ),
        )

        assert [hit.id for hit in active] == ["chunk-a", "chunk-b"]
        assert [hit.id for hit in historical] == [
            "chunk-a",
            "chunk-b",
            "failed-source",
            "inactive",
            "inactive-source",
            "pending-source",
            "stale-authority",
        ]

    def test_citations_are_tenant_bound_deterministic_and_privacy_safe(self) -> None:
        historical = self.index.resolve_historical_citations(
            TENANT,
            frozenset(
                {
                    "citation-b",
                    "citation-a",
                    "privacy-deleted",
                    "deleted-source",
                    "empty-content",
                    "other-tenant",
                }
            ),
        )

        assert [hit.id for hit in historical] == ["chunk-a", "chunk-b"]


class TestFakeRetrievalContract(RetrievalContract):
    def setup_method(self) -> None:
        rows = contract_rows()
        rows.append(
            FakeRecruitingChunk(
                id="privacy-deleted",
                tenant_id=TENANT,
                citation_id="privacy-deleted",
                source_type="resume",
                source_id="resume-1",
                source_version="resume-v1",
                generation=1,
                active_source_generation=2,
                embedding=unit_vector(),
                embedding_model=MODEL,
                is_active=False,
                content="must never resolve",
                privacy_deleted=True,
            )
        )
        self.index = FakeRecruitingVectorIndex(rows)
