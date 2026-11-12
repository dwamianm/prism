# Read cost of chat stores: report

Task P4, prme-manager, 2026-11-12. Measurement only: no engine change. Branch `research-chat-read-cost-2026-11`, cut
from `origin/main` at `e10ca299` (0.17.0a7). **DuckDB backend only**; PostgreSQL was not measured.

## Answer

**Yes. The hub walk already slows chat stores that have named speakers, and it grows with the store.**

- On the 10 LoCoMo `ingest()` packs (369 to 689 turns, 915 to 1,801 nodes), one `retrieve()` takes 313 to 472 ms
  (median) and 449 to 962 ms (p95) in run 2. The graph stage is 63 to 73% of that time. Each pack has two hubs, its two
  speakers, with 406 to 610 links. The walks return 2,232 to 6,086 rows (median per read) and reach 72 to 86% of the
  store, and 150 are kept.
- On grown copies of two LoCoMo packs (below), read time rises with store size. Fit over all 18 LoCoMo packs:
  median 322 ms + 105 ms per 1,000 nodes, p95 194 ms + 325 ms per 1,000 nodes.
- **A read passes 1 s at about 2,500 nodes at p95 (about 1,200 turns), and at about 6,500 nodes at the median
  (about 3,200 turns).** Interpolated per conversation: p95 at 2,136 nodes (conv-47, 866 turns) to 2,790 nodes
  (conv-42, 1,319 turns); median at 4,191 nodes (conv-42, 2,095 turns) to 6,353 nodes (conv-47, 3,094 turns).
- At 16 times (18,583 to 21,361 nodes, 10,064 to 11,024 turns) one read takes 2.2 to 2.3 s (median) and 4.4 to 8.5 s
  (p95). The walk alone takes 1.8 to 1.9 s (median), and the largest read walks 352,211 rows.
- The 12 LongMemEval-S `ingest()` packs (413 to 545 turns, 1,882 to 2,490 nodes) are not slowed today. They have no
  speaker hub (max entity degree 18 to 38, one pack 194). A read takes 254 to 293 ms (median). The walk takes 6 to 20 ms
  and returns 11 to 119 rows (median).

## Method

- Packs: scratch copies of `data/mid-tier-v1/builds/main-0.17.0a6-locomo` and `-longmemeval` (built through
  `ingest()` with the 0.17.0a6 defaults). The saved builds were never opened.
- Engine: `gate_config()` of the offline evidence gate: current defaults, organizer off
  (`opportunistic_enabled=False`), no environment, providers blocked.
- Questions, per pack: one warm-up read, then 20 timed reads, each at the pack's reference time.
  - LoCoMo: the conversation's own questions in the mid tier's seeded hash order (seed 20261019); the first is the
    warm-up.
  - LongMemEval-S: each pack holds one question's history, so the pack's own question comes first, then 19 other
    LongMemEval-S questions in the seeded order. The 20th other question is the warm-up.
- Timing (`chat_read_cost.py`): wrappers time `_generate_graph_candidates`, `_find_seed_entities`, `_seed_reach`,
  `_generate_vector_candidates`, and the store's `_get_neighborhood_with_depth_sync` (the walk's SQL plus building a
  `MemoryNode` from each row). Rows walked is the sum of rows that the per-seed walks returned. Nodes reached is the
  count of distinct ids among them. Kept is the length of the graph candidate list.
- The four searches run at the same time and share one DuckDB connection lock. So the graph stage, seed reach and
  vector times are wall times that include waiting for the lock. The walk time is measured inside the worker thread,
  so it is the walk's own cost. On LongMemEval-S, about 70 ms of the graph stage is that wait, not graph work.
- p95 is the nearest rank (the 19th of 20 reads).
- Machine: Apple M5 Pro, macOS 26.6.1, Python 3.11.6, DuckDB 1.4.4. Other sessions ran at the same time: load average
  2 to 5 for the base runs, 5 to 17 for the grown runs.

### Grown packs

`scale_pack.py` grows a copy of a LoCoMo pack k times (k = 2, 4, 8, 16) for conv-42 and conv-47:

