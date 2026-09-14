# SDD ledger — plan: docs/superpowers/plans/2026-08-24-recruitmatch-v2-cp5-observability.md

## Identity and authority

- Worktree: /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2
- Branch: codex/recruitmatch-cp5-observability
- Baseline: f297b0a509fca92b37de1be174e18a601ccfd64e (merged CP4 + official Quay fix).
- Frozen authority: docs/superpowers/specs/2026-08-24-recruitmatch-productionization-design.md, all 978 lines read by controller.
- User's latest “下一步” proceeds to the already-approved CP5 plan after CP4 merge. No push, PR creation or main merge authorized for CP5.
- Use existing isolated worktree, preserving previous branch, ignored configuration/data, and other plans' scratch. No real .env, user data, external models or model downloads.
- Baseline: current worktree clean; test-unit 286 passed in 17.53s using existing exact-source Compose test image. Main merge CI had all three lanes successful.

## Task checklist

- [x] Task 1 — OTel adapter, typed signals, bounded SDK lifecycle and privacy policy (accepted at d9dc9ab after fix round1).
- [ ] Task 2 — framework and recruiting instrumentation, logging and operational metrics.
- [ ] Task 3 — internal Collector/Prometheus/Tempo, local-only Grafana and dashboards.
- [ ] Task 4 — real metric/trace queries, PII and Collector-outage business isolation.
- [ ] Whole-checkpoint review, final verification, evidence archive and finishing choice.

## Preflight consistency table

| Tasks | Shared production/consumption boundary | Checked result |
| --- | --- | --- |
| 1 self | Two-argument DomainEvent vs counters/histograms; settings vs provider lifetime | Plan lacks numeric observation and duration carrier; R1 below. Private SDK lifetime must remain testable, disabled must not export. |
| 2 self | Instrument listed services vs frozen explicit spans/JSON logs and actual CP4 interfaces | Listed files omit logging, leases/recovery, retrieval/artifact adapters and composition; R2 below. No SDK imports in domain services. |
| 3 self | YAML/JSON substring examples vs genuine validation and alert Firing | Source-text checks cannot prove provisioning; use parsed/validated configs and actual backend checks in Task 4 (R3). No container-resource claims. |
| 4 self | stack.stop inside runner vs prohibition of Docker Socket | Use host-side bounded Compose orchestration limited to owned test project, and in-container query/assertions (R4). |
| 1,2 | Privacy-safe provider/recorder -> auto-instrumentation/domain recording | R1/R2: SDK-free Port, bounded values, safe span interface at adapters; filter before storage/export, not only domain event attributes. |
| 1,3 | SDK endpoint/resource/sampling -> Collector OTLP receiver | Agree: gRPC OTLP internal, Compose enabled sampling1.0, units unchanged; schema options must have safe bounded defaults. |
| 1,4 | SDK injection/shutdown/outage -> in-memory and real exporters | Agree: tests inject readers/exporters, no network unit tests, disabled creates no exporting threads, bounded failure/shutdown. |
| 2,3 | Exact OTel instruments/finite labels -> PromQL dashboards and rules | Agree: standard HTTP metrics not duplicate recruitmatch HTTP; resolve actual Prometheus translated names with live query; PG is queue authority. |
| 2,4 | API publication context -> worker spans and terminal counters | Agree: only W3C traceparent/tracestate, no business Baggage; Beat recovery starts fresh root; no claims from mocked trace IDs. |
| 3,4 | Real provisioned backends -> queries/outage and alerts | Task 4 must prove actual Prometheus/Tempo data and firing rules; no Docker Socket, no Grafana business DB, no masking missing backend tests (R3/R4). |

## Preflight rulings

Ruling: R1 — Extend the existing SDK-free event contract additively with a numeric observation and an SDK-free operation scope as needed for durations/spans; retain two-argument construction and Noop defaults. Instrument names/types/UCUM units remain exactly spec9.2 and aliases map existing domain names, rather than creating arbitrary metric names. — The plan's two-field sketch cannot carry histogram values or span duration, but spec3.1 forbids domain SDK imports. — Cost if wrong: rework the small event Port and its new consumers; no business/schema behavior changes.

