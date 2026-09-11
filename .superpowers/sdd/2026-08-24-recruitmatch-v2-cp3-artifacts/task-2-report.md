# CP3 Task 2 implementer report

Worktree: `/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2`.
Branch: `codex/recruitmatch-v2`; starting commit `ccba866`.
Dedicated synthetic Compose project: `recruitmatch-cp3-task2-sep11`.
All Compose invocations explicitly use `--env-file .env.example`; no real `.env`
contents or secret-bearing resolved configuration are printed.

## Implementation

- SDK-free immutable location validation, stable storage failures, and a separate
  typed `ArtifactStoreInspector` returning `ArtifactInspection(size_bytes, sha256)`.
- Bounded single-part S3 adapter, explicit base64 SHA-256, checksum-enabled Head,
  requested payload length/digest checks, Head/Get/stream consistency checks, and
  StreamingBody closure on success and failure. Maximum object size is 10 MiB;
  reads consume at most the smaller of caller/global limits plus one byte.
- Independent read-only authenticated HeadBucket health probe; configuration repr
  and exception text redact endpoint, credentials, keys and provider messages.
- SDK-free reusable Fake with typed put/read/delete/inspect and idempotent deletion.
- Private MinIO initialization, application IAM policy, digest-pinned client,
  immutable security-fixed upstream server source build, non-root server runtime,
  upstream license/README/source info retained in the image, no host MinIO ports.
- Explicit runtime environment allowlist replaces broad `.env` imports. Root
  credentials enter only MinIO, its initializer and one-shot synthetic test setup. Application credentials are
  distinct; default credentials are marked demo-only.
- Real MinIO contracts live in the existing Compose CI integration selector;
  deterministic contracts remain in the SQLite foundation lane. The default
  Compose unit command now uses the same exclusions as CI.
- Upload consumers and local storage composition remain unchanged for Task 3;
  reconciliation/enumeration remains Task 4. No controller ledger edits.

## TDD evidence

1. Before implementation, `.venv/bin/pytest tests/foundation/test_artifact_store.py -q`
   failed during collection with `ImportError: cannot import name 'ArtifactAccessDenied'
   from 'app.artifacts.ports'`: the new failure/adapter contract was absent.
   The initial contract collection is deliberately RED, not a passing infrastructure check.
2. After adapter/Fake implementation, the same command passed 28 tests.
3. Added SDK-native streaming integrity tests before fixing exception translation.
   Same command: `2 failed, 31 passed` (two `artifact_storage_failure` results
   instead of checksum/length-specific errors). Added explicit FlexibleChecksumError
   and IncompleteReadError translation; all 33 tests passed.
4. Added configuration validation tests before implementation. Same command:
   `4 failed, 33 passed`, each `DID NOT RAISE ArtifactStorageFailure` for empty
   identity or invalid/credential-bearing endpoint. Added validation; 37 tests passed.
5. Added `tests/foundation/test_minio_init.py` to execute init against controlled
   administrative responses. RED: `.venv/bin/pytest tests/foundation/test_minio_init.py -q`
   produced `2 failed, 1 passed`: extra policy/group still exited successfully.
   Added fail-closed exact policy/group/status checks; GREEN: 3 passed.
   The equivalent live synthetic test first attached `readwrite` and observed the
   old initializer incorrectly succeed; after the fix it printed only
   `minio_init_failed`, exited nonzero, then detaching that test policy restored
   `minio_init_ready`. No extra test permissions remain.

Deterministic coverage includes namespace/traversal rejection, exact server-derived
key, requested digest/length/global size rejection, Head missing/malformed/mismatched
checksums, oversized Head rejection before Get, partial/lying peer consuming exactly
6 bytes for a 5-byte bound, closed bodies on every fetched-body path, specific SDK
integrity failures, sanitized missing/access-denied/transient failures, and Fake deletion.

## Verification and environment notes

- Focused contracts: 40 passing (`test_artifact_store.py` + `test_minio_init.py`);
  changed Python files pass ruff and mypy.
- Full `ruff check app tests scripts` passed. Initial full format check found two
  pre-existing layout hunks in `tests/retrieval/test_pgvector_search.py`. Controller
  explicitly approved a narrow formatter-only repair (2026-09-11 message and brief
  appendix); included exactly those two formatting hunks, no test behavior changes.
- Full configured mypy targets plus `app/artifacts`: 41 files passed.
- `uv lock --check --offline`: 110 packages resolved, consistent lock. Runtime adds
  boto3 1.43.92, botocore 1.43.92, jmespath 1.1.0, s3transfer 0.19.2, urllib3 2.7.0.
  No unrelated lock versions changed.
- Parsed `docker compose --env-file .env.example -p recruitmatch-cp3-task2-sep11
  --profile test config --format json` through assertions, printing only pass/fail:
  no root variables in API/Worker/bootstrap/test-unit/test-integration; no MinIO host
  ports; MinIO persists only its named data volume. An initial assertion without
  `--profile test` failed because Compose omitted test services; corrected selector passed.
