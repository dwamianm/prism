# P7 report: an address lookup by subject and predicate

Date: 2026-11-12
Branch: `research-address-lookup-2026-11`, worktree `~/Sites/prism-address-lookup`, from `origin/main` at `3fc87120`.
All code is under `research/address-lookup-2026-11/` plus one behavior-preserving refactor in
`src/prme/retrieval/temporal.py`. No default path changed its results.

## Verdict

The headline number is **2.6 s to about 7 ms**. The 5 ms bar is not met; the measured floor is p50 5.1 ms and
p95 6.7 ms, stated beside it.

The index on the claim address does the larger part of the work and does it cheaply. What it does not do alone is
reach 5 ms, because the privacy range check and the contradiction edge fetch together cost more than the whole bar.
An engine change should be argued on 2.6 s to about 7 ms, not on 5 ms.

The brief's question was whether a lookup by address can answer in about 5 ms without a full scan and return
exactly what the current lookup returns. The answer has two halves. It can return exactly what the current lookup
returns, and it can do it without a full scan. It cannot do it in 5 ms on this machine at this size. It can do it in
about 7 ms, which is where an engine change should aim.

- Bar (a), identical records: **passes**, 233 of 233 pairs, zero mismatches.
- Bar (b), p95 under 5 ms at 20,500: **fails**. The prototype reaches p95 53.4 ms (p50 41.1 ms), down from
  2,979 ms. The floor with every privacy fence and both scan counts removed is p95 6.7 ms (p50 5.1 ms).
- Bar (c), write cost and no record lost: **passes**. 0.209 ms p50 per fact added when the key goes in the
  insert statement, and no node, event or key changed across a reopen and a rebuild.

The reason bar (b) fails is not the index. The index works: the page went from 42 pages of 500 rows to one query of
4.7 ms. The remaining time is the privacy range check, which the lookup pays twice, and the contradiction edge
fetch. At 13.4 ms each, one fence is more than twice the whole bar.

## Bar (a): byte-identical records, same order

Probe: `probes/compare.py`. Result: `results/compare-20500.json` (222 pairs) and `results/compare-20500-seeded.json`
(233 pairs). Pack: `/tmp/p6/lookup/pack` copied to `/tmp/p7/compare`, 20,500 stored facts.

The comparison is the whole `AssertionState`, serialized with `model_dump_json()`, not a summary: status, every
count, every current candidate, every timeline entry with its exclusion reasons and contradiction ids, every value
group, every conflict, and the exclusion map.

Second run added the shapes the pack does not carry, because the pack holds only tentative and superseded
`robot-1` claims with no archived, deprecated or contested ones:

| shape | pairs |
|---|---|
| matches nothing | 111 |
| one claim | 9 |
| several claims | 113 |
| has a retired claim in the timeline | 111 |
| another user's address | 2 |

Statuses across those pairs: unknown 116, multiple 112, single 4, contested 1. The seeded shapes are an archived claim,
a deprecated claim, a contested pair, a hypothetical, a conditional, an inferred claim, a closed validity window, and
a subject stored with doubled spacing and mixed case.

Mismatches: **0**.

One defect was found and fixed while building this. The first run mismatched on almost every pair, all on
`exclusions`, because the prototype reported `missing_structured_assertion: 0` where the current path omits the key.
The current path builds a `Counter` and only ever increments it, so a zero is absent rather than present. The
prototype now sets a count only when it is non-zero.

## Bar (b): p95 at 20,500, under 5 ms

Probe: `probes/timing.py`, results `results/timing-400.json`, `timing-4050.json`, `timing-20500.json`. Each pack is
copied once, migrated, then both paths read the same copy over the same pairs, current first. Ten calls per pair,
three pairs. Load average is beside every number.

