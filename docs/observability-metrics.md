# Recruiting telemetry semantics

The exact 27 `recruitmatch.*` instruments and units are defined in
`app/observability/policy.py`. Standard HTTP/DB instrumentation is separate;
HTTP durations use seconds and validated subsecond bucket boundaries. No
custom duplicate HTTP counter is emitted.

## Durable processing

- `task.started`: one successfully committed lease claim, excluding duplicate,
  deferred, terminal and lost-lease deliveries.
- `task.completed`: successful guarded publication and outer commit only.
  Celery task return/success and `ProcessDisposition.COMPLETED` do not imply this.
- `task.failed`: one failed owned attempt whose guarded failure/retry decision
  committed, including short retry, long retry, permanent and exhausted failure.
- `task.retry`: the overlapping subset of failed attempts whose same committed
  decision arranged a future retry. It is neither mutually exclusive with
  `task.failed` nor proof of broker delivery. Never sum failed + retry as outcomes.
- `task.duration`: elapsed processing after the committed claim until that owned
  invocation exits, including failures and lost ownership, but not duplicates or
  queue wait. It is not an outcome counter.
- `lease.takeover`: a committed expired-lease claim or recovery reservation;
  `lease.renew_failure`: renewal lost ownership or failed/unknown transaction.
  Renewal failure does not fabricate a committed task failure.
- `vector.indexed_chunks`: chunks in a successfully activated publication, not
  staging writes. Resume deterministic fallback may complete with no new index.

Task labels are `resume` / `knowledge_document`; source labels additionally use
`job_version` for indexing. All IDs, lease owners and payloads stay out of telemetry.

## Model, retrieval and matching

`model.request` and `model.duration` count/measure each physical provider attempt,
including retries and schema repair; `model.schema_failure` counts each invalid
physical output. `model.tokens` records input + output tokens when a valid
completion supplies usable usage; failures do not invent token counts.
`model.fallback` counts a logical degradation decision, labelled by controlled
operation and reason, including evidence/retrieval/index/citation degradation.
It is not a physical request counter. Disabled AI is not a failed request.

`citation.rejection` counts one validation event containing any rejected evidence
or claims, not individual claims. Partial rejection may still yield a useful
result. Final active-source validation remains authoritative.
Final explanation validation counts only newly discarded claims: partial
survivors add one rejection event but no fallback; losing all remaining guidance
adds one fallback. An earlier rejected status without another dropped claim is
not counted again. These observations use `operation=match_explanation`, separate
from semantic-score validation at `operation=matching.run`.
`vector.search.results` records the result count of each successful search;
duration includes failed searches too. Embedding spans cover one batch or query,
never one span per chunk. Artifact count/duration cover each put/get/delete,
including cleanup delete failures using the existing artifact error codes.

`match.completed` counts a successfully committed run. `match.score` records each
final persisted recommendation's fraction converted to 0..100, not an aggregate
run score. `match.duration` measures the invocation, including unsuccessful runs.

## Aggregate gauges and freshness

One Beat scheduler polls PostgreSQL for active queued resume/knowledge depth,
DB-clock age of the oldest `queued_at`, and cleanup-pending artifacts. Redis is
used only for the existing aggregate heartbeat snapshot: live worker count,
oldest retained worker heartbeat age, and Beat tick age. Redis queue length is
not queue authority. Missing/unreachable heartbeat values are absent, not zero.
The existing oldest-retained-heartbeat semantics include stale retained entries.

Each dependency family's fixed snapshot is replaced atomically after a poll;
failure invalidates that family without invalidating a healthy other dependency.
Owner-bound observable callbacks do no I/O and emit only snapshots younger than
25 seconds. Beat polls on its existing 10-second tick with bounded dependency
timeouts. Probes suppress recursive SQL/Redis instrumentation and are not a
readiness prerequisite. `/system` reports telemetry `unknown` when enabled but
backend reachability has not been independently established.

SDK 1.39.1 synchronous Gauge clears its value on collection; indefinite retained
re-export was a risk hypothesis, not an observed SDK bug. Observable snapshots
provide intentional repeated healthy export plus explicit bounded availability.
The exported sample timestamp is SDK export/collection time, **not source poll
time**. A healthy snapshot may be exported until expiry; after invalidation or
expiry, a downstream receiver may still retain its last point. Total possible
stale observation is snapshot lifetime (25s) + export/collector delay + the query's
accepted sample age. This is not immediate upstream-failure detection.

Beat restart creates another writer series. Downstream queries must choose the
latest authoritative, freshness-checked sample per service/environment and
bounded gauge grouping (including source type), not sum/max all instance series.
No extra freshness metric is added to the frozen registry.

## Ownership and transport

FastAPI owns a runtime per lifespan; each Celery prefork child initializes its
runtime after fork, and Beat owns one runtime. Public framework instrumentors
route at operation time to the explicit live owner; no owner means Noop, never
the last configured app. Threadpool context propagation and explicitly captured
lease-renewer continuations preserve ownership. Closed owners are not used.

Each actual SDK writer generates a random Resource-only `service.instance.id`
UUID. It is not caller/environment supplied or derived from host, PID, worker or
business identity. Preserve it across OTLP to distinguish cumulative writers.
Only validated W3C traceparent/tracestate propagate; baggage never propagates.
Beat recovery uses fresh roots with `queued_stale`, `lease_expired`, `retry_due`
or `cleanup_pending`. Operational state and authorized business audit/model
trace-sink payloads are not exported. Stdout is bounded JSON diagnostics without
raw messages, URLs, exception text/stacks, model content or task arguments.
Supported Compose Worker/Beat commands use Celery's global `--quiet` option:
startup banners write directly to stdout, bypassing configured JSON handlers.

Task2 tests cover real SDK readers and public instrumentor seams. Backend
ingestion, real broker-to-prefork continuity, multi-writer/restart queries and
collector outage/privacy proof remain separate CP5 Tasks3/4 acceptance work.
