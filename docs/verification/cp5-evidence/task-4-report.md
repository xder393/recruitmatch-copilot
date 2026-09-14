# CP5 Task 4 implementation report

Status: fix round 1 DONE, verified and ready for scoped review; no staging/commit authorized yet. This is not checkpoint acceptance.
Base: `ccb662824dba93597e251f0d443fa2da4a49df7d`.
Worktree: `/Users/xder393/Desktop/agent/.worktrees/recruitmatch-v2`.
Branch: `codex/recruitmatch-cp5-observability`.

## Implementation and ownership

- Added the explicit `test-telemetry` Compose runner, bounded backend verifier,
  real Uvicorn/public application-factory and prefork-two synthetic composition,
  host-only Collector outage/restoration helper and separate Worker restart.
- The injected fake OpenAI client retains the real gateway `generate/_completion`
  path, physical request/token instrumentation and PostgreSQL publication. Both
  API and Worker use normalized deterministic 512-dimensional embeddings. AI
  addresses are closed loopback, the fake key is public/synthetic, HF and
  Transformers offline flags are mandatory. No host Python/venv or real provider
  was used, and no model was downloaded.
- HTTP creates unique synthetic tenants and jobs, activates/indexes the job,
  uploads eight unique resumes concurrently, and waits for persisted `succeeded`
  plus `search_index_status=ready`. Rules matching must return a positive score
  and nonempty results. No tenant truncate/delete/reset endpoint was added.
- Tempo acceptance checks the externally supplied W3C parent, actual server →
  producer → consumer → resume.process ancestry, expected business span names,
  model.generate below processing, and two actual worker UUID resources.
  Prometheus assertions use those queried runtime writers, never Task3's broad
  historical synthetic counters. Counts must cover each writer's traced work.
- Host outage verification checks exact fixed project/service labels, actual
  stopped Collector state, authenticated jobs/nonempty rules/readiness, continued
  fresh Beat and Worker heartbeats after a full heartbeat interval, and a newly
  uploaded source processed successfully during the outage. A shell trap restores
  Collector on failure; the successful path probes its actual health extension.
- The separate SDK gauge probe reads real PostgreSQL and Redis using a unique
  test heartbeat namespace and `staging` telemetry environment. It demonstrates
  positive → new random writer zero, failed Redis invalidation → unknown, source
  snapshot expiry → unknown while SDK exports continue. This is real SDK-owner
  replacement, not an actual Beat process restart. Actual prefork Worker restart
  evidence is separate and checks four distinct old/new counter/histogram writers.
- R14 alert acceptance is isolated in `recruitmatch-cp5-alerts-sep14`: real pinned
  Prometheus, the accepted production rules (plus the R16 approved fix), bounded
  synthetic metric exposition and ephemeral tmpfs. It establishes all ten rules
  inactive with positive healthy samples before stimulus, then observes actual
  Firing with production `for` windows unchanged. It proves rule evaluation; it
  does not prove physical Collector saturation, real backend failure generation,
  load capacity or production paging.
- No domain instrumentation changed. R16 authorized the production-config
  correction: independently rate each Collector failure family
  before alert OR. The matching numeric dashboard uses finite query-only family
  labels before OR and outer sum, preserving totals without label collisions or
  missing-family masking. Deferred legend/5xx display polish was not changed.
- R17 authorized a pinned Celery 5.6.3 control-output compatibility adapter,
  installed only through `celeryd_after_setup`. It rebinds only `safe_say`, reuses
  Celery's captured original low-level write and descriptor, and writes a bounded
  pre-encoded JSON diagnostic without locks, message serialization or a retry
  loop. Existing shutdown handlers/state/callbacks and task semantics are intact.
  No parser exception was added; all runtime log lines must still be JSON.
- `.env.example` is the approved public synthetic environment file. Only the
  owned main project and named child alert project were operated. Host port 8000
  and unrelated `odp` projects/data were left untouched. No Docker socket,
  volume wipe, Grafana recruiting database, real API key, push/PR/main merge,
  subagent or reviewer was used.

## RED evidence

Commands use worktree CWD. `D` means
`docker compose --env-file .env.example -p recruitmatch-cp5-sep14`.

1. `D run --rm test-telemetry pytest tests/observability/test_telemetry_e2e.py -q`
   exited 1: `no such service: test-telemetry`.
