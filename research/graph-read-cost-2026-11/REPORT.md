# P8 report: why a read on a real graph pack costs three times a read on a flat pack

Date: 2026-11-12
Branch: `research-graph-read-cost-2026-11`, worktree `~/Sites/prism-graph-read`, cut from `origin/main` at
`3fc87120` ("Cut read path cost with SQL literals and a one-row profile lookup (#563)").
All work is under `research/graph-read-cost-2026-11/`. Nothing in `src/` changed.

## Verdict

The extra time is one stage: the graph candidate branch of `generate_candidates`. It is 61.8% of a read on the
m3 pack and 22.1% on the flat pack, and it carries 190 of the 229 ms p50 gap between the two.

Inside that branch, the cost is what the walk does with 84,980 edges and what Python then does with its rows. A
question with 25 seeds reaches **38,140 nodes** and the stage keeps **150**. Of the walk's 137.1 ms, the SQL fetch
is about 41 ms, and the decode of those 38,140 rows into `WalkedNode` records is about **89 ms**. The seed reach
count that runs after it costs a further 92.8 ms, and its own link read is 36.5 ms of that.

One cheaper shape, measured interleaved against the current path on the same pack, same 20 questions, 3 rounds,
pair order flipped each pair:

| | p50 | p95 |
|---|---|---|
| current path | 381.5 ms | 529.1 ms |
| cheaper shape | 230.0 ms | 388.8 ms |
| difference | **151.5 ms** | **140.3 ms** |

Load 3.80 to 3.88 before, 3.93 to 5.31 after. The median of the 60 paired differences is 114.8 ms.

**All 60 pairs returned the same ranked ids, the same scores, the same packed ids and the same context digest.**
The 20 pairs that fetched a receipt had the same receipt digest too.

That shape does not need a new column and does not need a new index, so **it costs nothing on the write path**.
The verdict is: worth doing, in the graph stage only, and it should be done in two steps, the walk cut first
because it alone is a third of the branch and needs no new read.

## Bar 1: the breakdown

Probe: `probes/stages.py`. Results: `results/stages-m3.json`, `results/stages-flat.json`. Both packs, 20 questions,
backlog 0, 3 rounds, 60 reads each, one warm-up read first. Every stage `retrieve()` runs is wrapped and each
reports its own time, which is its inclusive time minus the children it owns. The graph, vector, lexical and
pinned branches run at once inside one `asyncio.gather`, so those four are reported apart and never subtracted.

Machine load: m3 run 2.97 to 3.25 before, 2.95 to 3.24 after. Flat run 3.04 to 3.25 before, 3.11 to 3.26 after.

### Totals

| pack | p50 | p95 | mean | reads |
|---|---|---|---|---|
| `m3-pack`, 20,500 facts, 84,980 edges | **342.7 ms** | 469.1 ms | 309.5 ms | 60 |
| `n20000-s1`, 20,000 notes, 0 edges | **113.2 ms** | 144.6 ms | 113.8 ms | 60 |

The p50 gap is **229.5 ms**. The brief's 650.3 and 205.6 ms were measured by P6 under a different load; the shape
here is the same and the size is smaller.

### Every stage, m3 pack

Own time per read. Shares divide by 320.7 ms, the sum of the sequential stages.

| p50 | p95 | share | stage |
|---|---|---|---|
| 232.36 | 357.24 | 61.8% | `S2-3 generate_candidates` |
| 41.46 | 77.90 | 14.4% | `retrieve: pipeline.retrieve` (own) |
| 21.74 | 48.77 | 7.5% | `S6 pack_context` |
| 13.94 | 15.01 | 4.3% | `S1b current_state_observations` |
| 12.69 | 13.12 | 3.9% | `retrieve: prepare_retrieval` |
| 12.57 | 41.96 | 4.7% | `S5 score_and_rank` |
| 2.03 | 2.39 | 0.9% | `S2-3 merge_candidates` |
| 1.51 | 1.98 | 0.5% | `S5.4 select_candidates` |
| 1.46 | 1.98 | 0.9% | `S5 rrf relevance` |
| 0.84 | 1.28 | 0.3% | `retrieve: ranking profile lookup` |
| 0.48 | 0.53 | 0.1% | `S5.5b session_context` |
| 0.43 | 0.52 | 0.1% | `S3.5 filter_candidate_types` |
| 0.27 | 0.31 | 0.1% | `retrieve: queue.debt` |
| 0.17 | 0.21 | 0.1% | `S5.5e claim folding` |
| 0.17 | 0.19 | 0.1% | `S4 filter_epistemic` |
| 0.09 | 0.66 | 0.2% | `S1 analyze_query` |