- Every non-entity node (notes, facts, preferences, decisions) is copied k-1 times with new ids and a new session id.
- Every edge that touches a non-entity node is copied, with its non-entity ends moved to the copy.
- Entities are shared, because `ingest()` reuses an entity whose name and type match exactly.
- Then `rebuild_indexes()` rebuilds the vector and lexical indexes from the graph.

This models the same two people talking k times longer about the same things. Speaker hubs grow linearly, as they
do in a real chat, where every turn names its speaker. A real longer chat also adds new entities. Those spread the
topic links, so this model may overstate the growth of topic hubs. It does not overstate the speaker hubs. The copies
were checked: no dangling edge, no dangling `superseded_by`, no duplicate id.

## Per-pack table (base run 2, for task 21's "before")

Times in ms. Run 2 used the script as committed (`0ae25e1f`); results in `results/base-run2.json`. "Graph stage" is
`_generate_graph_candidates`; "Walk" is the per-seed neighborhood walk inside it.

| Pack | Scale | Turns | Nodes | Entities | Edges | Entity degree mean / p90 / max | retrieve() median / p95 ms | Graph stage median / p95 ms | Graph share | Walk median / p95 ms | Seed reach median ms | Vector median ms | Seeds median / max | Rows walked median / max | Nodes reached median | Kept median |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| conv-26 | 1 | 419 | 1,151 | 337 | 1,623 | 4.81 / 4.0 / 479 | 313 / 449 | 190 / 330 | 63% | 48 / 132 | 63 | 61 | 3.5 / 21 | 2,232 / 11,570 | 864 | 150 |
| conv-30 | 1 | 369 | 915 | 225 | 1,336 | 5.92 / 5.0 / 406 | 380 / 723 | 262 / 610 | 73% | 148 / 459 | 59 | 50 | 12 / 37 | 6,086 / 16,114 | 767 | 150 |
| conv-41 | 1 | 663 | 1,516 | 382 | 2,082 | 5.44 / 4.0 / 610 | 434 / 601 | 284 / 452 | 66% | 92 / 267 | 89 | 76 | 5 / 20 | 3,914 / 14,641 | 1,144 | 150 |
| conv-42 | 1 | 629 | 1,543 | 407 | 2,053 | 5.04 / 4.0 / 585 | 426 / 600 | 289 / 433 | 68% | 115 / 222 | 87 | 77 | 7 / 21 | 4,452 / 13,517 | 1,237 | 150 |
| conv-43 | 1 | 680 | 1,594 | 400 | 1,852 | 4.62 / 4.0 / 536 | 462 / 699 | 332 / 568 | 71% | 126 / 395 | 83 | 77 | 7 / 24 | 4,696 / 13,685 | 1,224 | 150 |
| conv-44 | 1 | 675 | 1,693 | 439 | 2,110 | 4.81 / 5.0 / 481 | 472 / 564 | 316 / 444 | 67% | 137 / 255 | 77 | 77 | 9 / 16 | 5,974 / 10,080 | 1,250 | 150 |
| conv-47 | 1 | 689 | 1,801 | 497 | 2,252 | 4.53 / 4.0 / 581 | 424 / 888 | 286 / 734 | 72% | 109 / 528 | 94 | 78 | 5 / 31 | 4,028 / 22,556 | 1,364 | 150 |
| conv-48 | 1 | 681 | 1,756 | 499 | 2,119 | 4.24 / 3.0 / 545 | 432 / 628 | 284 / 454 | 68% | 101 / 270 | 84 | 78 | 6 / 29 | 4,188 / 11,787 | 1,268 | 150 |
| conv-49 | 1 | 509 | 1,252 | 326 | 1,681 | 5.15 / 3.0 / 529 | 338 / 468 | 202 / 345 | 64% | 57 / 167 | 78 | 65 | 4 / 12 | 2,742 / 8,953 | 1,036 | 150 |
| conv-50 | 1 | 568 | 1,269 | 287 | 1,927 | 6.71 / 5.0 / 573 | 378 / 962 | 253 / 833 | 72% | 75 / 616 | 84 | 66 | 4 / 35 | 2,952 / 26,520 | 1,095 | 150 |
| 2bf43736 | 1 | 478 | 2,277 | 1,141 | 2,897 | 2.54 / 5.0 / 38 | 285 / 450 | 107 / 148 | 34% | 20 / 59 | 4 | 78 | 3.5 / 11 | 102 / 295 | 54 | 54 |
| 36580ce8 | 1 | 545 | 2,490 | 1,165 | 3,164 | 2.71 / 6.0 / 32 | 254 / 514 | 90 / 171 | 38% | 6 / 54 | 2 | 78 | 2 / 45 | 22 / 4,076 | 22 | 21.5 |
| 488d3006 | 1 | 509 | 2,354 | 1,179 | 2,819 | 2.39 / 4.2 / 31 | 268 / 514 | 99 / 192 | 32% | 10 / 98 | 3 | 80 | 3 / 35 | 44 / 720 | 44 | 44 |
| 54026fce | 1 | 472 | 2,211 | 1,116 | 2,501 | 2.24 / 4.0 / 21 | 272 / 460 | 102 / 162 | 35% | 8 / 70 | 3 | 78 | 2 / 15 | 28 / 383 | 28 | 27.5 |
| 6a1eabeb | 1 | 413 | 1,882 | 961 | 2,092 | 2.17 / 4.0 / 19 | 285 / 403 | 106 / 175 | 36% | 20 / 80 | 3 | 79 | 3 / 27 | 42 / 2,178 | 32 | 32.5 |
| 6b168ec8 | 1 | 482 | 2,401 | 1,247 | 2,867 | 2.3 / 4.0 / 18 | 277 / 498 | 99 / 168 | 31% | 14 / 58 | 3 | 79 | 3 / 12 | 63 / 222 | 41 | 41 |
| 6b7dfb22 | 1 | 471 | 2,103 | 1,040 | 2,518 | 2.42 / 5.0 / 18 | 269 / 408 | 90 / 135 | 26% | 8 / 43 | 2 | 78 | 2 / 11 | 11 / 245 | 11 | 11 |
| 77eafa52 | 1 | 527 | 2,222 | 1,109 | 2,628 | 2.37 / 4.0 / 23 | 271 / 462 | 102 / 170 | 34% | 11 / 78 | 4 | 80 | 3 / 13 | 55 / 329 | 55 | 55 |
| b01defab | 1 | 503 | 2,467 | 1,141 | 3,188 | 2.77 / 5.0 / 194 | 293 / 466 | 100 / 192 | 34% | 12 / 93 | 5 | 82 | 3 / 16 | 119 / 850 | 111 | 109 |
| e6041065 | 1 | 493 | 2,227 | 1,140 | 2,643 | 2.31 / 4.0 / 18 | 264 / 421 | 94 / 158 | 34% | 9 / 58 | 3 | 78 | 2 / 11 | 28 / 215 | 28 | 27.5 |
| gpt4_2655b836 | 1 | 465 | 2,066 | 1,047 | 2,289 | 2.19 / 4.0 / 21 | 278 / 434 | 97 / 174 | 34% | 10 / 76 | 2 | 79 | 2 / 22 | 43 / 323 | 43 | 43 |
| gpt4_b5700ca9 | 1 | 537 | 2,407 | 1,211 | 2,766 | 2.28 / 4.0 / 23 | 279 / 470 | 112 / 173 | 33% | 11 / 83 | 5 | 79 | 3.5 / 20 | 46 / 525 | 42 | 42.5 |

