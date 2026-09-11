# Bounded HNSW fixture-isolation investigation — 2026-09-11

## Result and limits

**Proven:** physical history inherited by the DELETE-based `pg_index` fixture
changed the ANN seed despite identical visible rows, authorized candidate count,
query parameters, and exact results. Rebuilding only the HNSW index reversed that
change. Fixture setup now physically resets `recruiting_chunks` before reseeding.

**Not proven:** the precise cause of the earlier `[]` result. Neither controlled
history reproduced that exact outcome: both returned the expected top two, while
their ANN seeds lost the third row. This change resolves the demonstrated fixture
isolation defect; it is not evidence of a fully diagnosed historical empty result
or of general ANN recall guarantees. The controller explicitly accepted this
narrow scope after receiving both diagnostic results.

Original evidence supplied in the brief, preserved without reinterpretation:

```text
base: 63ec4ed (retrieval source unchanged from 410019e)
pytest tests/artifacts tests/integration tests/retrieval -q
162 passed, 1 failed
test_exact_threshold_boundary_and_hnsw_explain_are_reproducible
count=5, strategy=hnsw, budget=3, expected HNSW EXPLAIN assertion passed
expected ["chunk-a", "chunk-b"], got []
one focused unchanged rerun passed
```

## Environment and boundaries

- All database operations used Compose project
  `recruitmatch-hnsw-isolation-sep11`; it had no containers before this work.
- Created dedicated PostgreSQL/Redis volumes and ran its bootstrap only. No API
  or worker started, no default/other-task volume was inspected or modified.
- PostgreSQL 16.12, Debian 16.12-1.pgdg12+1, aarch64; pgvector 0.8.1;
  Alembic head `20260911_14`.