`S1c owner_profile`, `S5a reranker`, `S5.5c episode_context`, `S5.5d` evidence projection and augment, `S5.5f`
current state, `S5.5g` owner profile and `S5.6` replaced sources did not run on these packs, so they are absent
rather than zero.

### Every stage, flat pack

Shares divide by 119.0 ms.

| p50 | p95 | share | stage |
|---|---|---|---|
| 37.23 | 67.47 | 34.9% | `retrieve: pipeline.retrieve` (own) |
| 25.57 | 29.20 | 22.1% | `S2-3 generate_candidates` |
| 17.31 | 20.06 | 15.7% | `S6 pack_context` |
| 12.51 | 39.30 | 13.1% | `S5 score_and_rank` |
| 6.65 | 6.73 | 5.6% | `S1b current_state_observations` |
| 1.62 | 27.44 | 2.8% | `S2-3 merge_candidates` |
| 1.15 | 1.45 | 1.0% | `S5.4 select_candidates` |
| 1.15 | 1.24 | 1.0% | `retrieve: prepare_retrieval` |
| 1.13 | 1.54 | 1.7% | `S5 rrf relevance` |
| 0.67 | 1.22 | 0.6% | `retrieve: ranking profile lookup` |
| 0.39 | 0.47 | 0.3% | `S5.5b session_context` |
| 0.32 | 0.40 | 0.3% | `S3.5 filter_candidate_types` |
| 0.24 | 0.26 | 0.2% | `retrieve: queue.debt` |
| 0.14 | 0.17 | 0.1% | `S4 filter_epistemic` |
| 0.08 | 0.64 | 0.5% | `S1 analyze_query` |

### The difference, stage by stage

| stage | m3 | flat | difference |
|---|---|---|---|
| `S2-3 generate_candidates` | 232.36 | 25.57 | **+206.79** |
| `retrieve: pipeline.retrieve` own | 41.46 | 37.23 | +4.23 |
| `S6 pack_context` | 21.74 | 17.31 | +4.43 |
| `S1b current_state_observations` | 13.94 | 6.65 | +7.29 |
| `retrieve: prepare_retrieval` | 12.69 | 1.15 | **+11.54** |
| `S5 score_and_rank` | 12.57 | 12.51 | +0.06 |
| everything else | | | about +0.6 |

Two stages carry it. The graph candidate branch is +206.8 ms and `prepare_retrieval` is +11.5 ms. The pipeline's
own time is larger on m3 but the two packs return different numbers of results, so that is the result size and
not the edges.

`prepare_retrieval` reads 12.69 ms on m3 and 1.15 ms on the flat pack. That is the privacy fence's node count
growing with the store, already named in P7's report: it rescans `nodes` for a marker row.

### The four branches of the gather, m3 pack

The gather's wall time is 234.4 ms. The four branches overlap, so their times add to more than that.

| inclusive p50 | inclusive p95 | of gather wall | branch |
|---|---|---|---|
| 216.45 | 335.08 | 92.3% | graph candidates |
| 174.85 | 213.46 | 74.6% | vector candidates |
| 4.77 | 6.38 | 2.0% | pinned candidates |
| 3.04 | 4.34 | 1.3% | lexical candidates |

The vector branch is 174.9 ms on both packs. It is a local embedding plus a USearch search, and the flat pack
pays the same, so it is not part of the gap. The graph branch is the wall on m3: it is the longest of the four and
the gather waits for it.

Candidates kept per read on m3: GRAPH 150, VECTOR 500, LEXICAL 500, PINNED 0. Results returned p50 998 on m3 and
817 on the flat pack.

## Bar 2: the top cost, named

Probe: `probes/graph_cost.py`. Result: `results/graph-cost-m3.json`, one round, 20 questions, load 3.00 to 3.15.
12 of the 20 questions find seeds; the other 8 return before reading anything and cost 1.8 to 4.3 ms.

### What the branch does with 84,980 edges

| part | p50 |
|---|---|
| `_find_seed_entities` | 3.29 ms |
| `walk_from_seeds` | 137.11 ms |
| `_seed_reach`, of which `walk_links` | 92.78 ms (36.54 ms) |
| `get_nodes`, the hydration of the kept nodes | 32.11 ms |
| whole branch | 216.45 ms |

