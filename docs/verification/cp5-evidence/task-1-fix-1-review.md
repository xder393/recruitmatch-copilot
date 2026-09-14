- **Real exporter logging bypasses the quiet wrapper** — **ADDRESSED.** The exact OTLP gRPC exporter logger now receives a process-lifetime privacy filter that replaces messages and clears arguments, exception, and stack data (`app/observability/otel.py:55`, `app/observability/otel.py:70`). It is installed only for enabled telemetry (`app/observability/otel.py:210`, `app/observability/otel.py:213`). The regression exercises actual span and metric exporters, multiple owners, shutdown, late diagnostics, and verifies unrelated application logging remains intact (`tests/observability/test_otel_adapter.py:274`).

- **The public meter permits out-of-range match scores** — **ADDRESSED.** The shared safe-instrument boundary now assigns `recruitmatch.match.score` a maximum of 100 and rejects observations above it before SDK recording (`app/observability/policy.py:330`, `app/observability/policy.py:339`, `app/observability/policy.py:360`). The real-SDK test covers valid and invalid values through both recorder and public-meter entry points (`tests/observability/test_otel_adapter.py:112`).

### New Breakage in the Fix Diff

None.

### Out-of-Scope Observations

- Minor, deferred: histogram boundary advisories remain discarded at `app/observability/policy.py:373`; unchanged and assigned to Task 2/final review.
- The telemetry-only instance-identity/aggregation exception for concurrent cumulative writers remains unresolved at `app/observability/policy.py:134`; this still requires controller/user resolution.
- Optional broadened mypy checks reported pre-existing `str | None` to `str` errors at `app/api/v1/endpoints/resumes.py:30` and `app/api/v1/endpoints/knowledge.py:54`. Both files are outside this fix diff and do not affect this verdict.

### Verdict

**Fix round:** All findings addressed, no new Critical/Important breakage.

### Controller citation correction

The review's last out-of-scope observation accidentally inserted an `endpoints` directory. Actual verified paths are `app/api/v1/resumes.py:30` and `app/api/v1/knowledge.py:54`, as shown in the recorded commands and baseline reproduction. The original reviewer wording is preserved above; this citation correction does not alter its verdict or any code.
