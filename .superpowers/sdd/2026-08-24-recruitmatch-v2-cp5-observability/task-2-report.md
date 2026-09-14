# CP5 Task2 implementation report

Base: `4648e575e5168d1687b767c1cc13bd216076e5bc`, branch
`codex/recruitmatch-cp5-observability`. Controller approvals R8–R11 and the
user-approved Resource-only UUID exception were applied. No dependency/lock,
schema, authorization, retry timing, Celery concurrency/time-limit or business
status changes; no push/PR/merge or real model calls/downloads.

## Implementation and lifecycle

- Real public FastAPI, SQLAlchemy, Redis, HTTPX and Celery instrumentors use
  safe owner-routing providers. Installed SDK1.39.1/instrumentors0.60b1 APIs were
  inspected in Docker. SafeSpan's public kind/name compatibility and validated
  HTTP bucket advisories are preserved. SQLAlchemy engine creation uses the
  instrumented module entry point, not an earlier imported function alias.
- Middleware is installed before FastAPI stack construction. Lifespan creates
  the runtime, initializes composition under its owner, and shuts that owner
  down. Route templates include nested routers. Retained application adapters
  resolve the recorder dynamically, including later lifespans.
- Global framework wrappers resolve the explicit live owner at operation time.
  No owner/closed owner means Noop, not the last configured app. Instrument
  caches use weak runtime keys; app owner closures use weak app references.
  Concurrent apps and repeated lifespans are tested through real requests.
- Celery config installs framework hooks before fork; worker_process_init binds
  a new process runtime and worker_process_shutdown closes it. No exporters are
  created per task. Actual enabled os.fork + signal initialization proves a new
  exported writer UUID; actual eager consumer and actual publisher carrier tests
  separately prove the public transport seams (not broker/prefork end-to-end).
- Every actual writer has one random UUID4 Resource service.instance.id, stable
  for that runtime and distinct for other owners/replacements/children. No
  environment/caller/hostname/PID/business/worker-heartbeat override is accepted.
- Only validated W3C traceparent/tracestate are injected/extracted. No baggage or
  payload becomes context. Malformed state is rejected before SDK diagnostics.
  Recovery uses a fresh safe root with finite reason; retries retain only safe
  headers. AnyIO request threadpool paths preserve owner context; lease-renewer
  threads explicitly capture only owner and validated W3C continuation.
- Domain code imports the SDK-free facade only. Optional recorder failures never
  replace business values/exceptions. Separate wrappers instrument artifact
  put/get/delete, authorized vector searches and embedding queries; index staging
  instruments one document batch. API, worker and maintenance composition use
  these adapters without importing SDK objects into services.
- JSON stdout replaces arbitrary text/exception stacks with stable diagnostic
  event/error codes, bounded HTTP status and request/trace/span correlation.
  Real Uvicorn/Celery handler paths and an unknown HTTP exception are exercised.
  Authorized database audit and model trace_sink payloads remain separate.
- Beat alone polls authoritative PostgreSQL queue/cleanup and aggregate Redis
  heartbeat state. Fixed family snapshots are atomically replaced; failure
  invalidates its dependency family. Owner-bound callbacks perform no I/O and
  expire snapshots after25s. PostgreSQL statement/lock timeouts and existing
  connect/Redis timeouts bound probes; probes suppress recursive instrumentation.
  Telemetry does not gate readiness; enabled but unprobed backend status is unknown.

## Exact instrument semantics

Names below have prefix `recruitmatch.`. Registry remains exactly27; metric
attributes pass the finite default-deny policy, never IDs or free text. Resource
service name/environment plus approved writer UUID are separate from datapoint
attributes. Unknown configured/domain values are dropped, not regex-admitted.

