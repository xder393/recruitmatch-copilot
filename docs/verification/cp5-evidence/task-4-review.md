# CP5 Task 4 — working-tree review

Date: 2026-09-14. User request: review only; implementation remains paused.
Reviewer: /root/cp5_task4_review (gpt-6-astra/high).
Base/current committed HEAD: ccb662824dba93597e251f0d443fa2da4a49df7d.
Target: 7 tracked modifications and 13 untracked new files in review-task-4-working-tree.diff.

## Controller verification and recovered evidence

- All 19 image-resident changed/new files match final test image sha256:3edc42a4ce8bcd524c744e09149661ce5f9bc508b9ffa2f21a091ad283e957b9. All 20 reviewed files remained unchanged through review.
- Fresh isolated, network-disabled Docker validation: Ruff check passed; format 233 files; CI-scope mypy 41 files passed. Compose config, shell syntax and git diff checks passed.
- Existing implementation report records 406 unit and 520 integration tests passing. These suites were not repeated during this review.
- Parent inspected only non-sensitive fields of /tmp/recruitmatch-cp5-telemetry.u9kw0Z/host.json and alerts.json. Both match source digest 68cb9fe89ab7ac67d8f4785a75e118b77bb0071d8b17730eea990753a3559f5f. Host flags all true; ten alerts reached Firing (cleanup 311.1 seconds). Synthetic credentials/state were not read or copied.
- Original implementer recovered final output from existing session 34288: helper exit 1; pytest tests/observability -q: 1 failed, 123 passed in 99.13s. This supersedes task-4-report.md's stale final-gate-running note.
- Failure: test_operational_gauge_replacement_and_failed_dependency_are_unknown; gauge_probe.py:85, expiry_positive_precedent_missing. No elapsed operands retained.
- Associated stderr: logging/__init__.py:1163 emit -> stream.write(msg + self.terminator) -> ValueError: I/O operation on closed file. Intervening callback frames were truncated; owning handler/lifecycle cannot be identified from this excerpt.
- No code, tracked documentation, index, HEAD, runtime services or test state changed during this review. Main remains clean at f297b0a509fca92b37de1be174e18a601ccfd64e. No fix dispatch, commit, push or merge.

## Independent reviewer — corrected final verdict

### Spec Compliance

- ❌ Task 4’s acceptance gate is not complete. The recovered final exact-image run exited 1: **1 failed, 123 passed in 99.13s**.
- ✅ The implementation otherwise follows the reviewed boundaries. Matching-source host evidence confirms Collector stop/restoration, business availability, private logs and Worker replacement; alert evidence confirms all ten rules reached actual Firing.
- ⚠️ The failure does not establish a production gauge defect or an R17 shutdown regression.

### Strengths

- scripts/verify_telemetry.py:108–120: Verifies actual HTTP/broker/prefork ancestry and distinct runtime writer identities.
- scripts/verify_telemetry_host.sh:38–57: Separates Worker replacement from Collector outage and restores Collector before final verification.
- app/core/celery_logging.py:19–41: R17 preserves the selected descriptor and original low-level writer, emits constant JSON bytes, and avoids logging locks or message serialization. Focused inspection of installed Celery 5.6.3 confirmed compatibility with its unchanged lifecycle handlers.
- ops/prometheus/rules.yml:118: R16 independently evaluates failure families before union; the dashboard separately preserves their numeric sum. Dedicated regressions and recovered actual alert evidence support the correction.

### Issues

#### Critical

- None found.

#### Important

1. **The final gauge acceptance fails, and its timing assertion uses the wrong reference point.**
   tests/observability/gauge_probe.py:83–85: The recovered run fails with expiry_positive_precedent_missing. The test starts measuring after recovery polling, flushing and querying, whereas the actual 25-second lifetime starts when the snapshot is replaced (app/observability/otel.py:157). Consequently, requiring another full 25 seconds from the later observation is not a sound direct TTL assertion. Recovery also accepts an environment-level zero without establishing that it represents the replacement writer’s newly recovered snapshot. Anchor timing to the relevant source observation, establish that observation’s identity/freshness, and retain the bounded eventual-NoData check. The retained output contains no elapsed operands, so these weaknesses cannot conclusively explain this particular early disappearance; production behavior remains unresolved.

2. **The final gate also emits closed-stream logging errors.**
   tests/observability/gauge_probe.py:85 — associated failure output: Python logging reports ValueError: I/O operation on closed file while writing a record. The retained callback frames are insufficient to identify the owning handler or lifecycle boundary. Resolve that ownership and remove the logging errors before accepting the gate; do not attribute them to R17 without further evidence.

#### Minor

1. scripts/verify_telemetry.py:265–283: Cumulative per-writer thresholds can reuse observations from earlier flows on the same writer, particularly the final suite’s additional business flow. Baselines/deltas would strengthen current-flow attribution beyond the approved runtime-writer scoping.

2. scripts/verify_telemetry.py:108–112: A partially available Tempo trace can make next(...) raise StopIteration, which bypasses eventually() retries. Convert a missing required span into a sanitized assertion so the bounded polling deadline governs it.

### Assessment

**Task quality: Needs fixes. Ready for checkpoint acceptance: No.**

The implementation has substantial verified live evidence and no identified Critical production defect. The confirmed failing gauge gate and closed-stream diagnostics must be resolved before completion can be claimed.

