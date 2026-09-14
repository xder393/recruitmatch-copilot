### Strengths

- Lease and publication composition is sound: exact tenant/type/source/Artifact/owner/epoch guards, Source→Artifact lock order, database time after lock acquisition, clean-session entry, and commit after guarded publication. Profile, trace, generation and processing state cannot escape a lost fence.
- Retry and recovery preserve one PostgreSQL attempt budget. Live duplicates ACK unchanged; exhausted non-CLAIMED results commit; long retries require explicit guarded requeue; reservation publication and late failure annotation respect queue-cycle identity.
- Current-owner staging reconciliation preserves active/historical generations and refuses uncertain references. Targeted inspection of unchanged matching and pgvector code confirmed new matching resolves only authorized active citations, supporting the reference-safety argument.
- Beat performs coordination and publication only. The actual Worker maintenance entrypoint reuses CP3 bounded reservations and exact tombstone cleanup. Contention regressions exercise actual scheduler composition with real PostgreSQL locks.
- Health probes use independent bounded clients, actual schema/vector/bucket checks, preserved admin authorization, and honest optional-component statuses.

### Issues

#### Critical

None found.

#### Important

None found.

#### Minor

**CP4-ARCH-M1 — Beat omits the required initialization dependency**

- **File:** `docker-compose.yml:160`
- **Defect:** Beat waits for `bootstrap` and Redis, but not `minio-init`. Frozen specification §3.2 explicitly requires API, Worker and Beat to wait for both one-shot initialization services. No exemption appears in the recorded rulings.
- **Impact:** Beat can start scanning/publishing and report a fresh heartbeat while bucket/application-IAM initialization is incomplete or failed. Worker’s existing dependency prevents premature object processing, so this is a startup-contract discrepancy, not a data-safety defect.
- **Fix:** Add `minio-init: { condition: service_completed_successfully }` to Beat’s dependencies, matching API/Worker.

### Recommendations

Resolve CP4-ARCH-M1 with the small Compose adjustment and focused configuration verification. No broad refactor or repeated full suite is warranted by this finding.

### Verification limits

Reviewed the supplied complete `6ab5e58..c3d3e12` package in bounded passes, the frozen requirements, ADRs, runbook and audit, plus targeted unchanged citation/maintenance composition. I did **not** rerun tests; the reported 285 unit/503 integration and static/Fake-golden gates are controller evidence reviewed against code and tests.

Physical SIGKILL evidence establishes Worker heartbeat loss only. Controlled in-flight termination recovery, real-model availability/quality, load performance and remote CI remain unverified and are not represented as established here.

### Assessment

**CP4 spec compliance:** Core reliability, concurrency, lifecycle, retry, recovery and health requirements are satisfied; one minor startup-order discrepancy remains. The recorded deviations are otherwise concrete and justified, including terminal staging retention and database-derived Resume repair.

**Prior parked/deferred review items:** None remain. CP4-ARCH-M1 is a new minor finding.

**Ready to merge? With fixes.**

The implementation’s cross-task safety boundaries hold under the reviewed scenarios. Add the missing Beat initialization dependency to align the shipped configuration with the frozen startup contract.
