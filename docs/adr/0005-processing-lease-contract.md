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