Seeds per seeded question: 12, 21, 24, 25 or 44. Rows the walk returns: **38,140 or 38,520**, every seeded
question. Rows kept: **150**. `walk_links` reads **44,734 links** over **3,423 nodes** (p50; it runs on 9 of the 12
seeded questions, and the other 3 skip the reach count). The `get_nodes` row above is every `get_nodes` call in a
read, about 1.6 calls and 1,064 rows, not only the graph stage's: the flat pack pays 12.2 ms for it with no walk at
all. The graph stage's own hydration of its 150 kept nodes is about 7.8 ms (`results/parts-m3.json`).

### The walk, split into SQL and Python

The walk's own SQL was replayed with the same seeds and parameters. Nothing is re-implemented: the text comes from
`prme.storage.seed_walk.seed_walk_sql` and the decode is `walked_nodes` itself.

| part | p50 | p95 | rows |
|---|---|---|---|
| SQL fetch | 38.7 ms | 43.0 ms | 38,140 |
| `walked_nodes` decode | **85.2 ms** | 90.4 ms | 38,140 rows to 38,140 records |

So of the walk's 137.1 ms, **about 39 ms is SQL and about 85 ms is Python**, and about 13 ms is the rest of the
call. The stage's filters and sort over the decoded rows come after the walk and cost a further 20.4 ms
(`results/parts-m3.json`). The decode is 62% of the walk and 39% of the whole branch.

The decode is expensive because every row becomes a dataclass with a `NodeType`, a `Scope`, three `datetime`
objects and sometimes parsed metadata, and then 37,990 of those records are thrown away.

### Depth

Two questions opened in full:

| question | seeds | rows at depth 1 | depth 2 | depth 3 | kept at depth 1 | kept at depth 3 |
|---|---|---|---|---|---|---|
| red mug | 44 | 3,288 | 120 | 34,732 | 14 | 136 |
| laundry room | 24 | 7,515 | 500 | 30,505 | 31 | 119 |

Depth 3 carries about 90% of the rows, and most of the kept set is depth 3. The walk cannot be shortened to depth
1 to save the cost without changing the answer.

### What is not the graph

The vector branch runs at 174.9 ms on both packs, so it is not part of the gap. `pack_context` is 21.7 against
17.3, `score_and_rank` is the same on both. No other stage moves by more than 12 ms.

## Bar 3: one cheaper shape, measured

Probe: `probes/interleave.py`. Result: `results/interleave-m3.json`. Prototype: `probes/cheap_graph.py`.

### The shape

One query replaces the walk read. It is the same walk SQL, wrapped:

```
WITH b(node_id, depth, node_type, user_id, scope, confidence, created_at, valid_from, valid_to, metadata) AS (<seed_walk_sql>),
ranked AS (
    SELECT *, ROW_NUMBER() OVER (ORDER BY confidence DESC, created_at DESC, node_id ASC) AS rn
    FROM b
    WHERE NOT <is_unindexed_summary> AND scope IN (...) AND <temporal window>
)
SELECT node_id, depth, rn <= 150 AS kept, node_type, CASE WHEN node_type = 'entity' THEN metadata END
FROM ranked
```

Two things change.

1. **The filter, the order and the cut move into SQL.** The stage's Python filters are the same conditions,
   written over the walk's own columns: an unindexed hierarchical summary is not a candidate, a node outside the
   requested scopes is skipped, a node outside the temporal window is skipped except an entity or a preference.
   The order is the stage's own key.
2. **Only the kept rows are decoded.** All 38,140 rows still cross the wire, but as five narrow columns, and the
   37,990 losers are never turned into records.

Everything else stays: the same seeds, the same `get_nodes` hydration, the same `_seed_reach`.

The measured parts, `probes/parts.py`, `results/parts-m3.json`, 8 seeded questions, 3 repeats:

| part | current | cheap |
|---|---|---|
| walk SQL | 40.8 ms | 52.0 ms (the window is in it) |
| decode / split | 89.7 ms | **4.8 ms** |
| order and cut | 20.4 ms | inside the SQL |
| hydrate | 7.8 ms | 7.8 ms |
| reach count | 114.9 ms | 114.9 ms |

Load 5.31 to 7.17 during this probe. The window costs 4 to 11 ms over the plain walk (4.0 ms in
`results/index-check-m3.json`, 11.2 ms here). The decode falls from 89.7 to 4.8 ms and the Python sort goes away
with it. The reach count is unchanged, because it is the same call on both sides. Hydrate and reach are one set of
numbers shared by both sides, so they appear once in the JSON with 48 samples.

These parts add to about 94 ms saved per seeded read (110.1 ms of decode and sort against 4.8 ms of split, less
11.2 ms more SQL). The end-to-end paired saving on the seeded questions is mostly larger, 92 to 178 ms per question (median 134 ms). The
rest was not attributed; it is likely the cost of creating and then collecting 38,000 records, but that was not
measured.