Run 1 (`results/base-run1.json`) had the same seeds and the same rows walked on every read. Its medians were within
8% of run 2 (-8.2% to +6.9%). Run 1 did not time the vector search.

## Grown packs

Same columns. Results in `results/grown-k{2,4,8,16}.json`. Turns are the source pack's turns times k.

| Pack | Scale | Turns | Nodes | Entities | Edges | Entity degree mean / p90 / max | retrieve() median / p95 ms | Graph stage median / p95 ms | Graph share | Walk median / p95 ms | Seed reach median ms | Vector median ms | Seeds median / max | Rows walked median / max | Nodes reached median | Kept median |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| conv-42 | 2 | 1,258 | 2,679 | 407 | 4,106 | 10.08 / 8.0 / 1170 | 723 / 962 | 518 / 784 | 73% | 192 / 372 | 185 | 90 | 7 / 21 | 8,690 / 26,795 | 2,270 | 150 |
| conv-47 | 2 | 1,378 | 3,105 | 497 | 4,504 | 9.06 / 8.0 / 1162 | 680 / 1,441 | 494 / 1,247 | 76% | 167 / 872 | 184 | 92 | 5 / 31 | 7,796 / 44,533 | 2,452 | 150 |
| conv-42 | 4 | 2,516 | 4,951 | 407 | 8,212 | 20.17 / 16.0 / 2340 | 1,139 / 1,742 | 865 / 1,552 | 82% | 385 / 802 | 341 | 88 | 7 / 21 | 17,165 / 53,351 | 4,336 | 150 |
| conv-47 | 4 | 2,756 | 5,713 | 497 | 9,008 | 18.12 / 16.0 / 2324 | 930 / 2,372 | 762 / 2,182 | 85% | 329 / 1,635 | 300 | 78 | 5 / 31 | 15,331 / 88,487 | 4,628 | 150 |
| conv-42 | 8 | 5,032 | 9,495 | 407 | 16,424 | 40.33 / 32.0 / 4680 | 1,726 / 2,502 | 1,506 / 2,321 | 88% | 768 / 1,518 | 605 | 77 | 7 / 21 | 34,115 / 106,463 | 8,468 | 150 |
| conv-47 | 8 | 5,512 | 10,929 | 497 | 18,016 | 36.25 / 32.0 / 4648 | 1,500 / 4,588 | 1,315 / 4,385 | 90% | 712 / 3,398 | 553 | 78 | 5 / 31 | 30,401 / 176,395 | 8,980 | 150 |
| conv-42 | 16 | 10,064 | 18,583 | 407 | 32,848 | 80.67 / 64.0 / 9360 | 2,347 / 4,426 | 2,143 / 4,237 | 91% | 1,905 / 3,849 | 5 | 887 | 7 / 21 | 68,015 / 212,687 | 16,732 | 150 |
| conv-47 | 16 | 11,024 | 21,361 | 497 | 36,032 | 72.5 / 64.0 / 9296 | 2,239 / 8,540 | 2,049 / 8,351 | 93% | 1,828 / 7,498 | 5 | 945 | 5 / 31 | 60,541 / 352,211 | 17,684 | 150 |

