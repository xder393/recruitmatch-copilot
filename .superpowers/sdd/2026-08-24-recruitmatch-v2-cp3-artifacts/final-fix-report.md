# CP3 final fix wave

Base: `65c1f86`, branch `codex/recruitmatch-v2`. Seven findings only; no broad audit,
provider calls, migration rewrites, project cleanup, main changes, merge or push.
Only Compose project `recruitmatch-cp3-task4-example-sep11`, explicit `.env.example`.
Both controller-owned projects remain retained.

## Investigation and contract

Confirmed invalid repair clears Artifact identity but leaves unique Source checksum;
matching truncates before final semantic invalidation; console multiplies 0–100 again.
ADR records exact identity-release audit tradeoff and full-pool guidance cost before
implementation. Parser behavior remains 1,048,576 Unicode characters.
Docker socket read required scoped escalation; approved project inspection succeeded
(PostgreSQL/Redis/MinIO healthy). UI harness currently checks text only; controller
asked to resolve smallest actual-JS execution gate without dependency expansion.

## Evidence (incremental)

RED: baseline image with only tests mounted read-only:
```
docker compose --env-file .env.example -p recruitmatch-cp3-task4-example-sep11 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-integration pytest tests/artifacts/test_reconciliation.py tests/artifacts/test_privacy_races.py tests/matching/test_matching_service.py tests/resumes/test_extractors.py -q -k 'invalid_repair_releases or mixed_lanes or privacy_guard_emits or former_fourth or chinese_text_limit'
```
Exit1: **13 failed, 4 passed, 43 deselected in 2.41s**. Twelve reuploads raised
uq_resume_tenant_hash/uq_knowledge_tenant_checksum_type unique violations; Top3
returned AI 0.72 instead of former-fourth Frontend 0.832. Existing-character-boundary,
mixed-lane reservation and actual SQL capture characterizations passed without
production changes. Host Node diagnostic executed actual renderMatches: displayed
semantic 8000 versus expected 80; this is RED diagnostic, not final UI acceptance.

Implementation boundary: source checksum cleared in the guarded invalid cleanup
transaction; full eligible pool retained until final Top3 truncation; renderer
uses native semantic scale; constant/docs clarify Unicode characters. HNSW comment
now records observed history/seed behavior and uncertainty. Task4 attribution
distinguishes prior behavioral tests, controller SQL capture, and this new test.

Controller authorized test-only official Node binary stage, pinned
`node:24-trixie-slim@sha256:6950b66b4c0cb0151ce89fa75074673850763d096b044f422c6729b588dd4956`
(controller verified v24.21.0). No npm/browser dependencies, runtime stage change
or host Python. Node becomes a bounded test supply-chain item for CP6 scanning;
pinning is not security certification. Awaiting image execution verification.

Focused GREEN after implementation and test formatting:
```
docker compose --env-file .env.example -p recruitmatch-cp3-task4-example-sep11 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-integration pytest tests/artifacts/test_reconciliation.py tests/artifacts/test_privacy_races.py tests/matching/test_matching_service.py tests/resumes/test_extractors.py tests/ui/test_ai_recruiter_console.py -q -k 'invalid_repair_releases or mixed_lanes or privacy_guard_emits or former_fourth or chinese_text_limit or renders_semantic_score' --tb=short
```
Exit0: **24 passed, 44 deselected in 1.09s**. Includes six additional inactive
Knowledge cases, real renderer execution and all new regressions. Rebuilt app code,
test-only read-only mount for final formatted tests; final unmounted gate follows.

## Final unmounted acceptance

Final build (after all code/test edits and formatting), exit0:
```
docker compose --env-file .env.example -p recruitmatch-cp3-task4-example-sep11 build bootstrap test-integration
```
Only existing build cache and the controller-approved Node stage were used; no
application dependency changes. No source bind mounts in any final command below.

```
docker compose --env-file .env.example -p recruitmatch-cp3-task4-example-sep11 run --rm --no-deps test-integration pytest tests/artifacts/test_reconciliation.py tests/artifacts/test_privacy_races.py tests/artifacts/test_upload_saga.py tests/artifacts/test_privacy_delete.py tests/matching/test_matching_service.py tests/matching/test_hybrid_engine.py tests/web/test_recruiter_console.py tests/ui/test_ai_recruiter_console.py tests/resumes/test_extractors.py -q --tb=short
```
Exit0: **111 passed in 5.34s**. Covers real PostgreSQL/MinIO reconciliation and
privacy/upload regressions, concurrent active deduplication, exact late-write
cleanup, FK KEY SHARE barriers, final score/citation fallback, and literal UI
80→80 / 0→0 / NULL→证据不足 with rule/final conversion preserved.