Ruling: R2 — Treat the plan's file lists as an initial map; allow focused logging/composition/lease/recovery/artifact/retrieval instrumentation files necessary to satisfy frozen9.1–9.4. Privacy covers span names, attributes/values, status descriptions, events/links/resources, metrics and ordinary JSON logs, including framework exceptions and URL identifiers. Keep database security audit separate. — Current main logs raw request paths/exception strings and emits no domain metrics; task list alone omits required surfaces. — Cost if wrong: additional small adapter wrappers and log tests require maintenance; unrelated refactoring remains prohibited.

Ruling: R3 — Replace plan illustration-only source substring assertions and vacuous negative tests with behavioral in-memory SDK tests, actual config validation/provisioning and real metric/trace/alert queries. Check positive expected output alongside PII absence. — The frozen acceptance criteria concern observable output and the test rubric rejects source-text/change-detector tests. — Cost if wrong: tests require real backend startup and take longer, but no acceptance gate is weakened.

Ruling: R4 — Collector-outage E2E is orchestrated by a bounded host shell helper using Docker Compose against only the named synthetic CP5 project; in-container runners perform probes/assertions without Docker Socket. Keep Fake Model/normalized512d Fake Embedding in test-only composition, with no real model keys/downloads. — The plan's in-container stack.stop sketch conflicts with frozen no-Docker-Socket boundary; actual service stop must not affect user stacks. — Cost if wrong: maintain a small orchestration helper/test composition; no new production control surface or CP6 load-test system.

## Execution

Task 1: in progress; implementer /root/cp5_task1_impl (gpt-6-astra/high). Base f297b0a509fca92b37de1be174e18a601ccfd64e. Controller independently mapping Task2's unchanged integration seams; no concurrent production edits.

Task2 seam notes recorded in `task-2-seams.md`. Multi-writer metric identity is an unresolved frozen-design tension: N workers vs no Worker ID/high-cardinality labels. Controller verified official OTel single-writer and Collector interval/delta behavior; neither identical cumulative resources nor simply switching to deltas is a correct solution. Requested user approval asynchronously for a system-generated telemetry-only service.instance.id exception, separate from business/internal heartbeat IDs. No exception applied. Task1 continues with fixed safe resources; do not start a conflicting Task2/3 implementation before resolution.

Task 1 implemented at `5abe44f74eab7ed962d41de63da212a8b4f20990`; not yet accepted. Controller fully read task-1-report.md; rebuilt-image evidence reports 362 passed (76 observability), lock/API dependency compatibility, scoped Ruff/mypy, regression RED/GREEN for owner isolation, foreign-span privacy and async decorator parentage. Working tree clean. Review package `review-f297b0a..5abe44f.diff` generated from recorded task base (106894 bytes). Independent task review dispatched to `/root/cp5_task1_review` (gpt-6-astra/high) using task-1-review-constraints.md and standard reviewer contract. Parent performs non-mutating lock/Compose/VCS checks; no concurrent production changes.

Controller validation at 5abe44f: `docker compose --env-file .env.example -p recruitmatch-cp5-sep14 config --quiet` and `git diff --check f297b0a HEAD` passed; diff stat exactly 9 implementation/test/lock files, 1578 insertions and 24 deletions; status clean. Offline pinned uv lock check initially emitted the known unrelated host .venv broken-interpreter warning. Re-ran with `-e UV_PROJECT_ENVIRONMENT=/tmp/recruitmatch-lock-check-env` on the read-only source mount: CPython3.12.14, 131 packages resolved in1ms, no warning. Exact final test image `uv pip check`:127 packages compatible. No user configuration changes or application test duplication during task review.

Task1 review: spec Issues found, quality Needs fixes. Full report saved as task-1-review.md. Important I1 real OTLP UNKNOWN exception logging bypasses quiet delegate wrapper; I2 direct safe meter bypasses 0–100 match.score upper bound. Controller verified the relevant wrapper/recording source and review's synthetic reproductions. Returning both findings as one fix round1/5 to original implementer, fix base5abe44f. No controller production edits.

Task1 minor (deferred): SafeMeter drops explicit_bucket_boundaries_advisory, producing coarse SDK histogram defaults for second-based HTTP metrics. Carry this into Task2 actual standard HTTP instrumentation and the final review; verify subsecond buckets before accepting P95 dashboards. Not included in Task1 Important-fix loop.