### The interleaved measurement

Both paths run in one process on one pack copy. Each question is read twice in a row, once through each path, and
the order inside the pair flips with every pair. 20 questions, 3 rounds, 60 pairs, 120 reads.

| | p50 | p95 | mean |
|---|---|---|---|
| current | 381.5 ms | 529.1 ms | 330.7 ms |
| cheap | **230.0 ms** | **388.8 ms** | 247.7 ms |

Load: 3.80, 3.88, 3.83 before; 5.31, 4.18, 3.93 after (1, 5 and 15 minute averages). Per question, the 12 seeded
questions save 92 to 178 ms at the median of their 3 pairs, and the 8 questions with no seed move between -55 and
+46 ms, which is the noise of a 3-sample median at this load: those 8 run the same code on both sides.

### Identity

Every pair compared the ranked ids with their composite scores, the packed ids, the context digest and the token
count. The 20 pairs of round 0 also compared the stored receipt digest with the request id replaced.

| field | same | pairs |
|---|---|---|
| ranked ids and scores | **60** | 60 |
| packed ids | **60** | 60 |
| context digest | **60** | 60 |
| tokens used | **60** | 60 |
| receipt digest | **20** | 20 |

Differences: **0**.

### The shapes the 20 questions do not reach

The 20 questions all run with one scope and no temporal window. The prototype was compared against the current
stage on the remaining shapes, on the same pack:

| case | current | cheap | same |
|---|---|---|---|
| `scope=None` | 150 | 150 | yes |
| `scope=None`, `seed_reach=False` | 150 | 150 | yes |
| two scopes | 150 | 150 | yes |
| `time_to` only | 150 | 150 | yes |
| `time_from` and `time_to` | 150 | 150 | yes |
| a question with no seeds | 0 | 0 | yes |

Compared on the candidate ids, their graph proximity scores and their seed reach counts. Every case matched.

### The flat pack control

`probes/flat_check.py`, the same harness, 2 rounds on `n20000-s1`. The flat pack has no edges, so every question
finds no seed and the stage returns before reading. Result: `results/interleave-flat.json`.

| | p50 | p95 | mean |
|---|---|---|---|
| current | 111.8 ms | 138.3 ms | 111.5 ms |
| cheap | 116.6 ms | 155.2 ms | 117.1 ms |

Load 8.36, 4.91, 4.20 before; 7.97, 4.94, 4.22 after. 40 of 40 pairs had the same ranked ids, packed ids, context
digest and tokens, and 20 of 20 the same receipt digest. Both sides run the same code here, so the 4.8 ms p50
difference is the noise floor of this harness under that load, not a cost of the shape.

## Bar 4: verdict, and what it costs to write

**In the engine, in the graph stage, not as a new stored column.** The change is a rewrite of the walk's read in
`_generate_graph_candidates` and nothing else. No schema change, no new index, no new write path.

### Write cost

There is none on the write path, because the shape adds no column, no index and no write. No `store()` was timed,
because nothing on the write side changes. What was measured is whether the read would need an index, which is the
one thing that would add a write cost. Probe: `probes/index_check.py`,
result: `results/index-check-m3.json`.

| read shape | p50 |
|---|---|
| walk alone | 41.1 ms |
| walk plus the window, no index | 45.1 ms |
| walk plus the window, with an index on the candidate order | 47.0 ms |

5 repeats each, 25 seeds, 38,140 rows, load 7.17 to 8.36. The window costs about 4 ms, and the index on
`(user_id, lifecycle_state, confidence, created_at)` does not help: it reads 1.9 ms slower, which is inside the
noise at this load. DuckDB scans the walk's rows once either way, so an index on `nodes` has nothing to shorten. So the recommended
change adds no column, no index, no `UPDATE` and no extra row write. P7's 0.209 ms a fact for an INSERT column and
3.871 ms for an UPDATE do not apply here.

### The smallest engine change

Replace the walk read in `candidates._generate_graph_candidates` with the one query above, and let the existing
`get_nodes` hydration and the existing `_seed_reach` call stay exactly where they are. In the prototype that is
`cheap_graph.cheap_graph_candidates`; the stage's other three reads and its return shape do not change.

The one detail a real change has to keep is the stand-in record for the reached nodes that the seed reach count
walks over. `_seed_reach` reads only `node_type` and `metadata` from those values, and it never reads their ids, so
a non-entity can share one record instead of building 38,000 objects. The prototype does that, and the 4.8 ms
split in the `parts` table includes building those shared records.

