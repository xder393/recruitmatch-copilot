# CP2 final review fix report — 2026-09-11

## Scope and root cause

The final guidance assembly in `MatchingService._add_grounded_guidance` unioned the already-validated semantic citation IDs with explanation IDs, but built persisted payloads only from the second guidance search's `hits`. Because that independent top-k can omit either semantic side, the service could persist a non-zero semantic score and `grounded` status without both Resume and current JobVersion payloads. The guidance exception path also bypassed payload resolution entirely.

This change is limited to final matching guidance/citation assembly and its service/API test boundaries. It does not change prompt construction, prompt citation whitelists, retrieval source authorization, migrations, CP3 production files, or `progress.md`.

## TDD evidence

### RED: persistence, invalidation, and failure branches

Command:

```text
./.venv/bin/python -m pytest tests/matching/test_matching_service.py -q
```

Result before production changes:

```text
...FFFF                                                                  [100%]
4 failed, 3 passed
```

The failures demonstrated that:

- guidance top-k persisted only `resume-cite` while retaining `semantic_score=80` and `grounded`;
- a missing final JobVersion citation retained the semantic contribution;
- a final resolver outage retained the semantic contribution;
- a guidance-search outage left unresolved string citation IDs instead of payloads.

### RED: final score ordering

With the score recomputation present but the final deterministic sort temporarily removed, the ranking regression was run:

```text
./.venv/bin/python -m pytest tests/matching/test_matching_service.py::test_final_semantic_invalidation_reranks_results_deterministically -q
```

Result:

```text
F                                                                        [100%]
1 failed
```

The stale order was `[('job-b', 0.72), ('job-a', 0.84)]` rather than the required `[('job-a', 0.84), ('job-b', 0.72)]`.

### GREEN

Commands and results:

```text
./.venv/bin/python -m pytest tests/matching/test_matching_service.py -q
.........                                                                [100%]
9 passed

./.venv/bin/python -m pytest tests/matching tests/ai -q
....................................................................     [100%]
68 passed

./.venv/bin/python -m pytest tests/evaluation -q
.........                                                                [100%]
9 passed
```

## PostgreSQL composition/privacy verification

One set of affected images was built and all database resources were isolated under the explicit Compose project `recruitmatch-cp2-finalfix-sep11`.

```text
docker compose -p recruitmatch-cp2-finalfix-sep11 build bootstrap test-integration
Image recruitmatch-app-test Built
Image recruitmatch-app Built

docker compose -p recruitmatch-cp2-finalfix-sep11 up -d postgres redis
isolated PostgreSQL and Redis started

docker compose -p recruitmatch-cp2-finalfix-sep11 run --rm bootstrap
upgraded through 20260824_12; seeded_job_templates=30

docker compose -p recruitmatch-cp2-finalfix-sep11 run --rm test-integration pytest tests/integration/test_retrieval_composition.py tests/integration/test_matching_privacy_concurrency.py -q
.....                                                                    [100%]
5 passed in 2.46s
```

After verification, only this isolated project was removed with `docker compose -p recruitmatch-cp2-finalfix-sep11 down -v`; no other Compose project was stopped or deleted.

## Static and golden verification

```text
docker compose -p recruitmatch-cp2-finalfix-sep11 run --rm --no-deps test-unit uv lock --check
Resolved 105 packages in 1ms

./.venv/bin/ruff check app tests scripts
All checks passed!

./.venv/bin/ruff format --check app tests scripts
162 files already formatted

./.venv/bin/mypy app/retrieval app/services app/ai app/tasks
Success: no issues found in 27 source files

./.venv/bin/python scripts/evaluate_ai_pipeline.py --dataset evaluation/recruitmatch-ai-v1.json --mode hybrid-v1 --fake-model --output /private/tmp/recruitmatch-cp2-finalfix-ai-results.json
150 cases; top1=0.9733, top3=1.0, citation validity=1.0

diff -u evaluation/recruitmatch-ai-v1-results.json /private/tmp/recruitmatch-cp2-finalfix-ai-results.json
no output (exact match)

git diff --check
passed
```

The first host-side `uv lock --check` attempt encountered sandbox access to the user's uv cache and the host uv workspace emitted parent-workspace warnings. The authoritative check above ran cleanly in the already-built isolated test image.

## Changes

- `app/services/matching.py`
  - re-resolves exactly the union of previously validated semantic IDs and explanation IDs under the per-candidate authorized scope;
  - ignores resolver output not present in that union or outside the exact authorized source tuple, so resolution cannot expand provenance;
  - persists both Resume and current JobVersion semantic payloads even when the guidance top-k omits one side;
  - removes the semantic score and contribution with stable `invalid_semantic_citations` fallback when any validated semantic ID is no longer active/current or either required side is absent;
  - removes final-resolution-invalidated explanation claims and questions, and never persists a claim referencing an absent payload;
  - degrades without unresolved citations when resolution fails, while a guidance-search failure can still retain independently re-resolved semantic evidence;
  - recomputes affected totals with the fixed `combine_scores` formula and sorts deterministically by `(-total_score, job_id)`.
- `tests/matching/test_matching_service.py`
  - adds persisted top-k exclusion, active-side invalidation, absent-claim scrubbing, resolver failure, guidance-search failure, and deterministic reranking regressions.
- `tests/matching/test_matching_api.py`
  - updates the API test fake to mirror the typed resolver and complete grounded-explanation citation contract.

## Self-review

- Provenance remains closed: only IDs already emitted by the validated semantic result or validated explanation are sent to final resolution.
- Authorization remains candidate-scoped and version-exact: returned hits must match an authorized `(source_type, source_id, source_version)` tuple before payload construction.
- Semantic evidence remains two-sided and current: all previously validated semantic IDs must still resolve, with at least one selected Resume and current JobVersion hit.
- Explanation fields and interview questions are filtered together; a filtered summary cannot overwrite the deterministic rule summary.
- Citation payload ordering and recommendation ordering are deterministic.
- Mutation checks are represented by the observed REDs for dropped union resolution and removed final sorting.

## Concerns

No known correctness blocker remains in the changed branch. The service continues to final-validate and rerank the recommendation set returned by the existing hybrid engine; expanding the pre-existing top-k selection boundary was intentionally out of scope for this citation-persistence fix.