| stored | pack | load before | current p50 | current p95 | indexed p50 | indexed p95 |
|---|---|---|---|---|---|---|
| 400 | `/tmp/p6/build/current-500/pack` | 7.21 | 33.2 ms | 45.5 ms | 8.3 ms | 10.9 ms |
| 4,050 | `/tmp/p6/build/current-4050/pack` | 8.51 | 292.6 ms | 335.8 ms | 14.0 ms | 18.3 ms |
| 20,500 | `/tmp/p6/lookup/pack` | 9.02 | 2,979.3 ms | 3,177.0 ms | 41.1 ms | 53.4 ms |

The 20,500 indexed side ran while load rose from 9.02 to 18.17, so it is the pessimistic side of the comparison.

Sizes are p50 of the three pairs' p50s, and p95 is the worst pair's p95. The same pack is measured in the baseline
probe at 400 and 4,050 stored with three separate pairs per pack; those are in `results/baseline-*.json` and agree.

### Where the time goes, measured

Probe: `probes/floor.py`, result `results/floor-20500.json`, load 2.46 to 6.85 before and after. One indexed lookup
split into parts, 30 calls:

| part | p50 |
|---|---|
| privacy fence, first (`engine._ordinary_graph`) | 13.4 ms |
| indexed page: SQL, row hydration, owner view | 4.7 ms |
| two scan counts | 1.8 ms |
| `classify_page` over the page | 0.2 ms |
| `_contradiction_edges`, which resolves the facade again | 14.2 ms |
| state build after the edges | 0.3 ms |
| **whole call** | **48.4 ms** |

Floor: the page, the classify and the state build with both fences and both counts taken out is **p50 5.1 ms,
p95 6.7 ms**. That is what an engine with an indexed address read, an indexed privacy marker and a maintained
count would still pay, and it is still above the 5 ms bar at p95.

### Where the 2.65 s goes in the current path

Probes: `probes/stages.py`, `probes/overhead.py`, `probes/page_parts.py`, results `results/stages-20500.json`,
`results/overhead-20500.json`, `results/page-parts-20500.json`. Pack `/tmp/p6/lookup/pack`, 44 pages of 500 rows.

The largest part is the privacy range check, paid once per page. Not the scan, and not the Python filter.

| part, one call at 20,500 | measured |
|---|---|
| paged `engine.scan_nodes`, 42 to 44 pages at 56.7 to 61.3 ms | 2,436 to 2,600 ms |
| inside one page: `require_legacy_call` | 13.7 ms |
| inside one page: `project_public_result` | 13.5 ms |
| inside one page: `_ordinary_graph`, a `require_legacy_range` | 13.9 ms |
| `_protected_range_sql` alone: admissions 0.15, events marker 3.9, nodes marker 9.2 | 13.6 ms |
| SQL fetch over all pages | 357.9 ms |
| row hydration over all pages | 188.6 ms |
| owner view over all pages | 243.0 ms |
| Python filter over all pages | 15.4 ms |

The same page loop run inline with the fences removed costs 805 ms in total. So roughly 1.6 to 1.8 s of the 2.65 s
is the three fences, each of which rescans `nodes` for a privacy marker row.

The nodes marker is the expensive one because `json_extract(metadata, '$.privacy...')` runs on every node row. That
scan grows with the store, which is why the current path grows three times faster than the data.

## Bar (c): write cost, and nothing lost or changed

Probe: `probes/write_cost.py`, result `results/write-cost-4050.json`, pack `/tmp/p6/build/current-4050/pack`, 150
facts, 10 `store()` calls. `results/write-cost.json` is an earlier run of the same probe under heavier load, where a
`store()` call measured 576.8 ms instead of 150.4 ms and the same in-statement key measured 0.67 ms. The two runs
agree in shape; the numbers above are the quieter one. Neither is a claim about absolute speed on a loaded machine.

| shape | added per fact, p50 | added per fact, mean |
|---|---|---|
| key written in the same `INSERT` statement | **0.209 ms** | 0.142 ms |
| key written by a second `UPDATE` after the insert | 3.871 ms | 3.318 ms |

