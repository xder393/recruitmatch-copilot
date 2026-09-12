# CP4 Task 2 implementation report

Worktree: `/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2`

Branch: `codex/recruitmatch-v2`

Reviewed predecessor: `5ecf455240684d5eb544cabe05618a37adaa567c`

Scope: Task 2 only. No migration, merge, push, provider/model downloads, `.env`
reads, default-project operations, user-data changes, or resource cleanup.
All Docker operations used `docker compose --env-file .env.example -p
recruitmatch-cp4-sep12` from this worktree. Root retains the synthetic resources
and owns independent review, ledger, and cleanup. This report is the only new
force-tracked ignored scratch file.

## Implementation and API contracts

- Resume and Knowledge processors claim through UoW.leases and return
  ProcessDisposition. COMPLETED includes terminal failures; live duplicates,
  terminal rows, DEFERRED and LEASE_LOST are normal returns. Due FAILED never
  implicitly retries. Celery only retries explicit RETRY_SHORT, which Task 2
  does not produce. No Redis business state/result backend was introduced.
- LeaseRenewer runs at duration/3 with a fresh independently owned UoW per
  heartbeat. False or exception sets loss; ensure_owned stops publication.
  Shutdown joins for at most one second. An already in-flight daemon heartbeat
  can finish its own UoW after bounded shutdown, but never publishes outputs.
  Final PostgreSQL ownership/expiry checks remain authoritative.
- Existing `fencing_token` is retained, now carrying ClaimedLease (including
  exact Artifact, tenant/type/source/owner/epoch), not a bare epoch integer.
  Async stage/activate/fail require it even when omitted by the caller. SourceRef
  additionally guards immutable version/lifecycle; inactive Knowledge is rejected.
  Synchronous JobVersion index/fail remain token-free.
- Added leases.owned for nonterminal guarded generation writes. It enforces
  clean-session entry, Source→Artifact locks, savepoint rollback, and post-flush
  expiry verification. finalize_owned retains its Task 1 contract.
- UoW.generations exposes GenerationPublicationPort.activate/fail. The concrete
  TransactionGenerationWriter uses the existing guarded UoW and never commits or
  opens a second transaction. An internal session marker verifies composition;
  no domain port or processor sees a Session or ORM lease object.
- SourceIndexer.stage embeds then persists inactive staging. Processors activate
  through UoW.generations inside finalize_owned. Resume profile/text/model trace,
  index outcome and SUCCEEDED commit together after successful guard exit.
  Expected generation errors can fall back to deterministic parse publication
  with a bounded search error. Trace/database failures are not relabeled as
  embedding failures; they propagate after rollback.
- Knowledge indexing failure publishes the index error inside owned, then calls
  leases.fail in the same outer transaction while retaining locks. FAILED is its
  processing outcome even if a prior generation is still searchable. Resume
  retrieval likewise no longer requires SUCCEEDED during a subsequent repair;
  exact active pointer, ready search status, version, tenant and lifecycle still
  govern retrieval. Initial failure with no active generation remains invisible.

## Abandoned N+1 staging decision

The controller approved the concrete strategy before implementation. It keeps
contiguous N+1 and strict exact replay, with no ownership column or migration.

An explicit current-lease reconcile_staging runs before each new claimant stages.
Under exact Source/Artifact ownership, only never-published inactive active+1 may
be reclaimed. Read is bounded to 2049 rows and reclamation to 2048. Any active
future row, inconsistent future generation, or reference refuses cleanup.
Historical <=active generations are never removed. The deletion includes exact
tenant/type/source/version/generation/inactive predicates and locked IDs; a lease
expiry at guard exit rolls it back before outer commit.

Reference proof is a PostgreSQL EXISTS join through tenant-qualified MatchRun,
with JSONPath value matching over all nine persisted JSON result payloads:
citations, evidence, grounded_explanation, interview_questions, dimension_scores,
matched_items, missing_items, uncertain_items, risk_flags. No unbounded Python
tenant scan and no serializer-escaping dependency. Ordinary matching can obtain
new legal citations only from active generations; held Source locks prevent
future staging from becoming active during this check. No Tenant lock is added
after Source, preserving existing lock order. The ADR records the policy and
staging_reconciliation_limit/inconsistent/referenced refusal codes; processors
map those validation errors to validation_failed. Lease loss is not persisted
by an old worker. Task 4 must consume this same cleanup contract.

## All asynchronous entry points and durable repair

- Resume upload, Knowledge upload, inline API dispatch and Celery dispatch enter
  the new processors using existing two-ID payloads.
