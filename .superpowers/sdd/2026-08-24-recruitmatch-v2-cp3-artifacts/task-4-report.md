# Task 4 implementation report (in progress)

Base: `24384d0`, branch `codex/recruitmatch-v2`, isolated project
`recruitmatch-cp3-task4-sep11`. No real `.env`, provider keys, real LLM calls,
default-project resets, main changes, merge or push. Controller owns review.

## Approved decisions and commits

- `2d1c780`: checked-in ADR for conservative tenant-wide Knowledge-result
  invalidation, Tenant-first privacy guard and durable bounded maintenance
  cursor design, approved before affected implementation.
- `2c25635`: explicit Knowledge DELETE acknowledgement before mutation;
  broad scrub occurs only once, retries preserve subsequent fresh results.
- Tenant privacy guard uses PostgreSQL `FOR NO KEY UPDATE` so worker FK
  `KEY SHARE` checks remain compatible while a worker holds Source locks.
- No exact input lineage is retained. Knowledge deletion conservatively
  invalidates all existing result content in its tenant plus linked feedback
  reasons. Citation IDs are references, not semantic entailment/provenance.
- PostgreSQL cursor positions are maintenance progress, not Source processing
  ownership, leases, or a scheduler. CP4 owns Beat wiring; no Beat object I/O.

## TDD and verification commands so far

All commands run from `/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2`.
Every Compose invocation uses `--env-file /dev/null -p recruitmatch-cp3-task4-sep11`.
Docker socket and git worktree-index operations require sandbox escalation;
approval succeeded. No automatic-approval rejection occurred.

1. Build predecessor runtime/test/MinIO images:
   `docker compose --env-file /dev/null -p recruitmatch-cp3-task4-sep11 build bootstrap test-integration minio`
   exit 0.
2. Initial RED:
   `docker compose --env-file /dev/null -p recruitmatch-cp3-task4-sep11 run --build --rm test-integration pytest tests/artifacts/test_privacy_delete.py -q`
   3 failed: Resume original_filename remained `deleted`, not NULL, after failed
   object cleanup. These failures also caught deterministic checksum retention.
3. During iteration, current source directories are mounted read-only on the
   disposable test container. Canonical focused command:
   `docker compose --env-file /dev/null -p recruitmatch-cp3-task4-sep11 run --no-deps --rm -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app:/app/app:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-integration pytest tests/artifacts/test_privacy_delete.py -q`
   RED 4 failed: the three prior failures plus absent Knowledge privacy-delete.
   GREEN 4 passed after lifecycle/DB-first deletion implementation.
   Additional RED 3 failed/4 passed: Resume Feedback.reason retained sentinel;
   Knowledge MatchResult retained sentinel; first deletion did not scrub history.
   Additional RED 5 failed/4 passed: both Knowledge matching/deletion commit
   orderings failed to block on one common guard.
   GREEN 9 passed with one new API RED: DELETE returned 405 rather than required
   acknowledgement-required 409. API implemented next.
4. Reconciliation RED using the same read-only source command and
   `pytest tests/artifacts/test_reconciliation.py -q`: 6 failed, bounded
   reconciliation module absent. Cases: valid repair/dispatch, grace, missing/
   wrong-size/wrong-checksum cleanup, late-write tombstone revisit.
5. Migration updates applied only to the isolated Task 4 database with
   `docker compose --env-file /dev/null -p recruitmatch-cp3-task4-sep11 run --no-deps --rm -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app:/app/app:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/alembic:/app/alembic:ro test-integration alembic upgrade head`
   revision 15→16 and then 16→17 each exit 0. Historical preservation tests and
   final fresh-image gate remain outstanding; this is not a completion claim.

## Implementation in progress

Separate `lifecycle_status` on Resume/Knowledge, nullable original filenames and
checksums, no new Resume processing statuses. Revision 16 preserves prior deletion
and scrubs legacy deleted-row derivatives even on databases already at 15; new
Knowledge deletion is independent of deactivation. Typed repository operations
own SQL; services do not import SQLAlchemy. Exact Source→Artifact locks authorize
cleanup. Store I/O occurs after the redaction transaction commits.

Revision 17 adds a unique, constrained scope/lane cursor table. Atomic initialization
and global-then-lexical-lane locks reserve pages for one rotating tenant. Independent
pending/cleanup/deleted pages advance durably before network calls. Permanent
tombstones survive missing observations and wrap. Five-minute UTC creation grace
protects ordinary PENDING puts; inspected metadata must match DB checksum/size,
and exact source/Artifact/state is rechecked after Head under publication locks.

## Pending evidence / limitations

Need final privacy FK compatibility, processing/generation and API sentinel race
coverage; durable across-tenant/page/crash fairness and transient-failure coverage;
bounded report-only orphan adapter/entrypoint; historical migration preservation;
full lanes, lint, current-source image gate, self-review and cleanup evidence.
Permanent tombstone and O(tenant × lane) metadata/I/O costs are deliberate. A
reserved batch lost to process failure can wait until a full wrap. Coarse Knowledge
invalidation removes unrelated same-tenant historical result content after explicit
operator acknowledgement, and slow matching calls serialize same-tenant privacy work.

## Final addendum — supersedes all earlier in-progress/pending wording

