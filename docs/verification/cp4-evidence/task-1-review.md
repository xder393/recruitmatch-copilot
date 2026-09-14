# CP4 Task 1 independent review

Reviewer: /root/cp4_task1_review_sep12, gpt-6-astra high. Read-only review of 6ab5e58636ec938422df104569456ac1c073fac1..5ecf455240684d5eb544cabe05618a37adaa567c.

### Spec Compliance

- Spec compliant for Task 1. Both Source models gain the six required fields while preserving existing status/error storage; migration 20260912_18 follows 20260911_17 and backfills timestamps without executing work (app/models/resumes.py:68, app/models/knowledge.py:67, alembic/versions/20260912_18_processing_leases.py:6).
- Claim, renewal and terminal operations enforce tenant/type/source/Artifact/owner/epoch boundaries, active lifecycle and database-observed expiry. Live duplicates return without changing counters or errors; FAILED requires explicit requeue (app/processing/leases.py:89, app/processing/leases.py:135).
- Infrastructure-independent port, UoW composition and atomic publication guard are present; processor/indexer/generation behavior remains outside this diff (app/repositories/ports.py:21, app/repositories/unit_of_work.py:32, app/processing/leases.py:194).
- Cannot verify from this Task 1 diff: worker duplicate ACK behavior, processor adoption of fencing, retry scheduling, timeout ordering, Redis configuration or workflow-health/readiness behavior. Controller must verify these during corresponding later tasks; integration boundary documented at docs/adr/0005-processing-lease-contract.md:59.

### Strengths

- Source→Artifact locking and clock_timestamp() after lock acquisition address ownership lineage and stale transaction-clock risks (app/processing/leases.py:54,66,80).
- Publication checks ownership before derived writes, isolates those writes in a savepoint, rechecks ownership at exit, and leaves the outer transaction with the caller (app/processing/leases.py:194).
- Real PostgreSQL tests cover concurrent arbitration, expired takeover, identity boundaries and rollback. Migration tests compare every pre-existing Source column across five state combinations (tests/integration/processing/test_leases.py:89,106; test_lease_migration.py:17).
- Upgrade/downgrade operational requirements explicit, including stopping legacy workers (docs/adr/0005-processing-lease-contract.md:61).

### Issues

Critical: none. Important: none. Minor: none.

### Checks

- Named risk: _locked() selects Knowledge for every validated non-Resume type. Unchanged-code check confirms ArtifactOwnerType has exactly Resume and Knowledge, preventing unsupported type routing (app/domain/artifacts.py:15; app/processing/leases.py:27,54).
- Supplied diff read; truncated omitted sections recovered. No changed files separately reread, git mutations/commands or repeated suites.
- Reported final 229 unit / 370 integration / 97 focused plus clean static gates, no reported warnings (task-1-report.md:167). No unanswered behavioral doubt warranted another test.

### Assessment

Task quality: Approved. The adapter provides a bounded, transaction-aware lease contract with strong ownership checks and meaningful PostgreSQL coverage. End-to-end fencing remains a later-task verification requirement.