| Instrument | Kind / unit | Actual observation and bounded attributes |
| --- | --- | --- |
| task.started | Counter / {task} | Committed CLAIMED; task.type resume/knowledge_document, outcome claimed |
| task.completed | Counter / {task} | Committed guarded successful publication; task.type, outcome success |
| task.failed | Counter / {task} | Committed failed owned attempt, including retries/permanent/exhausted; task.type, error.code, outcome failure |
| task.retry | Counter / {task} | Overlapping failed-attempt subset whose locked committed decision arranges future retry; task.type, error.code, outcome retry |
| task.duration | Histogram / s | Processing after committed claim until owned invocation exits; task.type; no duplicates/queue wait |
| queue.depth | Gauge / {source} | Active PostgreSQL QUEUED resume/uploaded knowledge count; source.type |
| queue.oldest_age | Gauge / s | PostgreSQL clock minus min queued_at, nonnegative; source.type; known empty queue zero |
| lease.takeover | Counter / {takeover} | Committed expired-lease claim/reservation; source.type, recovery.reason lease_expired |
| lease.renew_failure | Counter / {failure} | Lost/failed/unknown renewal; source.type, error.code; not a fabricated task failure |
| worker.live | Gauge / {worker} | Existing Redis aggregate count; no worker identity datapoint attribute |
| worker.oldest_heartbeat_age | Gauge / s | Existing oldest retained heartbeat age; missing unknown omitted |
| beat.tick_age | Gauge / s | Existing aggregate Beat heartbeat age; missing unknown omitted |
| artifact.operation | Counter / {operation} | Physical put/get/delete call; operation, outcome, actual stable storage error.code on failure |
| artifact.operation.duration | Histogram / s | Same artifact call, success/failure; operation |
| artifact.cleanup_pending | Gauge / {artifact} | PostgreSQL CLEANUP_PENDING count; no business labels |
| vector.search.duration | Histogram / s | Each authorized search including failure; retrieval.strategy pgvector |
| vector.search.results | Histogram / {result} | Returned hit count per successful search; retrieval.strategy pgvector |
| vector.indexed_chunks | Counter / {chunk} | Successfully activated/published chunk count after owning commit; source.type, outcome success; not staging |
| model.request | Counter / {request} | Each physical provider attempt, including retry/repair; configured model.provider/name, outcome, stable error.code |
| model.duration | Histogram / s | Each physical provider attempt; configured model.provider/name |
| model.tokens | Counter / {token} | Input+output usage on usable validated completion, no invented failure usage; configured model.provider/name |
| model.fallback | Counter / {fallback} | Logical degradation decision, not physical request; controlled operation/error.code, outcome fallback |
| model.schema_failure | Counter / {failure} | Each physical invalid output; configured model.provider/name, error.code invalid_output |
| citation.rejection | Counter / {rejection} | One validation event with any rejection, not per claim; controlled operation/error.code; partial survivors allowed |
| match.completed | Counter / {match} | Successfully committed run; match.mode rules/hybrid, outcome success |
| match.duration | Histogram / s | Matching invocation including unsuccessful result; match.mode |
| match.score | Histogram / 1 | Each final persisted recommendation score converted fraction×100 and guarded0..100; match.mode, outcome success |

Task.failed includes committed short retry, long retry and exhausted/permanent
attempts; task.retry is overlapping, not mutually exclusive and not proof of
broker delivery. Never sum failed+retry as total outcomes. No completed/failed/
retry count follows a lost fence or failed commit. task.started may legitimately
exist without an outcome when later publication/commit fails. Task durations
include lost-lease exits, but do not classify them as committed failures.

Model fallback is per logical degradation boundary (`resume.parse`,
`semantic_project_match`, `match_explanation`, `matching.run`, `vector.index`),
not one per whole match run. Disabled AI is not a model failure. Citation active
source authorization, repair behavior and business trace_sink writes are unchanged.

Explicit spans cover resume.upload, artifact.put/get/delete, resume.process,
knowledge.process, resume.parse, embedding.generate, vector.index/search,
matching.run, model.generate, citation.validate, lease.claim/renew/finalize and
beat.recover. Recovery reason is finite queued_stale/lease_expired/retry_due/
cleanup_pending (R11 adds retry_due for FAILED+due, distinct from stale queued).
Standard HTTP server/client histograms remain distinct from this27 registry and
use real subsecond buckets; SQL/Redis/Celery auto spans pass safe providers.

## Gauge freshness contract and downstream prerequisites

SDK1.39.1 synchronous Gauge clears its value on collect; indefinite re-export
was an investigated risk hypothesis, NOT an observed SDK defect. The observable
snapshot design provides repeated healthy export between polls plus explicit
expiry/invalidation. Tests require positive second healthy export before expiry,
then absent metrics on failure/expiry and positive recovery.

SDK export timestamps are collection/export time, NOT source poll time. A retained
healthy snapshot can be re-exported until25s expiry. After invalidation/expiry the
receiver may retain its previous point. Total possible stale-observation bound is
snapshot25s + export/collector delay + accepted query sample age; this is NOT
immediate upstream-failure detection. Polling remains Beat's existing10s tick.
Atomic family replacement prevents partial-poll replacement; callbacks cannot do
SQL/Redis I/O. PostgreSQL and Redis failures independently invalidate snapshots.

