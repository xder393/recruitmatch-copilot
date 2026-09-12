# Transactional Source processing leases

Status: CP4 Task 1 implementation contract, 2026-09-12.

Resume keeps its existing ResumeStatus. Knowledge translates uploaded to QUEUED,
processing to RUNNING, ready to SUCCEEDED and failed to FAILED. Inactive Knowledge
is deactivation, not privacy deletion; neither inactive nor lifecycle-deleted
Sources can acquire, renew or publish leases. Existing error_code stores the
processing error; no second persisted processing status or error column exists.

The infrastructure-independent LeaseRepository port is exposed as UoW.leases.
claim(tenant_id, source_type, source_id, owner, *, duration) returns ClaimResult.
Its immutable ClaimedLease includes tenant, type, source, exact Artifact, owner,
epoch and the expiry observed at claim. Resume and Knowledge are the only types.
IDs are nonempty strings up to 36 characters; owner is 1–100 token characters
(letters, digits, underscore, dot, colon, at-sign, slash or hyphen). Duration is
positive and at most one day. Malformed arguments raise ValueError.

Claim locks Source then its exact tenant/type/owner-linked Artifact. Only
AVAILABLE permits work. It samples PostgreSQL clock_timestamp() after lock waits;
transaction-start now() cannot fence a transaction that waited for a lock.
QUEUED and expired/unleased RUNNING can be claimed, incrementing both counters.
A valid RUNNING duplicate returns DUPLICATE_ACTIVE without any changes, including
attempts, epoch, errors and timestamps. Missing/terminal/deleted/inactive Sources
return TERMINAL. Future retries or unavailable/missing Artifacts return DEFERRED
without mutation. FAILED is never implicitly reclaimed, even when retry is due;
Task 4 will explicitly requeue it. queued_at records the queue cycle, not renewals.

renew(lease, *, duration), finalize(lease), and
fail(lease, error_code, *, next_retry_at=None) return bool. All re-read persisted
Source and AVAILABLE Artifact state under Source→Artifact locks and require the
exact tenant/type/source/Artifact/owner/epoch, RUNNING, active lifecycle and an
unexpired database lease. Token expires_at is informational: renewal does not
invalidate the token. False means lost ownership with no writes. Successful
terminal changes clear owner/expiry, retain attempt/epoch and use the existing
public terminal status. fail accepts a lower-case machine-code token up to 100
characters and an optional timezone-aware retry timestamp, never exception text.

For atomic derived publication use a clean caller UoW, acquire the guard before
any pending writes, and commit only after its successful exit:

```python
with uow.leases.finalize_owned(lease):
    # Persist derived results through repositories sharing this same UoW.
    # No independent transactions or commit calls in this block.
    ...
uow.commit()
```

The guard exposes no ORM object or Session. It validates ownership at entry,
retains Source/Artifact locks, and uses a savepoint for derived writes and terminal
success. It rechecks expiry at exit after flushing derived writes. Entry or exit
ownership loss raises LeaseOwnershipLost; an exception in the block or an exit
failure rolls back its savepoint. Outer commit remains the caller's responsibility.
Pending new/dirty/deleted ORM state at entry raises ValueError with
processing_publication_requires_clean_session: SQLAlchemy begins a savepoint by
flushing pending state, so guard-first ordering is enforced. Loaded clean objects
are permitted. Locks survive successful guard exit until outer commit/rollback.
Callers must not commit inside the guard or publish after a separately committed
ownership check. Processors and GenerationWriter integration belong to Task 2.

Revision 20260912_18 follows 20260911_17 and preserves all existing Source and
derived data. It initializes counters to zero, leaves lease owner/expiry empty,
and backfills queued_at from updated_at (created_at fallback). Legacy RUNNING
stays RUNNING and is recoverable by a later claim; migration performs no work,
dispatch or reset. Stop old workers before upgrade and resume only lease-aware
workers when Task 2 is integrated: old workers cannot honor epoch fences.
Downgrade drops lease metadata only; stop all workers before downgrade and accept
loss of attempt/ownership history. The migration does not change CP3 privacy,
Source→Artifact lineage or tombstone recovery policy.

## Task 2: renewal and publication

