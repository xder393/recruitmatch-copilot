**CP4-ARCH-M1 — Beat omits the required initialization dependency** — ADDRESSED. `docker-compose.yml:161-162` now gates Beat on `minio-init` with `service_completed_successfully`; `tests/foundation/test_container_contract.py:31-42` parses the shipped YAML and asserts both one-shot dependencies for API, Worker, and Beat.

**New Breakage in the Fix Diff:** None. The production change is limited to the required two-line dependency; the regression is structurally valid and preserves unrelated Beat configuration.

**Out-of-Scope Observations:** None.

**Checks reviewed:** RED `1 failed, 4 passed` → GREEN `5 passed`; rebuilt unmounted focused contract `5 passed`; full unit lane `286 passed`; Ruff lint and format `211 files`; rendered dependency allowlist matched all six service/dependency conditions; exact host/image hashes matched for both changed shipped files. Controller independently reports final unmounted gates passing, including `286` unit and `503` integration tests, and all `67` shipped-file hashes matching the current image.

**Fix round:** All findings addressed, no new Critical/Important breakage.
