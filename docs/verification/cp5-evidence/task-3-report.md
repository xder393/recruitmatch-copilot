# Task 3 implementer report — 2026-09-14

Status: DONE; final verification passed. Local commit recorded below.
Base: `26f9d9b50fe9449c888db46ba6095544e9c27409`.
Branch: `codex/recruitmatch-cp5-observability`.
Worktree: `/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2`.
Commit: `863a4ffa25e806c956608742ee47eda3dee3b0c6` — `ops: provision RecruitMatch metrics traces and dashboards`.
Post-commit `git status --short` is empty; the ignored SDD report is intentionally outside the implementation commit.

## Implementation

- Added internal OTLP Collector → Tempo traces and Collector → Prometheus OTLP/HTTP metrics. Prometheus scrapes only Collector `:8888` and has six fresh/latest Beat gauge recording rules plus ten alerts. No application scrape, notification stack, metrics generator, Kafka or Docker socket.
- Pinned the four new images to the controller-verified multi-platform manifest-list digests. Used exact pinned binaries to validate final files and actual running endpoints to validate liveness.
- Added Grafana filesystem provisioning for the four required dashboards and only Prometheus/Tempo datasources. Anonymous access and optional reporting/update/plugin-preinstall network fetches are disabled; credentials come from Compose environment/example configuration. API/Grafana publish only loopback 8000/3000.
- Enabled telemetry with role-specific service names in API/worker/Beat composition, keeping demo sampling 1.0 and no business dependency on telemetry readiness. Both ordinary test lanes explicitly disable telemetry, including inherited integration environment. Preserved Celery `--quiet` options.
- Added parsed offline config tests, an explicitly invoked real-backend provisioning/privacy/name probe and promtool rule fixtures. The test image copies the new operations assets so final checks do not hide missing content with host mounts.
- Added focused operator note `docs/observability-stack.md`, including finite-label policy, current rendering names, startup/probe commands, snapshot freshness and Task 4 limitations. `.env.example` now describes Compose PostgreSQL/Celery and synthetic Grafana settings. No dependency or lock changes were necessary: PyYAML was already installed and used by existing container-contract tests.

## Privacy, boundedness and controller decision R13

Processor order for both signals is `memory_limiter → filter/redaction → transform/privacy → batch`. Both privacy stages use `error_mode: propagate`. Resources must identify a known service/environment and UUIDv4 writer. Unknown instrument names are filtered. Only finite attribute values survive; known metric units and scope metadata are normalized. Resource/scope schema URLs, free-form metric descriptions, unapproved resource/span/datapoint attributes, unsafe span names/status descriptions and span tracestate are removed or normalized. Resource `service.instance.id` survives to Prometheus `instance`, but it is not accepted as a span/datapoint label.

R13 was explicitly raised to and resolved by the controller: secondary defense drops optional events, links and exemplars entirely while retaining primary spans and parentage. It retains only reviewed demo model names `deepseek-chat` and `BAAI/bge-small-zh-v1.5`; unfamiliar optional model labels are removed without dropping their whole span/metric. Cost if this is too restrictive is lost optional diagnostic detail/labels requiring reviewed configuration rework, not lost business work or trace parentage. This is a narrower secondary policy, not a privacy expansion.

Actual pinned Collector execution discovered that `set(..., [])` validates but cannot assign an empty generic slice to typed event/link/exemplar slices. Primary pinned source shows `SetPSliceValue` clears on nil, and `set` requires explicit feature gate `ottl.set.allowNil`. Compose enables that gate and final config uses nil; the real positive/negative probe verifies the statements execute. `set(..., nil)` without this gate must not be substituted. Relevant primary source inspected:

- `https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/v0.160.0/pkg/ottl/contexts/internal/ctxutil/slice.go`
- `https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/v0.160.0/pkg/ottl/ottlfuncs/func_set.go`
- Pinned `ctxresource/resource.go` and `ctxscope/scope.go` expose writable schema URLs, despite abbreviated README tables.

Collector uses 192MiB memory limit/48MiB spike, 256-item batches/max512, 1s batching, two consumers and 256-slot queue per exporter, `wait_for_result:false`, `block_on_overflow:false`, 2s timeout and 30s maximum retry. No persistent telemetry queue or synchronous application readiness gate.

## TDD evidence

All Python/test commands below ran through Compose in the owned synthetic project. Define command prefix `D` in this report as the exact text:

`docker compose --env-file .env.example -p recruitmatch-cp5-sep14`