Task3 must preserve writer identity, select latest authoritative freshness-checked
aggregate sample per service/environment/source grouping rather than sum/max
all Beat instance series, and label failed attempts without failed+retry summing.
Task4 still must prove Collector/Tempo/Prometheus/Grafana ingestion, real
API→broker→prefork parentage, multi-writer/restart query correctness, outages and
backend-side synthetic PII checks. Task2 makes no whole CP5/backend success claim.

Durable operator-facing semantics are also in `docs/observability-metrics.md`.

## RED/GREEN evidence

All Python/build/test/format work used scoped Docker. Focused iterations used
the following prefixes with source mounts; final runs below use the rebuilt
image without mounts. No local/personal .env was read.

```sh
docker compose --env-file .env.example -p recruitmatch-cp5-sep14 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app:/app/app:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-unit pytest <path/selectors> -q --tb=short
docker compose --env-file .env.example -p recruitmatch-cp5-sep14 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app:/app/app:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-integration pytest <path/selectors> -q --tb=short
```

| Focused test path/selector | RED observed before corresponding implementation | GREEN evidence |
| --- | --- | --- |
| tests/observability/test_trace_propagation.py (writer/dispatcher) | Missing writer identity and outgoing W3C headers | Real exported UUIDs stable/distinct and only trace headers through actual before_publish carrier |
| same, actual_http_and_sql | Missing actual server span; public FastAPI required SafeSpan.kind | Repeated lifespans produce server+SQL spans with correct owner and subsecond HTTP buckets |
| same, real_celery_consumer | Consumer trace ID differed; request carrier stores supplied values in headers | Actual eager Celery consumer retains valid parent via W3C-only getter |
| same, enabled_worker_init_after_fork | Child retained parent writer before process initialization wiring | Actual enabled fork+worker init returns distinct exported UUID and new runtime |
| same, invalid_w3c | Inject retained baggage | Baggage removed, zero/invalid parent rejected, valid state preserved |
| tests/observability/test_domain_metrics.py, http_histogram | Advisory discarded, default0/5/10... buckets observed | Exact requested subsecond boundaries/bucket_counts exported |
| same, gateway/schema/citation/parser | Missing physical request/schema/fallback/rejection observations | Timeout+success2attempt durations,120tokens; schema failures2 and logical parser fallback1; partial citation rejection1 |
| same, operational_gauges | Corrected RED required second healthy export; synchronous Gauge omitted it | Healthy repeated export, expiry absence; integration failure→recovery positives |
| same, broken_optional_recorder | Telemetry RuntimeError replaced business behavior | Recorder/operation exit failures contained; original business exception preserved |
| same, final_citation_invalidation | No rejection/fallback at final active-source invalidation | Actual final guidance falls back and emits rejection+invalid_citation |
| same, missing_owner | Nested activate(None) inherited outer recorder | No metric outside explicit live owner |
| tests/observability/test_structured_logging.py | Real Uvicorn/Celery emitted raw private URL/exception stack |2passed1.23s; positive bounded JSON/status and unknown500 trace correlation without sentinel |
| tests/integration/processing/test_observability.py | Initial6 missing durable-counter failures; later2missing lease/embedding/index spans |17passed2.04s: real PG processing/locked retry decisions, failed outer commits, authoritative gauges, lease renewal, recovery, real Redis |
| same, renewal | No lease.renew_failure | Thread renewal lost-lease counter1 and exact safe parent |
| same, actual_redis | Actual instrumentor required SafeSpan.name | Real GET CLIENT span present, no private key |
| same, beat_recovery retry_due | Branch incorrectly labelled failed+due as queued_stale | Both source types and expiry/retry_due reasons; fresh root; takeover only expiry |
| tests/observability/test_telemetry_privacy.py -k allowed_observable_callback_failure | Independent SafeMeterProvider+real reader logged PRIVATE-CALLBACK-SENTINEL exception/stack |1passed28deselected0.10s; callback failure contained before SDK diagnostics; following healthy callback emits3, without app logging setup |
| tests/observability/test_trace_propagation.py -k invalid_tracestate | SDK warning echoed PRIVATE-STATE-SENTINEL | Combined invalid_tracestate/invalid_w3c2passed7deselected0.15s after prevalidating member grammar |