- With synthetic parent `AI_ENABLED=true OPENAI_API_KEY=synthetic-not-real`, parsed
  Compose assertions verify both test lanes force AI_ENABLED=false and an empty key.
- Source build finished successfully in 455.2 seconds. Server health passed and
  two consecutive `docker compose --env-file .env.example -p recruitmatch-cp3-task2-sep11
  run --rm minio-init` commands printed `minio_init_ready`, exit 0. After drift-guard
  amendment the live initializer also passed with least-privilege membership restored.
- Host manifest lookup timed out; Docker daemon pulls succeeded. Host uv's first
  restricted-network/cache attempt failed; command-scoped inactive-proxy removal
  and a task-owned `/private/tmp/recruitmatch-cp3-task2-uv` cache succeeded. No global
  proxy configuration changed. uv emitted upstream historical specifier-normalization
  warnings while resolving, plus parent project-table warnings.

## Pinned build identities

- MinIO source: `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`, upstream
  `RELEASE.2025-10-15T17-29-55Z`, including session-policy privilege escalation fix.
- Go builder: `golang:1.24.8-bookworm@sha256:4ed690d6649d63c312b99a6120025ec79ce3b542968a37da53d6236c7c61a848`.
- Runtime: `alpine:3.22.2@sha256:4b7ce07002c69e8f3d704a9c5d6fd3053be500b7f1c69fc0d80990c2ad8dd412`.
- Client: `minio/mc:RELEASE.2025-08-13T08-35-41Z@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727`.
- Server module is downloaded via Go proxy and verified through the Go checksum
  database. Build uses CGO_ENABLED=0, GOTOOLCHAIN=local, -mod=readonly, -trimpath,
  -buildvcs=false. Upstream source is unmodified.

## Concerns / limits

MinIO upstream is archived/unmaintained. This pinned source build includes the named
security fix but is not a full security-scan claim; CP6 remains the image/security
scan gate. Maintainers own future updates/replacement. The ADR documents this and
the necessary `HeadBucket -> s3:ListBucket` mapping, including ability to list the
one bucket. The adapter holds at most a bounded 10 MiB input copy for single-part
Put; Task 3 still owns request streaming to bounded temporary files.

Final real-backend evidence, full suites, image identities and cleanup follow below.

## Changed files

`app/artifacts/ports.py`, `app/artifacts/s3.py`, `tests/fakes/artifacts.py`,
`tests/foundation/test_artifact_store.py`, `tests/foundation/test_minio_init.py`,
`tests/artifacts/test_s3_artifact_store.py`, `ops/minio/Dockerfile`,
`ops/minio/init.sh`, `ops/minio/test-setup.sh`, `ops/minio/app-policy.json`,
`docs/adr/0003-artifact-minio-build-and-iam.md`, `pyproject.toml`, `uv.lock`,
`Dockerfile`, `docker-compose.yml`, `.env.example`, `.github/workflows/ci.yml`,
controller-approved formatting only in `tests/retrieval/test_pgvector_search.py`,
and this force-tracked report.

## Final backend and full-suite evidence

The initial Python image build completed successfully after the locked PyTorch
wheel downloaded at 1220.2 seconds. A separate bounded urllib probe of that public
URL returned HTTP 403 (`error code: 1010`), but uv's ongoing download succeeded;
the live build was never restarted or replaced. The finished dependency layer
was cached for rebuilding current source before formal verification.

While it downloaded, an explicitly interim check mounted current source and
the exact locked pure-Python boto dependencies read-only into an existing runner.
It reported 11 passed, 1 failed, 2 deselected: only the extra `ListBuckets -> 403`
assumption failed. This was not counted as final verification. Controller confirmed
the pinned server's filtered-list behavior and required an actually existing
second private bucket. A test-profile-only, one-shot administrative service now
creates `synthetic-other`; the normal test process has no admin credentials.
The policy is not broadened. The ADR links the exact upstream handler, and the
canonical final suite below proves the allowed listing excludes that second bucket.

Commands below all run from the task worktree with the dedicated project and
synthetic `.env.example`; none use real LLM calls or default-project data.

1. `docker compose --env-file .env.example -p recruitmatch-cp3-task2-sep11 build minio bootstrap test-integration`
   passed against finalized adapter/source pins, using completed dependency caches.
2. `docker compose --env-file .env.example -p recruitmatch-cp3-task2-sep11 run --rm test-unit`
   used its default SQLite selector: **217 passed in 12.92s**, no skips/warnings.
3. `docker compose --env-file .env.example -p recruitmatch-cp3-task2-sep11 run --rm test-integration pytest tests/artifacts/test_s3_artifact_store.py -q`
   passed **13 tests in 0.34s**, including mc admin denials. The later test-only
   second-bucket setup did not change any adapter, test Python, lockfile or runtime code.