Task1 Cannot Verify resolution: multi-writer deployment remains an explicit approval-blocked Task2/3 dependency; no deployment acceptance claimed. Enabled-prefork rebinding and before-middleware-construction API lifecycle are Task2 integration and Task4 real-E2E gates, now added explicitly to task-2-seams.md. Current Task1 disabled-fork test is not claimed to prove enabled worker correctness.

Ruling: R5 — Represent future cleanup failures via spec9.2 artifact.operation with operation=delete/outcome=failure plus stable code/duration; no extra artifact.cleanup_failed instrument. — Section3.1's illustrative event must not create an instrument outside the normative registry. — Cost if wrong: revise the small cleanup emitter/alias mapping and matching dashboard query; no storage/state changes.

Task1 fix round1/5 implemented at d9dc9ab3aeaa935c0bcf2f596bd6c6add511be92. Controller read entire appended fix report: real span/metric OTLP UNKNOWN RED sentinel leak -> scoped process-lifetime public exporter logger filter GREEN; recorder/direct-meter score0..100 both RED/GREEN. Rebuilt image 30d1d412d2fc163750e0a60ea4b18ecf34b8bfad41c43b3efe3d27a238d66620 has79 focused tests passing plus Ruff/strict mypy. Status clean. Fix-only package review-5abe44f..d9dc9ab.diff generated (13345 bytes); scoped review dispatched `/root/cp5_task1_rereview` (gpt-5.6-sol/high). Parent now executes final full-unit/static checks on the rebuilt exact-source image; no production edits during re-review.

Controller final verification at d9dc9ab: full `docker compose --env-file .env.example -p recruitmatch-cp5-sep14 run --rm --no-deps test-unit` returned **365 passed in21.32s**, no warnings/failures. Full repo Ruff check app/tests/scripts passed and format check reported215files formatted. Exact existing CI mypy targets `app/models app/retrieval app/services app/ai app/tasks` passed41files. Additional scoped observability/config/main with `--follow-imports=silent` passed6files; observability `--check-untyped-defs --follow-imports=silent` passed4files. All9 changed implementation/test/lock files' host SHA256s equal image SHA256s. Docker image Descriptor confirms OCI image index sha256:7287722c373746429a393b8a0c2ac19654ba324833c05f939d95b97b811a27fa (different identifier layer from build config digest30d1d412...). Working tree and range diff check clean.

Additional diagnostic (not hidden as a green gate): broadening current CI mypy to include main/config and follow imported API modules emitted2 type errors, in unchanged `app/api/v1/resumes.py:30` and `app/api/v1/knowledge.py:54`: original_filename `str | None` passed to response field `str`. Repeated equivalent broad check in the retained CP4 baseline image recruitmatch-ci-fix-20260914-app-test and reproduced exactly the same2errors (43sourcefiles vs47includingnewobservability). `git diff f297b0a HEAD` for both APIs and their source models is empty. No API/source/model changes were made. Track this pre-existing optional-broader-scope typing debt in the final checkpoint review; do not claim global mypy coverage is clean or silence errors to hide them.

Task1 fix round1/5: independent scoped re-review `/root/cp5_task1_rereview` confirms both Important findings ADDRESSED, no new Critical/Important breakage. Saved complete verdict as task-1-fix-1-review.md, with controller correction of a citation-only extra endpoints directory in the out-of-scope typing note. No production edit followed this verdict.

Task 1: complete (commits f297b0a..d9dc9ab; implementation5abe44f + fixd9dc9ab; task review and scoped fix review accepted). Deferred minor and cross-task guarantees remain explicitly tracked above; no whole CP5 completion, production-ready telemetry stack, remote CI, push or main merge claimed.

Current stopping condition: user approval is still pending for a minimal telemetry-only service.instance.id privacy/label-policy exception. It is security-sensitive because frozen allowlist and Worker-ID restrictions must not be silently weakened. Do not implement/enable multi-writer telemetry or force solo workers as a workaround. Continue Task2 after user resolves this decision, incorporating Task2 seam notes, deferred histogram precision and enabled-prefork/API-lifecycle tests. The isolated branch remains local; main is unchanged/clean at f297b0a.

Evidence is archived under docs/verification/cp5-evidence for the accepted Task1 stage. Live .superpowers/sdd workspace remains for continuation, not deleted. Later tasks still require per-task reviews and a final whole-checkpoint review before finishing/integration choices.