2. Before implementation, mounting only the new E2E test into the baseline test
   image and running its two tests gave `2 failed in 0.02s`: both imports failed
   because `scripts.verify_telemetry` did not exist. This was a focused missing
   feature RED, not a repeated baseline regression.
3. Added gauge and actual-Firing acceptance tests before their helpers. Explicit
   runner gave `2 failed in 0.01s`: missing `gauge_probe` and missing alert evidence.
4. Added stale-source host-evidence rejection before the source binding. Focused
   suite gave `1 failed, 3 passed in 0.50s`, specifically `DID NOT RAISE` for a
   different-source evidence document. After source binding: `4 passed in 0.36s`.
5. Real isolated Prometheus exposed the accepted Collector expression defect:
   HTTP 422, `vector cannot contain metrics with the same labelset`, and
   CollectorExportFailure rule `health=err`. The bounded ten-alert run timed out
   after the other nine reached Firing; the child services stopped in the trap.
   No fixture stream was suppressed. Controller ruling R16 approved the fix.
6. New simultaneous-positive/missing and all-zero failure-family fixtures with
   the old rule: `promtool test rules` exited 1 twice at 5s with that same duplicate
   labelset error. After the four-branch alert fix the command printed `SUCCESS`.
7. CI collection regression was added before the collection guard. It failed
   because `test_telemetry_e2e.py::` appeared in ordinary collection (`1 failed in
   1.99s`). After the narrow `conftest.py` guard, `5 passed in 3.52s`, including
   checking that `TELEMETRY_E2E=1` still collects the live tests.

Development-only source mounts were used for these focused RED/iteration tests.
They are not the final exact-source image evidence.

## Early GREEN and discovered limitations

- First real HTTP/broker/prefork/Tempo/Prometheus test: `1 passed in 14.84s`.
  Eight uploads exercised actual worker resources
  `c498b97a-8ac4-43a0-aa92-86bccf1bc48e` and
  `d439b678-32cc-4f1c-b309-581c918abfc8`, with API resource
  `96585149-5b26-44e8-aee1-e3dfed866d61`. Parentage and privacy passed.
- First SDK gauge probe passed with writers
  `1f7e0a12-a99e-40ae-9287-7032fe01a23b` and
  `be0e5c94-21e8-41ac-8997-daeb32f186af`: positive-to-replacement-zero,
  failed-dependency unknown, snapshot-expiry unknown all true.
- First new-image full regression before CI collection correction:
  `403 passed in 37.38s`; integration `520 passed in 26.93s`; Ruff check clean,
  `231 files already formatted`, mypy clean across 41 source files.
- Exact unchanged CI pytest flags, with the collection-guard files mounted for
  focused verification: `404 passed in 31.94s`. No CI workflow expansion was
  made. The final image run below must repeat this without replacement mounts.
- Live corrected numeric panel query returned `1.9997454869380262` for
  simultaneous positive families (other families zero), and
  `0.9998727434690131` for a single present family. Final alert helper checks the
  actual provisioned expression against expected approximately 2/s and 1/s.
- Test tmpfs's first YAML form interpreted its comma as a second array item and
  Docker rejected it; quoting the mount option fixed that test-only composition.
- Formatting and closure lint findings were corrected before full verification.

## Final source/image identity and verification

Final rebuilt test image:
`sha256:3edc42a4ce8bcd524c744e09149661ce5f9bc508b9ffa2f21a091ad283e957b9`.
Final rebuilt production runtime image:
`sha256:71677fede76c38e34c77b287e39eead74122f4f1ef3bf09e049ea09c05af596d`.
The actual synthetic API/Worker/Beat use the test image, which includes all
helpers plus the same final application code. No final Python source replacement
mount is used. Host versus test-image SHA256 matched for all 19 changed/new
image-resident files. Full application/config/helper source digest:
`68cb9fe89ab7ac67d8f4785a75e118b77bb0071d8b17730eea990753a3559f5f`.

Final rebuild command:
`HTTP_PROXY=http://127.0.0.1:12001 HTTPS_PROXY=http://127.0.0.1:12001 D build test-unit api`.
Dependency layers were cached; no model or provider call was made.

Final-source results, without source replacement mounts:

| Command | Result |
| --- | --- |
| `D run --rm --no-deps test-unit pytest tests --ignore=tests/integration --ignore=tests/artifacts --ignore=tests/retrieval/test_citation_lifecycle.py --ignore=tests/retrieval/test_generation_switch.py --ignore=tests/retrieval/test_pgvector_search.py -q` | `406 passed in 58.93s` |
| `D run --rm test-integration` | `520 passed in 35.34s` |
| `D run --rm --no-deps test-unit ruff check app tests scripts` | `All checks passed!` |
| `D run --rm --no-deps test-unit ruff format --check app tests scripts` | `233 files already formatted` |
| `D run --rm --no-deps test-unit mypy app/models app/retrieval app/services app/ai app/tasks` | `Success: no issues found in 41 source files` |
| `git diff --check` | exit 0 |
| `sh -n scripts/verify_telemetry_host.sh scripts/verify_telemetry_alerts.sh` | exit 0 |

The unit command has exactly the unchanged CI pytest arguments. Ordinary
collection omits the live file through `tests/observability/conftest.py` unless
`TELEMETRY_E2E=1`; the dedicated runner always enables it. Collection behavior is
tested both ways in subprocesses. No CI workflow was edited. Integration ran
with API/Worker/Beat stopped and finished before the final live gate started.

The prior final-source live gate failed (recovered result below). All data below labeled preliminary comes from
the earlier image and does not replace that final gate.

The preliminary host run used test image
`sha256:b43eb1f9a957dd522ccf8acbc5e6d3765ef70c845e9204eacd5d5a7283efda15`
for actual API, Worker and Beat, all confirmed by Docker inspect and the helper's
identity guard. No API/Worker/Beat host ports were published. Evidence directory:
`/tmp/recruitmatch-cp5-telemetry.P84wQN`.

Actual isolated alert result: all ten Firing after known nonfiring prerequisite.
Times after stimulus were AiFallback/LeaseTakeover 10.0s, CollectorExportFailure
and CollectorQueuePressure 40.1s, QueueOldestAge/WorkerZero/BeatStale 70.3s,
Http5xx/ApiP95 135.6s, ArtifactCleanup 311.3s. Actual dashboard sums were
2.0001090968598287/s and 1.0000545484299144/s. Source digest for that image:
`42cab4f3bf141109b1204916f587f62e746a16d01d39e8ec6d9e8ddf1998e594`.

Actual API resource was `a00f3de5-db88-4bf6-8301-295d98b51e09`. The eight-upload
before-restart flow exercised worker resources
`795bcd66-43f9-40da-8d97-387b0765c297` and
`eaff99c3-5329-487b-86d7-f8ed62ac1634`, each with four completions, four task
histogram observations, four model requests, 72 tokens and four indexed chunks.
The eight-upload after-restart flow exercised new resources
`2a00bb59-a76d-4e86-a5d9-af00d3a6aa40` and
`f726d58a-b8a1-4d2a-92e5-259553bdc841`, with the same per-writer values; API
identity remained stable. Both flows passed exact Tempo parentage and privacy.

Collector was actually stopped, source processing during outage succeeded,
authenticated nonempty rules/jobs and readiness stayed available, and Beat plus
Worker heartbeats remained fresh after a 12-second wait. It was restored and
its extension returned HTTP 200. The subsequent log scan passed all privacy
sentinels but failed strict JSON parsing, before finalizing the host evidence.
No successful `host.json` or complete final suite is claimed for this run.

Actual API-writer scoped follow-up query found HTTP duration histogram count121,
match completed5, match duration histogram count5, match score histogram count5,
artifact operation18. Prometheus's metadata endpoint returned an empty metadata
map for the histogram; this report does not claim metadata API type validation.

## Resolved architectural choice R17 — Celery warm-shutdown stdout

Only non-JSON lines in the actual collected Worker log were a blank line and the
fixed string `worker: Warm shutdown (MainProcess)` produced by Docker restart.
Installed `celery.apps.worker._shutdown_handler` explicitly passes
`sys.__stdout__` to `safe_say`, which uses original direct `os.write` in signal
context. This bypasses `--quiet` and normal logger formatting. It is stdout,
not stderr (an initial assumption was corrected after source inspection).

Controller R17 preserved strict JSON and approved only the narrow diagnostic
compatibility binding described above. Normal logging from a signal handler
would be inappropriate because it can acquire locks. The host failure provides
RED; additional real CLI prefork-two SIGTERM and import/descriptor tests gave
`2 failed, 4 deselected in 9.73s` before the fix. The in-flight task completed
even on RED, but blank/raw stdout failed JSON parsing. After implementation,
`6 passed in 16.03s`: SIGTERM still completed the already-started task, process
exit was zero, stderr empty, and every stdout line parsed as JSON. A real pipe
test verified one bounded private record on the selected descriptor, stable
repeated installation, no binding change from API/task-module imports, and safe
handling of a closed output stream. An exploratory test based on the incorrect
stderr assumption was removed; it does not define the final specification.