1. RED before production stack files: `D run --rm --no-deps -v ./tests/observability/test_observability_configs.py:/app/tests/observability/test_observability_configs.py:ro test-unit pytest tests/observability/test_observability_configs.py -q` → **4 failed**. API still had `8000:8000`, Grafana port absent, and Collector/Prometheus/Grafana files absent. This deliberately used the accepted predecessor image plus the newly written test, not a baseline suite rerun.
2. Live prerequisite RED: `D run --rm --no-deps -e GRAFANA_ADMIN_USER=admin -e GRAFANA_ADMIN_PASSWORD=demo-grafana-password-change-me -v ./tests/observability/provisioning_probe.py:/app/tests/observability/provisioning_probe.py:ro test-unit pytest tests/observability/provisioning_probe.py -q -x` → **1 failed**, DNS unavailable for missing backend. There is no skip behavior.
3. Actual privacy execution RED: same live probe with `-k preserves` → **503** because `set(datapoint.exemplars, [])` cannot assign a generic slice. Fixed with nil plus explicit gate. This demonstrates why config-only checks were insufficient. The corrected positive metric and trace passed after adjusting Tempo's documented protobuf JSON status/parent ID representation in the probe.
4. Registry probe exposed actual rendering: `recruitmatch_match_score_count`, not a guessed `_ratio_count`; Collector internal counters have no `_total` suffix. Queries were corrected to match actual backend names, with positive per-writer rates/P95 validated.
5. Rule RED: `D run --rm --no-deps --entrypoint /bin/promtool -v ./tests/observability/rules.test.yml:/tmp/rules.test.yml:ro prometheus test rules /tmp/rules.test.yml` → **FAILED CollectorExportFailure**, expected firing alert but got none with a send-only failure series. Fixed names and removed vector addition that would suppress a failure when the other failure series is absent. Same command → **SUCCESS**. Expanded fixtures cover all ten alerts and latest old-positive → replacement-zero → expiry-to-unknown behavior.
6. GREEN focused development checks: offline **4 passed**, live **3 passed**, then final combined image-only check **7 passed**. Final output/identity is recorded below. Development-only test mounts were removed for final image verification.

## Exact versions and service validation

| Component | Pinned image |
| --- | --- |
| Collector | `otel/opentelemetry-collector-contrib:0.160.0@sha256:799dc6cf12c96192af37b5bdba804da8c10b3bc563b43cb90c3f3c58d9572ad6` |
| Prometheus | `prom/prometheus:v3.14.0@sha256:5ce7540c3c00ef4ab0c9d2c995c6a5b9c421f44b4a115d97a2c7af3b1c21cbb0` |
| Tempo | `grafana/tempo:3.0.3@sha256:0296560ac66f8a3600d7fb3014a52c189d4d9c3549ad6ff441bf2409855d68d5` |
| Grafana | `grafana/grafana:13.2.1@sha256:f772d434e8fab0049deb2b1b30abd43342bcfca1537614aa8d36080232cf4283` |

Executed final configuration commands:

```sh
D config --quiet
D run --rm --no-deps otel-collector validate --config=/etc/otelcol-contrib/config.yaml --feature-gates=ottl.set.allowNil
D run --rm --no-deps --entrypoint /bin/promtool prometheus check config /etc/prometheus/prometheus.yml
D run --rm --no-deps tempo -config.file=/etc/tempo/tempo.yaml -config.verify=true
```

All exit0. Promtool: `SUCCESS: 1 rule files found`, valid config syntax, `SUCCESS: 16 rules found`. Pinned Tempo 3.0 removed the legacy compactor field; the final minimal monolithic local WAL/blocks configuration validates and runs with no Kafka. No legacy compactor or derived-metric configuration remains.

Started only the owned new services using `D up -d otel-collector prometheus tempo` and `D up -d grafana`. Recreated Collector/Tempo during focused compatibility fixes; restarted owned Prometheus after final rule edit so running evaluation uses the current expressions.

Actual internal HTTP evidence from final-source runner:

```text
Collector http://otel-collector:13133/ 200
Prometheus http://prometheus:9090/-/ready 200
Tempo http://tempo:3200/ready 200
Datasources [('prometheus','prometheus','http://prometheus:9090'),('tempo','tempo','http://tempo:3200')]
Dashboard ai-rag-pipeline AI & RAG Pipeline
Dashboard async-reliability Async Reliability
Dashboard recruiting-business Recruiting Business
Dashboard system-overview System Overview
Anonymous /api/datasources 401
Anonymous /api/search 401
Anonymous /api/dashboards/uid/system-overview 401
Running rules: 6 recording + 10 alerting; all health=ok, current expressions match exact-source image and query successfully
Embedding TraceQL 200
```

