# CP3 Task 1 implementation report

Date: 2026-09-11. Base: `0c36cb5575b45d14a3576523cdeca2fee394a37e`.
Branch: `codex/recruitmatch-v2`.

## Delivered scope

- Added the independent Artifact ORM entity and domain enums: six storage states, two owner types, and six bounded storage error codes. Source processing states remain separate.
- Added migration `20260824_13` immediately after actual head `20260824_12`. Artifact status/owner/error enums use explicit VARCHAR check constraints, with identical persisted values, lengths, constraints, and indexes in SQLAlchemy metadata and Alembic.
- Active upload uniqueness is `(tenant_id, owner_type, sha256)` in PENDING, AVAILABLE, and FAILED. Proposed owner ID is deliberately excluded. PostgreSQL `INSERT ... ON CONFLICT DO NOTHING` waits for the competing transaction; the next statement retrieves and locks the winning row. Cleanup between those statements retries the now-free claim. This uses PostgreSQL's existing READ COMMITTED default.
- The caller gets the winning Artifact and its owner ID. The repository neither commits nor rolls back the caller's transaction. The integration test commits two concurrent callers' unrelated Tenant inserts and proves both survive while exactly one Artifact wins.
- Added tenant- and owner-qualified lifecycle methods, guarded against persisted state under a row lock. Refreshing the ORM identity map and suppressing autoflush during the guard prevent stale or locally dirty status from authorizing resurrection. Identical target calls are idempotent; all other transitions follow the frozen six-state graph.
- SHA-256 is cleared on entry to CLEANUP_PENDING, enforced by a database lifecycle check. CLEANUP_FAILED remains redacted, retry clears the previous error code, and deleted content can be uploaded again with a new Artifact ID.
- Added immutable `ArtifactLocation` resolution for the exact tenant/owner type/owner/Artifact tuple. Namespace maps to `resumes` or `knowledge`; absent or out-of-scope rows raise the same bounded `ArtifactNotFoundError`.
- Added nullable `artifact_id` on Resume and KnowledgeDocument, with composite foreign keys requiring the same tenant and owner ID. Namespace ownership is additionally checked by repository transitions and location resolution. Registered the model and added the Artifact repository protocol and UoW wiring.
- Kept the existing ResumeArtifact/extracted-text relationship and local knowledge producers. KnowledgeDocument.artifact_key is now nullable so a new anchor can exist independently. The legacy processor reports the stable `knowledge_artifact_missing` failure for an absent local key; it does not guess another storage path. Production composition is otherwise unchanged.
- CI excludes `tests/artifacts` from SQLite and includes it in the PostgreSQL job. Updated head expectations to revision 13 while retaining all pgvector-specific catalog assertions.

No S3 adapter, upload cutover, legacy backfill, production reset, real LLM call, push, merge, controller-ledger edit, or subagent dispatch occurred.

## Interface for following tasks

```python
artifact = uow.artifacts.claim_upload(
    tenant_id, owner_type, proposed_owner_id, sha256, media_type, size_bytes
)
# Use artifact.owner_id, which may differ from proposed_owner_id.
uow.artifacts.mark_available(
    tenant_id, artifact.id, owner_type=owner_type, owner_id=artifact.owner_id
)
location = uow.artifacts.resolve_location(
    tenant_id, owner_type, artifact.owner_id, artifact.id
)
```

All transition methods require `owner_type` and `owner_id` keyword arguments.
`mark_failed` and `mark_cleanup_failed` also require an `ArtifactErrorCode`.
The application UoW owns Source creation and the commit; Task 3 owns actual storage routing and privacy deletion of Source content and derived evidence.

## RED/GREEN evidence

All Docker operations used only Compose project `recruitmatch-cp3-task1-sep11`, and images were built from this worktree.

Initial RED, before Artifact production files existed:

```text
docker compose -p recruitmatch-cp3-task1-sep11 build bootstrap test-integration
docker compose -p recruitmatch-cp3-task1-sep11 run --rm -e AI_ENABLED=false \
  test-integration pytest tests/artifacts/test_artifact_repository.py -q
ModuleNotFoundError: No module named 'app.domain.artifacts'
1 error during collection
```