Collector is restored/running (confirmed HTTP200 after the failure); API and
Worker are running, Beat is healthy, child alert services are stopped. No commit
has been made. All implementation files and this report remain in the worktree
for final verification after the ruling.

## Self-review and limits

Self-review covered the focused production diff and all new helper/runner code.
The initial Compose-only ignore would not preserve the unchanged CI command;
the final mechanism lives in pytest collection and is tested both disabled and
explicitly enabled. Explicit telemetry prerequisites fail clearly and do not
skip. Host/alert evidence is bound to image source bytes and expires after one
hour. The host helper also checks actual API/Worker/Beat image identities against
the current test image before lifecycle operations.

Privacy pairs positive real backend data and nonempty actual process logs with
negative unique synthetic names, email, filename, phone, body/prompt, model
response, checksum, credentials, token, business IDs and Baggage sentinels.
Authorized PostgreSQL audit/model trace storage is outside the telemetry/log
privacy scan, as specified. Test evidence contains only synthetic state and is
kept in a printed local temporary directory; it is not committed or published.

Gauge freshness is delayed by the approved snapshot/export/query windows. The
test uses bounded 110s deadlines and makes no instantaneous dependency-health
claim. Alert stimuli are synthetic even though actual Prometheus reaches Firing.
No production capacity, notification chain, SLO/SLA or Beat restart is claimed.

Local commit deferred until scoped review passes (review-before-commit instruction). Full checkpoint
acceptance remains the controller's independent review.

## Final image-resident file hashes

Matched host and final test image, 19/19 files:

```text
15bbd88f683618cd8d1f0690ea024568911a5a1165ea8055c2d964e89e1a8f31  ops/grafana/dashboards/system-overview.json
247226203a11384a5c75ad97120c3cd6b407cea0f09e1b827d2d66357209e38a  tests/observability/prometheus.alerts.yml
31d93ff187ef42dcc8f8fbabc69f9a424a42ceaf8079cbe89365c568dfd270f3  tests/support/telemetry_runtime.py
61ffbbd2b6541648aa28a58f85070a2a34622bd908b1b20adba8134ddf915e33  scripts/verify_telemetry_alerts.sh
67d209ae61c9e995818b09811e1ebc49bca60344ef2a709f1e91b6ac6f08fe5d  tests/observability/gauge_probe.py
92553f6a0492889ce7f94d10e71c03ab74f643f7763c134f538a1a9bbee62e5e  scripts/verify_telemetry.py
9ef071eccba5ec4873b22c7b4593c00fa10e42155133580b9212ea23a9c89e52  tests/observability/compose.alerts.yml
a697c4de2be442cc7f31f73384910df5c4b7a2e275e0c54a37ac70dc90e5b02d  app/core/celery_logging.py
acf6d58806a6e3487337e114f770b63377aa2487578e5320b5ddca5381481f07  tests/observability/test_structured_logging.py
b79b27ab7e79325b655e925c9143517a02a6c02aa01d7ca2e9df8db75ebb4271  tests/observability/conftest.py
d00ea39dbb024f1cb24b5620ce8917f695dd3525ee38e2d7b8a03cf05e4ff18d  docker-compose.yml
d0644c982ab2659499815fc9dcced3ed73a68850fb99185725248b65151a540c  tests/observability/compose.telemetry.yml
d0ea9d75edd5afa147f7ed260a9b5c1db3bdfd2ea25c90f8ffd26b6f3d5b7232  tests/observability/test_telemetry_e2e.py
d12bb76b09e92b3bff5fd33680cfe0192e2f63df88c4d82d33287416eeac431c  tests/observability/test_telemetry_acceptance.py
d24501183b6607ee426e9c8c0afffb64e57a75231c175e2bd4eae59fa1356349  app/tasks/celery_app.py
f0b8a3c931897166ba69a5e4c5aa51924d7314f4e0bd7cc8cd8129ca080a0a1f  tests/observability/rules.test.yml
f66b3248d18fbb290557a5c80fe638fc4e55af3bbdfd5766b8a904ff4ccb27e5  ops/prometheus/rules.yml
fc40b554bb880809356994b9310650afd581b422610e15fa8baa9014754ea295  scripts/verify_telemetry_host.sh
fcaf812badd87f92acfba96213ac52cacf60a8b48c75cd2f63fce6e943b1c1a2  tests/observability/alert_probe.py
```

