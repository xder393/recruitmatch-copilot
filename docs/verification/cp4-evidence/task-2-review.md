# CP4 Task 2 independent review

Reviewer /root/cp4_task2_review_sep12, gpt-6-astra high. Range 5ecf455240684d5eb544cabe05618a37adaa567c..539fb8478c6d6e334e914babbeea67aee2b11251. Verdict: spec issues found; task quality Needs fixes.

## Required finding

Important R2-I1: Incorrect repair success reporting, app/services/source_index_backfill.py:64–70. COMPLETED includes terminal failure; retained ready/no-error index alone is insufficient. An inconsistent parsed pair becomes FAILED/resume_parsed_state_invalid but the old healthy generation makes rebuild increment resumes_indexed. Require reloaded SUCCEEDED and preferably intended generation advancement. Endpoint regression must assert failed=1/resumes_indexed=0, retained previous retrieval and explicit parsed-state error. No Critical or additional Minor findings.

## Strengths verified

- Transaction-bound generation publication under lease guard, no separate commits (generations.py:549, leases.py:216).
- Independent renewal UoWs, loss on ambiguity, bounded shutdown (renewal.py:28,36).
- Guarded abandoned staging refuses active/referenced/excess data and retains history (generations.py:259).
- Real PG processor takeover, profile/trace fencing, rollback and expired reclamation tests (test_processors.py:61,231,272,303).
- Durable repair preserves parsed pair and skips parser/object reads; inconsistent pairs explicitly fail (resume_processing.py:65; test_resume_processing.py:165).
- Existing generation tests acquire real leases, no production no-token test bypass (test_generation_switch.py:43,223).

## Named integration checks

- Resume schema, heuristic parser and evidence resolver agree with complete parsed-pair validation (app/resumes/schemas.py:27, parser.py:59, app/ai/evidence.py:22).
- Resume repository locked refresh/reload/privacy clearing behavior checked (app/repositories/resumes.py:29,68,72).
- Configured S3/gateway finite timeouts and bounded retries checked (app/artifacts/s3.py:110; app/ai/gateway.py:63,69).
- Full ADR read; truncated report sections restored; cut generation/lease context expanded only where needed. No tests rerun, subagents dispatched or mutations.

## Cannot verify across task boundary

- Final Celery timing/visibility, durable retry/recovery and workflow-health/readiness belong to Tasks 3/4, not established here.
- Normal matching's inability to introduce citations to never-active staging is documented and consistent with retrieval changes, but unchanged matching publication's active-citation validation needs controller integration check (ADR 0005:140).

Assessment: ownership/publication design coherent with meaningful PG coverage; the backfill reporting adapter must preserve the same distinction between task outcome and retained retrieval availability.
