"""PostgreSQL/pgvector adapter for authorized recruiting evidence retrieval."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Callable, Sequence

from sqlalchemy import Select, and_, cast, exists, func, literal, or_, select, text, tuple_
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement
from sqlalchemy.types import String

from app.domain.enums import JobStatus
from app.models.jobs import Job, JobVersion
from app.models.knowledge import KnowledgeDocument
from app.models.retrieval import RecruitingChunk
from app.models.resumes import Resume
from app.retrieval.ports import (
    RetrievedChunk,
    SearchScope,
    validate_citation_ids,
    validate_scope,
    validate_search_request,
)


DEFAULT_EXACT_SEARCH_MAX_CANDIDATES = 10_000
DEFAULT_ANN_CANDIDATE_MULTIPLIER = 4
DEFAULT_ANN_CANDIDATE_BUDGET_MAX = 256


@dataclass(frozen=True)
class VectorSearchPlan:
    strategy: str
    authorized_candidate_count: int
    plan: str
    statement: str
    ann_candidate_budget: int | None


class PgVectorRecruitingIndex:
    """Keep authorization predicates and cosine ordering in one SQL statement."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        exact_search_max_candidates: int | None = None,
        ann_candidate_multiplier: int | None = None,
        ann_candidate_budget_max: int | None = None,
    ):
        configured_threshold = int(
            os.getenv("VECTOR_EXACT_SEARCH_MAX_CANDIDATES", str(DEFAULT_EXACT_SEARCH_MAX_CANDIDATES))
        )
        threshold = configured_threshold if exact_search_max_candidates is None else exact_search_max_candidates
        if threshold < 0:
            raise ValueError("exact_search_max_candidates must be non-negative")
        configured_multiplier = int(os.getenv("VECTOR_ANN_CANDIDATE_MULTIPLIER", str(DEFAULT_ANN_CANDIDATE_MULTIPLIER)))
        configured_budget_max = int(os.getenv("VECTOR_ANN_CANDIDATE_BUDGET_MAX", str(DEFAULT_ANN_CANDIDATE_BUDGET_MAX)))
        multiplier = configured_multiplier if ann_candidate_multiplier is None else ann_candidate_multiplier
        budget_max = configured_budget_max if ann_candidate_budget_max is None else ann_candidate_budget_max
        if multiplier <= 0:
            raise ValueError("ann_candidate_multiplier must be positive")
        if budget_max <= 0:
            raise ValueError("ann_candidate_budget_max must be positive")
        self.session_factory = session_factory
        self.exact_search_max_candidates = threshold
        self.ann_candidate_multiplier = multiplier
        self.ann_candidate_budget_max = budget_max

    def search(
        self,
        scope: SearchScope,
        query_embedding: list[float],
        embedding_model: str,
        top_k: int,
        min_score: float,
    ) -> list[RetrievedChunk]:
        validate_search_request(scope, query_embedding, embedding_model, top_k, min_score)
        if not scope.authorized_sources:
            return []
        with self.session_factory() as session:
            self._begin_repeatable_read(session)
            statement, _, strategy, _ = self._prepare_search(
                session, scope, query_embedding, embedding_model, top_k, min_score
            )
            self._configure_search_strategy(session, strategy)
            rows = session.execute(statement).all()
        return [self._result(chunk, float(score)) for chunk, score in rows]

    def resolve_active_citations(
        self,
        scope: SearchScope,
        citation_ids: frozenset[str],
    ) -> list[RetrievedChunk]:
        validate_scope(scope)
        validate_citation_ids(citation_ids)
        if not scope.authorized_sources or not citation_ids:
            return []
        predicates = [
            RecruitingChunk.tenant_id == scope.tenant_id,
            RecruitingChunk.citation_id.in_(sorted(citation_ids)),
            RecruitingChunk.content != "",
            RecruitingChunk.source_type.in_(sorted(scope.source_types)),
            self._authorization_predicate(scope),
            RecruitingChunk.is_active == literal(True),
            self._source_authority_predicate(active_only=True),
        ]
        return self._resolve(predicates)

    def resolve_historical_citations(
        self,
        tenant_id: str,
        citation_ids: frozenset[str],
    ) -> list[RetrievedChunk]:
        if not tenant_id.strip():
            raise ValueError("tenant_id must be non-empty")
        validate_citation_ids(citation_ids)
        if not citation_ids:
            return []
        return self._resolve(
            [
                RecruitingChunk.tenant_id == tenant_id,
                RecruitingChunk.citation_id.in_(sorted(citation_ids)),
                RecruitingChunk.content != "",
                self._source_authority_predicate(active_only=False),
            ]
        )

    def explain_search(
        self,
        scope: SearchScope,
        query_embedding: list[float],
        embedding_model: str,
        top_k: int,
        min_score: float,
    ) -> VectorSearchPlan:
        """Return reproducible planner evidence; it deliberately makes no Recall claim."""
        validate_search_request(scope, query_embedding, embedding_model, top_k, min_score)
        if not scope.authorized_sources:
            return VectorSearchPlan("exact", 0, "empty authorized scope", "", None)
        with self.session_factory() as session:
            self._begin_repeatable_read(session)
            statement, candidate_count, strategy, candidate_budget = self._prepare_search(
                session, scope, query_embedding, embedding_model, top_k, min_score
            )
            self._configure_explain_strategy(session, strategy)
            compiled = statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True, "render_postcompile": True},
            )
            statement_sql = str(compiled)
            plan_lines = session.execute(text(f"EXPLAIN (COSTS OFF) {statement_sql}")).scalars().all()
        return VectorSearchPlan(strategy, candidate_count, "\n".join(plan_lines), statement_sql, candidate_budget)

    def _prepare_search(
        self,
        session: Session,
        scope: SearchScope,
        query_embedding: list[float],
        embedding_model: str,
        top_k: int,
        min_score: float,
    ) -> tuple[Select, int, str, int | None]:
        base_predicates = self._search_predicates(scope, embedding_model)
        candidate_count = int(
            session.scalar(select(func.count()).select_from(RecruitingChunk).where(*base_predicates)) or 0
        )
        strategy = "exact" if candidate_count <= self.exact_search_max_candidates else "hnsw"
        candidate_budget = None
        distance = RecruitingChunk.embedding.cosine_distance(query_embedding)
        score = (literal(1.0) - distance).label("score")
        if strategy == "exact":
            exact_candidates = (
                select(RecruitingChunk.id.label("chunk_id"), distance.label("distance"))
                .where(*base_predicates, score >= min_score)
                .cte("exact_candidates")
                .prefix_with("MATERIALIZED")
            )
            exact_score = (literal(1.0) - exact_candidates.c.distance).label("score")
            statement = (
                select(RecruitingChunk, exact_score)
                .join(exact_candidates, exact_candidates.c.chunk_id == RecruitingChunk.id)
                .order_by(exact_candidates.c.distance, RecruitingChunk.id)
                .limit(top_k)
            )
        else:
            candidate_budget = min(
                candidate_count,
                max(
                    top_k,
                    min(self.ann_candidate_budget_max, top_k * self.ann_candidate_multiplier),
                ),
            )
            # ANN defines a bounded, approximate seed. Expand every authorized
            # row tied at its exact boundary distance so the outer exact rerank
            # has a global ID tie-break at that boundary. Rows at distances the
            # approximate seed missed still have no Recall guarantee.
            ann_seed = (
                select(RecruitingChunk.id.label("chunk_id"), distance.label("distance"))
                .where(*base_predicates)
                .order_by(distance)
                .limit(candidate_budget)
                .cte("ann_seed")
                .prefix_with("MATERIALIZED")
            )
            boundary_distance = select(func.max(ann_seed.c.distance)).scalar_subquery()
            boundary_ties = select(
                RecruitingChunk.id.label("chunk_id"),
                distance.label("distance"),
            ).where(
                *base_predicates,
                distance == boundary_distance,
                RecruitingChunk.id.not_in(select(ann_seed.c.chunk_id)),
            )
            ann_candidates = (
                select(ann_seed.c.chunk_id, ann_seed.c.distance)
                .union_all(boundary_ties)
                .cte("ann_candidates")
                .prefix_with("MATERIALIZED")
            )
            ann_score = (literal(1.0) - ann_candidates.c.distance).label("score")
            statement = (
                select(RecruitingChunk, ann_score)
                .join(ann_candidates, ann_candidates.c.chunk_id == RecruitingChunk.id)
                .where(ann_score >= min_score)
                .order_by(ann_candidates.c.distance, RecruitingChunk.id)
                .limit(top_k)
            )
        return statement, candidate_count, strategy, candidate_budget

    @staticmethod
    def _begin_repeatable_read(session: Session) -> None:
        # COUNT-based strategy selection and retrieval must observe one source
        # generation snapshot; setting isolation here precedes the first query.
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})

    @staticmethod
    def _configure_search_strategy(session: Session, strategy: str) -> None:
        if strategy == "hnsw":
            session.execute(text("SET LOCAL enable_seqscan = off"))
            session.execute(text("SET LOCAL enable_sort = off"))
            session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))

    @classmethod
    def _configure_explain_strategy(cls, session: Session, strategy: str) -> None:
        if strategy == "exact":
            # Diagnostic-only: demonstrate that the authorized exact query can use
            # the tenant/source B-tree access paths without changing real search.
            session.execute(text("SET LOCAL enable_seqscan = off"))
            return
        cls._configure_search_strategy(session, strategy)

    def _search_predicates(self, scope: SearchScope, embedding_model: str) -> list[ColumnElement[bool]]:
        return [
            RecruitingChunk.tenant_id == scope.tenant_id,
            RecruitingChunk.is_active == literal(True),
            RecruitingChunk.embedding_model == embedding_model,
            RecruitingChunk.content != "",
            RecruitingChunk.source_type.in_(sorted(scope.source_types)),
            self._authorization_predicate(scope),
            self._source_authority_predicate(active_only=True),
        ]

    @staticmethod
    def _authorization_predicate(scope: SearchScope) -> ColumnElement[bool]:
        return tuple_(
            RecruitingChunk.source_type,
            RecruitingChunk.source_id,
            RecruitingChunk.source_version,
        ).in_(sorted(scope.authorized_sources))

    @staticmethod
    def _source_authority_predicate(*, active_only: bool) -> ColumnElement[bool]:
        resume_predicates = [
            Resume.id == RecruitingChunk.source_id,
            Resume.tenant_id == RecruitingChunk.tenant_id,
            Resume.sha256 == RecruitingChunk.source_version,
            Resume.deleted_at.is_(None),
            Resume.lifecycle_status == "active",
        ]
        job_predicates = [
            JobVersion.id == RecruitingChunk.source_id,
            cast(JobVersion.version, String) == RecruitingChunk.source_version,
            Job.id == JobVersion.job_id,
            Job.tenant_id == RecruitingChunk.tenant_id,
        ]
        knowledge_predicates = [
            KnowledgeDocument.id == RecruitingChunk.source_id,
            KnowledgeDocument.tenant_id == RecruitingChunk.tenant_id,
            KnowledgeDocument.checksum == RecruitingChunk.source_version,
            KnowledgeDocument.lifecycle_status == "active",
        ]
        if active_only:
            resume_predicates.append(Resume.active_index_generation == RecruitingChunk.generation)
            resume_predicates.append(Resume.search_index_status == "ready")
            job_predicates.append(Job.status != JobStatus.INACTIVE)
            job_predicates.append(JobVersion.active_index_generation == RecruitingChunk.generation)
            job_predicates.append(JobVersion.search_index_status == "ready")
            knowledge_predicates.append(KnowledgeDocument.status.not_in({"inactive", "deleted"}))
            knowledge_predicates.append(KnowledgeDocument.active_index_generation == RecruitingChunk.generation)
            knowledge_predicates.append(KnowledgeDocument.search_index_status == "ready")
        else:
            resume_predicates.append(Resume.search_index_status != "deleted")
            job_predicates.append(JobVersion.search_index_status != "deleted")
            knowledge_predicates.append(KnowledgeDocument.search_index_status != "deleted")
        return or_(
            and_(
                RecruitingChunk.source_type == "resume",
                exists(select(1).select_from(Resume).where(*resume_predicates)),
            ),
            and_(
                RecruitingChunk.source_type == "job_version",
                exists(select(1).select_from(JobVersion).join(Job, Job.id == JobVersion.job_id).where(*job_predicates)),
            ),
            and_(
                RecruitingChunk.source_type == "knowledge_document",
                exists(select(1).select_from(KnowledgeDocument).where(*knowledge_predicates)),
            ),
        )

    def _resolve(self, predicates: Sequence[ColumnElement[bool]]) -> list[RetrievedChunk]:
        statement = select(RecruitingChunk).where(*predicates).order_by(RecruitingChunk.id, RecruitingChunk.citation_id)
        with self.session_factory() as session:
            chunks = session.scalars(statement).all()
        return [self._result(chunk, 1.0) for chunk in chunks]

    @staticmethod
    def _result(chunk: RecruitingChunk, score: float) -> RetrievedChunk:
        return RetrievedChunk(
            id=chunk.id,
            tenant_id=chunk.tenant_id,
            citation_id=chunk.citation_id,
            source_type=chunk.source_type,
            source_id=chunk.source_id,
            source_version=chunk.source_version,
            generation=chunk.generation,
            content=chunk.content,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            page_number=chunk.page_number,
            section=chunk.section,
            score=score,
        )