## Fix round 1 — review I1 and I2 (2026-09-14)

The recovered prior session `34288` ended with helper exit **1**, not success:
`1 failed, 123 passed in 99.13s`. The failed test was the live gauge acceptance,
at `expiry_positive_precedent_missing`; retained output did not display measured
elapsed operands. `/tmp/recruitmatch-cp5-telemetry.u9kw0Z` has same-source host
evidence, ten actual synthetic-input Firing results, successful runtime log privacy,
Worker restart and Collector outage/restoration. Those completed host phases do
not make the final pytest gate successful. Source/image hashes above describe
that reviewed pre-fix image, not the forthcoming fix image.

I2 root-cause reproduction used only the reviewed image and one existing test:
`D run --rm --no-deps test-unit python -c '<pytest.main([HTTP-exception-test,
-q]); inspect root handlers; emit real operations warning>'`. The test passed
in 2.48s, then root handlers were `[('StreamHandler', 'CaptureIO', True)]`.
The following actual `app.observability.operations` warning produced:
`logging/__init__.py:1163 emit -> stream.write -> ValueError: I/O operation
on closed file`. This independently establishes the leaked capsys handler after
the structured test's application factory configures process logging. No Worker
or R17 callback runs in this reproduction. The fix restores the prior root and
six explicitly configured named loggers after these structured-logging tests;
it neither suppresses stderr nor changes production logging or R17.

I1 source investigation established that `replace_gauges` begins its 25-second
TTL during `poll()`, while the acceptance timer formerly started after export
and backend queries. That timer imposed an additional 25 seconds incorrectly.
The old recovery condition also accepted an environment-wide zero without a
fresh current-writer sample. The corrected probe brackets the source poll before
export/query delay, polls the source once, requires fresh current-writer evidence,
and retains bounded positive-to-zero, failed-dependency NoData, recovery and
unpolled-source NoData checks. Production TTL, rules, privacy and business code
remain unchanged. The prior run's exact early-disappearance duration is unknown.

Focused RED command mounted only `test_telemetry_acceptance.py` into the reviewed
image: `D run --rm --no-deps -v "$PWD/tests/observability/test_telemetry_acceptance.py:/app/tests/observability/test_telemetry_acceptance.py:ro" test-unit pytest tests/observability/test_telemetry_acceptance.py -q`.
Result: **7 failed, 5 passed in 10.37s**. One failed on real closed-stream output
and missing restoration of the original live handler. Six failed because the
writer/freshness/source-timer helpers did not exist yet. After the focused fixes,
the acceptance, structured logging and domain metrics selection passed **34 in
33.79s** with no warnings/errors. Added real-SDK snapshot replacement/invalidation/
recovery/expiry characterization and missing-data cases are covered by the later
focused run. These source-mounted development results are not final image proof.

The first combined live focused selection passed **39 in 148.84s**, output
pristine. Self-review then found that instant-query result timestamps represent
query time, not the stored recording-rule sample. The fixture was corrected to
model that real boundary; the stale-recorded-sample case failed as intended:
`D run --rm --no-deps -v "$PWD/tests/observability:/app/tests/observability:ro" test-unit pytest tests/observability/test_telemetry_acceptance.py -k gauge_recovery_requires -q`
gave **1 failed, 4 passed, 10 deselected in 0.75s** (`True is False`). The helper
now explicitly queries `timestamp(recording_rule)` as well as the selected raw
writer's timestamp; it does not use the instant-query evaluation timestamp as
freshness evidence. This closes a test-evidence weakness without changing rules.

Final focused command (development source mount):
`D run --rm --no-deps -v "$PWD/tests/observability:/app/tests/observability:ro" test-telemetry pytest tests/observability/test_telemetry_acceptance.py tests/observability/test_structured_logging.py tests/observability/test_domain_metrics.py tests/observability/test_telemetry_e2e.py::test_operational_gauge_replacement_and_failed_dependency_are_unknown -q`
completed with **39 passed in 130.69s**, exit 0, pristine output. This includes
real PG/Redis/OTLP/Prometheus positive/zero/unknown/recovery/expiry, lifecycle
restoration, actual prefork SIGTERM in-flight completion, and real SDK observable
snapshot behavior. The live probe uses real SDK owners, not a Beat process restart.

