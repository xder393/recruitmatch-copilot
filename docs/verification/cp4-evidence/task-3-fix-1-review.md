### Finding Verdicts

- **T3-I1 — Duplicated durable failure policy** — **ADDRESSED**. Shared decision, delay construction, lease-loss rollback, and commit now reside in app/processing/retry.py:35-57. Both processors delegate to it at app/services/resume_processing.py:216-218 and app/services/knowledge_processing.py:122-127. Knowledge retains its guarded index-error write in the same caller-owned UoW before delegation; ownership loss rolls back the whole UoW.
- **T3-M1 — Artifact-read soft timeouts receive the wrong diagnostic code** — **ADDRESSED**. Timeout exceptions are re-raised before the broad storage catch at app/services/resume_processing.py:92-96 and app/services/knowledge_processing.py:74-78, reaching the existing processing_timeout handlers. The PostgreSQL regression at tests/integration/processing/test_retry_processors.py:85-102 covers both Source types and verifies the durable retry diagnosis.
- **Evidence check** — The report records the new regression RED as 2 failures with storage_unavailable, focused GREEN as 71 passed, and final rebuilt unmounted gates as 268 unit and 434 integration tests plus Ruff, format, mypy, and offline-lock checks. These claims match the fix diff; no additional test was warranted.

### New Breakage in the Fix Diff

None.

### Out-of-Scope Observations

None.

### Fix-round Verdict

All findings addressed, no new Critical/Important breakage.