The manual running-rule comparison keys by rule name (the API sorts groups differently than the source); an initial order-dependent check was corrected before the successful comparison. All dashboard PromQL queries are submitted to actual Prometheus by the live test; the embedding TraceQL query was also submitted to actual Tempo. Metrics have positive actual ingestion/rates/histogram quantile evidence, not only syntactic query success.

`docker inspect` shows Collector `running no-Docker-Health {}`, Prometheus `running healthy {}`, Tempo `running healthy {}`, Grafana `running healthy {"3000/tcp":[{"HostIp":"127.0.0.1","HostPort":"3000"}]}`. This honestly implements R12: Collector health is the real extension endpoint from the runner, not a fake Docker healthcheck. API's intended loopback8000 binding and lack of telemetry dependency are parsed from final Compose; no API host8000 listener was started because that port belongs to unrelated odp. Task 4 owns actual API/broker/prefork startup testing with its override.

## Actual data assertions

- A synthetic counter arrives in Prometheus with value7, `outcome=success`, `source_type=resume` and its distinct UUID `instance`. An injected unknown metric name is absent. Optional unrecognized model name and sensitive tenant/resource/scope/schema/exemplar payloads do not appear in queried data.
- Two synthetic spans are retrievable by their trace ID in Tempo: `resume.process` remains; an unsafe name becomes `internal.operation`. Parent ID remains exactly the sent parent; error status remains `STATUS_CODE_ERROR`. Retained finite outcome/model values are asserted, and all injected free text/status descriptions/events/links are absent from the complete returned trace JSON.
- All27 approved domain instruments and3 standard HTTP instruments reach actual Prometheus with the expected rendered names; two random writers remain separate. Per-writer counter rates are positive and aggregated HTTP P95 lies in the expected0.5..1s range. `retry_due` is retained. Latest gauge recording output is positively checked.
- Promtool fixture tests demonstrate old positive → new zero replacement, per-source isolation and expiry to no data, plus Firing for each of the ten configured alerts. This is fixture Firing, not Task4 actual runtime Firing acceptance.
- A search of the current Collector logs for the synthetic privacy sentinel returned no matches. No real credentials, recruiting data or model calls were used.

## Image identity and regression

Initial full build used `HTTP_PROXY=http://127.0.0.1:12001 HTTPS_PROXY=http://127.0.0.1:12001 D build test-unit api`; frozen dependency layers were cached, and no models were downloaded. Self-review then found trailing whitespace in the rule test fixture; it was removed, the test image rebuilt and final suites repeated. The final code/config behavior is unchanged by that cleanup.

Final test image `recruitmatch-cp5-sep14-app-test:latest`: `sha256:ed4049093c1aef27ed4ad77e3997a32b5235d7385b57ace0d954543f1c4857f0`.
Runtime image `recruitmatch-cp5-sep14-app:latest`: `sha256:a2590cd81e188f51e324ba98705b7e965c9886f17ce4449f33a9bd2e1b60f506`.

Final exact-image verification results (all exit0, no failed/skipped tests or test warnings):

| Exact command after expanding D above | Final output |
| --- | --- |
| `D run --rm --no-deps test-unit` | `399 passed in 43.98s` |
| `D run --rm test-integration` | `520 passed in 42.27s` |
| `D run --rm --no-deps -e GRAFANA_ADMIN_USER=admin -e GRAFANA_ADMIN_PASSWORD=demo-grafana-password-change-me test-unit pytest tests/observability/test_observability_configs.py tests/observability/provisioning_probe.py -q` | `7 passed in 7.36s` |
| `D run --rm --no-deps test-unit ruff check app tests scripts` | `All checks passed!` |
| `D run --rm --no-deps test-unit ruff format --check app tests scripts` | `225 files already formatted` |
| `D run --rm --no-deps test-unit mypy app/models app/retrieval app/services app/ai app/tasks` | `Success: no issues found in 41 source files` |
| `D run --rm --no-deps test-unit uv lock --check --offline` | `Resolved 131 packages in 5ms` |
| `D run --rm --no-deps --entrypoint /bin/promtool -v ./tests/observability/rules.test.yml:/tmp/rules.test.yml:ro prometheus test rules /tmp/rules.test.yml` | `SUCCESS` |
| `git diff --cached --check` | exit0, no output |

Full test/static and combined live runs used the final image without source mounts. The promtool fixture command intentionally mounts the actual repository fixture read-only into the pinned Prometheus binary; its production rule mount is the final file whose hash also matches the source image. Both test Python and rule fixture final SHA256 values were independently matched host versus final image after the final build. No baseline suite was run before implementation; later repeats followed explicit test-fixture improvements.