4. After adding the second-bucket setup, `docker compose --env-file .env.example -p recruitmatch-cp3-task2-sep11 build test-integration`
   passed. Then the default `docker compose --env-file .env.example -p recruitmatch-cp3-task2-sep11 run --rm test-integration`
   ran the actual CI-selected PostgreSQL/retrieval/artifact lane: **177 passed in
   8.32s**, no skips/warnings. This includes all 13 real MinIO contracts with the
   forbidden second bucket present.

Backend facts proved by those contracts:

- Explicit incorrect SHA-256 is rejected by the server with
  `XAmzContentChecksumMismatch`; subsequent Head reports the object missing.
- Successful single-part Put returns backend SHA-256 on checksum-enabled Head;
  inspection matches the trusted input digest and Get bytes match input exactly.
  Head rejects caller-size overflow, Delete is idempotent, and Head after Delete
  returns stable `artifact_missing`.
- Anonymous Get returns HTTP 403 for a known existing object.
- Application credentials receive HTTP 403 for bucket creation, bucket policy
  replacement, bucket deletion, Put/Get/Delete outside `tenants/*`, Put into and
  Head of the second private bucket. mc user-list and IAM policy-create are denied.
- HeadBucket succeeds without writes; bucket-scoped listing succeeds and
  ListBuckets returns only `recruitmatch-artifacts`, excluding `synthetic-other`.

Final local checks: `ruff check app tests scripts`, `ruff format --check app tests scripts`
(173 files), `mypy app/models app/retrieval app/services app/ai app/tasks app/artifacts`
(41 files), `uv lock --check --offline`, and `git diff --check` passed.

## Final image/source identities

- Server image: `recruitmatch-cp3-task2-sep11-minio:2025-10-15`, image/index ID
  `sha256:d8f63a8e7ca79f34add5734dc2dfaeb0273fe6b74e0048826419c61a07030fd6`.
- Runtime app image: `recruitmatch-cp3-task2-sep11-app:latest` (local project tag), ID
  `sha256:dbd97b5ced6cfc967add3151e9f486a6bcc3adbf1a77a1538b9b771fa93ea161`.
- Final integration image: `recruitmatch-cp3-task2-sep11-app-test:latest` (local
  project tag), ID `sha256:b1392fac32de8874660abef20229ab7058da1cbe145822f87b44a9dc994976ce`.
  The prior unit/full-MinIO image before the test-only setup addition was
  `sha256:f347ed1da382f7ef1a67a303c1eb279992d9a8f99a5fac19ebc99f0dcd501676`.
- Built server binary SHA-256:
  `3328138fec7830926ef46007c8ab1b65eccba66bf6346ca4267dac734a7ace87`.
- Retained source-info.json confirms Go module
  `v0.0.0-20251015172955-9e49d5e7a648`, Origin.Hash
  `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`, module sum
  `h1:6TdolSCLSs2nwm8i0PpWDqf9iX2Ty9WQK8wmr7dCnUM=`, and go.mod sum
  `h1:yCWDkwWO9IWpGsT4mreDDN/B/QVmK2zC666uInRAcqE=`.
- Runtime reports Go 1.24.8 linux/arm64. Because upstream source is compiled
  without release-branding ldflags, its version string is `DEVELOPMENT.GOGET`;
  provenance is established by the pinned source, retained origin/hash metadata,
  OCI revision label and recorded binary/image identities, not by that display string.

## Self-review

Reviewed the full diff against the binding brief. All storage SDK imports remain
inside the adapter; production upload consumers are unchanged. New runtime settings
and public failure strings do not contain provider details. Only the application
identity is sent to runtime/test processes. One-shot administrative configs are
created with mktemp and removed on exit. Tests consume required `ops/minio` assets
from the actual Docker test image; no excluded Markdown/.env fixture is assumed.
The only unrelated-file change is the explicitly authorized two-hunk formatter repair.
No unaddressed correctness issue found; archival/maintenance and CP6 scan limitations
are explicit above and in the ADR.

## Final infrastructure check and cleanup

After the final test-only setup change, the final image ran
`docker compose --env-file .env.example -p recruitmatch-cp3-task2-sep11 run --rm --no-deps test-unit pytest tests/foundation/test_container_contract.py tests/foundation/test_minio_init.py -q`:
**7 passed in 0.93s**. This verifies the amended infrastructure assets without
repeating the unchanged 217-test application/unit suite.

`docker compose --env-file .env.example -p recruitmatch-cp3-task2-sep11 --profile test down -v`
exited 0 and removed only the project's six service containers, its network, and
`recruitmatch-cp3-task2-sep11_{minio-data,postgres-data,redis-data}` volumes.
Subsequent Docker container and volume listings filtered by the exact project
label returned no entries. All deleted data was synthetic/recreatable. Images
and build cache remain available for review; no default-project volume, container,
data or shared image tag was mutated.
