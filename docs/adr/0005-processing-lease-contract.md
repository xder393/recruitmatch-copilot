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
