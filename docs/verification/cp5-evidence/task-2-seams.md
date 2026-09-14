# Controller integration notes — Task 2 (not implementation approval)

Verified against CP4 baseline f297b0a; Task1 supplies the additive safe recorder/provider APIs. Read actual signatures again after Task1 review. These notes do not replace the frozen spec or the full Task2 brief.

## Unresolved multi-writer constraint

The frozen design allows N workers, forbids Worker heartbeat IDs as metric labels, defaults to bounded attribute values, and requires a unique OTLP pipeline. Multiple cumulative writers with identical resources/attributes collide. Delta temporality alone is NOT a remedy: receivers may drop overlapping intervals. The official interval processor passes deltas through and only keeps the latest cumulative observation, not cross-writer spatial summation.

Controller requested user approval for the minimal exception: system-generated telemetry-only `service.instance.id`, never derived from business IDs, host/environment/credential values, or internal Worker heartbeat identity. Approval pending; do not add it, force solo/concurrency1, hide conflicting series or claim multi-writer metrics are correct. Task1 can finish/review fixed resources independently. Task2/3 must await this decision or a proven compliant alternative.

Evidence consulted 2026-09-14:

- https://opentelemetry.io/docs/specs/otel/metrics/data-model/#single-writer
- https://prometheus.io/docs/guides/opentelemetry/ (service name and instance become job/instance; delta conversion is stateful)
- https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/intervalprocessor/README.md

## Durable task semantics (critical)

- `ProcessDisposition.COMPLETED` is NOT synonymous with success. `finish_failed_attempt` returns it after a committed permanent failure, a long retry scheduled as FAILED, or exhausted attempts. Celery return/task-success signals are delivery mechanics, not durable business success.
- `task.started`: actual CLAIMED lease and successful outer UoW commit only. No count for duplicate-active, terminal, deferred or lost ownership.
- `task.completed`: successful `finalize_owned` publication AND outer commit. Never emit from a finally clause or before commit.
- `task.failed` and `task.retry`: successful guarded failure/retry decision and commit only. Inspect the durable decision, not exception/return status alone. A failed commit emits no durable terminal count.
- Keep CP4 business behavior/state machine unchanged. Any enriched internal result needed for observation must be additive and maintain current public behavior.
- Resume processing can publish deterministic fallback successfully when vector indexing fails. Classify pipeline degradation separately from task failure.
- Knowledge processing has its own `ready/processing/uploaded` status vocabulary versus Resume enum `SUCCEEDED/RUNNING/QUEUED`.

## Instrumentation seams

| Surface | Existing ownership | Integration caution |
| --- | --- | --- |
| `app/services/resume_processing.py` | Lease claim -> parse/stage -> guarded publication -> commit | Already-parsed index-only path; avoid duplicate counts. Explicit resume.process/parse, task duration, index outcome. |
| `app/services/knowledge_processing.py` | Same lease model; finalize generation in one UoW | Failure may return COMPLETED; successful generation count only after commit. |
| `app/processing/retry.py` | Helper owns commit of failure/retry | Distinguish actual failure, short queued retry, long failed retry, exhausted attempts. |
| `app/processing/leases.py` | Repository flushes; caller owns commit | Do not record committed outcomes inside flush-only methods. Claim/finalize/renew spans must not contain owner or source IDs. |
| `app/processing/renewal.py` | Background thread with independent UoW | Carry safe trace context explicitly if needed; never copy request/PII baggage; no span/SDK imports in business services. |
| `app/retrieval/indexing.py` | `stage` writes chunks; `index` additionally activates | Staged chunks are not committed active chunks; semantic count must not overstate success. Embedding span per batch, not per chunk. |
| `app/services/matching.py` | Rules/hybrid ranking, persisted results, run succeeded commit | Match completed after commit. Explicit histogram scope (recommendation score vs aggregate) must be documented and tested. |
| `app/ai/gateway.py` | Manual bounded retry loop, provider max_retries=0 | Separate physical provider attempts from logical pipeline fallback. Stable codes only, never raw exception/message/model output. |
| `app/tasks/dispatcher.py` | Celery publication uses redacted argument repr | Add only validated W3C traceparent/tracestate; no Baggage or trace IDs in business task args. |
| `app/tasks/celery_app.py` | `_deliver`; WorkerDependencies currently constructed per task | Per-process runtime lifecycle, not exporters/threads per task; initialize SDK after prefork. Nonbusiness delivery statuses not mapped to success counters. |
| `app/tasks/beat.py` | Singleton scheduler composes own NullPool engine | Fresh root recovery trace; safe new root context before publishing recovered work; finite recovery reason. |
| `app/processing/recovery.py` + recovery_repository | Scan/reserve/commit/publish; reserve returns modified queued state | Derive reason from original scan candidate before reserve loses previous status; count takeover only durable successful transition. |
| Artifact store/dependencies/composition | ArtifactStore ports; actual adapters injected in API and workers | Focused wrapper can measure put/get/delete without location/file/body; do not import SDK into domain/application services. |

## Operational gauges