```
docker compose --env-file .env.example -p recruitmatch-cp3-task4-example-sep11 run --rm --no-deps test-unit ruff check app/services/artifact_reconciliation.py app/services/matching.py app/resumes/extractors.py tests/artifacts/test_reconciliation.py tests/artifacts/test_privacy_races.py tests/matching/test_matching_service.py tests/resumes/test_extractors.py tests/retrieval/test_pgvector_search.py tests/ui/test_ai_recruiter_console.py
docker compose --env-file .env.example -p recruitmatch-cp3-task4-example-sep11 run --rm --no-deps test-unit ruff format --check app/services/artifact_reconciliation.py app/services/matching.py app/resumes/extractors.py tests/artifacts/test_reconciliation.py tests/artifacts/test_privacy_races.py tests/matching/test_matching_service.py tests/resumes/test_extractors.py tests/retrieval/test_pgvector_search.py tests/ui/test_ai_recruiter_console.py
docker compose --env-file .env.example -p recruitmatch-cp3-task4-example-sep11 run --rm --no-deps test-unit mypy app/models app/retrieval app/services app/ai app/tasks app/resumes/extractors.py
docker compose --env-file .env.example -p recruitmatch-cp3-task4-example-sep11 run --rm --no-deps test-unit uv lock --check --offline
```
All exit0: Ruff all checks passed; 9 files already formatted; mypy no issues in
40 source files; offline lock resolved 110 packages in 3ms. Parallel Compose
checks emitted one transient orphan-container warning naming a concurrently
running --rm test container. No cleanup or --remove-orphans was performed.

```
docker compose --env-file .env.example -p recruitmatch-cp3-task4-example-sep11 run --rm --no-deps test-unit python -c 'import os, subprocess; assert os.getuid() == 10001; print("UID", os.getuid()); subprocess.run(["node", "--version"], check=True); subprocess.run(["node", "tests/ui/render_matches_scores.cjs"], check=True)'
docker compose --env-file .env.example -p recruitmatch-cp3-task4-example-sep11 run --rm --no-deps bootstrap python -c 'import os, shutil; assert os.getuid() == 10001; assert shutil.which("node") is None; print("runtime UID", os.getuid(), "node absent")'
docker image inspect recruitmatch-cp3-task4-example-sep11-app-test recruitmatch-cp3-task4-example-sep11-app --format '{{.RepoTags}} {{.Id}} {{.Config.User}}'
```
All exit0: test UID10001, Node **v24.21.0**, actual renderer literal assertions
passed; runtime UID10001 and Node absent. Copying only the Node binary works,
with no additional libraries or npm. Final image identities (Config.User recruitmatch):

- Test: `sha256:aed2f00d5fea05e2c0fbdf29c88348530edbabda40d0fb12479a31b7638f9790`.
- Runtime: `sha256:041a8a3e270f865198b65bc931a92e0cafd06f8ec0d5989783a9dcafa033a5f4`.

