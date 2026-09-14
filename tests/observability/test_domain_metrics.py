"""Observable domain instrument behavior, using real SDK export."""

from app.config import Settings
from app.observability.otel import Observability
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
import pytest
from app.observability.instrumentation import activate
from tests.observability.test_otel_adapter import metrics
from tests.matching import test_matching_api

matching_client = test_matching_api.matching_client


@pytest.fixture
def telemetry():
    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    runtime = Observability(Settings(telemetry_enabled=True), span_exporter=exporter, metric_reader=reader)
    with activate(runtime):
        yield runtime, exporter, reader
    runtime.shutdown()


def test_http_histogram_preserves_subsecond_boundaries():
    reader = InMemoryMetricReader()
    runtime = Observability(
        Settings(telemetry_enabled=True), span_exporter=InMemorySpanExporter(), metric_reader=reader
    )
    try:
        histogram = runtime.meter_provider.get_meter("http").create_histogram(
            "http.server.request.duration", unit="s", explicit_bucket_boundaries_advisory=(0.005, 0.01, 0.1, 0.5, 1)
        )
        histogram.record(0.02)
        point = reader.get_metrics_data().resource_metrics[0].scope_metrics[0].metrics[0].data.data_points[0]
        assert point.explicit_bounds == (0.005, 0.01, 0.1, 0.5, 1)
        assert point.bucket_counts == (0, 0, 1, 0, 0, 0)
    finally:
        runtime.shutdown()


def test_gateway_records_physical_attempts_without_logical_fallback(telemetry):
    from app.ai.gateway import OpenAICompatibleGateway
    from tests.ai.test_gateway import Answer, StubCompletions, _client, _request

    runtime, exporter, reader = telemetry
    gateway = OpenAICompatibleGateway(
        Settings(api_key="synthetic", model_max_retries=1),
        client=_client(StubCompletions([TimeoutError("PRIVATE"), Answer(value="PRIVATE")])),
    )
    result = gateway.generate(_request())
    assert result.attempts == 2
    observed = metrics(reader)
    assert "recruitmatch.model.request" in observed
    points = observed["recruitmatch.model.request"].data.data_points
    assert {point.attributes["outcome"]: point.value for point in points} == {"failure": 1, "success": 1}
    assert sum(point.count for point in observed["recruitmatch.model.duration"].data.data_points) == 2
    assert observed["recruitmatch.model.tokens"].data.data_points[0].value == 120
    assert "recruitmatch.model.fallback" not in observed
    runtime.force_flush()
    assert "PRIVATE" not in " ".join(span.to_json() for span in exporter.get_finished_spans())


def test_schema_failure_and_logical_parser_fallback_are_separate(telemetry):
    from app.ai.gateway import OpenAICompatibleGateway
    from app.ai.resume_parser import LLMResumeParser
    from app.resumes.parser import HeuristicResumeParser
    from tests.ai.test_gateway import StubCompletions, _client

    _, _, reader = telemetry
    gateway = OpenAICompatibleGateway(Settings(api_key="synthetic"), client=_client(StubCompletions([None, None])))
    parser = LLMResumeParser(gateway, HeuristicResumeParser())
    result = parser.parse_with_metadata("Python PRIVATE-SENTINEL")
    assert result.outcome.mode == "rules_fallback"
    observed = metrics(reader)
    assert "recruitmatch.model.schema_failure" in observed
    assert observed["recruitmatch.model.schema_failure"].data.data_points[0].value == 2
    assert observed["recruitmatch.model.fallback"].data.data_points[0].value == 1


def test_citation_rejection_counts_validation_events_even_if_some_claims_survive(telemetry):
    from app.ai.explanations import GroundedExplanationService, GroundedModelOutput, GroundedClaim
    from app.retrieval.ports import RetrievedChunk

    _, _, reader = telemetry
    hit = RetrievedChunk("i", "t", "allowed", "resume", "r", "v", 1, "PRIVATE", 0, 7, None, None, 1)
    result = GroundedExplanationService._validate(
        GroundedModelOutput(
            strengths=[
                GroundedClaim(text="PRIVATE", citation_ids=["allowed"]),
                GroundedClaim(text="PRIVATE", citation_ids=["forged"]),
            ]
        ),
        [hit],
    )
    assert len(result.strengths) == 1
    observed = metrics(reader)
    assert "recruitmatch.citation.rejection" in observed
    assert observed["recruitmatch.citation.rejection"].data.data_points[0].value == 1


