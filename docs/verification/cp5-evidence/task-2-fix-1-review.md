1. **Startup banners bypass the JSON/privacy boundary.** — **ADDRESSED.** [docker-compose.yml:149](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/docker-compose.yml:149) and [docker-compose.yml:167](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/docker-compose.yml:167) enable Celery’s global `--quiet` behavior for Worker and Beat. [test_structured_logging.py:10](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/observability/test_structured_logging.py:10) covers both actual CLI startup paths, requires every stdout line to be JSON, rejects the sentinel on both streams, and requires empty stderr and exit 0. The reported 2-failure RED and 4-pass GREEN evidence matches this diff.

2. **Final explanation-only citation rejection is invisible to metrics.** — **ADDRESSED.** [matching.py:311](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/services/matching.py:311) adds the final `citation.validate` span; [matching.py:333](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/services/matching.py:333) emits one rejection only when this validation newly removes a claim; and [matching.py:335](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/app/services/matching.py:335) emits fallback only when no guidance survives. [test_domain_metrics.py:242](/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2/tests/observability/test_domain_metrics.py:242) covers partial loss, total loss, and prior rejection without recounting. The reported 3-failure RED and 26-pass GREEN evidence matches the amended behavior.

### New Breakage in the Fix Diff

None.

### Out-of-Scope Observations

None.

### Verdict

**Fix round: All findings addressed, no new Critical/Important breakage.**
