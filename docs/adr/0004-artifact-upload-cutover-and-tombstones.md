# Artifact upload cutover and retained tombstone recovery

Status: accepted by the CP3 controller on 2026-09-11. This clarifies frozen
design §6.6; the six storage states and separate Source authority are unchanged.

## Publication and privacy races

Uploads stream/hash into a bounded temporary file, validate before persistence,
claim a unique Artifact, and commit the winning Owner with PENDING before Put.
Publication and deletion acquire Source then Artifact row locks and re-read
persisted state. Duplicate claims hold the Artifact lock but only read the
existing Source without a lock, then end the transaction; they must never invert
the publication lock order. Only the winning proposal writes an object.

Privacy cancellation of PENDING follows PENDING → FAILED → CLEANUP_PENDING in
the DB-first transaction. The intermediate existing STORAGE_UNAVAILABLE code is
not a user-facing assertion that the backend failed. The cleanup transition
clears it and the stable checksum. Deactivation of Knowledge is not deletion.

A slow Put can complete after deletion has observed no object and marked its
Artifact DELETED. The live uploader refuses publication and deletes the exact
ArtifactLocation. If that compensation fails, record a sanitized existing
ArtifactErrorCode on the retained DELETED row, without changing its state.

The retained Artifact identity is also the crash-recovery anchor. Task 4 must
fairly revisit DELETED rows indefinitely, including rows whose last inspection
found no object. A finite missing-object observation does not prove a previously
issued Put cannot finish later. These are exact DB-associated objects, not
unassociated orphan candidates; unrelated objects must not be deleted.

## Repository contract for Task 4

`list_deleted_tombstones(tenant_id, *, after_id=None, limit=100)` returns immutable
ArtifactTombstone values containing typed `location` and optional bounded
`error_code`. Page size is 1–1000. Results use ascending Artifact ID, with an
exclusive cursor; after an empty page the scheduler must eventually restart at
the beginning. Tenant scheduling must also be fair. Do not retain an end cursor
forever, filter to errors only, or retire a tombstone after a missing result.

`record_deleted_cleanup_result(tenant_id, artifact_id, error_code, *, owner_type,
owner_id)` locks/refreshes the exact row, checks tenant/type/owner and DELETED,
and changes only its sanitized error metadata. Passing None records a successful
cleanup observation without removing the row. The caller owns the transaction.
Later health/metrics/invariant reporting must include DELETED cleanup errors as
well as CLEANUP_PENDING/CLEANUP_FAILED. Task 4 supplies bounded orchestration
and full cross-Source privacy cleanup. CP4 owns the sole Beat schedule and
dispatch/recovery wiring; Beat must not perform object I/O. Cleanup health and
performance invariants remain CP5/CP6 work.

The tradeoff is permanent tombstone retention and repeated bounded inspection
cost. Retiring them requires a future proven upload-fencing/completion protocol.

## Task 4 privacy boundary and maintenance progress

Accepted by the controller on 2026-09-11 before implementation. Grounded model
explanations persist selected citations, not complete input lineage. Knowledge
may influence generated text without appearing in its displayed citations.
Privacy deletion therefore clears every existing MatchResult content/JSON field
in the tenant and every linked Feedback.reason. Results retain deliberate
non-content audit/numeric fields and a privacy-redacted marker; later feedback
on them is rejected. Fresh matching can use the remaining live Sources. Resume
deletion clears its own result and feedback content. This conservatively
invalidates unrelated same-tenant match history; it is not exact derivation
tracking or a semantic content classifier.

The Knowledge DELETE API requires `confirm_tenant_history_redaction=true`.
Without it, return stable `knowledge_privacy_confirmation_required` (409) before
any mutation. API callers must inform the operator that all existing match
content in their tenant is cleared. No console Delete action is exposed here.
The tenant-wide scrub runs only on the first live→deleted privacy transition;
cleanup retries must preserve fresh matching results created after deletion.

