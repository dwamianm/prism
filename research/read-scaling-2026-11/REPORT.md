# Read scaling on the stated-fact packs: report

Task P3, prme-manager, 2026-11-12. Diagnosis only: no engine change. Branch `research-read-scaling-2026-11`, cut from
`0d44a7d6` (task P2). Every read ran on a scratch copy of a kept P2 pack, with the opportunistic organizer off and no
write running. The questions are the five "Where is the {object} now?" questions P2's `read_latency.py` asked, after
one warm-up question.

## Answer

**The graph walk grows faster than the store, and nothing else does.** On these packs one `retrieve()` spends 63% of
its time in `_generate_graph_candidates` at 400 stored, 88% at 4,000 and 99% at 20,500. The walk reads about 500,000
rows at 20,500 stored, builds a full record from each, and keeps 150.

Task 18's packs (`read-path-cost-2026-11`) hold no entities and no edges, so that study never ran the graph walk. That
is the whole 50 times gap.

The `455b6b7a` fixes do not change it: 19.0 s at 20,500 stored with them, 18.5 s without.

## 1. Time per stage

Median of five reads, ms per read. `generate_candidates` runs its four searches at the same time, so the parts add up
to more than the stage. Results: `results/stages-p2code.json`, cProfile: `results/profile-p2code-*.txt`.

| Stored | `retrieve()` | Graph search | Vector search | Packing | Scoring | Pinned | Lexical |
|---|---|---|---|---|---|---|---|
| 400 | 372 | 235 | 78 | 19 | 14 | 6 | 2 |
| 4,000 | 1,340 | 1,174 | 110 | 20 | 16 | 7 | 3 |
| 20,500 | 18,519 | 18,329 | 328 | 21 | 17 | 7 | 6 |

Every stage apart from the two searches stays flat. At 20,500 stored, cProfile puts 16.7 s of one 19.3 s read in the
event loop's wait for the worker thread that runs the walk's SQL and builds its rows.

The vector search also grows (78 to 328 ms), but it shares one DuckDB connection and one event loop with the graph walk
while both run. Its own cost was not separated here.

## 2. The stage that grows, and why

### The pack shape

Row counts from scratch copies (`rows.py`):

| Stored | Entities | Facts | Notes | Edges (`mentions`, `has_fact`, `supersedes`) | Entity degree, mean / max |
|---|---|---|---|---|---|
| 400 | 408 | 400 | 400 | 1,200 / 400 / 0 | 3.9 / 20 |
| 4,000 | 620 | 4,000 | 4,000 | 12,000 / 4,000 / 115 | 25.8 / 102 |
| 20,500 | 620 | 20,500 | 20,500 | 61,500 / 20,500 / 2,980 | 132.3 / 434 |

The world has 500 objects and 120 places. Each fact moves one object, so every place entity links to every fact and
note that ever named it. The place entities become hubs whose degree grows with the store.

### The walk

Counts from one read per question (`walk_counts.py`, `results/walk-p2code.json`):

| Stored | Seeds | Walk queries | Rows returned, all walks | Rows, largest walk | Walk time, sum |
|---|---|---|---|---|---|
| 400 | 24 to 31 | 24 to 31 | 325 to 418 | 33 to 43 | 0.18 to 0.24 s |
| 4,000 | 44 | 44 | 23,995 to 25,180 | 958 to 1,084 | 0.89 to 0.97 s |
| 20,500 | 44 | 44 | 478,763 to 514,444 | 13,666 to 15,295 | 14.8 to 15.9 s |

1. **Many seeds.** `graph_seed_words` (on by default) makes every question word a seed when it is a whole word of an
   entity name (`src/prme/retrieval/candidates.py:160-171`). "Where is the yellow bowl now?" seeds 44 entities: the 20
   yellow objects and the 25 bowls, one counted twice.
