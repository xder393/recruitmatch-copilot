# Task4 fix round1 — /root/cp4_task4_rereview1_sep12

- **R1 — Row contention aborts the entire recovery cycle.** — **ADDRESSED.** Both Source and Artifact acquisitions use `SKIP LOCKED` at `app/processing/recovery_repository.py:128` and `:149`; a skipped candidate returns `None`, allowing the scanner loop to continue (`app/processing/recovery.py:35-49`). PostgreSQL coverage exercises both Source types, both locks, reservation and late `dispatch_failed`, while verifying unrelated dispatch, maintenance, heartbeat, unchanged blocked state, and released Source locks (`tests/integration/processing/test_recovery_contention.py:59-125`). Genuine connection and schema failures remain visible (`:133-151`).

- **C1 — Frozen `/health/system` fields `api` and `minio` are missing.** — **ADDRESSED.** `api` reports local request capability and `minio` derives directly from the sanitized bucket probe at `app/operations/health.py:58-60`. Required fields, hard-dependency mapping, stale-Worker degradation, successful application-IAM probing, and denied MinIO identity are covered at `tests/operations/test_health_and_metrics.py:60-111` and `tests/integration/test_operational_health.py:7-46`.

### New Breakage in the Fix Diff

None.

### Out-of-Scope Observations

None.

### Checks

Fix report contains the expected RED evidence (`8 failed`, `6 failed`), focused GREEN evidence (`58 passed`, `6 passed`), and rebuilt unmounted gates (`285` unit, `503` integration, Ruff/format/mypy/offline lock all clean). Controller independently corroborated these gates and shipped-file/image hashes. No suites or git commands rerun.

### Verdict

**Fix round: All findings addressed, no new Critical/Important breakage.**
