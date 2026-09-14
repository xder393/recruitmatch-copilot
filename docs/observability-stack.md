# Local observability stack

Use Docker Compose with a disposable environment derived from `.env.example`.
The example credentials are public demo values. Grafana requires an explicit
password and disables anonymous access. Only API `127.0.0.1:8000` and Grafana
`127.0.0.1:3000` publish host ports. Collector, Prometheus and Tempo are reachable
only through the Compose network. No Docker socket is mounted.

API, each worker child and Beat export OTLP to Collector. Traces go to Tempo;
metrics go to Prometheus's OTLP receiver. Prometheus's only scrape target is
Collector on `:8888`. Grafana provisions four dashboards and exactly two data
sources, Prometheus and Tempo. No recruiting database, infrastructure exporter,
Loki, Alertmanager, notification integration or Tempo metrics generator is used.

Telemetry is enabled by default for application Compose services, sampled at
1.0 for this demo. Metrics are unsampled. `TELEMETRY_ENABLED=false` disables
application export. Ordinary unit and integration services explicitly disable
telemetry. API/worker/Beat have no startup or readiness dependency on this stack;
Collector has a bounded memory budget, batches and queues, 2s export timeout,
30s maximum retry duration and nonblocking queue overflow.

## Start and validate

```sh
docker compose --env-file .env.example up -d otel-collector prometheus tempo grafana
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example run --rm --no-deps otel-collector validate --config=/etc/otelcol-contrib/config.yaml --feature-gates=ottl.set.allowNil
docker compose --env-file .env.example run --rm --no-deps tempo -config.file=/etc/tempo/tempo.yaml -config.verify=true
docker compose --env-file .env.example run --rm --no-deps --entrypoint /bin/promtool prometheus check config /etc/prometheus/prometheus.yml
docker compose --env-file .env.example build test-unit
docker compose --env-file .env.example run --rm --no-deps test-unit pytest tests/observability/test_observability_configs.py -q
```

Use the same `-p PROJECT` on every command when using a named disposable project.
Do not stop another project to free an occupied host port. A test-only override
may clear published ports and run all probes through internal service DNS.

The official Collector image is scratch-based: it has no shell or HTTP client.
Docker reports it as running without a Docker Health field. Configuration
validation is not liveness. The explicit live test checks the actual internal
health extension at `http://otel-collector:13133/`, backend readiness, all four
provisioned dashboard objects, datasource URLs, unauthenticated API rejection,
synthetic positive OTLP metric/trace data, privacy and actual metric names.
The command below deliberately uses the public `.env.example` demo identity:

```sh
docker compose --env-file .env.example run --rm --no-deps -e GRAFANA_ADMIN_USER=admin -e GRAFANA_ADMIN_PASSWORD=demo-grafana-password-change-me test-unit pytest tests/observability/provisioning_probe.py -q
docker compose --env-file .env.example run --rm --no-deps --entrypoint /bin/promtool -v ./tests/observability/rules.test.yml:/tmp/rules.test.yml:ro prometheus test rules /tmp/rules.test.yml
```

Use the configured credentials for a non-example environment. The explicit
probe fails when prerequisites are unavailable; it never silently skips.
It writes only synthetic telemetry with fresh random writer/trace IDs, and
may activate demo rules. It does not modify recruiting records or call models.

## Privacy and query semantics

The application policy is the first defense. Collector then applies
`memory_limiter -> filter/redaction -> transform/privacy -> batch` in that order.
It accepts only known services, environments, UUIDv4 writer identities and
instrument names. It normalizes units and scope metadata, clears resource/scope
schema URLs and drops unapproved resource and datapoint/span attributes.
Optional values must match finite allowlists. Span names fall back to
`internal.operation`; status descriptions and tracestate are cleared. All span
events, links and metric exemplars are removed. Parent/trace/span IDs remain.
The exact pinned Collector requires `--feature-gates=ottl.set.allowNil` for
clearing typed event/link/exemplar slices; config validation alone cannot prove
the statements execute. The live privacy test covers those surfaces.

Collector retains only the reviewed demo model names `deepseek-chat` and
`BAAI/bge-small-zh-v1.5`. Other configured models retain their spans/metrics but
lose the optional model-name label. Extending this list requires review; do not
replace it with a free-text regex. This deliberately reduces optional detail.

Prometheus preserves Resource `service.instance.id` as the technical `instance`
label and `service.name` as `job`. Counters/histograms use rate per writer before
aggregation. Actual domain counter names end in `_total`; seconds histograms
end in `_seconds_bucket/_sum/_count`. `recruitmatch.match.score` renders as
`recruitmatch_match_score_*` with no ratio suffix and a 0–100 score scale.
Pinned Collector internal counters have no `_total` suffix; its failure rule
accepts independent send/enqueue series so an absent zero-failure series cannot
hide the other failure type.

The six aggregate gauge recording rules choose the latest Beat producer per
environment/source grouping and require a sample less than 30s old. This avoids
old positive writers hiding a replacement's zero. Missing values are unknown,
never coerced to healthy zero. Time is export time, not dependency observation
time: the 25s application snapshot lifetime plus 5s export cadence, up to 1s
Collector batching and 30s query freshness can delay stale-data detection;
network delays and retries may add further delay. Alerts add evaluation and
`for` intervals. These snapshots do not prove immediate dependency health.

Failed attempts and scheduled retries overlap; do not add them as exclusive
outcomes. Successful task publication and committed match runs are distinct
from task return status. Physical model requests and logical fallback decisions
are different signals; fallback is pipeline degradation, not system downtime.
Embedding appears as batch/query traces; no embedding metric is invented.

Rules cover HTTP 5xx/P95, queued-source age, zero live workers, stale/unavailable
Beat observations, cleanup backlog, AI fallback, Collector send/enqueue failure
and queue pressure, and lease takeover. Thresholds are demo defaults, not SLAs.
Beat absence alerts on missing observations for the single deployed Compose
stack; it does not identify the failed dependency from absence alone. Fixture
tests prove each rule can fire and that replacement gauges expire correctly.
Task 4 separately verifies actual runtime Firing, API/broker/prefork parentage,
end-to-end privacy, writer restart and Collector outage behavior. No production
on-call notification system is claimed.