Focused GREEN after implementation: first `15 passed`, then expanded coverage `58 passed`, and finally `59 passed in 0.81s` after adding the revision-12/13 legacy-preservation roundtrip.

To establish behavioral regression sensitivity beyond the initial missing-module RED, eight disposable in-memory mutations were run against the real PostgreSQL database. The temporary probe was `/private/tmp/recruitmatch_cp3_task1_mutation_probe.py`; it changed imported behavior only, never production files. Each invocation returned pytest exit 1 as expected, and the shell verified that exact exit code. The unmodified suite then returned exit 0.

| Disabled invariant | Observed RED |
| --- | --- |
| Include proposed owner in claim identity | Distinct concurrent owners returned different Artifact IDs: 1 failure |
| Refresh and lock persisted state | Stale AVAILABLE object failed to reject the post-deletion call: 1 failure |
| Tenant/owner location filtering | Out-of-scope lookup did not raise ArtifactNotFoundError: 1 failure |
| Transition validation | All three forbidden PENDING cleanup/deletion paths stopped rejecting: 3 failures |
| Immediate checksum redaction | PostgreSQL rejected retained SHA in CLEANUP_PENDING: 2 failures |
| CLEANUP_FAILED retry edge | Both cleanup branches failed retry: 2 failures |
| Exclude DELETED from upload identity | Re-upload returned the deleted Artifact ID: 2 failures |
| ORM/migration schema agreement | Removing ORM size constraint made exact catalog comparison fail: 1 failure |

Probe command shape:

```text
docker compose -p recruitmatch-cp3-task1-sep11 run --rm --no-deps \
  -e AI_ENABLED=false -e PYTHONPATH=/app \
  -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/artifacts:/app/tests/artifacts:ro \
  -v /private/tmp/recruitmatch_cp3_task1_mutation_probe.py:/tmp/artifact_mutation_probe.py:ro \
  test-integration python /tmp/artifact_mutation_probe.py <variant>
```

Variants: `owner-key`, `stale`, `tenant`, `invalid-transition`, `privacy`, `retry`, `reupload`, `catalog`.

The narrow legacy-key adjustment also had a direct RED/GREEN cycle:

```text
.venv/bin/pytest tests/knowledge/test_knowledge_ingestion.py::test_legacy_processor_reports_missing_key_without_guessing_storage -q
RED: expected knowledge_artifact_missing, got knowledge_processing_failed
GREEN: passed after the explicit absent-key branch
```

Two expanded-test setup issues were corrected before final verification: the free-text error fixture originally exceeded the VARCHAR limit and hit DataError before the intended check constraint; the dirty-state test originally accessed an expired ID before calling the repository, triggering caller-side autoflush. A shorter invalid code and `expire_on_commit=False` isolate the intended checks. Neither was reported as a production defect.

## Final verification

Final worktree images were rebuilt before the combined PostgreSQL run:

```text
docker compose -p recruitmatch-cp3-task1-sep11 build bootstrap test-integration
docker compose -p recruitmatch-cp3-task1-sep11 run --rm -e AI_ENABLED=false \
  test-integration pytest tests/artifacts tests/integration tests/retrieval -q
158 passed in 7.71s
```

This includes all 59 Artifact checks, the required pgvector migration suite, actual concurrent distinct-owner claims, all 30 public transition edges, cross-tenant/owner rejection, immutable locations, error/check constraints, rollback of caller-owned work, legacy anchor isolation, exact PostgreSQL catalog equality, and upgrade preservation of legacy local keys and extracted text with no backfill.

The related SQLite regression suite ran once at the end and exited 0:

```text
DATABASE_URL=sqlite:////tmp/recruitmatch-cp3-task1-sqlite.db TASK_MODE=inline AI_ENABLED=false \
  .venv/bin/pytest tests --ignore=tests/integration --ignore=tests/artifacts \
  --ignore=tests/retrieval/test_citation_lifecycle.py \
  --ignore=tests/retrieval/test_generation_switch.py \
  --ignore=tests/retrieval/test_pgvector_search.py -q
All selected tests passed (pytest's configured double-quiet output suppressed the count).
```

Other checks:

```text
.venv/bin/ruff check app tests scripts
All checks passed!
.venv/bin/ruff format --check app tests scripts
168 files already formatted
.venv/bin/mypy app/models app/retrieval app/services app/ai app/tasks \
  app/domain/artifacts.py app/repositories/artifacts.py
Success: no issues found in 40 source files
UV_CACHE_DIR=/private/tmp/recruitmatch-cp3-task1-uv uv lock --check --offline
Resolved 105 packages in 12ms; exit 0
docker compose -p recruitmatch-cp3-task1-sep11 run --rm --no-deps -e AI_ENABLED=false \
  bootstrap python -c 'import app.main; print("production import passed")'
production import passed
git diff --check
exit 0
```

The first host `uv lock --check` could not open the sandbox-excluded global uv cache. Using an explicit disposable cache resolved it without escalation; the tool emitted only the existing parent-directory pyproject warning. Docker socket and Git metadata actions used the available explicit escalation path.

## Boundaries and remaining work

No Task 1 blocker remains. Source owner-type enforcement is at the repository boundary; the composite Source foreign key itself enforces tenant and owner ID. Storage reads and reconciliation can resolve a location in any storage lifecycle state, so the later caller must apply its operation-specific state policy.

Downgrading after future anchor-only Knowledge rows exist intentionally refuses to restore the old NOT NULL key constraint; it does not fabricate local keys. The tested roundtrip covers legacy rows, which retain their real keys. Task 3 owns the coordinated production cutover and remaining legacy-field migration.

Cleanup completed successfully with `docker compose -p recruitmatch-cp3-task1-sep11 down -v`: the task's PostgreSQL, Redis and bootstrap containers, network, and the two disposable database volumes were removed. No default Compose project or shared production data was targeted.

## Review fix round 1 — Source owner-type anchors

Date: 2026-09-11. Fix base: `410019ee5215cd5e7320ab0a3cdb34a9ea64ed46`.
Fix commit subject: `fix: enforce artifact owner type on source anchors`; this section is included in that commit together with the implementation and regression tests.
Status: DONE_WITH_CONCERNS, solely because the broader retrieval suite had the unexplained intermittent HNSW failure recorded below. The scoped owner-type fix and its tests pass.

The review finding was verified on real PostgreSQL revision 13: both Resume→knowledge Artifact and KnowledgeDocument→resume Artifact persisted when tenant and owner ID matched. This corrective section supersedes the original report's limitation that Source owner type was enforced only by later repository operations.

Changes:

- Appended `20260911_14_artifact_owner_type.py`, revision `20260911_14`, directly after `20260824_13`. Committed migration 13 was not edited.
- Each Source now has a stored database-generated, non-null `artifact_owner_type`, fixed to `resume` or `knowledge_document`. Callers cannot override the discriminator to attach another Source type's file.
- Source foreign keys now reference `(tenant_id, artifact_owner_type, id, artifact_id)` against Artifact `(tenant_id, owner_type, owner_id, id)`. The matching Artifact unique constraint includes owner type. Nullable artifact_id still permits legacy rows.
- Migration preflight checks existing typed anchors before schema changes. An invalid row raises `RuntimeError("artifact_owner_type_mismatch: <table>")`, exposing neither file IDs nor content and never rebinding the anchor. Tests confirm the failed migration leaves revision 13 and the original invalid anchor intact; restoring the correct reference permits upgrade.
- Updated actual-head expectations to revision 14 and extended exact PostgreSQL catalog checks to the generated-column type, nullability, generation mode/expression, and four-column foreign keys.
- Added both wrong-type directions, valid typed-anchor 13→14 preservation, and both invalid historical-anchor upgrade failures. The existing legacy-local-data preservation test now covers 12→head, including 13→14; old local keys/text survive with null Artifact anchors.

Direct TDD evidence before changing production code:

```text
docker compose -p recruitmatch-cp3-task1-sep11 run --rm -e AI_ENABLED=false \
  -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/artifacts:/app/tests/artifacts:ro \
  test-integration pytest tests/artifacts/test_artifact_repository.py::test_source_anchor_rejects_other_artifact_owner_type -q
RED: resume and knowledge_document both FAILED: DID NOT RAISE IntegrityError
2 failed in 0.49s
```