def test_composed_artifact_store_records_delete_without_private_location(tmp_path, monkeypatch, telemetry):
    from fastapi.testclient import TestClient
    from tests.support.application import create_sqlite_test_app
    from app.artifacts.ports import ArtifactLocation
    import app.main as main

    runtime, exporter, reader = telemetry
    monkeypatch.setattr(main, "configure_observability", lambda *args, **kwargs: runtime)
    app = create_sqlite_test_app(Settings(database_url=f"sqlite:///{tmp_path}/artifact.db"))
    with TestClient(app):
        app.state.artifact_store.delete(ArtifactLocation("PRIVATE", "resumes", "PRIVATE", "PRIVATE"))
    observed = metrics(reader)
    assert "recruitmatch.artifact.operation" in observed
    assert observed["recruitmatch.artifact.operation"].data.data_points[0].attributes == {
        "operation": "delete",
        "outcome": "success",
    }
    assert observed["recruitmatch.artifact.operation.duration"].data.data_points[0].count == 1
    runtime.force_flush()
    assert any(span.name == "artifact.delete" for span in exporter.get_finished_spans())


def test_semantic_insufficient_evidence_is_one_pipeline_fallback(telemetry):
    from app.ai.semantic_matching import SemanticMatcher
    from app.retrieval.ports import SearchScope
    from tests.support.application import DeterministicEmbeddingAdapter

    _, _, reader = telemetry
    matcher = SemanticMatcher(None, None, DeterministicEmbeddingAdapter())
    assert (
        matcher.score(SearchScope("PRIVATE", frozenset({"resume"}), frozenset()), "r", "j", "PRIVATE", "PRIVATE")
        is None
    )
    observed = metrics(reader)
    assert "recruitmatch.model.fallback" in observed
    assert (
        observed["recruitmatch.model.fallback"].data.data_points[0].attributes["error.code"] == "insufficient_evidence"
    )
    assert "recruitmatch.model.request" not in observed


def test_operational_gauges_disappear_after_snapshot_expiry(telemetry, monkeypatch):
    from app.observability.events import DomainEvent

    runtime, _, reader = telemetry
    runtime.recorder.record(DomainEvent("worker.live", {}, 2))
    assert metrics(reader)["recruitmatch.worker.live"].data.data_points[0].value == 2
    assert "recruitmatch.worker.live" in metrics(reader), (
        "a healthy bounded snapshot remains observable between Beat polls"
    )
    import app.observability.otel as otel

    now = otel.time.monotonic()
    monkeypatch.setattr(otel.time, "monotonic", lambda: now + 26)
    assert "recruitmatch.worker.live" not in metrics(reader), (
        "expired snapshots must not be re-exported as fresh healthy data"
    )


def test_replaced_sdk_snapshot_recovery_keeps_source_ttl_not_observation_ttl(telemetry, monkeypatch):
    from app.observability.events import DomainEvent
    import app.observability.otel as otel

    runtime, _, reader = telemetry
    now = [100.0]
    monkeypatch.setattr(otel.time, "monotonic", lambda: now[0])
    family = {"worker.live"}
    runtime.recorder.replace_gauges(family, [DomainEvent("worker.live", {}, 1)])
    assert metrics(reader)["recruitmatch.worker.live"].data.data_points[0].value == 1
    runtime.recorder.replace_gauges(family, [])
    assert "recruitmatch.worker.live" not in metrics(reader)
    now[0] = 110
    runtime.recorder.replace_gauges(family, [DomainEvent("worker.live", {}, 0)])
    for observed_at in (116, 134.999):
        now[0] = observed_at
        assert metrics(reader)["recruitmatch.worker.live"].data.data_points[0].value == 0
    now[0] = 135
    assert "recruitmatch.worker.live" not in metrics(reader)