## Fit: read time against store size

Least squares over the 18 LoCoMo packs (10 base packs from run 2, 8 grown packs), `fit.py`:

| Measure | Fit | R² | Passes 1,000 ms at |
|---|---|---|---|
| `retrieve()` median | 322 ms + 105 ms per 1,000 nodes | 0.94 | 6,481 nodes |
| `retrieve()` p95 | 194 ms + 325 ms per 1,000 nodes | 0.91 | 2,482 nodes |
| Graph stage median | 173 ms + 101 ms per 1,000 nodes | 0.96 | 8,174 nodes |
| Walk median | -58 ms + 91 ms per 1,000 nodes | 0.97 | 11,581 nodes |

These LoCoMo packs hold about 0.50 turns per node, so 2,500 nodes is about 1,200 turns and 6,500 nodes about 3,200
turns. A power fit per conversation gives exponents of 0.66 to 0.68 for the median and 0.79 to 0.92 for p95, so the
p95 grows almost in proportion to the store.

Linear interpolation between measured points, per conversation:

| Conversation | Median passes 1 s | p95 passes 1 s |
|---|---|---|
| conv-42 | 4,191 nodes (2,095 turns) | 2,790 nodes (1,319 turns) |
| conv-47 | 6,353 nodes (3,094 turns) | 2,136 nodes (866 turns) |

LongMemEval-S shows no growth with size over its narrow range (1,882 to 2,490 nodes): median fit slope -9 ms per
1,000 nodes, R² 0.03. Its packs are one user talking to an assistant, with no speaker entity, so no hub forms. A
LongMemEval-S store would slow only where some entity collects links the way a speaker does. That was not tested.

## What else the numbers show

