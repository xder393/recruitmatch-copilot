# Final fix scoped re-review

Reviewer: /root/cp5_final_fix_review, gpt-5.6-sol/high. Fix base 1a20117; reviewed complete 15-file uncommitted package. Full verdict below.

1. **Dashboard legends identify retained query dimensions** — **NOT ADDRESSED.** Most legends are corrected, but `ALERTS{alertstate="firing"}` retains the varying `severity` label—rules produce both `warning` and `critical`—while the legend omits it at `ops/grafana/dashboards/system-overview.json:368`. The regression test hard-codes the same incomplete label set at `tests/observability/test_observability_configs.py:94-102`, so its claimed “all active-alert dimensions” coverage is insufficient. Evidence: `ops/prometheus/rules.yml:40-41`, `ops/prometheus/rules.yml:73-74`.

2. **Healthy HTTP traffic displays zero rather than unknown 5xx ratio** — **ADDRESSED.** The expression derives an environment-matched zero numerator from observed total traffic and divides only where the denominator is strictly positive, preserving absence for idle or absent environments at `ops/grafana/dashboards/system-overview.json:79`. The fixture reads that actual dashboard expression and covers success-only, 20% errors, two idle variants, mixed environments, and entirely absent telemetry at `tests/observability/dashboard_probe.py:10-52`; the reported pinned-promtool run succeeded.

3. **Repeated acceptance flows require current-flow metric contributions** — **ADDRESSED.** All six Worker families and the API match counter are snapshotted before flow setup, per writer, with absent series defaulting to zero at `scripts/verify_telemetry.py:241-258`. Current values are compared against those baselines per actual writer and emitted as baseline/current/delta/minimum evidence at `scripts/verify_telemetry.py:282-300` and `scripts/verify_telemetry.py:312-319`. Regressions cover every stale family plus absent and existing baselines at `tests/observability/test_flow_metric_deltas.py:22-47`; the reported two live flows show the stable API writer increasing `0→1` and `1→2`.

4. **Partial Tempo responses use bounded sanitized retry** — **ADDRESSED.** A missing HTTP server span now produces `missing_http_server_span` rather than `StopIteration` at `scripts/verify_telemetry.py:108-116`; an empty gauge producer trace produces `missing_gauge_producer_span` rather than `IndexError` at `tests/observability/gauge_probe.py:49-52`. The existing bounded retry catches assertions without broadening its exception set at `scripts/verify_telemetry.py:39-50`. Partial/empty-to-complete and sanitized-deadline regressions are present at `tests/observability/test_partial_trace_polling.py:60-84`.

5. **Fork lifecycle probes are isolated, bounded, and reap children** — **ADDRESSED.** Each lifecycle case runs in a fresh session-owning subprocess with timeout and process-group termination fallback at `tests/observability/fork_probe.py:14-38`. Pipe reads, nonblocking `waitpid`, kill, and reaping all have deadlines at `tests/observability/fork_probe.py:41-95`; stalled-pipe, stalled-exit, crash, and supervisor-timeout cases are covered at `tests/observability/test_fork_probe.py:10-30`. The disabled and enabled real lifecycle paths remain intact at `tests/observability/fork_probe.py:114-127` and `:131-180`. The enabled test positively requires the live SDK batch thread, distinct exported writer, reaped child, and exactly one locally captured warning at `tests/observability/test_trace_propagation.py:166-177`; no global warning suppression was introduced.

## New Breakage in the Fix Diff

None. The omitted `severity` dimension is the unresolved portion of Finding 1, not separate breakage.

## Out-of-Scope Observations

None.

## Evidence Check

The supplied report names focused RED/GREEN coverage and records 24 focused tests, 435 unit tests, 520 integration tests, 153 live observability tests, pinned-promtool success, and the expected enabled-fork warning assertion at `final-fix-report.md:28-33` and `:66-79`. No additional suite was run.

## Verdict

**Fix round: Findings remain open** — Finding 1 remains incomplete because the active-alert legend and its regression omit the retained `severity` dimension.

## Controller disposition (after the one permitted final fix wave)

R19 in controller-final-ledger.md explicitly parks this remaining Minor. Four findings are fully addressed; finding1 is substantially improved but not fully addressed. No Critical/Important or new breakage was reported. Do not rewrite this scoped verdict as “all five findings addressed” or a zero-findings review.