- Knowledge operator reindex verifies exact AVAILABLE Artifact, queues explicitly,
  clears owner/expiry/retry/errors, records queued_at and resets attempts for the
  new operator budget. It never resets epoch; old tokens become invalid before
  the next claim increments epoch.
- Artifact reconciliation requeue clears ownership/retry, records queued_at,
  preserves epoch/recovery attempts and exact Artifact; no object I/O moved to Beat.
- Resume source-backfill no longer calls the synchronous SourceIndexer.index
  path. It locks/requeues/commits, then invokes the ordinary Resume processor.
  PostgreSQL's complete committed text/profile pair selects index-only repair
  for every delivery, including future Beat recovery. No transient task argument
  or new persisted operation field is needed. Profile/text/traces are preserved.
- Parsed-pair validation requires nonempty text, all serialized ResumeProfile
  fields, schema 1.0, Pydantic-valid values and evidence resolving into the text.
  Initial None/{} remains unparsed. Inconsistent legacy pairs become FAILED with
  resume_parsed_state_invalid while preserving data for deliberate repair.
  Successful atomic publication is the only parsed-pair writer; immutable
  Source/Artifact content and privacy clearing are documented in ADR 0005.
- Job create/update and JobVersion backfill retain independent synchronous indexing.
- API create_app/init_recruiting_state accept an explicit UoW factory for test
  composition. Production API and Worker use SqlAlchemyUnitOfWorkFactory with
  transaction-bound generation publication. SQLite tests use explicit lease and
  publication fakes; PostgreSQL ownership SQL never runs against SQLite.

## TDD and diagnostic evidence

All diagnostics used the named synthetic Compose project, AI_ENABLED=false and
OPENAI_API_KEY empty in the test services. Only tests that explicitly inject fake
structured models exercise model paths; no real provider requests occurred.

The common current-tree diagnostic mount arguments were:

```text
-v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app:/app/app:ro
-v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro
```

