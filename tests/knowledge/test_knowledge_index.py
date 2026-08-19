from __future__ import annotations

import numpy as np

from app.database import Base, create_engine_and_session
from app.knowledge.index import RecruitingVectorIndex
from app.knowledge.schemas import ChunkInput
from app.models.identity import Tenant


class KeywordEmbedder:
    def _vector(self, text: str):
        lowered = text.casefold()
        vector = np.array([float("python" in lowered), float("java" in lowered), 0.1], dtype="float32")
        return vector / np.linalg.norm(vector)

    def embed_documents(self, texts):
        return [self._vector(text).tolist() for text in texts]

    def embed_query(self, text):
        return self._vector(text).tolist()


def _index(tmp_path):
    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'index.db'}")
    Base.metadata.create_all(engine)
    with factory() as session:
        acme = Tenant(name="Acme")
        globex = Tenant(name="Globex")
        session.add_all([acme, globex])
        session.commit()
        return factory, acme.id, globex.id, RecruitingVectorIndex(factory, KeywordEmbedder())


def _chunk(source_type: str, content: str):
    return ChunkInput(source_type=source_type, content=content, start=0, end=len(content))


def test_search_filters_tenant_before_similarity_scoring(tmp_path):
    _, acme, globex, index = _index(tmp_path)
    index.index_source(acme, "policy", "a-policy", "v1", [_chunk("policy", "Python policy")])
    index.index_source(globex, "policy", "g-policy", "v1", [_chunk("policy", "Python secret")])
    hits = index.search(acme, "Python", {"policy"}, top_k=5, min_score=0)
    assert [item.source_id for item in hits] == ["a-policy"]
    assert "secret" not in hits[0].content


def test_search_filters_source_type_and_score_threshold(tmp_path):
    _, acme, _, index = _index(tmp_path)
    index.index_source(acme, "resume", "resume-1", "sha", [_chunk("resume", "Python")])
    index.index_source(acme, "job", "job-1", "v1", [_chunk("job", "Java")])
    hits = index.search(acme, "Python", {"resume"}, top_k=5, min_score=0.9)
    assert [item.source_type for item in hits] == ["resume"]
    assert index.search(acme, "Python", {"job"}, top_k=5, min_score=0.9) == []


def test_citation_id_is_stable_across_index_instances(tmp_path):
    factory, acme, _, index = _index(tmp_path)
    index.index_source(acme, "policy", "policy-1", "v1", [_chunk("policy", "Python")])
    first = index.search(acme, "Python", {"policy"}, 5, 0)[0]
    second_index = RecruitingVectorIndex(factory, KeywordEmbedder())
    second = second_index.search(acme, "Python", {"policy"}, 5, 0)[0]
    assert first.citation_id == second.citation_id
    assert len(first.citation_id) == 20


def test_embed_populates_vectors_without_mutating_inputs(tmp_path):
    _, _, _, index = _index(tmp_path)
    original = [_chunk("policy", "Python")]
    embedded = index.embed(original)
    assert original[0].vector == []
    assert len(embedded[0].vector) == 3
