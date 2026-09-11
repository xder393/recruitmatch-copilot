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