Processors return ProcessDisposition, never booleans. COMPLETED means processing
reached a terminal database result (success or failure); DUPLICATE_ACTIVE,
TERMINAL, DEFERRED and LEASE_LOST are normal ACK/no-side-effect outcomes.
RETRY_SHORT remains reserved for Task 3 policy; these processors do not emit it.
A due FAILED row is still terminal until explicitly requeued in PostgreSQL.

LeaseRenewer renews every duration/3 in a newly opened UoW on its own daemon
thread. No Session crosses threads. A false result or exception marks ownership
lost; callers stop publication and the database guard independently verifies
ownership. Shutdown joins for at most one second (injectable in tests); an
already in-flight renewal may finish using its own UoW, but cannot publish
derived results. The configured S3 connect/read/retry bounds and model gateway
timeout/retry bounds remain in force. Task 3 supplies final worker hard/soft and
broker timing policy; tests use fake models/embeddings only.

Async GenerationWriter.stage/activate/fail/reconcile_staging require the existing
`fencing_token` keyword containing the complete immutable ClaimedLease, rather
than a bare epoch integer. Missing, wrong identity, old epoch/owner, expired or
unavailable exact Artifact tokens cannot write. SourceRef additionally validates
immutable version and lifecycle, including inactive Knowledge. JobVersion keeps
the synchronous token-free path and rejects processing tokens.

`SourceIndexer.stage` embeds and commits only inactive staging.
`UoW.generations` exposes the minimal GenerationPublicationPort (activate/fail).
Its SQL adapter requires the caller's active lease guard and never opens or
commits another transaction. Resume publication writes profile, text, model
traces, index activation/error and success inside finalize_owned; commit follows
successful context exit. An activation error rolls back that publication before
a fresh guarded transaction may retain deterministic parsing and record an index
error. Knowledge indexing failure commits FAILED processing separately from
retrieval availability: a prior ready active generation remains available.
Resume retrieval likewise uses the active generation and ready search status,
not the processing status of a subsequent repair cycle.

The new `leases.owned(lease)` guard has the same clean-session, exact identity,
Source→Artifact lock, savepoint and entry/exit wall-clock checks, without a
terminal transition. Standalone generation writes use it; Knowledge failure
publishes the index error inside it, then calls leases.fail while retaining the
same locks and outer transaction. No Session or ORM object is exposed by either
lease guard or the publication port.

## Explicit reconciliation of abandoned staging

Controller-approved Task 2 policy keeps contiguous active N→N+1 and strict exact
replay within stage. A successful new claimant may call reconcile_staging once
before embedding/staging. This is explicit current-owner reconciliation, never
cleanup by a worker that lost ownership. No schema or generation ownership
column is added.

The operation locks the exact tenant/type/source/version and AVAILABLE Artifact
under the current ClaimedLease. It requests only active+1, reads at most 2049
future chunks, and refuses to reclaim more than 2048. Every future row must be
inactive and exactly active+1; unexpected future generations or active rows
fail closed. Generations <= the active pointer are never deleted. Reference
checks are PostgreSQL EXISTS predicates, joining MatchResult through its
tenant-qualified MatchRun and matching JSON values with jsonb_path_exists.
Checks include citations, evidence, grounded_explanation, interview_questions,
dimension_scores, matched_items, missing_items, uncertain_items and risk_flags;
no tenant-wide JSON is materialized in Python. Checks and the tenant/type/source/
version-qualified deletion occur inside leases.owned; expiry at guard exit
rolls back deletion, and the outer transaction commits afterward.

Bounded refusal codes are staging_reconciliation_limit,
staging_reconciliation_inconsistent and staging_reconciliation_referenced
(GenerationValidationError; processing maps them to validation_failed). A lost
lease raises LeaseOwnershipLost, not an index error to persist. Task 4 must use
this same operation/contract if it later performs staging cleanup.

Normal matching obtains new citations only from the active pointer and resolves
active citations before publication. A future N+1 can therefore never acquire
new legal matching references while held under this Source lock. No Tenant lock
is acquired after the Source lock: the existing Tenant→Source order is unchanged.
If inconsistent legacy references already exist, reclamation refuses them.
Historical published generations and citations remain available for authorized
audit; privacy deletion retains its existing independent cleanup rules.

## Queue cycles and durable index repair