Representative exact RED/GREEN commands (insert those two arguments after
`--no-deps`; initial missing-module RED used the tests mount only):

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-integration pytest tests/integration/processing/test_processors.py -q
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit pytest tests/resumes/test_lease_renewal.py -q
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-integration pytest tests/integration/processing/test_processors.py -k reconcil -q
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit pytest tests/resumes/test_resume_processing.py -k 'recovery_delivery or inconsistent_parsed' -q --tb=short
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit pytest tests/knowledge/test_knowledge_api.py::test_source_backfill_repairs_existing_resume_and_job_indexes -q --tb=short
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-integration pytest tests/integration/processing/test_processors.py -k trace_storage_failure -q --tb=short
```

- Initial processor RED: 6 failed (exit 1). Both old processors persisted
  embedding_failed after takeover, duplicate outcome was still bool, and omitted
  async fencing was accepted. GREEN: first focused sweep 16 passed (exit 0).
- Renewal RED: 2 failures for absent app.processing.renewal (exit 1). GREEN
  covers false renewal, independent thread/UoWs, bounded blocked shutdown, and
  successful heartbeat commits. The final suite includes three renewal tests.
- Reconciliation RED: 8 failures because predecessor rejected non-null fencing
  before reaching reconciliation (exit 1). GREEN covers takeover/replacement,
  exact replay rejection, stale stage/activate/fail/cleanup, active/history
  preservation, reference refusal (including interview_questions), bounded
  refusal and deletion rollback on post-delete lease expiry.
- Added processor-level PostgreSQL checks for stale parser/profile/trace,
  activation-time expiry rolling back all visible results, and guarded
  generation operations without a token. Earlier fixture-only failures (required
  media_type and grounded fake model evidence) were corrected before acceptance.
- Reindex regression RED used predecessor knowledge_documents.py copied by
  apply_patch to ignored task-2-red-knowledge.py and mounted over that one module:
  `-v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/.superpowers/sdd/2026-08-24-recruitmatch-v2-cp4-reliability/task-2-red-knowledge.py:/app/app/services/knowledge_documents.py:ro`.
  Command target:
  `test-unit pytest tests/knowledge/test_knowledge_api.py::test_reindex_invalidates_old_execution_without_resetting_epoch -q --tb=short`.
  RED retained attempts=5 instead of reset=0 (exit 1); current-tree GREEN 1 passed
  (exit 0), checking epoch, owner, expiry, queue timestamp, retry/error and Artifact.
- Existing retrieval-generation refresh test provided RED when the prior Resume
  generation disappeared during RUNNING; changing retrieval authority to the
  active pointer/ready status restored GREEN. Full old privacy/citation tests pass.
- Backfill-preservation RED overwrote a reviewed profile (exit 1); GREEN retained
  it (1 passed, exit 0). Recovery RED reached FAILED by rereading storage and
  inconsistent-pair RED incorrectly succeeded (2 failed, exit 1). GREEN: 16
  Resume/API tests passed after durable pair inference (exit 0).
- Self-review trace-storage RED did not raise the injected RuntimeError because
  it retried as soft index failure (1 failed, exit 1). Narrowing fallback to
  GenerationWriterError produced GREEN (1 passed, 23 deselected, exit 0), with no
  published profile/index/trace after the infrastructure failure.
- Interim whole diagnostic lanes: 231 unit tests and 384 integration tests
  passed. First rebuilt unmounted lanes: 235 unit / 393 integration passed.
  A later mypy list-invariance annotation issue was fixed, and diagnostic mypy
  passed 43 files. Final acceptance below supersedes interim counts.

## Final rebuilt, unmounted acceptance

All final gates passed on 2026-09-12. All commands ran from the worktree with no
`-v` arguments; test services have no source volumes. Runtime and test images were
rebuilt from the final application/test tree. Only this report changed afterward.

| Gate | Final result | Exit |
| --- | --- | --- |
| Rebuild bootstrap/test-unit | Both images built | 0 |
| Unit lane | 235 passed in 18.77s | 0 |
| Integration/retrieval/artifact lane | 394 passed in 22.31s | 0 |
| Ruff check app/tests/scripts | All checks passed | 0 |
| Ruff format including new lease migration | 192 files already formatted | 0 |
| Mypy normal CI scope plus app/processing | 43 files, no issues | 0 |
| Mypy Task 1 lease migration explicitly | 1 file, no issues | 0 |
| uv lock --check --offline | 110 packages resolved in 3ms | 0 |
| git diff --check | Clean | 0 |

Final image configuration digests from the rebuild:
runtime `sha256:e910239153472d90b10f5c7295ae7ab0703b6c45ac2bc534c183a169fb3b305d`;
test `sha256:7bc94083d02a47544956c4d7ef62b3299980e2a51a47c69d6ec33d81a89d6c95`.
No test warnings or unexpected noise remained in the final output.

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 build bootstrap test-unit
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-integration
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit ruff check app tests scripts
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit ruff format --check app tests scripts alembic/versions/20260912_18_processing_leases.py
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit mypy app/models app/retrieval app/services app/ai app/tasks app/processing
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit mypy alembic/versions/20260912_18_processing_leases.py
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps test-unit uv lock --check --offline
git diff --check
```

## Changed files

Production: app/processing/{renewal,leases,outcomes}.py;
app/repositories/{ports,unit_of_work,sqlalchemy_unit_of_work}.py;
app/retrieval/{generations,indexing,pgvector_index}.py;
app/services/{resume_processing,knowledge_processing,knowledge_documents,
artifact_reconciliation,source_index_backfill}.py;
app/main.py; app/tasks/celery_app.py; app/api/v1/knowledge.py;
docs/adr/0005-processing-lease-contract.md.

Tests: tests/fakes/{processing,retrieval}.py; tests/support/application.py;
tests/resumes/{test_resume_processing,test_lease_renewal}.py;
tests/knowledge/{test_knowledge_api,test_knowledge_ingestion}.py;
tests/retrieval/{test_generation_switch,test_indexing_pipeline}.py;
tests/artifacts/test_privacy_races.py;
tests/integration/processing/test_processors.py; this report.

## Self-review and downstream notes

Self-review confirmed guard-first writes/commit-after-exit, no independent
generation commits under processor locks, no old-worker failure writes or
cleanup, no Session sharing, and no async no-token bypass. It found and corrected
retrieval availability, backfill profile preservation/recoverability, complete
parsed-pair validation, and trace-failure classification issues.

Task 3 still owns final Celery timing/retry configuration and Task 4 durable
requeue/recovery policy. They must branch on ProcessDisposition, preserve epochs
and exact AVAILABLE Artifact identity, and explicitly requeue FAILED rows.
Resume recovery needs no index-only broker flag. Artifact maintenance remains a
worker operation and Beat must not do object I/O. Renewal may complete an
in-flight independent transaction after bounded shutdown; the final guard and
current token remain the authority. No new schema or unreviewed generation
semantics were introduced. Root owns final fake evaluations/golden diff and
independent review; no subagents were dispatched by this implementer.