def test_actual_matching_route_records_committed_recommendations_and_upload(matching_client, telemetry):
    client, resume_id = matching_client
    runtime, exporter, reader = telemetry
    client.app.state.observability = runtime
    response = client.post(f"/api/v1/resumes/{resume_id}/matches")
    assert response.status_code == 201
    observed = metrics(reader)
    assert "recruitmatch.match.completed" in observed
    assert observed["recruitmatch.match.completed"].data.data_points[0].value == 1
    scores = observed["recruitmatch.match.score"].data.data_points[0]
    assert scores.count == 3
    assert scores.sum == pytest.approx(sum(item["total_score"] * 100 for item in response.json()["results"]))
    assert scores.max > 1 and scores.max <= 100
    assert observed["recruitmatch.match.duration"].data.data_points[0].count == 1
    response = client.post(
        "/api/v1/resumes", files={"file": ("PRIVATE.txt", b"Python synthetic PRIVATE", "text/plain")}
    )
    assert response.status_code == 202
    runtime.force_flush()
    assert any(span.name == "resume.upload" for span in exporter.get_finished_spans())


def test_broken_optional_recorder_cannot_replace_business_result_or_exception():
    from contextlib import contextmanager
    from app.observability.events import bind_recorder, operation, record

    class Broken:
        def record(self, event):
            raise RuntimeError("telemetry failure")

        @contextmanager
        def operation(self, *args):
            yield
            raise RuntimeError("telemetry exit failure")

    with bind_recorder(Broken()):
        record("task.completed")
        with operation("resume.process"):
            result = 7
        assert result == 7
        with pytest.raises(ValueError, match="business failure"):
            with operation("resume.process"):
                raise ValueError("business failure")


def test_final_citation_invalidation_records_degradation(telemetry):
    from types import SimpleNamespace
    from app.services.matching import MatchingService
    from app.retrieval import SearchScope
    from tests.matching.test_matching_service import (
        _ExplanationService,
        _FinalResolutionIndex,
        _Embedder,
        _hybrid_recommendation,
    )

    _, _, reader = telemetry
    scope = SearchScope(
        "tenant-1",
        frozenset({"resume", "job_version"}),
        frozenset({("resume", "resume-1", "1"), ("job_version", "job-version-1", "1")}),
    )
    service = MatchingService(
        SimpleNamespace(),
        explanation_service=_ExplanationService(),
        source_index=_FinalResolutionIndex(missing=frozenset({"job-cite"})),
        embedder=_Embedder(),
        uow=SimpleNamespace(),
    )
    [result] = service._add_grounded_guidance(
        scope,
        "resume-1",
        [SimpleNamespace(job_version_id="job-version-1", jd_text="PRIVATE")],
        [_hybrid_recommendation()],
    )
    assert result.semantic_score is None
    data = metrics(reader)
    assert "recruitmatch.citation.rejection" in data
    assert data["recruitmatch.citation.rejection"].data.data_points[0].value == 1
    assert data["recruitmatch.model.fallback"].data.data_points[0].attributes["error.code"] == "invalid_citation"


