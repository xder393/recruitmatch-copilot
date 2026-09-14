# Whole CP5 review — f297b0a..1a20117

Reviewer: /root/cp5_final_review, gpt-6-astra/high. Read-only whole-branch review. Full verdict retained below.

## Strengths

Reviewed the complete supplied 12-commit package from f297b0a to 1a20117, against the approved CP5 plan, binding specification, and R1–R18 rulings. No Critical or Important defect found.

The implementation maintains useful boundaries:

- Business code uses the SDK-free facade; instrumentation and process-owned SDK lifecycle remain in the observability layer.
- Completion, indexed-chunk, retry, and takeover metrics follow committed state transitions. Physical model attempts and logical fallbacks remain distinct.
- Trace propagation crosses HTTP, broker, and actual prefork Workers without carrying business identifiers or baggage.
- SDK filtering, Collector filtering, and JSON logging provide separate privacy controls.
- Gauge callbacks perform no dependency I/O. Family invalidation, freshness expiry, writer identity, and downstream latest-writer selection preserve unknown versus zero.
- Collector loss is a soft dependency failure. Export queues, timeouts, and shutdown are bounded.
- The corrected exporter-failure alert and dashboard handle independently present failure families correctly.

Named cross-boundary checks completed: transaction → metric emission; HTTP → Celery parentage; process lifecycle → writer identity; SDK → Collector privacy; dependency polling → SDK snapshots → Prometheus recording rules; alert expressions → synthetic Firing evidence; host ownership guards → Worker restart/Collector restoration; acceptance assertions → reported evidence.

The recorded final 417-unit/520-integration/135-observability results and successful host helper are consistent with the reviewed code and their stated scope. They do not establish remote CI, production capacity, or actual Beat-process restart.

## Findings

### Critical

None.

### Important

None.

### Minor

1. **Dashboard legends discard the dimensions needed to identify series.**

Locations: ops/grafana/dashboards/ai-rag-pipeline.json:44, async-reliability.json:44, recruiting-business.json:80, system-overview.json:368.

All dashboards reuse `{{source_type}} {{operation}} {{outcome}}`, including queries retaining different labels such as model, task type, match mode, exporter, or alert name. Consequently distinct series receive ambiguous or uninformative legends.

Fix each target’s legend to use its actual retained dimensions, including environment where relevant; use a fixed meaningful label for scalar results. Add focused dashboard assertions pairing legends with query dimensions.

2. **Healthy HTTP traffic can display an unknown 5xx ratio.**

Location: ops/grafana/dashboards/system-overview.json:79.

If successful requests exist but no 5xx-labelled series has ever been emitted, the numerator is absent. PromQL division therefore returns no series instead of zero, making known healthy traffic look unknown.

Supply a zero numerator only for environments with an observed denominator—for example, an environment-matched zero derived from that denominator. Preserve unknown for absent telemetry and avoid treating a zero request rate as an observed healthy ratio. Test success-only, actual errors, and absent traffic separately.

3. **Repeated acceptance flows can reuse earlier cumulative metric contributions.**

Location: scripts/verify_telemetry.py:269, with the same issue for matching at line 282.

Worker assertions compare cumulative values against the current flow’s delivery count, without subtracting a pre-flow baseline. Matching only requires a positive cumulative value. When a writer handles subsequent flows, previous activity can satisfy these checks even if the later flow stops emitting that metric.

Snapshot relevant writer series before submitting the flow, then assert per-writer increases; absent pre-flow series start at zero. Apply the same principle to the API matching counter. Do not introduce business/run identifiers into metric labels.

This weakens repeated-flow attribution, but does not invalidate the recorded new-writer/restart evidence or the transaction-boundary tests.

4. **Partial Tempo responses can bypass the intended bounded retry.**

Locations: scripts/verify_telemetry.py:108, tests/observability/gauge_probe.py:70; retry handling at scripts/verify_telemetry.py:46.

An otherwise partially available trace without its server span makes next(...) raise StopIteration. An empty gauge trace raises IndexError through [0]. Neither is retried by eventually, so ordinary ingestion timing can cause an immediate acceptance failure.

Convert missing required spans into explicit, sanitized assertion failures using guarded lookups. Add focused tests for partial/empty responses followed by a complete response; retain the bounded deadline rather than broadly swallowing exceptions.

5. **Fork probes lack bounded child-process failure handling.**

Locations: tests/observability/test_otel_adapter.py:359, :368, tests/observability/test_trace_propagation.py:195, :212.

The parent’s full unit run observed a multithreaded-os.fork warning. These probes fork the ambient pytest process and then use blocking pipe reads and waitpid without deadlines. If an inherited thread-owned resource stalls a child, the test can hang indefinitely. The enabled-runtime probe suppresses the warning but does not bound this failure mode.

Run each fork scenario inside a fresh, timeout-bounded subprocess, with guaranteed child cleanup/reaping. Keep the enabled-runtime inheritance case explicit; do not remove its lifecycle assertion merely to eliminate the warning.

One focused diagnostic on the exact final test image ran the preceding OTLP-failure test and disabled-runtime fork probe: 2 passed in 0.12 seconds, with only MainThread enumerated at fork and two native threads. It did not reproduce the full-suite warning or identify its owning test. This is a test-isolation/reliability finding, not evidence of a production Worker lifecycle defect.

## Deferred-item and documentation triage

The four deferred issues are confirmed and covered by Minor findings 1–4. None needs escalation to a merge blocker.

SDK-owner replacement versus process restart is accurately distinguished in docs/observability-stack.md:146: the gauge probe demonstrates real SDK-owner replacement and snapshot behavior; the host helper separately demonstrates actual Worker-process replacement. No actual Beat-process restart is claimed. Earlier Task3 prospective wording is historical, not additional completed evidence. No documentation fix is required on this point.

## Recommendations

Address the five small findings in one consolidated cleanup wave, with focused regression checks for query semantics, cumulative deltas, partial trace polling, and subprocess deadlines. No covering-suite rerun, broader CP6/security/load work, or additional instrumentation is necessary merely to triage these findings.

## Assessment

Ready to merge: Yes.

The CP5 architecture and demonstrated behavior satisfy the reviewed checkpoint requirements. Remaining findings concern dashboard clarity and acceptance-test reliability, and are nonblocking. This verdict does not authorize merging.
