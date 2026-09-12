# CP4 Task 2 fix round 1 report

Base: `539fb8478c6d6e334e914babbeea67aee2b11251`.
Worktree: `/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2`.
Branch: `codex/recruitmatch-v2`.

## Finding and fix

Addressed Important R2-I1 only. The backfill adapter treated COMPLETED plus
retained ready retrieval as repair success, although COMPLETED also includes
terminal processing failure. This was independently verified from the processor
and endpoint code and reproduced through the HTTP endpoint before implementation.

The three-line production change captures `target_generation = active + 1`
while the existing Source lock is held, before the queue-cycle commit. A repair
counts as indexed only when the reloaded row is SUCCEEDED, its active pointer is
at least that target, and the existing COMPLETED/ready/no-search-error checks
hold. This prevents both a terminal failure with an old healthy index and a
successful processing finalization that did not publish an index from being
counted as indexed.

The comparison is `>= target`, deliberately not equality: the frozen contiguous,
monotonic generation contract means a later published generation also proves the
requested generation was reached. It avoids reporting failure solely because a
subsequent valid repair published again before reload. The target is an integer
captured under lock, never computed from an expired/refreshed ORM snapshot.

No processing, index retention, generation publication, queue, JobVersion,
schema or retrieval behavior changed. Failed repairs preserve the prior ready
generation and citations. Only the endpoint's result qualification changed.

## Regression and TDD evidence

Added the parameterized HTTP regression
`test_source_backfill_reports_failed_repair_and_retains_prior_retrieval` in
`tests/knowledge/test_knowledge_api.py`:

1. Upload and index a valid Resume, then make its persisted text/profile pair
   inconsistent. Ordinary backfill processing must record
   FAILED/resume_parsed_state_invalid, preserve old generation/readiness/text,
   retain the same retrieval citations, and return resumes_indexed=0/failed=1.
2. Use the real ResumeProcessingService without its optional indexer. It reaches
   COMPLETED/SUCCEEDED but publishes no new generation. The adapter must still
   report failed=1 and retain prior retrieval. This proves status alone is not
   sufficient and exercises the requested-generation predicate.

Existing successful Resume/JobVersion backfill coverage was included in the
focused run. Tests use the explicit SQLite lease/retrieval fakes established in
Task 2, exercising the real endpoint, backfill service and processor.

Exact focused command (same for RED and GREEN):

```sh
docker compose --env-file .env.example -p recruitmatch-cp4-sep12 run --rm --no-deps -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app:/app/app:ro -v /Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests:/app/tests:ro test-unit pytest tests/knowledge/test_knowledge_api.py -k 'source_backfill' -q --tb=short
```

RED: exit 1, 2 failed / 1 passed / 8 deselected in 1.76s. Both regressions reached
their expected persisted processing/index state and preserved prior retrieval;
the final endpoint assertion failed because it returned
`{"resumes_indexed": 1, "job_versions_indexed": 0, "failed": 0}` instead of
`{"resumes_indexed": 0, "job_versions_indexed": 0, "failed": 1}`.

GREEN: exit 0, 3 passed / 8 deselected in 1.59s after the minimal qualification fix.

## Final rebuilt unmounted verification

All final gates passed with exit 0. These commands have no source mounts and
use the rebuilt current-tree runtime/test images:

| Gate | Result |
| --- | --- |
| Runtime/test image rebuild | Passed |
| Full unit suite | 237 passed in 18.75s |
| Full integration suite (real PostgreSQL/Redis/MinIO) | 394 passed in 21.57s |
| Ruff check, full CI paths | All checks passed |
| Ruff format, full CI paths plus new migration | 192 files already formatted |
| Mypy, full CI paths plus app/processing | No issues in 43 source files |
| Mypy, new migration | No issues in 1 source file |
| Offline lock check | Resolved 110 packages in 2ms |
| Git whitespace check | Passed |

Rebuilt image config digests:

- Runtime: `sha256:202691f1c055a82d7f8ef93d144ffa95b9be5dca75a80b682ebdba4ca4361048`.
- Test: `sha256:d28cee597fbdd14ec02e644a33dd7ca5931b2dab4213078bb69d2f5df4709340`.

No source or test changes followed the image rebuild. Only this report was
completed after verification. Exact final commands:

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

## Scope and self-review

Changed only app/services/source_index_backfill.py,
tests/knowledge/test_knowledge_api.py and this report. The new report is the only
force-tracked ignored scratch file in this fix round. No other plan/report was
changed. Self-review confirmed the target is captured under the refreshed Source
lock and that failure reporting does not mutate or invalidate retained retrieval.
The existing successful backfill/JobVersion case remains covered.

No blockers or unresolved concerns. Same isolated synthetic Compose project,
AI off in test services, explicit fake model/embedding paths only. No `.env`
reads, model downloads, real provider calls, user/default data changes, extra
agents/reviewers, merge, push or resource cleanup. Root retains resources and
owns the scoped re-review.