Implementation and verification are complete for controller review. Earlier RED
filename failure did not reach its checksum assertion; code inspection identified
the deterministic checksum issue, and GREEN tests assert actual NULL. One formatter
approval timeout occurred before execution; its single retry succeeded (1 formatted,
1 unchanged). No tool/approval remains blocked. TDD and verification skills used.

Final commands below ran from the worktree. `EX` means the exact prefix
`docker compose --env-file .env.example -p recruitmatch-cp3-task4-example-sep11`.
This separate project was empty before creation; original project credentials were
not changed. Final gates used built unmounted images, disabled/Fake AI, no real LLM.

| Exact command after EX | Observed result |
| --- | --- |
| `build bootstrap test-integration minio` | exit0 |
| `up -d postgres minio` | fresh owned network/volumes |
| `run --rm minio-init` | exit0, minio_init_ready |
| `run --rm bootstrap` | exit0, all revisions through17, seeded_job_templates=30 |
| `run --rm test-integration pytest tests/artifacts tests/resumes tests/knowledge -q --tb=short` | **200 passed in10.38s** |
| `run --rm test-unit` | **225 passed in12.11s** |
| `run --no-deps --rm test-unit ruff check app tests scripts` | All checks passed |
| `run --no-deps --rm test-unit ruff format --check app tests scripts` |182 files already formatted |
| `run --no-deps --rm test-unit mypy app/models app/retrieval app/services app/ai app/tasks` | Success,39 source files |
| `run --no-deps --rm test-integration` (initial full lane) |251passed/2failed, stale head15 assertions |
| `build test-integration` (after test-only corrections) | exit0 |
| `run --no-deps --rm test-integration` (final) | **253 passed in14.62s** |
| `exec -T postgres psql -U recruitmatch -d recruitmatch -Atc 'SELECT version_num FROM alembic_version'` | **20260911_17** |
| `run --no-deps --rm api python -m scripts.reconcile_artifacts --batch-size 1 --orphan-pages 1` | exit0; partial reconciliation counters0; partial orphan observed1/unassociated1/unrecognized0/errornull; no orphan Delete |

Final-image ruff check and format commands above were repeated after test-only
changes and passed. Production source did not change after200/225; final253 includes
two corrected head assertions and strengthened API sentinel/redacted-result checks.
Local `git diff --check` exit0; `rg 'LocalArtifactStore|KnowledgeArtifactStore|storage_key|artifact_key|ResumeStatus.DELETED' app`
exit1/no matches. New migrations linted successfully; pre-existing revision15 E501
in an earlier overbroad all-Alembic check was not changed. No remote CI claim.

Final runtime image: `sha256:114263e9d31dfbfef498dbe40ddf43b3dfaf81000d1dbd6c4f5e96b08f725522`.
Final test image: `sha256:ed0dd1abd3c0ddceecab553b83694a687528da165ab76e5e27224b46c90ac2d5`.
Prior200/225 image: `sha256:4ea33b40ca59788453484fc4e0847a47f15b967843986bb8f9e4a84fb3b4c20a`.
Local shasum and unmounted-runtime sha256sum matched reconciliation/resume/Knowledge
services, Artifact repository and migrations16/17. Controller independently verified
final image/worktree hashes, UID10001 and offline lock110 packages, no warning.

Migration RED3 (guard absent, skipped FAILED, terminal error erased) became GREEN3.
Real historical-PG tests preserve live data, erase deleted Resume derivatives, prove
PENDING→FAILED→CLEANUP_PENDING via trigger, preserve DELETED error_code, and prove
legacy Knowledge guard leaves head15/schema/content untouched. Revision16 now fails
before DDL with `knowledge_privacy_migration_requires_operator_resolution` when old
Knowledge deletion would newly erase tenant history without consent; ADR documents
resolution. Earlier blanket legacy-scrub wording is superseded by this guard.

Knowledge DELETE acknowledgement409 is side-effect-free; RBAC and tenant isolation
remain enforced. Broad result/Feedback.reason scrub occurs only on first transition;
retry after S3 failure preserves fresh matching/feedback. Real-PG barriers cover both
matching/delete and feedback/delete orders, in-flight processing/generation, and
Source-held Tenant-FK insert versus NO KEY UPDATE deletion guard. Tests assert SQL,
persisted/API sentinel absence, citation denial and real MinIO permission failure.
Reconciliation tests cover grace/metadata/revalidation, dispatch durability, retry
PENDING committed before I/O (RED1/15pass then fixed), and fresh service/UoW durable
tenant/page progress, crash-after-reservation, wrap and exact late-object cleanup.
Unrelated orphans survive; terminal transient errors recover. Real MinIO paginated
orphan reporting is bounded and report-only. No new processing authority/scheduler.

Both owned projects retained for controller review/cleanup:
`recruitmatch-cp3-task4-sep11`, `recruitmatch-cp3-task4-example-sep11`.
Final ps shows postgres/minio/redis healthy and init/bootstrap exited0 in each.
Controller owns exact-label network/volume cleanup, including example hf-cache-v2
created by admin runtime invocation, and optional project images/cache. No default,
user or Task3 resources removed. Self-review checked brief, changed interfaces and
Source→Artifact ordering; no subagent review. Coarse consented tenant-history loss,
slow-model tenant serialization, permanent tombstone/O(tenant×lane) cost and crash
wrap delay remain accepted limitations. CP4 owns Lease/fencing/Beat; CP5/6 metrics
and capacity remain out of scope. No global cleanup or throughput guarantee.
