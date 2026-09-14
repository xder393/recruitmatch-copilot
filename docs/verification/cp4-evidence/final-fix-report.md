# CP4 final fix report — startup dependency

## Status and scope

Implemented the sole final-review finding, `CP4-ARCH-M1`, from base
`c3d3e1290afaa72be1b7778fcdf68233df63c4f6`.

- Added Beat's missing `minio-init` dependency with
  `condition: service_completed_successfully`.
- Added one parsed-YAML regression covering the required `minio-init` and
  `bootstrap` one-shot conditions for API, Worker, and Beat.
- Preserved all other dependency conditions, commands, timing, healthchecks,
  environment aliases, restart policies, and named-cache volumes.
- Did not modify root-owned audit/ledger material or other task reports.

The mutation this regression catches is removal of either required one-shot
dependency, or changing either success condition, on any of the three runtime
services. Expectations are hand-written literals and the test parses the shipped
Compose document rather than grepping source text.

## Root-cause verification

Before editing production configuration, inspected the parsed source pattern:
API and Worker each declared both `minio-init` and `bootstrap` with
`service_completed_successfully`; Beat declared only `bootstrap`. This exactly
matched the review finding and isolated the root cause to one missing Beat mapping.

## TDD evidence

### RED

Added the regression test first, leaving production Compose unchanged, then ran it
against the retained test image with only the new test file mounted:

```sh
AI_ENABLED=false OPENAI_API_KEY= docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/foundation/test_container_contract.py:/app/tests/foundation/test_container_contract.py:ro test-unit pytest tests/foundation/test_container_contract.py -q
```

Exit `1`, expected failure:

```text
...F.                                                                    [100%]
E           AssertionError: assert {'minio-init'...uccessfully'}} == {'minio-init'...uccessfully'}}
E             {'minio-init': None} != {'minio-init': {'condition': 'service_completed_successfully'}}
FAILED tests/foundation/test_container_contract.py::test_runtime_services_wait_for_one_shot_initialization
1 failed, 4 passed in 0.03s
```

The failure was the intended missing Beat `minio-init` condition, not a test error.

### GREEN

After the minimal two-line Beat dependency change, ran the same focused file with
only the corrected Compose file and test file mounted:

```sh
AI_ENABLED=false OPENAI_API_KEY= docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/docker-compose.yml:/app/docker-compose.yml:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/foundation/test_container_contract.py:/app/tests/foundation/test_container_contract.py:ro test-unit pytest tests/foundation/test_container_contract.py -q
```

Exit `0`:

```text
.....                                                                    [100%]
5 passed in 0.03s
```

These mounts were diagnostic only. All acceptance runs below used the rebuilt,
unmounted test image.

## Final rebuilt, unmounted verification

### Build

```sh
AI_ENABLED=false OPENAI_API_KEY= docker compose --env-file .env.example -p recruitmatch-cp4-sep12 build test-unit
```

Exit `0`. BuildKit reported:

- image config digest:
  `sha256:5f1af5a869e1a29d233bb2b7a5db4d77742429836ecd1822de53e0ba7138edc4`
- manifest-list/image ID:
  `sha256:b61c6b52c53d4f427890569ff28de210fcbe7f7c6d86971a8f66a95f1c8bf834`

Selective `docker image inspect recruitmatch-cp4-sep12-app-test:latest` confirmed
the latter ID without printing image environment values.

### Focused contract

```sh
AI_ENABLED=false OPENAI_API_KEY= docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit pytest tests/foundation/test_container_contract.py -q
```

Exit `0`: `5 passed in 0.03s`.

### Full unit lane

```sh
AI_ENABLED=false OPENAI_API_KEY= docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit
```

Exit `0`: `286 passed in 18.42s`.

### Ruff lint and formatting

```sh
AI_ENABLED=false OPENAI_API_KEY= docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit sh -c 'ruff check app tests scripts && ruff format --check app tests scripts'
```

Exit `0`:

```text
All checks passed!
211 files already formatted
```

### Rendered Compose dependency allowlist

The rendered document was piped directly into a Python assertion that retained and
printed only service names, the two one-shot dependency names, and their conditions;
no full rendered config or environment was printed:

```sh
AI_ENABLED=false OPENAI_API_KEY= docker compose --env-file .env.example -p recruitmatch-cp4-sep12 --profile test config --format json | python3 -c 'import json, sys; config=json.load(sys.stdin); services=("api","worker","beat"); one_shots=("minio-init","bootstrap"); projection={service:{dependency:config["services"][service]["depends_on"][dependency]["condition"] for dependency in one_shots} for service in services}; expected={service:{dependency:"service_completed_successfully" for dependency in one_shots} for service in services}; assert projection == expected, projection; print(json.dumps(projection, sort_keys=True))'
```

Exit `0`:

```json
{"api": {"bootstrap": "service_completed_successfully", "minio-init": "service_completed_successfully"}, "beat": {"bootstrap": "service_completed_successfully", "minio-init": "service_completed_successfully"}, "worker": {"bootstrap": "service_completed_successfully", "minio-init": "service_completed_successfully"}}
```

### Exact source/image hashes

Host `shasum -a 256` and the rebuilt unmounted test-container `sha256sum`
produced identical values:

```text
9a3ecf973575e3eabb9e96e4d6ef05c1ce454c9f68bcf2b3d40a60d1e3369860  docker-compose.yml
8c88cfbf94aaebc25c54595f4cd03c4842bc2b6843e0985b877e8defc4840089  tests/foundation/test_container_contract.py
```

Image-side command:

```sh
AI_ENABLED=false OPENAI_API_KEY= docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit sha256sum docker-compose.yml tests/foundation/test_container_contract.py
```

Exit `0`. Host hashing also exited `0`.

### Hygiene and retained state

```sh
git diff --check -- docker-compose.yml tests/foundation/test_container_contract.py
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 ps --all beat
```

Both exited `0`; diff-check output was empty. Beat remained stopped as
`Exited (0)` and was not restarted.

## Files changed

- `docker-compose.yml`
- `tests/foundation/test_container_contract.py`
- `.superpowers/sdd/2026-08-24-recruitmatch-v2-cp4-reliability/final-fix-report.md`

## Self-review

- The production diff is exactly the missing two-line Beat dependency mapping.
- The test exercises parsed YAML and covers all three required runtime services and
  both required one-shot services, so API/Worker drift is protected alongside Beat.
- Existing Redis health dependency and all non-dependency Beat configuration remain
  unchanged.
- No source mounts were used for acceptance, and no edits were made to code/tests
  after the rebuilt acceptance gates.
- Root-owned dirty documentation and evidence files were not staged.

## Honest limits and concerns

Per the brief, this narrow dependency-only correction did not rerun the PostgreSQL
integration suite, lifecycle smoke, app/Worker/Beat processes, real model/provider
paths, remote CI, performance, or download behavior. It establishes the source and
rendered startup dependency contract only. The controller owns final whole-checkpoint
gates and cleanup. No implementation concerns remain within this scope.
