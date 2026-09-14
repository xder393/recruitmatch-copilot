### Spec Compliance

❌ Issues found: startup stdout bypasses JSON/privacy handling, and final explanation-citation invalidation lacks required rejection/fallback metrics.

⚠️ Cannot verify from this diff: Task 3 must preserve Resource writer identities and implement freshness-aware aggregate queries; Task 4 must prove real API→broker→prefork parentage, backend ingestion/privacy, outages, and multi-writer/restart behavior. These are explicitly deferred interfaces in [observability-metrics.md](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/docs/observability-metrics.md:75).

### Strengths

- Committed outcome attribution is carefully placed: failure/retry observations follow the owning commit in [retry.py](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/processing/retry.py:59), while successful publication/chunk observations follow commit in [resume_processing.py](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/services/resume_processing.py:225). Integration tests exercise failed outer commits and duplicate delivery.
- Framework routing uses explicit owners and weak runtime caches in [instrumentation.py](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/observability/instrumentation.py:22). Tests exercise real HTTP/SQL/Redis/HTTPX instrumentation, concurrent applications, and repeated lifespans.
- Gauge snapshots have family invalidation, expiry, and callbacks without dependency I/O in [otel.py](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/observability/otel.py:148). The report clearly distinguishes source freshness from export timestamps.
- Domain instrumentation remains behind the SDK-free facade; recorder failures preserve business results/exceptions in [events.py](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/observability/events.py:70).

### Important Issues

1. **Startup banners bypass the JSON/privacy boundary.**  
   [logging.py:64](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/core/logging.py:64) and [celery_app.py:38](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/tasks/celery_app.py:38) configure logging handlers, but production Worker and Beat commands at [docker-compose.yml:149](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/docker-compose.yml:149) and [docker-compose.yml:167](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/docker-compose.yml:167) omit quiet mode. Installed `Worker.on_start` invokes `emit_banner`, which writes directly to `sys.__stdout__`; Beat prints banners before `setup_logging`. A focused exact-image probe confirmed synthetic startup content remained raw text after `worker_logging()` ran. Enable supported quiet startup behavior for both entrypoints and add startup-path coverage; the handler-only test at [test_structured_logging.py:9](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/observability/test_structured_logging.py:9) cannot detect this requirement breach.

2. **Final explanation-only citation rejection is invisible to metrics.**  
   [matching.py:247](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/services/matching.py:247) emits rejection/fallback only when semantic citations fail. The subsequent call at [matching.py:269](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/services/matching.py:269) can independently discard explanation claims whose citations are no longer active. Its rejection branch at [matching.py:318](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/services/matching.py:318) emits nothing, including when all guidance disappears. Consequently, valid semantic citations plus an invalidated explanation-only citation produce an uncounted validation rejection and potentially an uncounted logical degradation. Instrument that final validation decision, preserving authorization behavior and avoiding recounting an earlier rejection. Add a case where semantic citations remain valid while an explanation-only citation disappears.

### Focused Checks

- Read the supplied 4,611-line diff once in consecutive ranges; no independently generated diff or full-suite rerun.
- Confirmed image digest `sha256:6a0ac2069ddc36f30376553f41524d8e874b3158b6410e27fde59cddff50a819`.
- Checked unchanged Compose commands and installed public Celery startup methods for the controller-named startup risk. After correcting two diagnostic-harness errors, the focused probe exited successfully and demonstrated non-JSON output.
- Read `matching.py:240–380` because the diff cut off the final-guidance branch/helper needed to assess citation invalidation. No additional runtime diagnostic.
- Checkout, index, HEAD, and files remained unchanged.

### Assessment

**Task quality: Needs fixes.** The implementation has strong transaction-boundary tests and thoughtful owner/privacy controls. Both findings are localized but violate required emitter behavior, so this Task 2 gate should remain open pending fixes.