Knowledge operator reindex locks and verifies its exact AVAILABLE Artifact,
sets uploaded/queued_at, clears owner/expiry/retry/errors, and resets the new
operator attempt budget to zero. Epoch is never reset; the next claim increments
it. Artifact reconciliation repair clears owner/expiry/retry and records queued_at
when requeuing, preserving the recovery attempt history and exact Artifact.

The Resume backfill endpoint also explicitly queues under Source→Artifact locks,
commits, then invokes the ordinary processor. It never indexes Resume through
the synchronous JobVersion path. After claim, an already committed parsed pair
(nonempty extracted_text plus a complete schema-valid 1.0 ResumeProfile whose
evidence resolves into that text) selects index-only repair. This is inferred
from PostgreSQL for every delivery; Beat recovery needs no special message
parameter. Profile/text/traces are preserved during this repair.

Only successful profile publication writes the parsed pair; the Source and
Artifact content are immutable, and privacy scrubbing clears both. New unparsed
rows have extracted_text=None/profile={}. Inconsistent legacy pairs fail closed
with resume_parsed_state_invalid, preserving their contents for deliberate
repair, rather than assuming one nonempty field proves successful parsing.
The exact AVAILABLE Artifact and current lease remain required even for index
repair that does not reread the object. Resume/Knowledge upload and reconciliation
deliveries, Knowledge reindex, inline API dispatch and Celery all enter the same
lease-aware processors. Synchronous JobVersion create/update/backfill stays on
SourceIndexer.index.

## Task 3: bounded delivery and retry policy

The default processing budget is five successful PostgreSQL claims per operator
cycle, including claims followed by crashes or lease expiry. Both processors pass
the configured budget to `leases.claim(..., duration=..., max_attempts=5)`.
The repository checks that budget under the same Source→Artifact locks as claim.
A live fifth-attempt duplicate remains an unchanged DUPLICATE_ACTIVE. An eligible
queued or expired RUNNING row already at the budget becomes FAILED with
`processing_attempts_exhausted`, no due time, cleared owner/expiry, and unchanged
attempts/epoch. Claim returns TERMINAL in that case. **Callers must commit even
non-CLAIMED results**; both processors do so. No ordinary FAILED delivery reopens
work. An explicit operator reindex may start a new attempt budget as described
above; automatic retry/recovery must never reset attempts or epoch.

`RetryPolicy(max_attempts=5, short_seconds=30, long_seconds=300)` is injected into
both API and Worker processors. `leases.schedule_retry(lease, error_code, *,
short_delay, long_delay, max_attempts)` accepts only `storage_unavailable`,
`embedding_failed`, and `processing_timeout`. It validates the exact current
tenant/type/Source/Artifact/owner/epoch, active lifecycle, AVAILABLE Artifact and
unexpired database lease, using clock_timestamp() after lock acquisition. A lost
guard returns LEASE_LOST with no mutation. Caller owns commit. When recording an
index error beforehand in the same UoW, losing the retry fence rolls back that
entire transaction, including the index error.

The guarded policy has these explicit state edges:

| Failure at claim attempt | Durable transition | Processing outcome / dispatch |
| --- | --- | --- |
| 1 or 2, transient, below budget | RUNNING → QUEUED (Knowledge uploaded), bounded error, next_retry_at=DB clock+30s, queued_at=DB clock, cleared lease | RETRY_SHORT; after commit, Celery publishes a 30s countdown |
| 3 or 4, transient, below budget | RUNNING → FAILED, bounded error, next_retry_at=DB clock+300s, cleared lease | COMPLETED / ACK; Beat later explicitly requeues |
| At budget, transient | RUNNING → FAILED, original bounded failure code, no due time, cleared lease | COMPLETED / ACK, no automatic retry |
| Permanent | RUNNING → FAILED, bounded permanent code, no due time, cleared lease | COMPLETED / ACK |

The short transition is a deliberate recovery decision made by the current lease
owner, atomically recording failure and requeue. It does not make arbitrary
FAILED deliveries eligible. Its next_retry_at is execution eligibility, not a
dispatch cooldown: an early duplicate returns DEFERRED, and a due QUEUED delivery
can claim normally. Every actual claim increments both epoch and attempts. Broker
request.retries is informational; task max_retries=None deliberately avoids a
second competing budget. Only the committed RETRY_SHORT outcome creates a bounded
countdown; no other ProcessDisposition retries. Long waits never use countdown.