The recommended shape is the first. DuckDB rewrites a row on `UPDATE` as a delete plus an insert, so a second
statement costs a row rewrite and two index updates; the same statement costs one more pair of values. As a share of
a `store()` call at p50 150.4 ms, the key is **2.57%**.

Migration cost for an existing pack, one time, Python side: 1,444 nodes 0.356 s, 8,720 nodes 2.296 s, 41,620 nodes
11.178 s.

Probe: `probes/rebuild.py`, results `results/rebuild-4050.json` and `results/rebuild-20500.json`. Both packs pass:

| check | 4,050 | 20,500 |
|---|---|---|
| no node lost | true | true |
| no node changed | true | true |
| no event changed | true | true |
| key stable across rebuild | true | true |
| key rebuilt equals key first written | true | true |

The digests cover explicit column lists, so the two new columns cannot make the comparison vacuous. Keys are cleared
and rebuilt rather than dropped, because DuckDB on this build refuses to drop a column while any index exists on the
table (`DependencyException: Cannot alter entry "nodes" because there are entries that depend on it`), reproduced on
an untouched pack and on a three-column table.

## The design the engine should carry

Two parts, and only the first is about the address.

1. **The key.** Two stored columns on `nodes`: `assertion_subject_key` and `assertion_predicate_key`, both
   `normalize_assertion_value` of the stored metadata, written in Python and never by SQL. SQL `lower()` and `trim()`
   are not `str.casefold()` and SQL has no NFKC, so a SQL-computed key would drift from the lookup's own rule on any
   non-ASCII subject. A node that `_assertion_values` refuses stores NULL, which is exactly what
   `missing_structured_assertion` counts.
2. **The index.** `CREATE INDEX idx_nodes_assertion_key ON nodes (user_id, scope, node_type,
   assertion_subject_key, assertion_predicate_key)`. Owner, scope and type come first because the lookup already
   filters on them. Lifecycle is a filter and not an index column, for the same reason `idx_nodes_lifecycle` is
   absent from `schema.py`: an indexed lifecycle update is a delete plus an insert on supported DuckDB and can bypass
   an in-flight `updated_at` column claim.

What this does not buy is the bar. The privacy fences and the scan counts must be addressed too:

- **An indexed privacy marker.** The range check scans `nodes` for a marker row on every page. A stored boolean
  column with its own index turns that into a lookup. This is the single largest saving in the whole measurement.
- **A maintained count.** The two scan counts exist only so the state can report how many claims sat at other
  addresses; a per-owner, per-type count kept on write removes both.
- **One fence per call, not two.** `build_state` resolves the facade again for `_contradiction_edges`. A caller that
  already holds a fenced facade should pass it in.

With all three, the floor measured here is p50 5.1 ms and p95 6.7 ms. The bar is 5 ms at p95. So even the full design
does not meet it on this machine at that store size, and an engine change should be justified on the 2.65 s to
roughly 7 ms improvement, not on the 5 ms figure.

## The smallest engine change it implies

One change, in one place: `get_assertion_state` takes an optional indexed page source. `prme.retrieval.temporal`
already had the page loop and the state build in one function; the refactor in this branch splits them at
`classify_page` and `build_state`, and those two functions are the whole of the change that a page source needs.

```
classify_page(page, *, engine, query, normalized_subject, normalized_predicate, exclusions)
    -> (records, matched)
build_state(engine, query, *, user_id, records, normalized_subject, normalized_predicate,
            exclusions, scanned_nodes, matched_records) -> AssertionState
```

`get_assertion_state` calls both exactly as before, so the current path's results are unchanged and its cost is
unchanged. `lookup.py` shows the other side: one query for the page, one for the counts, then the same two functions.