Two initially vacuous test designs were corrected before relying on their proof:
expiry-only synchronous Gauge test lacked positive second export, and creating
another runtime gauge with the same name reused the existing callback. The
amended tests above meaningfully failed before their fixes. HTTPX local transport,
concurrent-app routing, artifact taxonomy and vector result/duration checks also
assert positive real SDK output, not merely sentinel absence.

First full mounted unit regression run:3failed376passed22.81s; failures were the previous exact
Resource expectation and2health not_configured expectations. Updated assertions
check approved UUID4 plus configured attrs, and truthful disabled health.
The definitive later exact-source full runs before final compatibility fixes
were2failed388passed31.57s (eager optional embedder.model_name startup access) and
9failed511passed26.22s (8Beat empty-header expectations,1undecorated index type).
Fixed only that optional metadata access and preserved business assertions while
checking the actual ObservedVectorIndex→PgVectorRecruitingIndex composition.
Focused reruns: foundation/schema + operations health19passed4.51s; recovery
contention + real retrieval composition17passed2.73s. These full-suite failures
are compatibility regressions found/fixed, not initial feature RED evidence.

Initial integration no-deps attempt failed DNS because scoped synthetic dependencies
were not started; it was not counted as RED. Started only owned postgres/Redis/
MinIO/bootstrap/test-setup via Compose. Existing volumes retained; unrelated odp
project/host8000 untouched. Models/embeddings always synthetic deterministic fakes.

## Final exact-source verification

Final image `recruitmatch-cp5-sep14-app-test`:
`sha256:6a0ac2069ddc36f30376553f41524d8e874b3158b6410e27fde59cddff50a819`.
Build command `docker compose --env-file .env.example -p recruitmatch-cp5-sep14 build test-unit`
completed successfully with frozen dependency cache. No source mounts in final
checks; image includes the final app/tests/scripts used for these results.

```sh
docker compose --env-file .env.example -p recruitmatch-cp5-sep14 run --rm --no-deps test-unit
# 390 passed in 31.02s; exit 0, no warnings
docker compose --env-file .env.example -p recruitmatch-cp5-sep14 run --rm --no-deps test-integration
# 520 passed in 26.94s; exit 0, no warnings
docker compose --env-file .env.example -p recruitmatch-cp5-sep14 run --rm --no-deps test-unit sh -c 'ruff check app tests scripts && ruff format --check app tests scripts && mypy app/models app/retrieval app/services app/ai app/tasks && mypy --follow-imports=silent app/observability app/core/logging.py app/main.py app/operations/health.py app/processing'
```

Ruff: All checks passed;223files already formatted. CI mypy: no issues in41source
files. Scoped mypy: no issues in18source files. Combined command exit0.
The unit service expands to pytest tests excluding tests/integration,
tests/artifacts and the3PostgreSQL retrieval files; integration expands to
pytest tests/integration tests/retrieval tests/artifacts -q. These suites include
the new Task2 regressions plus prior CP1–4 business/locking/artifact contracts.

## Changed files and self-review

Added focused instrumentation/context/adapters/operations modules under
app/observability; expanded its SDK-free events, safe policy and runtime. Modified
main/database/logger/health composition; processing leases/outcomes/retry/renewal/
recovery and lease port; resume/knowledge/matching/upload services; model gateway,
parser/semantic/explanation boundaries; retrieval index staging; Celery/Beat/
dispatcher/maintenance. Added3observability behavior files and1real integration
file; extended privacy tests and updated health/fake lease/contention/composition
assertions. Added operator metric semantics documentation and this report.

Self-review read the production diffs and new modules, traced transaction owners,
reviewed positive/negative SDK tests, and ran git diff --check. Fixed public
instrumentor kind/name/advisory compatibility, callback and tracestate diagnostic
leaks, missing-owner isolation, failed optional recorder behavior, final citation
degradation, metadata eagerness and locked retry attribution. No SDK imports were
introduced into domain/services; authorized business persistence remains unchanged.
Controller requested/approved architecture/lifecycle and metric-semantic decisions
were escalated before implementing them. No unresolved Task2 design blocker.

Known typing boundary: fully following main imports surfaces baseline unrelated
Optional original_filename errors in app/api/v1/resumes.py and knowledge.py.
Scoped --follow-imports=silent is explicitly approved; no claim of global typing
cleanliness. No unrelated fixes or dependency changes were made.

Implementation status: DONE for Task2, with the explicit Task3/4 acceptance
boundaries above. TDD drove missing-behavior and privacy regressions; systematic
debugging isolated suite compatibility failures; verification-before-completion
required the final rebuilt-image suites and typing before this handoff/commit.
