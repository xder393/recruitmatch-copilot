"""Telemetry decorators for storage and authorized vector ports."""

from app.artifacts.errors import storage_error_code
from app.observability.events import operation, record


class ObservedArtifactStore:
    def __init__(self, store):
        self.store = store

    def _call(self, name, function, *args, **kwargs):
        attributes = {"operation": name}
        with operation(f"artifact.{name}", attributes):
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                record(
                    "artifact.operation",
                    {**attributes, "outcome": "failure", "error.code": storage_error_code(exc).value},
                )
                raise
            record("artifact.operation", {**attributes, "outcome": "success"})
            return result

    def put(self, location, stream, size_bytes, sha256):
        return self._call("put", self.store.put, location, stream, size_bytes, sha256)

    def read_bounded(self, location, max_bytes):
        return self._call("get", self.store.read_bounded, location, max_bytes)

    def delete(self, location):
        return self._call("delete", self.store.delete, location)

    def inspect(self, location):
        return self.store.inspect(location)

    def list_page(self, **kwargs):
        return self.store.list_page(**kwargs)


class ObservedVectorIndex:
    def __init__(self, index):
        self.index = index

    def search(self, scope, query_embedding, embedding_model, top_k, min_score):
        attributes = {"retrieval.strategy": "pgvector"}
        with operation("vector.search", attributes):
            hits = self.index.search(scope, query_embedding, embedding_model, top_k, min_score)
            record("vector.search.results", attributes, len(hits))
            return hits

    def resolve_active_citations(self, scope, citation_ids):
        with operation("citation.validate"):
            return self.index.resolve_active_citations(scope, citation_ids)

    def resolve_historical_citations(self, tenant_id, citation_ids):
        return self.index.resolve_historical_citations(tenant_id, citation_ids)


class ObservedEmbeddingQueries:
    """Document batches are traced in SourceIndexer; query calls are traced here."""

    def __init__(self, embedder):
        self.embedder = embedder
        # Query-only/test adapters need not expose indexing metadata; telemetry
        # must not turn otherwise valid composition into a startup failure.
        self.model_name = getattr(embedder, "model_name", "")

    def embed_documents(self, texts):
        return self.embedder.embed_documents(texts)

    def embed_query(self, text):
        with operation("embedding.generate", {"model.name": self.model_name}):
            return self.embedder.embed_query(text)