2. **One recursive walk for each seed** (`candidates.py:309-315`, which calls
   `DuckPGQGraphStore._get_neighborhood_with_depth_sync`, `src/prme/storage/duckpgq_graph.py:1939-2110`), three hops
   deep (`graph_max_hops=3`). Hop 1 reaches the object's facts and notes. Hop 2 reaches the places it was on. Hop 3
   reaches every fact and note of each of those places. At 20,500 stored, 497,048 of 502,012 rows come from hop 3.
3. **Growth faster than the store.** One walk reaches about (places the object visited) times (records per place).
   Both numbers grow with the store until an object has visited all 120 places. Rows per read rise 63 times from 400 to
   4,000 stored (10 times more facts) and 20 times from 4,000 to 20,500 (5.1 times more facts).
4. **Each row becomes a full record.** The walk's SQL returns `n.*` for every row (`duckpgq_graph.py:2072`), and
   `_row_to_node` builds a `MemoryNode` from each (`duckpgq_graph.py:2106`). Walks repeat the same node: 44 walks
   return about 500,000 rows for 38,184 distinct nodes, which is 92% of the 41,620 nodes in the store.
5. **150 are kept.** `candidates.py:352-360` sorts every reached node by confidence and creation time and keeps
   `graph_max_candidates=150`. Hop depth is not in that sort key.
6. **The seed reach read** (`_seed_reach`, `candidates.py:228-229`) then reads the edges of 3,395 to 3,646 nodes at
   20,500 stored, with each id bound twice as a parameter (`duckpgq_graph.py:1560-1567`). That read takes 1.1 to 1.3 s.
   It is the same bound-parameter cost that task 18 removed from two other queries.

Where the walk time goes (`walk_split.py`, mean of two reads):

| Stored | Walk total | Building records (`_row_to_node`) | Owner view | SQL and fetch |
|---|---|---|---|---|
| 4,000 | 759 ms | 252 ms (24,937 rows) | 2 ms | 505 ms |
| 20,500 | 15,342 ms | 9,156 ms (490,388 rows) | 105 ms | 6,081 ms |

The same 44 queries returning only id and depth (`narrow_sql.py`) take 3,643 ms at 20,500 stored, against 6,228 ms for
`n.*`. So most of the SQL cost is the walk itself, run once per seed.

### Why task 18 did not see it

Task 18's packs are built with `ingest_fast_many()` and no extraction. They hold only notes: 3,000 or 20,000 nodes,
no entity, no edge. The graph search finds no seed and returns at once (`candidates.py:291-292`). Task 18's 317 ms
p95 at 20,000 memories is the read without a graph walk. P2's stated facts carry assertions, so each fact adds two
entity links and a fact node, and the graph walk runs.

### Chat stores

