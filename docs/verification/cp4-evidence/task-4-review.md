# Task 4 independent review — /root/cp4_task4_review_sep12

Spec Compliance ❌

- Important fairness defect: one contended recovery row can abort unrelated recovery and maintenance work (R1).
- ⚠ Cannot verify from this diff alone: predecessor duplicate-ACK, renewal/final-write fencing, and all CP3 reconciliation guarantees. Those remain controller checks against accepted Tasks 1–3.
- ⚠ Controlled in-flight Worker termination recovery is not established by the reported smoke; the report accurately distinguishes heartbeat process-loss evidence from manually expired lease recovery.

Strengths

- Recovery preserves tenant, Source, Artifact, epoch, status, queue-cycle and reservation identity; commits reservations before publishing; and keeps dispatch pacing separate from claim eligibility (`app/processing/recovery_repository.py:119`, `:151`; `app/processing/recovery.py:31`).
- Actual Beat executes recovery directly, while a Worker maintenance task composes the existing Artifact reconciler without processor construction (`app/tasks/beat.py:70`; `app/tasks/maintenance.py:11`).
- Readiness uses dedicated bounded clients and actual Alembic heads; admin health retains authentication and separates hard failures from workflow degradation (`app/operations/probes.py:21`, `:32`; `app/api/v1/operations.py:35`; `app/operations/health.py:47`).
- Tests exercise real PostgreSQL transitions, real dependency failures, actual client timeout behavior and Worker lifecycle callbacks. Reported final rebuilt gates are pristine; the earlier stopped-helper warning is explicitly disclosed and resolved.

Critical findings

- None.

Important findings

- **R1 — Row contention aborts the entire recovery cycle.** `app/processing/recovery_repository.py:124` and `:145` acquire blocking row locks. The production 500ms lock timeout becomes `PersistenceUnavailable`; `app/processing/recovery.py:36` lets it escape, and `app/tasks/beat.py:74` returns before processing remaining candidates, dispatching maintenance or updating Beat observations. Consequently, a repeatedly contended oldest Source or Artifact can prevent unrelated tenants and the other Source type from progressing even though PostgreSQL remains available. Treat contention as a skipped candidate while preserving Source→Artifact ordering and all fences, and continue the bounded cycle. Add a focused PostgreSQL regression holding the oldest candidate’s lock while verifying another candidate and maintenance progress.

Minor findings

- None outstanding.

Checks

- Read the complete brief, 322-line report and full review package in sequential passes; no cut-off function required completion.
- One focused outside-diff check: the named contention-propagation risk, checked against `app/repositories/sqlalchemy_unit_of_work.py:44`, which translates `OperationalError` into `PersistenceUnavailable`.
- No suites rerun, subagents dispatched, or checkout/external state changed.

Assessment: **Needs fixes.** The implementation otherwise follows the approved task boundaries, but recovery should isolate row contention before its bounded-fair-progress guarantee can be trusted.
