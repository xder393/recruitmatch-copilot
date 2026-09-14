# CP4 recovery and health

The supported full runtime uses Docker Compose: PostgreSQL with pgvector, Redis,
the private MinIO bucket, the API, one Beat process and one or more Workers.
Stop legacy Workers before migrating. They cannot honor the processing lease
epochs. Run bootstrap against the existing database, then start lease-aware
Workers and exactly one Beat. Revision `20260912_19` adds dispatch metadata only;
it does not reset Sources, attempts, generations, citations or Artifacts.

For an explicitly named installation and environment file:

```sh
docker compose --env-file .env.example -p recruitmatch-demo run --rm bootstrap
docker compose --env-file .env.example -p recruitmatch-demo up -d api worker beat
docker compose --env-file .env.example -p recruitmatch-demo up -d --scale worker=2 worker
```

Use the installation's explicit environment file and project name consistently.
Do not start a second Beat or use `worker -B`. AI disabled does not disable BGE
embedding: ordinary Workers still use the configured embedding model. Automated
synthetic smoke tests use `tests.support.celery_fake_worker`, an explicitly
injected deterministic embedder, AI off, empty model key and offline model flags.
That entrypoint is test-only and is not a production fallback.

## Recovery decisions

The real Celery `RecoveryScheduler.tick` executes PostgreSQL scans and guarded
transitions in Beat itself, even with no Worker. Every tick returns a ten-second
delay after its bounded work. Each Source type has a separate batch of at most
50 candidates. Stale QUEUED means queue age at least 60 seconds; retry eligibility
and the separate 60-second dispatch cooldown must also be satisfied. Expired
RUNNING and due transient FAILED retries are considered independently of queue
age. Rows are ordered by `COALESCE(recovery_dispatch_at, queued_at)`, then tenant
and Source ID. This gives progress beyond a batch, including old failed
reservations competing with newly stale unreserved work. It is global oldest
visit order within each type, not tenant service quotas or a throughput claim.

Candidates require the exact tenant/type/owner-linked AVAILABLE Artifact and
active lifecycle. Before mutation, recovery locks Source then Artifact and
revalidates the snapshot's epoch, Artifact, status, queue cycle and reservation
token. PostgreSQL wall clock is sampled after locks. No application clock can
override this authority; tests change synthetic persisted eligibility times.
Both row locks use SKIP LOCKED. A held Source or Artifact is skipped without
rewriting its state or dispatch pacing; its transaction releases any acquired
Source lock. Other selected candidates, maintenance dispatch and Beat heartbeat
continue. The same rule applies to late publish-failure recording: under
contention the optional error annotation may be absent, but the already committed
reservation remains recoverable after cooldown. Genuine database failures are not
silently treated as contention. Selection remains bounded; skipped rows are not
replaced from beyond the selected batch, so this is not a strict fairness or
latency guarantee under sustained contention.
An expired run requeues without incrementing epoch/attempts. Eligible expired or
queued work already at the configured cap becomes FAILED with
`processing_attempts_exhausted`. A valid fifth-attempt lease stays untouched.
Due FAILED work uses the existing guarded `leases.requeue_due` policy: only known
transient failures below the cap can requeue. Permanent and exhausted failures
remain terminal.

Recovery commits a fresh UUID reservation and database timestamp before broker
publication. It never refreshes the queue age of an existing QUEUED row and never
sets a future `next_retry_at` to throttle dispatch. A Worker can claim immediately
after publication. Failure records `processing_dispatch_unavailable` in the
separate dispatch-error field, only if the exact reservation and queued snapshot
still match; it cannot overwrite a new claim or queue cycle. Business retry error
codes remain intact. The dispatch fields describe the last recovery reservation,
not a second processing status graph.

A crash before reservation commit leaves the original work eligible. A crash
after commit but before publish delays another attempt until cooldown. A crash
after publish can cause another delivery after cooldown; existing lease guards
make this at-least-once behavior safe. PostgreSQL remains authoritative. Redis
has no result backend or business task status. Broker publication retries are
disabled; publication uses a one-second connection/read budget and no shared
producer-pool wait. Beat's SQL connections have a two-second connect timeout,
one-second statement timeout and 500ms lock timeout.

## Maintenance and generations

Every 60 seconds Beat dispatches `recruitmatch.reconcile_artifacts` with a
60-second message expiry. Worker execution invokes the existing CP3
`ArtifactReconciliationService.run_once(batch_size=25)`. Its durable tenant/lane
reservations, Source→Artifact lineage, cleanup codes and permanent DELETED
tombstone revisits are unchanged. Beat never reads or deletes objects, parses,
embeds, matches or performs this cleanup itself. A crash after a CP3 page
reservation defers that page until its fair cursor wraps.