The refactor is behavior preserving. `tests/test_assertion_state.py`, `tests/test_temporal_validity.py` and
`tests/test_privacy_source_routing.py` pass, and `research/address-lookup-2026-11/test_page_seam.py` holds the seam:
it checks the refactored path's counts on a hand-built store, checks the prototype equals the current path field for
field, checks every stored key equals what `normalize_assertion_value` says, checks a claim stored after the
migration is found without a backfill, and checks that rewriting a subject moves the claim to its new address.

## Tests

Providers blocked throughout
(`OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1
ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY=`).

Targeted suites on this branch: `tests/test_assertion_state.py`, `tests/test_temporal_validity.py` and
`tests/test_privacy_source_routing.py` give 28 passed, 19 skipped. `research/address-lookup-2026-11/test_page_seam.py`
gives 5 passed.

Full suite: `pytest tests -q --ignore=tests/test_pg_store.py` on commit `c2f50338`, providers blocked:
**1 failed, 7,633 passed, 1,728 skipped, 127 xfailed in 2,300 s (38 min 20 s)**. A first run used `-x` and stopped at
the same failure after 849 tests; those 849 are a prefix of this run and add nothing.

The single failure is `tests/test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself`, and it is a
harness artifact of running from a git worktree, not a result of this branch.

```
assert checkout.main_checkout() == checkout.REPO
E   AssertionError: assert PosixPath('/Users/dwamianm/Sites/prism') == PosixPath('/Users/dwamianm/Sites/prism-address-lookup')
```

`checkout.main_checkout()` derives the main checkout from `git rev-parse --git-common-dir`, so from a worktree it
returns the main checkout by design. The test asserts the two are equal, which only holds in a plain clone. Evidence
that this branch did not cause it: the failure reproduces in `~/Sites/prism-working-tier`, a different worktree of the
same repository that this branch never touched; it passes in `~/Sites/prism` itself (13 passed); and this branch
changes neither `tests/` nor `benchmarks/checkout.py`. `tests/test_checkout.py` and that module both last changed in
`075b5f03` on 2026-09-28, which is on `origin/main`.

## Limits, stated

- One machine, one run per number. Load average moves with other sessions on this machine and is recorded beside
  every number. The 400 and 4,050 stored cases were measured at load 5.6 to 10.5; the 20,500 case at load 9.0 to 18.2.
- The store has no model calls in it. Nothing here needs a model call.
- The prototype falls back to the current path on a protected store, because that store resolves to
  `AdmittedGraphView`, which deliberately exposes no connection. Widening the fence to save a scan is not something a
  prototype should do; a real change would add the indexed page as a method on both facades.
- Only DuckDB is measured. The PostgreSQL backend needs the same two columns and a partial index per model, and is
  untested here.
- The packs are synthetic and built from `store()` for the 400 and 4,050 cases; `/tmp/p6/lookup/pack` is the 20,500
  fact pack from P6. None of them carries archived or deprecated claims, which is why `compare.py` seeds them.

## Scratch usage

- `/tmp/p7`: 28 GB, 30 pack copies at about 950 MB each, one per probe run. Only `results/` in the repo is needed.
  Safe to delete with `rm -rf /tmp/p7`.
- `/tmp/p6`: 12 GB, reused from P6. `/tmp/p6/lookup/pack` (950 MB) and `/tmp/p6/build/current-500` and
  `current-4050` (467 MB) are the packs the 20,500 and small numbers came from.

## Files

- `keys.py`: the key, the index, the migration, the write path, and the insert-with-key statement.
- `lookup.py`: the indexed page source and `indexed_state`, which runs the unchanged lookup with that page source.
- `test_page_seam.py`: holds the refactor and the equality between the two paths.
- `probes/baseline.py`, `stages.py`, `overhead.py`, `page_parts.py`: the current path and its attribution.
- `probes/compare.py`: bar (a).
- `probes/timing.py`: bar (b).
- `probes/floor.py`: what remains of an indexed lookup once the fences and counts are removed.
- `probes/write_cost.py`, `probes/rebuild.py`: bar (c).
- `results/`: every number above, as JSON.