@pytest.mark.parametrize("boundary", ["partial", "all", "prior_rejection"])
def test_final_explanation_only_invalidation_is_counted_without_recounting_prior_rejection(telemetry, boundary):
    from types import SimpleNamespace
    from app.ai.explanations import GroundedExplanationService, GroundedModelOutput, GroundedClaim
    from app.services.matching import MatchingService
    from app.retrieval import SearchScope
    from tests.matching.test_matching_service import (
        _FinalResolutionIndex,
        _Embedder,
        _hybrid_recommendation,
        _retrieved_chunk,
    )

    runtime, exporter, reader = telemetry
    scope = SearchScope(
        "tenant-1",
        frozenset({"resume", "job_version", "knowledge_document"}),
        frozenset(
            {
                ("resume", "resume-1", "1"),
                ("job_version", "job-version-1", "1"),
                ("knowledge_document", "knowledge-1", "1"),
            }
        ),
    )

    class Index(_FinalResolutionIndex):
        def search(self, *args):
            return super().search(*args) + [_retrieved_chunk("guidance-cite", "knowledge_document", "knowledge-1")]

    class Explanation:
        def generate(self, scope, resume_id, job_version_id, rule_result, hits):
            # Actual first validation may already reject an unsupported claim;
            # final resolution must not recount its status when no new claim drops.
            return GroundedExplanationService._validate(
                GroundedModelOutput(
                    summary=GroundedClaim(
                        text="PRIVATE",
                        citation_ids=["already-gone" if boundary == "prior_rejection" else "guidance-cite"],
                    ),
                    strengths=[]
                    if boundary == "all"
                    else [GroundedClaim(text="PRIVATE", citation_ids=["resume-cite"])],
                ),
                hits,
            )

    service = MatchingService(
        SimpleNamespace(),
        explanation_service=Explanation(),
        source_index=Index(),
        embedder=_Embedder(),
        uow=SimpleNamespace(),
    )
    [result] = service._add_grounded_guidance(
        scope,
        "resume-1",
        [SimpleNamespace(job_version_id="job-version-1", jd_text="PRIVATE")],
        [_hybrid_recommendation()],
    )
    assert result.semantic_score == 80
    assert {item["id"] for item in result.citations} == {"resume-cite", "job-cite"}
    assert result.grounded_explanation["summary"] is None
    assert len(result.grounded_explanation["strengths"]) == (0 if boundary == "all" else 1)
    assert result.grounding_status == ("empty_model_output" if boundary == "all" else "rejected_unsupported_claims")
    data = metrics(reader)
    assert "recruitmatch.citation.rejection" in data
    assert sum(point.value for point in data["recruitmatch.citation.rejection"].data.data_points) == 1
    assert all(
        point.attributes["operation"] == "match_explanation"
        for point in data["recruitmatch.citation.rejection"].data.data_points
    )
    if boundary == "all":
        assert data["recruitmatch.model.fallback"].data.data_points[0].value == 1
        assert data["recruitmatch.model.fallback"].data.data_points[0].attributes["error.code"] == "invalid_citation"
    else:
        assert "recruitmatch.model.fallback" not in data
    runtime.force_flush()
    assert sum(span.name == "citation.validate" for span in exporter.get_finished_spans()) == 3


def test_missing_owner_masks_an_enclosing_recorder(telemetry):
    from app.observability.events import record

    _, _, reader = telemetry
    with activate(None):
        record("model.request", {"outcome": "success"})
    assert "recruitmatch.model.request" not in metrics(reader)


def test_artifact_failure_uses_real_storage_taxonomy_and_preserves_exception(telemetry):
    from app.observability.adapters import ObservedArtifactStore
    from tests.fakes.artifacts import FakeArtifactStore
    from app.artifacts.ports import ArtifactLocation, ArtifactMissing

    _, _, reader = telemetry
    store = ObservedArtifactStore(FakeArtifactStore())
    with pytest.raises(ArtifactMissing):
        store.read_bounded(ArtifactLocation("PRIVATE", "resumes", "PRIVATE", "PRIVATE"), 100)
    data = metrics(reader)
    assert data["recruitmatch.artifact.operation"].data.data_points[0].attributes == {
        "operation": "get",
        "outcome": "failure",
        "error.code": "object_not_found",
    }
    assert data["recruitmatch.artifact.operation.duration"].data.data_points[0].count == 1


def test_vector_search_counts_results_and_duration_once(telemetry):
    from app.observability.adapters import ObservedVectorIndex
    from app.retrieval import SearchScope
    from tests.matching.test_matching_service import _FinalResolutionIndex

    _, _, reader = telemetry
    index = ObservedVectorIndex(_FinalResolutionIndex())
    hits = index.search(
        SearchScope("tenant", frozenset({"resume"}), frozenset({("resume", "source", "1")})), [1.0], "fake", 6, 0.35
    )
    assert len(hits) == 1
    data = metrics(reader)
    assert data["recruitmatch.vector.search.results"].data.data_points[0].sum == 1
    assert data["recruitmatch.vector.search.duration"].data.data_points[0].count == 1