Task 4 consumes `leases.requeue_due(tenant_id, source_type, source_id, *,
expected_epoch, expected_artifact_id, max_attempts) -> bool`. Its bounded scan
must select active FAILED sources with a known transient error, non-null due
next_retry_at, and attempts below the configured cap. The transition independently
relocks Source→exact Artifact and rechecks all of those conditions, the snapshot's
epoch and Artifact ID, and AVAILABLE owner-linked Artifact state against database
wall clock. Success consumes the snapshot once: FAILED→QUEUED/uploaded, new
queued_at, cleared next_retry_at/owner/expiry, preserved error/attempts/epoch. It
does not process or dispatch. Caller commits first, then dispatches the ordinary
tenant/Source task; two concurrent consumers cannot both requeue the same snapshot.

Crash and dispatch windows remain recoverable in PostgreSQL:

- Before retry persistence commits, rollback leaves the prior RUNNING lease for
  expiry recovery. Database inability to record failure must not fabricate FAILED.
- After a short retry commits but before/during publish, the due QUEUED row remains
  discoverable by Task 4's stale-queue scanner. Celery's failed retry publish raises
  Reject; the wrapper ACKs it and records only `processing_dispatch_unavailable`.
- A long FAILED retry stays terminal until requeue_due commits. Death or publish
  failure after that commit leaves QUEUED, also recoverable by the stale-queue lane.
- Worker hard termination leaves the prior lease. Redelivery and recovery claims
  use the same cap. Beat must not advance RUNNING or reset attempts itself.
- Beat must exclude future next_retry_at from queued redispatch, and must not
  refresh queued_at simply to pace repeated publishes. That timestamp measures
  the real queue cycle. Task 4 owns separate bounded dispatch pacing, scans and
  stable dispatch-failure recording; this task adds no scheduler or schema.

Ordinary Resume recovery continues to infer index-only repair from the complete
persisted valid text/profile pair; no message mode flag exists. Resume embedding
soft failure still atomically preserves deterministic parsing and trace results,
with an index error for later bounded repair. Knowledge embedding failure records
the index error and retry decision together while retaining any older retrievable
generation. Malformed parse state and validation failures remain permanent.
LLM gateway retries/fallback stay unchanged and do not create business task retries.
Worker soft timeout types are injected into processors, so services import no
Celery or billiard implementation. A timeout during embedding is unwrapped from
the embedding-only exception and handled by the same lease-fenced retry policy.

Only the SQL UoW and independent GenerationWriter transaction boundaries translate
SQLAlchemy OperationalError, DisconnectionError and pool TimeoutError into
PersistenceUnavailable, retaining the original exception as cause. This covers
connection/transaction interruptions, including a real closed PostgreSQL
connection. ProgrammingError, data/schema/type faults and unknown exceptions are
not classified as recoverable database outages. Indexing catches only known
generation/validation failures or EmbeddingFailure from the external embed call;
database/unknown writer faults therefore cannot become embedding soft success.
The Celery wrapper ACKs PersistenceUnavailable with the bounded log code
`processing_database_unavailable`, leaving database recovery authoritative.
Other unhandled faults produce an actual Celery FAILURE using only
`ProcessingTaskFailed("processing_task_failed")`; arguments are redacted from
task log context and free exception text is suppressed. No error result is stored.
These narrow task protections do not complete CP5's full telemetry privacy work.

Default timing seconds: lease 300, soft limit 540, hard limit 600, Redis visibility
900, safety margin 30, maximum countdown 30, long durable retry 300. Settings.load
requires positive finite integers, lease at most 86400, soft<hard,
visibility>max(hard,short countdown)+margin, and long>short. The strict visibility
inequality covers countdowns greater than hard limits too. Compose passes all
eight timing/budget fields to API/Worker and the integration test service.

Actual Celery configuration enables late ACK, reject-on-worker-loss, prefetch 1,
ignore_result and both worker time limits. The Redis transport and top-level
visibility setting agree. No result backend is configured, ignored errors are
not persisted, and nonempty CELERY_RESULT_BACKEND is rejected at startup because
Celery otherwise lets that environment variable override its explicit config.
Automatic broker publish retries are disabled; the database recovery paths above
handle failed publishing. There is no Redis business status or result polling.