Matching, privacy deletion and feedback first lock the existing Tenant row with
`FOR NO KEY UPDATE` (compatible with implicit FK `KEY SHARE` checks by workers),
then acquire Source and Artifact locks in that order when needed. Matching
holds the tenant guard across Knowledge selection, model generation and commit.
Deletion scrubs under the same guard; feedback revalidates after locking.
Thus either matching/feedback commits first and deletion clears it, or deletion
commits first and stale sensitive writes are rejected. Slow model calls serialize
same-tenant matching/deletion/feedback. This is a correctness-first Compose demo
boundary, not a throughput claim. Processing and generation publication continue
to lock/revalidate their exact Source before writing; lifecycle deletion wins.

A PostgreSQL maintenance cursor table with unique (scope, lane), validated
finite lanes and atomic initialization records only exclusive last IDs. A global
tenant lane rotates one tenant in stable ID order per invocation and wraps.
Each tenant has independent stale-PENDING, cleanup and DELETED lanes, reserving
bounded Artifact pages. Cursor rows follow one deterministic lock order. The
short reservation transaction advances all positions and commits before object
I/O or Source publication locks. Fresh service instances/processes resume this
progress; concurrent invocations may overlap only across eventual wraps, and
all work remains idempotent and revalidated under Source→Artifact.

A crash after reservation may defer that page until a full wrap. Rows are never
retired after missing-object observations. Independent lanes prevent perpetual
tombstone sweeps from starving repair/cleanup. Cost is O(tenants × lanes) cursor
metadata, permanent Artifact tombstones and repeated bounded object I/O. This
table represents maintenance progress, not processing ownership or a second
scheduler. Reports describe bounded partial observations only. Orphan inspection
is a separate controlled administrative paged operation, returning sanitized
counts with an opaque adapter cursor; unrelated objects are never auto-deleted.

## Schema and data preservation

Revision 20260911_15 follows 20260911_14. It refuses unsupported legacy content
before DDL: every non-deleted Resume and every retained Knowledge document,
including inactive Knowledge, needs a valid Artifact anchor. A legacy deleted
Resume may be anchorless only when its private Source fields are sanitized.
There is no legacy local-object backfill or fabricated S3 availability.

Valid ResumeArtifact text moves to Resume.extracted_text. Existing non-null
resume chunk document IDs map from validated tenant/Source-owned legacy IDs to
the new Artifact ID. NULL, Knowledge document IDs, and JobVersion NULL semantics
are preserved. Chunk content, citations, versions and generations are untouched.
New generation writes validate the exact tenant/type/owner/linked Artifact.

An installation containing unsupported legacy local content must explicitly
export/retain anything it needs and approve a one-time reset of that specific
legacy installation before fresh bootstrap. This migration never performs that
reset. Do not run volume-removal commands against a user/default project.
Already-valid new Artifact data can migrate in place. This cutover cannot be
downgraded automatically because removed local object identities cannot be
reconstructed; restore an operator-managed pre-cutover backup for rollback.

Revision 16 makes Source lifecycle explicit and preserves prior deletion. For
databases already at 15, it scrubs legacy deleted Resume fields, every chunk
generation (including NULL document linkage), owned result content and linked
feedback reasons. Live Source and result content are preserved. Legacy Knowledge
`status='deleted'` does not imply consent to tenant-wide history erasure: if any
such tenant retains result content or feedback reasons, migration fails before
DDL with `knowledge_privacy_migration_requires_operator_resolution`. Operators
must review that tenant's deletion/history scope and explicitly resolve it under
their approved privacy procedure before retrying; no reset or acknowledgement
bypass is supplied by the migration. Already-empty legacy history can migrate.
PENDING cancellation uses FAILED before CLEANUP_PENDING within the migration
transaction; existing terminal cleanup error codes remain until real cleanup.

## Resource and execution bounds

Upload orchestration runs in Starlette's bounded thread pool, sequentially using
one UnitOfWork. Both request and temporary streams close on success and failure.
Input is at most 10 MiB; DOCX validates at most 512 entries, 16 MiB expanded total,
8 MiB per entry and 200:1 expansion ratio. Extracted output is at most 1 MiB.
Locked pypdf 6.16.2 filter/stream caps are configured once at module import,
never per request. Referenced resources, pages and decoded page contents are
bounded before text extraction. No OCR or model inference occurs in validation.