### What it does not fix

The reach count is unchanged at about 115 ms, and it is now the largest single item in the branch. It reads
44,734 links over 3,423 nodes through `walk_links`, then runs one breadth-first walk per seed in Python. That is a
second, larger piece of work and it should not be bundled with this one. A shape for it is to make the same walk
SQL return, per reached node, how many seeds reach it within 3 hops; the seeds and their depths are already in
the SQL's own `seed_depth` CTE, so the count is a join away. That change would need the owner's read of whether a
SQL seed count gives exactly the same `graph_seed_reach` tuple (count, total hops, fewest hops) that `_seed_reach`
gives, and it was not prototyped here.

The vector branch, 174.9 ms on both packs, is the next largest stage and is untouched.

## Limits, stated

- **One machine, one run per number.** Load average is beside every number. The interleave run was taken at load
  3.8 to 5.3, not at an idle machine, and the probes after it at up to 8.4.
- **The numbers are on the pre-#564 and pre-#565 side.** Both were still open when this branch was cut from
  `origin/main` at `3fc87120`. #564 changes `result_limit` and the vector and lexical k for a robot profile; #565
  turns `memory_mode=address_first` on by default. Both are read-time only. The stage measured here is in
  `generate_candidates` and neither option changes the graph branch's shape, but the totals would move.
- **The m3 pack is synthetic**, built by P2's write-rate stream through `ingest()`. It is the only pack in the
  project with a real edge population.
- **The 20 questions are 10 object questions and 10 read-cost diagnostic shapes.** They are not a benchmark. The
  identity check says the output is unchanged on these 20; it is not a claim about every possible question.
- **The prototype replaces the stage, it does not live in `src/`.** It is called by monkeypatching
  `candidates._generate_graph_candidates` inside the probe. `src/` on this branch is unchanged.
- **The prototype only covers DuckDB.** The SQL is written for DuckDB and uses `epoch_us`. PostgreSQL needs the
  same change with its own JSON and time functions, and is untested here.
- **A protected store is not covered.** The prototype reads `graph_store._conn` and `_conn_lock` directly, which an
  admitted graph view does not expose. A real change would add the read as a method on both facades, as P7's
  report says for the indexed page.
- **No model calls were made or needed anywhere in P8.**

## What failed and what was not done

- **A timing run of the interleave and a second probe ran at the same time**, which spoils a load-sensitive
  measurement. Both were stopped and the whole chain (interleave, parts, index check, flat control) was re-run one
  after another. The numbers above are from that serial run. Two earlier interleave runs are not used: a first full
  run at 364.4 against 256.1 ms p50 (load 4.57 to 3.89), and the overlapped run at 369.1 against 225.6 ms. Both
  also had 60 of 60 pairs identical.
- **A reduced-column variant of the walk was tried and rejected.** Selecting `NULL` for scope and the times made
  `walked_nodes` raise `ValueError: None is not a valid Scope`. Keeping only ids and depths was measured at 33 to
  35 ms of SQL against 38 to 43 for the full walk, which is about 4 ms for the whole branch's read, and it cannot
  feed the seed reach count, which needs the metadata. It was not worth another read.
- **The seed reach count was not prototyped.** It is now the largest item in the branch and it deserves its own
  measurement rather than a guess.
- **`prme rebuild`, the full test suite and any `src/` change were not run.** This branch changes no `src/` file,
  so there is nothing for the suite to cover that `origin/main` at `3fc87120` does not already cover. If the shape
  moves into the engine, the suite is needed there.

## Files

- `probes/stages.py`: bar 1, every stage of `retrieve()` with own time and share.
- `probes/graph_cost.py`: bar 2, the graph branch row by row, with the walk's SQL and decode timed apart.
- `probes/cheap_graph.py`: the prototype, the SQL-cut walk and the stage that uses it.
- `probes/interleave.py`: bar 3, the two paths interleaved, with the identity comparison on every pair.
- `probes/flat_check.py`: the flat-pack control.
- `probes/parts.py`: the two paths part by part, which is where the 90 ms decode and the 4.8 ms split come from.
- `probes/index_check.py`: bar 4, whether a new index helps the read. It does not time a write.
- `results/`: every number above, as JSON.

## Scratch usage

- `/tmp/p8/m3-pack` (950 MB) and `/tmp/p8/n20000-s1` (74 MB) are the packs. `/tmp/p8/work/` holds one copy per
  probe run at about 950 MB each, plus a smaller one per flat run. Only `results/` in the repo is needed.