The LoCoMo `ingest()` packs in `data/mid-tier-v1/builds/main-0.17.0a6-locomo` (`locomo_shape.py`) hold 225 to 499
entities and 1,336 to 2,252 edges. Most entities have degree 3 to 5, but each pack has hubs: its two speakers, with
degree 299 to 610 in the four packs `locomo_hubs.py` named (for example Caroline 479 and Melanie 319 in conv-26). The
neighborhood walk does not stop at any hub (only `_seed_reach` declines to expand through the owner's entity), so any
seed within two hops of a speaker reaches most of the store at hop 3. These stores are small, so the cost stays low there. A large chat store
would likely pay the same growth. That is an inference from the shape; it was not timed.

## 3. The `455b6b7a` code at 20,500

The `src/` changes of `455b6b7a` (three files: vector allowed keys and node ids as SQL literals, one-row
ranking-profile lookup) applied cleanly on `0d44a7d6` in a temporary worktree. Same script, same packs
(`results/stages-fix455*.json`):

| Stored | Without `455b6b7a` | With `455b6b7a` | Graph search, with |
|---|---|---|---|
| 400 | 0.372 s | 0.247 s | 147 ms |
| 4,000 | 1.340 s | 1.271 s (rerun; the first run gave 1.66 s, two reads at 2.35 s under load) | 1,128 ms |
| 20,500 | 18.519 s | 19.015 s | 18,864 ms |

The fixes cut the vector search at 400 and 4,000 stored (78 to 12 ms, 110 to 25 ms), as task 18 found. They do not
touch the graph walk, so they do not explain the growth. The operations table (61,500 rows at 20,500 stored) is not
the cause either: the profile lookup fix made no difference there.

## 4. Candidate fix (not built)

**One walk for all seeds, ids only, ranked and capped in SQL.**

- Replace the per-seed loop at `candidates.py:309-349` with one recursive query that starts from every seed at once
  and returns `(id, fewest hops)` only. `UNION` then drops a node that two seeds reach at the same depth, so the walk
  does each node once per depth instead of once per seed per depth.
- Apply the same filters in SQL (owner, lifecycle, valid-at, scope, unindexed summaries, the temporal window with its
  entity and preference exemption). Sort by `confidence DESC, created_at DESC` and keep 150 there, then build
  `MemoryNode` records for the 150 only.
- Pass the ids to `_seed_reach`'s edge read as SQL literals, as `455b6b7a` does for `get_nodes`.

Measured on the SQL alone (`multi_source.py`, third run, "yellow bowl" and "paper tape measure"): one multi-source walk
takes 36 ms at 4,000 stored (7,851 nodes reached) and 608 ms at 20,500 (38,184 nodes). The 44 walks take 505 ms and
6,081 ms of SQL now, plus the record building. The full read was not timed with this change.

What the fix must prove, with the fingerprint check of `read_cost.py` (ranked ids and scores, packed ids, context,
receipt):

- The same 150 nodes with the same proximity. A seed that another seed reaches needs its depth from the other seeds,
  not depth 0.
- The same order on ties. The sort today is stable over the walk's row order, which SQL does not fix, so ties may
  already differ between runs. That belongs with task 20's tie-break.
- PostgreSQL has its own walk and needs the same change.

**Limit.** Even this fix leaves a three-hop walk that reaches 92% of the store through the place hubs. It will not give
50 ms at 20,000 facts. A bounded read must not expand through hub nodes, as `_seed_reach` already declines to expand
through the owner's entity, or must use fewer hops. That changes results, so it needs the channel's gates. Not
measured here.

## Limits of this run

- One run per measurement on a busy laptop (load average 4.6 to 6.4 from other sessions). Apple M5 Pro, Python
  3.11.6, DuckDB 1.4.4.
- The 20,500 pack is P2's M3 pack, written with the prototype entity lookup. The prototype does not change what is
  stored (P2, bar 3 at 400 and 4,000) or how reads run.
- `rows.py`, `multi_source.py`, `locomo_shape.py` and `locomo_hubs.py` read scratch copies under `/tmp/p3/`.
- Cause found in the first attempt; no fix was built.

## Commands

```sh
cd ~/Sites/prism-write-rate
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY= PYTHONPATH=.
R=research/read-scaling-2026-11
.venv/bin/python $R/profile_read.py --sizes 400 4000 20500 --label p2code --out /tmp/p3/out
.venv/bin/python $R/walk_counts.py --out /tmp/p3/out
.venv/bin/python $R/walk_split.py 4000; .venv/bin/python $R/walk_split.py 20500
.venv/bin/python $R/narrow_sql.py 4000; .venv/bin/python $R/narrow_sql.py 20500
.venv/bin/python $R/multi_source.py /tmp/p3/rows/20500/memory.duckdb paper tape measure
# Step 3: 455b6b7a's src changes on this tree, in a temporary worktree
git diff e10ca299 455b6b7a -- src > /tmp/p3/455b6b7a-src.patch
git worktree add --detach /tmp/p3/patched 0d44a7d6 && (cd /tmp/p3/patched && git apply /tmp/p3/455b6b7a-src.patch)
ln -s ~/Sites/prism-write-rate/data /tmp/p3/patched/data
(cd /tmp/p3/patched && PYTHONPATH=/tmp/p3/patched/src:/tmp/p3/patched ~/Sites/prism-write-rate/.venv/bin/python \
  ~/Sites/prism-write-rate/$R/profile_read.py --label fix455 --scratch /tmp/p3/scratch-fix --out /tmp/p3/out-fix)
```