1. **The slowest reads are the questions with the most seeds.** Their rows walked double each time the store
   doubles. In conv-47, "What is the board game where you have to find the ..." has 31 seeds. It walks 22,556 rows
   at 1 time, 44,533 at 2 times and 352,211 at 16 times, and takes 966 ms, 1,652 ms and 9,869 ms. In conv-42, the
   21-seed question walks 13,517 rows at 1 time and 212,687 at 16 times (705 ms and 5,913 ms). Each seed that
   reaches a speaker hub walks the whole hub, so the seeds multiply the hub's size.
2. **Seed reach starts to stop at 8 times.** `_seed_reach` takes 173 to 345 ms per read at 2 times and 269 to 576
   ms at 4 times. At 8 times it takes 544 to 707 ms on most reads, but 3 reads of 20 (conv-42) and 4 of 20
   (conv-47) take 2 to 3 ms. At 16 times every read with two or more seeds takes 4 to 7 ms. Those fast reads are the
   reads where more than `SEED_REACH_MAX_NODES` (5,000) nodes could continue the walk, so `_seed_reach` returns None
   before it reads anything (`src/prme/retrieval/candidates.py:225`). From that size on, the graph list loses its seed-count order
   (`rrf_graph_seed_count`, on by default), and the diagnostics say `neighborhood_limit`. So large chat stores
   already read with different ranking from small ones.
3. **The vector search waits for the walk.** The vector stage is 77 to 92 ms at 1 to 8 times and 887 to 945 ms at
   16 times. It shares the DuckDB connection lock with the walk, so its wall time includes waiting while the walk
   holds the lock. Its own cost was not separated.
4. **The walk scales with rows.** Across all base reads, walk time is about 14 ms + 23 ms per 1,000 rows returned.
   This matches P3 (`research-read-scaling-2026-11`): most of the cost is the per-seed walk and building a record
   for each row.

## For task 21 (bounded graph walk)

- Use the per-pack table above as the "before" on chat stores. Each pack's 20 questions and their order are fixed by
  the seed, so `chat_read_cost.py` on the fixed tree gives a paired "after" on the same reads.
- The grown packs give the slope. A bounded walk should make the median and p95 flat in store size, or close to it.
  To rebuild them: `scale_pack.py` on `conv-42` and `conv-47` at k = 2, 4, 8, 16 (commands below).
- Watch two things that change results, not only time: seeds above the 5,000-node limit now lose seed reach, and a
  walk that stops at hubs changes which 150 are kept.

## Limits

- DuckDB only. PostgreSQL has its own walk and was not measured.
- One machine, shared with other sessions. The grown runs ran at load 5 to 17, so their absolute times may be high.
  The rows walked do not depend on load: two base runs gave the same seeds and rows on every read.
- Grown packs are synthetic copies (see Method). They keep the speaker hubs right and may overstate topic hubs.
- Base packs are small (at most 689 turns). The 1 s crossing is from grown packs, not from real long chats.
- 20 reads per pack. p95 over 20 reads is the second slowest read, so it moves with one question.

## Commands

```sh
cd ~/Sites/prism-chat-reads
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY= PYTHONPATH=.
R=research/chat-read-cost-2026-11
# Scratch copies of the saved builds (manifest.json and pack/ per unit)
for b in locomo longmemeval; do for d in ~/Sites/prism/data/mid-tier-v1/builds/main-0.17.0a6-$b/$b/*/; do
  u=$(basename $d); mkdir -p /tmp/p4/packs/$b/$u; cp -R $d/pack /tmp/p4/packs/$b/$u/pack; cp $d/manifest.json /tmp/p4/packs/$b/$u/
done; done
# Base packs (run 2)
.venv/bin/python $R/chat_read_cost.py --packs /tmp/p4/packs --scratch /tmp/p4/scratch/run2 --out /tmp/p4/out/run2.json
# Grown packs, then their reads
for k in 2 4 8 16; do for u in conv-42 conv-47; do
  .venv/bin/python $R/scale_pack.py /tmp/p4/packs/locomo/$u /tmp/p4/scaled/k$k/locomo/$u $k
done; .venv/bin/python $R/chat_read_cost.py --packs /tmp/p4/scaled/k$k --scratch /tmp/p4/scratch/k$k \
  --out /tmp/p4/out/k$k.json --benchmarks locomo; done
# Tables and fit
python3 $R/fit.py $R/results/base-run2.json $R/results/grown-k{2,4,8,16}.json
```