Ordinary recovered processing obtains a new valid lease and calls the reviewed
`reconcile_staging` before staging. Only inactive, never-published active+1 is
eligible, within the existing 2048-chunk bound and reference checks. Active and
historically published citations remain. A stale Worker cannot clean staging.
SUCCEEDED Sources with soft indexing failure, permanent failures and exhausted
Sources are not automatically reopened to clean inactive data. Their remaining
staging requires deliberate operator reindex/backfill under a new legal queue
cycle and lease. This checkpoint does not automatically delete all inactive
generations. Resume repair continues to infer index-only processing from its
validated committed profile/text pair.

## Health observations

`GET /api/v1/health/live` is async and returns `{"status":"alive"}` with no
external I/O. `GET /api/v1/health/ready` returns 200/ready or 503/unavailable with
database, schema, vector, redis and bucket fields. Each has a finite value:
`ok`, `unavailable`, or (version checks) `incompatible`. It runs SELECT 1,
compares the installed migration set to the actual code Alembic heads, requires
pgvector >=0.8, PINGs Redis and performs HeadBucket with the existing application
identity. It performs no schema bootstrap, object write, model call or domain
storage/vector health method. SQLite unit apps must explicitly inject Lease and
health Fakes; only Compose tests establish real dependency readiness.

Readiness creates a dedicated PostgreSQL NullPool engine, with connect_timeout=2,
statement_timeout=500ms, lock_timeout=500ms and read-only transactions; TCP user
timeout and keepalive options also bound broken-connection detection. Redis uses
0.5-second connect/read limits with zero retries. HeadBucket uses a dedicated S3
client with 0.5-second connect/read limits and one total attempt, with no object
store retries inherited from processing. Engines and clients close after each
probe. No Future timeout or abandoned request thread is used. The API container
healthcheck allows an 8-second client budget and 9-second process budget. These
are driver/network budgets for the Compose deployment, not a universal wall-time
SLA under OS scheduling or resolver failure.

`GET /api/v1/health/system` requires an admin JWT, including during dependency
outages; unauthenticated/other roles remain 401/403. It returns the dependency
statuses plus `overall` (`ok|degraded|unavailable`), Worker/Beat freshness and
bounded heartbeat aggregates. Hard dependency failure yields 503/unavailable.
Worker or Beat stale/unknown yields 200/degraded while readiness may remain 200.
No endpoint, credential, bucket/key, Worker identity or exception text is returned.
Required system fields are `api`, `database`, `redis`, `minio`, `worker`, `beat`,
`ai`, `telemetry` and `overall`. `api:ok` means this local process is serving the
request, not that its dependencies or every API feature are available; there is
no additional HTTP probe. `minio` is the sanitized result of the actual private
bucket probe using application IAM, also retained as `bucket` for compatibility.
Existing `schema`, `vector` and heartbeat aggregate diagnostics remain. A failed
bucket probe yields `minio:unavailable` and overall unavailable while `api` remains
ok. The `/ready` field set is unchanged.

A Worker timer bootstep renews its random process ID every ten seconds. Stop
cancels and disables its timer before removing only that process record. Worker
observations live in one Redis sorted set, using Redis TIME. A pulse prunes entries
older than 90 seconds and refreshes the set's 90-second TTL. Read-only snapshots
use fixed aggregate commands rather than KEYS or all-member materialization.
`worker_count` counts observations within 30 seconds, not busy capacity or
business progress. `worker_oldest_heartbeat_age_seconds` is the age of the oldest
retained observation, including stale observations not yet pruned. No samples
means stale/count0/age null; Redis failure means unknown/count null/age null.
Beat records Redis time after successful recovery cycles, also with 30-second
freshness and a 90-second TTL. A process dying without shutdown becomes stale by
age; any retained operational observations expire. These are CP4 operational
inputs for future telemetry, not implemented CP5 metrics.

AI is `disabled` when deliberately off, otherwise `unknown` with a configured key
or `not_configured` without one. Enabled but unverified AI degrades overall health.
Deliberately disabled optional AI and `telemetry:not_configured` do not alone
degrade an otherwise fresh runtime. No inference is attempted and no telemetry
health is invented. CP5 telemetry and CP6 load/security/CI/runbook finalization
remain deferred; legacy local-runtime instructions elsewhere are not a supported
full-runtime path.