After the fix, rebuilt worktree images ran the focused suite. The first catalog run showed that PostgreSQL normalizes generated constants to `::character varying`, not the test's initial `::text` expectation; actual ORM/migration equality already passed. The expected catalog representation was corrected, with no production change:

```text
docker compose -p recruitmatch-cp3-task1-sep11 build bootstrap test-integration
docker compose -p recruitmatch-cp3-task1-sep11 run --rm --no-deps -e AI_ENABLED=false \
  -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/artifacts:/app/tests/artifacts:ro \
  test-integration pytest tests/artifacts -q
GREEN: 64 passed in 0.98s
```

Final worktree images were rebuilt, then the combined suites ran once in this exact order:

```text
docker compose -p recruitmatch-cp3-task1-sep11 run --rm -e AI_ENABLED=false \
  test-integration pytest tests/artifacts tests/integration tests/retrieval -q
1 failed, 162 passed in 7.82s
FAILED tests/retrieval/test_pgvector_search.py::test_exact_threshold_boundary_and_hnsw_explain_are_reproducible
tests/retrieval/test_pgvector_search.py:331:
assert [hit.id for hit in hits] == ["chunk-a", "chunk-b"]
actual: []
```

All Artifact, owner-type, migration, and catalog tests passed in that combined run. Before the failure, the same test's exact and HNSW candidate-count assertions (5), HNSW strategy, candidate budget (3), and expected HNSW index plan assertions passed.

The systematic-debugging investigation preserved the first failure and performed exactly one unchanged, targeted rerun—no retry loop, retrieval modification, VACUUM, REINDEX, data reset, or assertion weakening:

```text
docker compose -p recruitmatch-cp3-task1-sep11 run --rm --no-deps -e AI_ENABLED=false \
  test-integration pytest tests/retrieval/test_pgvector_search.py::test_exact_threshold_boundary_and_hnsw_explain_are_reproducible -q
1 passed in 0.83s
```

Read-only diagnostics: PostgreSQL 16.12 on aarch64; pgvector 0.8.1; default transaction isolation READ COMMITTED. Retrieval sets REPEATABLE READ and strict-order iterative HNSW, using an approximate ANN seed with budget 3 in this test. The HNSW index remains `USING hnsw (embedding vector_cosine_ops) WHERE (is_active = true)`. Its fixture repeatedly DELETEs/reinserts source/chunk rows. After the rerun, statistics showed 24 dead recruiting_chunk tuples and autovacuum activity (`2026-09-11 07:51:47.247176+00`). Retrieval implementation and tests have no diff from the fix base. Index/fixture-state-dependent ANN behavior is a plausible hypothesis, not a proven root cause; the combined suite is not claimed green. This unresolved concern is handed to the controller for separate diagnosis rather than broadening the Source-anchor fix.

The separate final SQLite suite passed:

```text
DATABASE_URL=sqlite:////tmp/recruitmatch-cp3-task1-sqlite.db TASK_MODE=inline AI_ENABLED=false \
  .venv/bin/pytest tests --ignore=tests/integration --ignore=tests/artifacts \
  --ignore=tests/retrieval/test_citation_lifecycle.py --ignore=tests/retrieval/test_generation_switch.py \
  --ignore=tests/retrieval/test_pgvector_search.py -o addopts= -q
177 passed in 7.25s
```

Final static checks passed: Ruff on app/tests/scripts plus migration 14; format check (`169 files already formatted`); mypy (`40 source files`); offline lock check (`105 packages`); `git diff --check`. The existing host parent-pyproject warning is unchanged and remains the controller's previously tracked minor item.

Production-image import was run separately after the failed combined command and passed: `docker compose -p recruitmatch-cp3-task1-sep11 run --rm --no-deps -e AI_ENABLED=false bootstrap python -c 'import app.main; print("production import passed")'`.

No other correctness finding was implemented, and no storage cutover, controller-ledger edit, subagent dispatch, push, or merge occurred. All Docker work stayed within `recruitmatch-cp3-task1-sep11`.

Fix-round cleanup completed with `docker compose -p recruitmatch-cp3-task1-sep11 down -v`; only its bootstrap/PostgreSQL/Redis containers, network, and two disposable volumes were removed.