Covering-source identity commands (both exit0, all 16 hashes match exactly):
```
docker compose --env-file .env.example -p recruitmatch-cp3-task4-example-sep11 run --rm --no-deps test-unit sha256sum Dockerfile app/services/artifact_reconciliation.py app/services/matching.py app/resumes/extractors.py app/matching/hybrid.py web/index.html tests/artifacts/test_reconciliation.py tests/artifacts/test_privacy_races.py tests/artifacts/test_upload_saga.py tests/artifacts/test_privacy_delete.py tests/matching/test_matching_service.py tests/matching/test_hybrid_engine.py tests/web/test_recruiter_console.py tests/ui/test_ai_recruiter_console.py tests/ui/render_matches_scores.cjs tests/resumes/test_extractors.py
shasum -a 256 Dockerfile app/services/artifact_reconciliation.py app/services/matching.py app/resumes/extractors.py app/matching/hybrid.py web/index.html tests/artifacts/test_reconciliation.py tests/artifacts/test_privacy_races.py tests/artifacts/test_upload_saga.py tests/artifacts/test_privacy_delete.py tests/matching/test_matching_service.py tests/matching/test_hybrid_engine.py tests/web/test_recruiter_console.py tests/ui/test_ai_recruiter_console.py tests/ui/render_matches_scores.cjs tests/resumes/test_extractors.py
```
```
d6bf0242fa47751f8132c1e32be39007e15c90cc913ef21459a82e722d340332  Dockerfile
0ef3e9e792be9522cd51a5ba7f4660b5bac80122d711a54e3ca1017793fed892  app/services/artifact_reconciliation.py
7602611d80c500ad4b77fd13663cd3179648176f17590fa5a385ccdd5daedd45  app/services/matching.py
0dfefc9eb5ddfa4e079ef542abefc2a936b5f4c1ef341ea8e2b7cb62379cf1a2  app/resumes/extractors.py
188a0ff4868e53c0f39b053dcee3b63a017a4d62725f798ae863c0958e9cd9ed  app/matching/hybrid.py
de2e72befc21d70f10ca248b1f854afa01da5ac1cc55312e04bb44659926ba4c  web/index.html
278bdd02fbd41a7de6b7809feb760d3f0abb4f8c2339759588216c565a97d458  tests/artifacts/test_reconciliation.py
3f8628b7985b59d4b93c62ebc90e37b43efac131c1105e7d40857f14ff42a7e7  tests/artifacts/test_privacy_races.py
96ee35d587685987275b9304f5cc7cfb7dffd2f2733a4b1b7426461e582229dc  tests/artifacts/test_upload_saga.py
8e30b7bf4605e0e9cc3bcbe9880b9aec1fda44a99b328466775a684adeabdb4b  tests/artifacts/test_privacy_delete.py
961db646178c3b8e820f4b20d62ed4780fa3d3f49ce6a814c38f959c7fbd442d  tests/matching/test_matching_service.py
376c0dc2279f379cbd90ab77f1c62502b527375eeb7f4b95d1cd57e84059fbb5  tests/matching/test_hybrid_engine.py
290b1cad208fabe8d04aba8c937efc166f791fb6443c6b82aadbdb155ed9af5b  tests/web/test_recruiter_console.py
3ac1c6ebee9947bd3991d667d36b0951aeed7d13457ce1c135ba2cdae9ee875d  tests/ui/test_ai_recruiter_console.py
474a7c71b313098804d3b676ccf15d1e00c8257f3fc4fb8e8f17c2e8de1e131a  tests/ui/render_matches_scores.cjs
eff10e33af7e5cad808a083d85582d49bef3f155dc32c1a29b964363166ef4c7  tests/resumes/test_extractors.py
```

## Seven dispositions and evidence limits

1. Important identity conflict: fixed; 18 Resume/Knowledge/inactive-Knowledge cases
   cover missing/size/checksum, failed cleanup, new owner, active duplicate reuse,
   and old exact-location late-write retry. Terminal work stays terminal.
2. Important Top3: fixed over the full eligible pool. Former fourth Frontend
   promotes to 0.832 behind Backend 0.864 and Data 0.848 after AI loses semantic
   evidence (AI becomes 0.72). Exactly three DB rows persist; four Fake semantic,
   four guidance and four final-resolution calls are explicit. Existing tests
   separately assert invalidated semantic NULL/fallback and deterministic ordering.
3. Important UI scale: fixed; actual inline script/renderMatches executes with a
   minimal DOM fake in final image. No copied Python formula or source-text oracle.
4. Minor parser units: constant renamed and ADR/Task3 corrected; Chinese input at
   1,048,576 characters succeeds and 1,048,577 fails. Byte/decompression caps unchanged.
5. Minor HNSW claim: comment describes observed index-history/reduced-seed behavior.
   No reproduction or fix of the original one-time empty result's cause is claimed.
6. Minor mixed lanes: real PG test commits two reservations in fresh UoWs while
   pending spans three pages; independent cleanup/DELETED slots and a fresh service
   resume the third pending page, then wrap to finish reserved work. Bounded observed
   progress only; no added scheduler/cursor authority or general starvation proof.
7. Minor lock evidence: actual IdentityRepository PostgreSQL statement captured and
   required to contain FOR NO KEY UPDATE; prior FK compatibility barrier retained.
   Task4 report now attributes prior behavioral tests and controller capture correctly.

Prior full default-lane evidence (225 unit / 253 integration) is reused only for
unaffected paths as recorded by the controller, not as coverage of these fixes.
The final 111-test gate reruns all affected named suites above. No repeat ANN stress,
broad audit, full default lanes, network resolution, real model or provider tests.
MinIO maintenance status and missing original dependency-warning diagnostics remain
explicit prior evidence limits. No claim to reconstruct those diagnostics or certify
dependencies. Full-pool guidance cost and Source-checksum audit tradeoff are in ADR.
Both controller-owned projects remain retained for controller cleanup. Controller
dispatches the single scoped re-review; no self-dispatched reviewer.