Host SHA256 and hashes read inside the source image agree for Dockerfile/Compose, Collector/Prometheus/Tempo configs, the two Python probes and all six Grafana provisioning/dashboard assets. Representative content hashes (unchanged by fixture whitespace cleanup):

```text
6beccafdd601805efc1acdbff9646287224e99aa89faf7430610622f897c2ddb docker-compose.yml
24f262d297e1520695874b5a88c9c222f6e82fdcfd1c7ce77db39d6f00e3b4d4 ops/otel/collector.yaml
33a048ad8e53df39b9241ae3c1f7d5782d23945c9c0cf33ea5af413d6dac6f18 ops/prometheus/rules.yml
2c86c35016412e2153aa98b8f74ccc3bf08dfff2a70d75a318f4f625a973f86e tests/observability/provisioning_probe.py
e66d98cefca2794f2f3e8937fd557a8a41c2d6586a2c63bc9e63ee2f0bb8c45e tests/observability/rules.test.yml
d80ef29fc2418b7d160c27d63868b3eb15adf2973b188e61676cffae9a1fad97 ops/grafana/provisioning/datasources/datasources.yaml
6015a2e72eddd94f8791d32996cf5932ca22e8df06a3fc9c9131b91d3253eb08 ops/grafana/provisioning/dashboards/dashboards.yaml
4a6c2b3ed050386c34dd0b41b95394dd7aa0899233016a8dc92e75cb8b7031e4 ops/grafana/dashboards/ai-rag-pipeline.json
9805bc02362133cdd981e895aab39f67376e756773b7b7e8a7ebcb0bb3db4920 ops/grafana/dashboards/async-reliability.json
1bf25375d3d8fc5d2346e0d3c7ea5ac99e8e32ae00e0679c59f132d568b6edaf ops/grafana/dashboards/recruiting-business.json
a0abeb9ee11d137ef683d451b3c62315cd99e7bad7bb2a473165495f8bac69a2 ops/grafana/dashboards/system-overview.json
```

## Self-review and remaining boundaries

Read the staged composition/privacy/rules/provisioning/test diff and checked dashboard queries/titles against the accepted emitter semantics. Fixed the nil-slice runtime bug, obsolete Tempo field, actual metric-name mismatches, send-only alert suppression, test polling assumptions and fixture whitespace. Also strengthened the unknown-metric negative assertion to require exactly the intended metric plus target_info: raw sentinel substring matching alone could miss Prometheus's normalized names. Rebuilt and repeated final checks after this test-only strengthening. `git diff --cached --check` passes after cleanup. No application production code, existing instrumentation semantics, CP6 scope, Actions/images unrelated to Task3 or user data was changed.

Dashboards identify retry as the overlapping failed-attempt subset, model requests as physical attempts, fallback as logical degradation, match score as0–100 and missing gauges as unknown. They do not invent CPU/RAM, dependency health, process-once outcomes, business identities, SLAs or alert notifications. Latest gauge selection matches the controller-probed pattern; full live restart/invalidated snapshot timing is still Task4. The default Beat absence alert describes the single deployed Compose stack and does not infer which dependency failed.

Task4-dependent guarantees remain: real API→broker→prefork parentage, full user-facing end-to-end PII proof, actual runtime alert Firing, SDK writer restart under real worker/Beat lifecycle, and Collector outage/business availability. No CP5 completion claim is made here. Test telemetry is synthetic and can leave temporary demo series/alerts in the owned backends; no recruiting records were written by these probes. Ordinary integration tests used only the existing owned synthetic PG/Redis/MinIO project.

No pushes, PRs, main merges, subagents, reviewer dispatch, destructive cleanup, host Python/venv, actual.env access or model downloads. Unrelated odp containers/ports/volumes are untouched. This report remains untracked SDD evidence for controller archival; it is not force-added to the implementation commit.

Changed files: `.env.example`, `Dockerfile`, `docker-compose.yml`, `docs/observability-stack.md`, `ops/otel/collector.yaml`, `ops/prometheus/prometheus.yml`, `ops/prometheus/rules.yml`, `ops/tempo/tempo.yaml`, two YAML files under `ops/grafana/provisioning`, the four required JSON files under `ops/grafana/dashboards`, and `tests/observability/{test_observability_configs.py,provisioning_probe.py,rules.test.yml}`. Seventeen files, 2572 insertions and8 deletions; no scratch evidence added to git.