- Test runs explicitly set `AI_ENABLED=false` and cleared `OPENAI_API_KEY`.
- Read the failing test/fixture, complete adapter search path, migration 11, and
  the official [pgvector troubleshooting guidance](https://github.com/pgvector/pgvector#troubleshooting).
  The documentation identifies dead tuples/filtering as possible causes of
  reduced HNSW results; it does not establish this historical failure's cause.
- Diagnostic-only `pgstattuple` was enabled in this disposable database. During
  each controlled history, autovacuum was disabled only on `recruiting_chunks`
  and restored in `finally`. Heap pruning remained possible. No VACUUM was run.
- Only `tests/retrieval/test_pgvector_search.py` changed. Artifact ownership,
  migrations, production query settings, strict-order iterative HNSW,
  candidate budgets, and existing retrieval assertions are unchanged.

## Controlled evidence

The unchanged original test passed once on a freshly bootstrapped database:
`1 passed in 0.70s`. This was a clean baseline, not resolution by rerunning.

Each diagnostic started with `TRUNCATE recruiting_chunks` on owned synthetic
data, disabled that table's autovacuum, created the real fixture, and inserted
the original `ann-c/d/e` rows with vectors `(0.8,0.6)`, `(0.6,0.8)`, `(0,1)`.
The query vector was `(1,0)` padded to 512 dimensions. Exact threshold was 5;
ANN threshold 1, multiplier 2, budget cap 3, `top_k=2`, `min_score=0`.
Each stage measured adapter exact and ANN hits, `explain_search`, actual HNSW
seed rows via `EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF, BUFFERS)`,
relation file identities/sizes, and `pgstattuple` heap counts.

One variable at a time: clean → prescribed DELETE-based history → index-only
`REINDEX INDEX ix_recruiting_chunk_embedding_hnsw_active`. No retries, parameter
tuning, or repeated pytest loops were used to obtain passing output.

| Prescribed history / stage | Candidates | ANN budget | Exact and ANN top two | Actual ANN seed rows | HNSW bytes |
| --- | ---: | ---: | --- | ---: | ---: |
| 40 fixture lifecycles: clean | 5 | 3 | chunk-a, chunk-b | 3 | 24,576 |
| 40 fixture lifecycles: churn | 5 | 3 | chunk-a, chunk-b | 2 | 245,760 |
| 40 fixture lifecycles: reindex only | 5 | 3 | chunk-a, chunk-b | 3 | 24,576 |
| One fixture + 1,000 equal vectors: clean | 5 | 3 | chunk-a, chunk-b | 3 | 24,576 |
| One fixture + 1,000 equal vectors: churn | 5 | 3 | chunk-a, chunk-b | 2 | 1,949,696 |
| One fixture + 1,000 equal vectors: reindex only | 5 | 3 | chunk-a, chunk-b | 3 | 24,576 |

First history: heap file `17139` persisted throughout; HNSW file `17147` persisted
through churn then changed to `18037` on reindex. Heap size was 8,192 bytes clean
and 32,768 bytes both before and after reindex. HNSW scan buffer hits were
20 → 215 → 26.

Second history: heap file `18038` persisted throughout; HNSW file `18046` persisted
through churn then changed to `19117` on reindex. The prior seeded fixture had
1,021 live rows and a 1,933,312-byte HNSW index. Final heap size was 196,608 bytes
both before and after reindex. HNSW scan buffer hits were 20 → 160 → 25.

All measured clean/churn/reindex search states had 24 live heap tuples and zero
`pgstattuple.dead_tuple_count`; heap pruning means this observation cannot be
used to claim absence of obsolete index references. It also means we do **not**
claim a measured accumulation of intact dead heap tuples as the precise cause.
Every plan used `ix_recruiting_chunk_embedding_hnsw_active`. Representative
actual plan excerpt, with the same query and count:

```text
clean:        CTE ann_seed -> Limit (actual rows=3 loops=1)
churn:        CTE ann_seed -> Limit (actual rows=2 loops=1)
reindex only: CTE ann_seed -> Limit (actual rows=3 loops=1)
                -> Index Scan using ix_recruiting_chunk_embedding_hnsw_active
```

These results isolate an index-history effect. They do not identify the internal
graph traversal path, strict-order discard mechanism, original autovacuum
timing, or sufficient historical conditions for the original empty seed.

## Change and RED/GREEN

Fixture setup replaces its initial `DELETE recruiting_chunks` with
`TRUNCATE TABLE recruiting_chunks`, with no CASCADE. It resets the heap and its
indexes before the existing source/tenant cleanup and seed. Teardown remains
unchanged; the next setup owns physical isolation, including history left by
other tests. This assumes the existing serial disposable-database test lane;
sharing this table across concurrent test workers is not supported.

New regression `test_pg_index_does_not_inherit_previous_fixture_hnsw_graph`
executes an actual previous fixture lifecycle, adds 1,000 equal-vector chunks,
records its HNSW file, tears it down, then asks pytest for the current fixture.
It asserts that the current HNSW file differs and invokes the unchanged original
boundary test, retaining all of its exact/HNSW plan, count, budget, and ordered
hit assertions. The regression catches physical graph reuse directly; it does
not depend on random ANN loss reappearing on a particular run.

```text
RED, new regression + original DELETE setup:
AssertionError: pg_index inherited the previous fixture's physical HNSW graph
assert 19117 != 19117
1 failed in 0.52s

GREEN, same regression + TRUNCATE setup:
1 passed in 1.00s

Final complete affected suite, rebuilt image at final source:
pytest tests/artifacts tests/integration tests/retrieval -q
164 passed in 8.42s
```

The final suite was executed once at the final source, after the focused GREEN
check. This is the full combined affected suite, not the repository's unrelated
unit/evaluation lanes. `git diff --check` passed.

## Commands and reproduction

All commands below run from `.worktrees/recruitmatch-v2`. The two diagnostics
ran before the fixture edit, at base `63ec4ed`; the standalone diagnostic script
used stdin, outside the application image. A temporary copy remains at
`/private/tmp/recruitmatch-hnsw-diagnostic.py` (second history shape). To reproduce
historical behavior, use the original fixture at that base; running the script
against the fixed fixture intentionally resets history between lifecycles.

```sh
docker compose -p recruitmatch-hnsw-isolation-sep11 ps -a
docker compose -p recruitmatch-hnsw-isolation-sep11 up -d postgres redis bootstrap
docker compose -p recruitmatch-hnsw-isolation-sep11 run --rm --no-deps -e AI_ENABLED=false -e OPENAI_API_KEY= test-integration pytest tests/retrieval/test_pgvector_search.py::test_exact_threshold_boundary_and_hnsw_explain_are_reproducible -q
docker compose -p recruitmatch-hnsw-isolation-sep11 run --rm --no-deps -T -e AI_ENABLED=false -e OPENAI_API_KEY= test-integration python < /private/tmp/recruitmatch-hnsw-diagnostic.py
docker compose -p recruitmatch-hnsw-isolation-sep11 build test-integration
docker compose -p recruitmatch-hnsw-isolation-sep11 run --rm --no-deps -e AI_ENABLED=false -e OPENAI_API_KEY= test-integration pytest tests/retrieval/test_pgvector_search.py::test_pg_index_does_not_inherit_previous_fixture_hnsw_graph -q
# Apply the one-line fixture setup fix, rebuild, then repeat the focused command once.
docker compose -p recruitmatch-hnsw-isolation-sep11 build test-integration
docker compose -p recruitmatch-hnsw-isolation-sep11 run --rm --no-deps -e AI_ENABLED=false -e OPENAI_API_KEY= test-integration pytest tests/artifacts tests/integration tests/retrieval -q
git diff --check
```

The first diagnostic invoked `pg_index.__wrapped__(engine)` for exactly 40
previous setup/teardown lifecycles between its clean and churn stages. The
second replaced that loop with one prior lifecycle plus
`[_chunk(f'prior-{i}', f'prior-{i}', offset=100+i) for i in range(1000)]`.
Both used the same final seed and reindex treatment. The final image manifest
was `sha256:824c88e1821082002e7ddffe15216a9d223ea20e5f5b581b1b7801d7cd0ae97f`.

## Commit

Commit subject: `test: isolate pgvector fixture physical index state`.
Parent: `63ec4ed`. This report is force-tracked in the same commit as the fix.
Resolve its immutable commit with:

```sh
git log -1 --format=%H -- .superpowers/sdd/2026-08-24-recruitmatch-v2-cp3-artifacts/hnsw-investigation-report.md
```

No push, merge, controller progress edit, or production behavior change occurred.

## Diagnostic source (second history)

The temporary script is reproduced here so this report is self-contained.

```python
import os
from sqlalchemy import create_engine, text
from tests.retrieval.test_pgvector_search import pg_index, _chunk
from tests.retrieval.test_retrieval_contract import MODEL, authorized_scope, unit_vector
from app.retrieval.pgvector_index import PgVectorRecruitingIndex

engine = create_engine(os.environ['DATABASE_URL'])

def physical(label):
    with engine.connect() as c:
        print(label, list(c.execute(text("SELECT relname, relfilenode, pg_relation_size(oid), reloptions FROM pg_class WHERE relname IN ('recruiting_chunks','ix_recruiting_chunk_embedding_hnsw_active')"))))
        print('heap tuples', c.execute(text("SELECT tuple_count, dead_tuple_count FROM pgstattuple('recruiting_chunks')")).one())

def measure(label, index):
    exact = PgVectorRecruitingIndex(index.session_factory, exact_search_max_candidates=5)
    ann = PgVectorRecruitingIndex(index.session_factory, exact_search_max_candidates=1, ann_candidate_multiplier=2, ann_candidate_budget_max=3)
    plan = ann.explain_search(authorized_scope(), unit_vector(), MODEL, top_k=2, min_score=0)
    print(label, 'count', plan.authorized_candidate_count, 'budget', plan.ann_candidate_budget,
          'exact', [h.id for h in exact.search(authorized_scope(), unit_vector(), MODEL, 2, 0)],
          'ann', [h.id for h in ann.search(authorized_scope(), unit_vector(), MODEL, 2, 0)])
    with index.session_factory() as s:
        ann._begin_repeatable_read(s)
        ann._configure_search_strategy(s, 'hnsw')
        print('\n'.join(line for line in s.execute(text('EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF, BUFFERS) '+plan.statement)).scalars() if any(key in line for key in ('CTE ann_seed', 'Index Scan using ix_recruiting_chunk_embedding', 'actual rows=', 'Buffers:'))))
    physical(label)

with engine.begin() as c:
    c.execute(text('CREATE EXTENSION IF NOT EXISTS pgstattuple'))
    c.execute(text('ALTER TABLE recruiting_chunks SET (autovacuum_enabled = false)'))
    c.execute(text('TRUNCATE recruiting_chunks'))

try:
    fixture = pg_index.__wrapped__(engine)
    index = next(fixture)
    with index.session_factory() as s:
        s.add_all([_chunk('ann-c','ann-c',embedding=unit_vector(.8,.6),offset=30), _chunk('ann-d','ann-d',embedding=unit_vector(.6,.8),offset=31), _chunk('ann-e','ann-e',embedding=unit_vector(0,1),offset=32)])
        s.commit()
    measure('clean',index)
    # One bounded history: a single earlier fixture with 1,000 equal vectors.
    try: next(fixture)
    except StopIteration: pass
    fixture = pg_index.__wrapped__(engine)
    prior = next(fixture)
    with prior.session_factory() as s:
        s.add_all([_chunk(f'prior-{i}',f'prior-{i}',offset=100+i) for i in range(1000)])
        s.commit()
    physical('before delete')
    try: next(fixture)
    except StopIteration: pass
    fixture = pg_index.__wrapped__(engine)
    index = next(fixture)
    with index.session_factory() as s:
        s.add_all([_chunk('ann-c','ann-c',embedding=unit_vector(.8,.6),offset=30), _chunk('ann-d','ann-d',embedding=unit_vector(.6,.8),offset=31), _chunk('ann-e','ann-e',embedding=unit_vector(0,1),offset=32)])
        s.commit()
    measure('churn',index)
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
        c.execute(text('REINDEX INDEX ix_recruiting_chunk_embedding_hnsw_active'))
    measure('reindex only',index)
    try: next(fixture)
    except StopIteration: pass
finally:
    with engine.begin() as c:
        c.execute(text('ALTER TABLE recruiting_chunks RESET (autovacuum_enabled)'))
    engine.dispose()
```