- PostgreSQL is queue/cleanup authority. Queue depth must honor correct per-source queued states and lifecycle; age uses DB clock and queued_at. Never use Redis list length.
- `OperationsHeartbeats.snapshot()` returns aggregate count/age/status. Worker UUID is internal only. Redis failure returns unknown and None, NOT zero. Do not fabricate worker=0 when unreachable.
- Worker oldest age currently reflects oldest retained heartbeat, possibly stale; do not silently change CP4 semantics.
- Single Beat is a natural authoritative producer for aggregate gauges; multiple API/worker copies must not create gauge duplication.
- Avoid blocking readiness/request work on gauge collection, unbounded queries, and recursively tracing metrics reader SQL/Redis traffic.
- `HealthService.system` currently says telemetry=not_configured unconditionally. Replace with truthful bounded status, but telemetry must never become a hard `/ready` dependency.

## Privacy/logging

- `app/core/logging.py` currently formats arbitrary message text; main middleware logs raw URL paths; unhandled logger.exception contains exception text. These are actual leaks to close, not just attributes to strip in the Collector.
- Uvicorn access/error and Celery own handlers can bypass the root formatter. Behavioral tests must capture the real emitted structured JSON, positive stable status fields and negative sentinel checks.
- Domain DB audit records are separate authorized business storage, not an OTel/log exporter input.
- Redact before SDK storage: initial and late attributes, names/status/descriptions/events/links/resources, instrumentation scope, exemplars, metrics, ordinary logs. Test real auto-instrumentor output through supplied safe providers; do not settle for no output/vacuous PII absence.
- HTTP metric should be the semantic-convention standard, `OTEL_SEMCONV_STABILITY_OPT_IN=http`, never duplicate custom HTTP counters.

## AI operation boundaries confirmed from source

- `app/ai/resume_parser.py`: a first `invalid_output` triggers exactly one schema-repair request; other errors or failed repair use rules fallback. Grounding removes unsupported facts, independently of schema validation. Record stable outcomes, never profile/evidence text. An AI-disabled deterministic parse is not a failed model request.
- `app/ai/semantic_matching.py`: retrieval/embed failure, no evidence, model failure, and invalid two-sided citations are distinct fallback causes. There are two authorized searches; requested citation IDs are resolved again against active sources. Preserve authorization and separate pipeline fallback from transport request failure.
- `app/ai/explanations.py`: disabled, insufficient evidence, provider failure, unsupported claims and empty output are distinct outcomes. Partially rejected claims may leave a useful grounded result. Define rejection metric as one validation rejection event or rejected claims explicitly, then test it; do not emit a success solely because HTTP/model schema returned.
- Existing `trace_sink` is a business model-trace persistence interface receiving tenant/source IDs and full ModelRequest/response. It must NOT be substituted with OTel or exported wholesale. Add the SDK-free recorder alongside it, passing only preselected safe fields.
- Main composition constructs parser/source indexer/semantic matcher/explanation service; Celery WorkerDependencies separately constructs parser/indexer/store. Matching service itself is instantiated in `app/api/v1/matching.py`, so wiring only main's long-lived objects misses match metrics.
- Artifact maintenance composes another store in `app/tasks/maintenance.py`; wrappers must cover this path for delete/cleanup spans without changing maintenance transaction ownership.

## Task1 handoff considerations reported during implementation

- Runtime provides recorder/tracer_provider/meter_provider explicitly; composition must bind the post-fork child providers. Inherited runtime guards are fail-closed for telemetry, not a replacement for correct worker startup signals.
- API owned runtime starts in lifespan. Verify FastAPI auto-instrumentation initialization order: instrument_app/add_middleware after stack construction may fail, so tests must exercise real startup/request flow rather than just constructing app objects. Avoid eager exporter/thread leaks in create_app tests.
- API configured route templates must include nested routers; only collecting top-level app.routes .path can omit FastAPI 0.141 included router paths. Treat missing routes as safe unknown, never export raw request URL as fallback.
- Existing section3.1 example artifact.cleanup_failed maps at the producer to spec9.2 artifact.operation with operation=delete/outcome=failure (and duration/error.code). Do not create a second undefined instrument.
- Task1 review's deferred Minor: `SafeMeter.create_histogram` discards `explicit_bucket_boundaries_advisory`. Actual export with requested subsecond buckets instead produced default (0,5,10,...,10000). When wiring standard HTTP metrics, adopt validated bounded advisory or reviewed registry defaults and assert real subsecond buckets before claiming useful P95. This remains for Task2/final review, not silently resolved.
- Current Task1 fork test exercises disabled telemetry only. Task2 must test enabled prefork child rebinding, and Task4 must prove API->real prefork Worker trace continuity; no extrapolation from disabled cache tests.

## Validation boundaries

- Use Docker Compose only, explicit .env.example, owned recruitmatch-cp5-sep14 project. No host .env reads, paid model calls or embedding downloads.
- Parent-based API -> actual broker -> prefork worker trace proof belongs in Task4. Task2 unit tests can verify actual carrier propagation and instrumentation behavior without claiming the full stack is proven.
- Unknown/silenced telemetry failures cannot modify business return or replace the original exception. No real business data in test artifacts.