Final rebuild used the same public/proxy-scoped build command above. Static
checking found one E501 (121-character line inside the nested pytest script);
the line was wrapped without behavioral changes and `build test-unit` repeated.
The pre-wrap CI invocation passed **417 in 50.02s**; it is superseded for exact
source proof by the post-wrap final-image run below.

Final fix test image: `sha256:3e5b9ea447be451796a097ec2a9c926c7fe2c14d505849404200b1a92352a3ae`.
Rebuilt production runtime image: `sha256:e3ec470aef9595af33c278717cef115026968562e8347873fac90cc39cf82029`.
No production source changed in this fix. The synthetic actual API/Worker/Beat
will use the final test image, which includes the scoped test-only fake clients.

Current phase: final-image CI flags finishing; integration and static complete.
Exact ownership was validated and API/Worker/Beat stopped before integration;
the final live flow will establish fresh state after integration has completed.

Final source digest: `879afdacae9b5fb36365ce92a854e01d76789f15b631e0903ae785290bc4967b`.
Four fix files matched host `shasum -a 256` against final-image `sha256sum`:

```text
3306b697ed38b7b580db397a0f0a6a2863a61c3968de76418094da0624a9866b tests/observability/gauge_probe.py
b9df51bf0360c56a2cb605505773fbecd3387cf2f4697085f89ec278651ec91f tests/observability/test_telemetry_acceptance.py
254479586dda6d5512e19a5fdd6c536a6a4b89ed7d9f0e75cc5da49752610105 tests/observability/test_structured_logging.py
d3ede1ff69b447bce6d6fd6102131b4d2a3f4f16c3be9f95e5ae765c2849dd0f tests/observability/test_domain_metrics.py
```

- `D run --rm test-integration`: **520 passed in 31.72s**, exit 0.
- `D run --rm --no-deps test-unit sh -c 'ruff check app tests scripts && ruff format --check app tests scripts && mypy app/models app/retrieval app/services app/ai app/tasks'`: all checks passed; **233 files already formatted**, **41 mypy source files**, exit 0.
- Compose `config --quiet`, shell syntax for both host helpers, `git diff --check`: exit 0. Index remains empty and HEAD unchanged; no commit.
- Exact unchanged CI flags, final image: `D run --rm --no-deps test-unit pytest tests --ignore=tests/integration --ignore=tests/artifacts --ignore=tests/retrieval/test_citation_lifecycle.py --ignore=tests/retrieval/test_generation_switch.py --ignore=tests/retrieval/test_pgvector_search.py -q`: **417 passed in 58.66s**, exit 0, pristine.

Final helper now running after all full regressions: `D -f docker-compose.yml -f tests/observability/compose.telemetry.yml up -d --no-build --force-recreate api worker beat && sh scripts/verify_telemetry_host.sh`.

Fix-only self-review: only the four hashed test files above changed since the
reviewed snapshot (plus this ignored report). The added SDK test calls the real
observable reader after replacement, invalidation, zero recovery, an intermediate
observation and the exact source expiry boundary; it does not redefine the TTL.
Behavioral probe tests reject old writers, old raw samples, old recording samples,
wrong values and absent data. The controlled clock test accounts for poll, export
and backend delay rather than resetting the timer after those operations.
The logging regression runs both actual structured tests in a fresh pytest
subprocess, then emits through the original handler and requires empty stderr.
The fixtures restore only the logger state these tests configure. No production
class gained test cleanup, no stderr filter was introduced, and R17 is unchanged.
Both review Minors remain deliberately deferred; no unrelated polish was done.

Pre-live ownership/image recheck confirms actual API/Worker/Beat all run the final
`3e5b9ea447be...` test image; Collector remains its accepted
`799dc6cf12c9...` image. HEAD is still `ccb662824dba93597e251f0d443fa2da4a49df7d`.
No staging/commit/push/PR/merge was performed.

Final helper alert phase completed: `/tmp/recruitmatch-cp5-telemetry.gTDwtg`.
Initial state was nonfiring. Actual Firing observed at: AiFallback/LeaseTakeover
5.0s; CollectorExportFailure/CollectorQueuePressure 35.2s; QueueOldestAge,
WorkerZero and BeatStale 70.3s; Http5xx/ApiP95 125.5s; ArtifactCleanup 311.1s.
Numeric dashboard rates were **2.0000727299174517** for two increasing families
and **1.0000363649587258** for a single present family. Alerts evidence matches
final digest `879afdac...`; the child Prometheus and stimulus both stopped.
These remain isolated synthetic rule-engine inputs, not physical Collector
saturation/backend fault or capacity claims. Business/restart/outage/final pytest
phases are still running; alert success is not overall helper success.

Final real business/restart phase: eight successful uploads before and eight
after actual owned Worker restart. Tempo verified actual W3C HTTP → broker →
prefork processing parentage for both flows. Stable API writer:
`c6503778-2a03-4a1b-a658-c3939a687991`. Old worker children:
`7c1aeccb-736c-455f-ba23-23359e4c6df1`,
`d462769c-3ce3-452b-9d86-e87ae9d05782`; replacement children:
`18e62b4b-183b-45ec-84d5-6e78945cb00e`,
`634206c1-d827-4842-9905-83244a61a50c`. Each queried writer contributed four
started/completed tasks, four task histogram observations, four model requests,
72 model tokens and four indexed chunks. These IDs came from actual queried
traces, not synthetic alert writers or predecessor fixture streams.

Final host phases completed: strict parser printed
`runtime_logs_nonempty_private_and_correlated`, then finalize printed
`host_outage_restoration_and_restart_verified`. Collector stop, continued fresh
Beat/Worker heartbeat, new processing plus authenticated readiness/jobs/matching
during outage, actual Collector HTTP200 restoration and distinct restart writers
were verified before `host.json` was written. Child services independently
inspected as exited with no port bindings and exact child project ownership.
The final full `pytest tests/observability -q` is now running; its exit is pending.

### Final fix result — complete

Session `30654` completed with **helper exit 0**. Its final exact-image command
`pytest tests/observability -q` completed **135 passed in 182.45s (0:03:02)**,
with pristine output (no warnings, traceback or closed-stream diagnostics), then
printed `Synthetic evidence directory: /tmp/recruitmatch-cp5-telemetry.gTDwtg`.
This supersedes all earlier pending/running phase notes in this report.
The full gate includes the corrected real gauge probe and the concrete logging
fixture-lifecycle regression, not merely the previously completed host phases.

Read-only safe host evidence confirms all seven booleans true: Collector stopped,
Collector restored, business available, Beat/Worker continued, processing during
outage, private logs and restart verified. Distinct actual writer contributions
sum to **18 task completions and 18 task histogram observations**, including the
bounded outage work. Host and alerts evidence both match final source digest
`879afdacae9b5fb36365ce92a854e01d76789f15b631e0903ae785290bc4967b`.

Final verification summary: focused **39/39**, exact CI **417/417**, integration
**520/520**, explicit full observability **135/135**, all ten isolated actual
Firing gates; Ruff check/233 formatted/41 mypy clean. All final full commands used
the rebuilt image without source replacement mounts. Integration completed before
the final live flow, and no concurrent database-reset fixtures ran during it.

Remaining limitations are unchanged: actual SDK gauge-owner replacement is not
an actual Beat process restart; actual Worker process restart is independently
verified. Synthetic alert inputs prove the real rule engine and dashboard query,
not physical saturation/backend failures/capacity or paging. R17 still carries
the explicitly approved pinned-Celery private utility upgrade coupling; this fix
does not alter that adapter. Review Minors are deferred as instructed. No new
correctness concern or blocker remains from I1/I2. Review-before-commit applies:
no files staged, no commit/push/PR/merge, and no parent snapshot/ledger edits.

Post-gate read-only verification: exact owned API/Worker/Beat remain running on
the final test image; Collector running on its accepted image and health endpoint
returned **HTTP 200**. Both exact-owned child alert services are exited. Image
`source_digest()` remains `879afdac...`; `git diff --check` passed, cached diff
was empty and HEAD remains `ccb662824dba93597e251f0d443fa2da4a49df7d`.
The final gauge's two most recent SDK owners, read back from scoped Prometheus
sample timestamps, are `7cf89db2-802f-4a90-a041-ca2ec9be4088` (older) and
`c0846064-eade-4e83-b48c-426c94726400` (replacement). The scoped staging
`recruitmatch:worker_live:latest` query returned **[]** after expiry, not zero.
These SDK-owner IDs are distinct from the actual Worker process IDs above.
