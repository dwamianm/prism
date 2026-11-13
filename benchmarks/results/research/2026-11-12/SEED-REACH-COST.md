# Seed reach count for the kept candidates only, same output (2026-11-12)

Task 26, prme-manager. Branch `seed-reach-cost-2026-11`, cut from `main` at `56b51d53` (#567 merged). The engine
change is in the branch; this file reports what was measured. The scripts and the compact results are in
`seed-reach-cost/`.

## Answer

**The seed reach count (`scoring.rrf_graph_seed_count`) now measures only the graph candidates the stage keeps, and
`retrieve()` gives the same output.**

- Every read measured gives the same ranked ids with scores, packed ids, context text, tokens, candidate counts and
  stored receipt (request id and `source_files_sha256` masked) as `main` at `56b51d53`: 160 of 160 reads on two packs
  under four settings, 37 of 37 probe receipts, and the offline evidence gate (below).
- On the m3 pack (20,500 stated facts), with the hosted seed count setting, the 9 questions that run the count save
  **100.1 ms** at the median per read (95% interval 74.2 to 135.0 ms over pairs, 63.7 to 137.0 ms over questions;
  bar: at least 50 ms), and the change was faster in 27 of 27 of their pairs. Over all 20 questions the median
  paired saving is 44.2 ms. The flat pack is not slower: median paired saving +0.2 ms (interval -0.6 to +0.9 ms).
- One warning log line now says when the 5,000 node limit turns the count off. No setting changes, no default
  changes, `SEED_REACH_MAX_NODES` is unchanged, no store method is added, no stored memory is rewritten, and no
  `DERIVATION_VERSION` bump: the change is in how the count is computed, not in anything a source derives.

## Part A: where the cost was

`seed-reach-cost/scripts/parts.py` times the parts of `_seed_reach` on `main` (`56b51d53`) under the defaults
(`memory_mode=address_first`) with the hosted seed count setting (`scoring.rrf_graph_seed_count=true`,
`scoring.rrf_graph_weight=0.5`), 20 questions, 3 repeats (`seed-reach-cost/part-a/`).

| Pack | Calls (of 60 reads) | Seeds | Nodes read (expandable) | Links | `_seed_reach` p50 | Read p50 |
|---|---|---|---|---|---:|---:|
| m3 (20,500 stated facts) | 27 walked, 9 over the limit | 12 to 44 | 1,941 to 4,422 | 43,068 to 45,061 | 126.2 ms | 324.9 ms |
| LoCoMo conv-30 (369 turns) | 60 walked | 2 to 37 | 17 to 794 | 455 to 1,295 | 5.4 ms | 136.9 ms |
| LoCoMo conv-26 (419 turns) | 54 walked, 6 with one seed | 2 to 21 | 20 to 911 | 401 to 1,523 | 5.9 ms | 149.8 ms |
| LoCoMo conv-47 (689 turns) | 48 walked, 12 with one seed | 2 to 31 | 770 to 1,406 | 1,329 to 2,137 | 10.2 ms | 246.1 ms |

On m3, the 126 ms of a walked call was (median per part): the `walk_links` read 34.2 ms (32.2 ms of it SQL), the
adjacency map 49.2 ms, the walks from each seed 32.1 ms, the result 8.5 ms and the setup 3.3 ms. The walks reached
about 38,100 nodes and made up to 502,000 steps, but the stage reads the count of its kept candidates only (150).
So two thirds of the cost was Python work on nodes whose count nothing reads. On the LoCoMo packs the count costs 5 to
10 ms, most of it the read.

**The limit.** On m3, 3 of 20 questions hit the 5,000 node limit ("What is on the kitchen counter?", "What is on
the office desk?", "What is in the laundry room?", with 7,232, 7,187 and 8,039 nodes to read). Over every question
of the three LoCoMo packs, 0 of 383 hit it (0 of 81, 0 of 152, 0 of 150). If the limit were raised past 8,039 nodes,
`main`'s code would spend about 115 ms more on each of those 3 m3 questions (measured with the limit lifted: read
59.1 ms, adjacency 15.5 ms, walks 32.8 ms, result 8.3 ms). The change cuts the Python part of that, but it was not
timed with the limit lifted. The limit is unchanged; raising it is a separate decision.

## What changed

`_seed_reach` (`src/prme/retrieval/candidates.py`) takes the stage's kept candidates (`targets`) and measures them
alone. It reads the same links over the same nodes as before (`walk_links`, or `get_edges` with the same self link
and unverified alias rule on a store without it, such as the privacy read view), after the same limit check. The
fewest hops from a seed to a target are then found from both ends instead of by a walk from every seed. A path of at
most three links has at most two inner nodes, and the walk never continues through an owner entity other than the
seed it started from, so a seed reaches a target:

- in 1 hop when they are linked;
- in 2 hops when a node that is not an owner entity is linked to both;
- in 3 hops when such a node next to the seed is linked to such a node next to the target.

That is the walk's result for `max_hops` 1, 2 and 3. The stage caps `max_hops` at `min(packing.graph_max_hops, 3)`,
and the setting allows 1 to 3, so no other value reaches it; any other value now raises `ValueError`. The per-seed
walk is gone: there is no flag and no second path. The one new log line is a warning when the limit turns the count
off: "Graph seed reach skipped: N nodes would be read, more than the limit of 5000; no graph candidate gets a seed
count rank". The response metadata still says `graph_seed_reach: neighborhood_limit`, as before.

**No other reader of the reach values.** The count of a node that is not a kept candidate was never read. Every
use in `src/` (`grep -rn -e "_seed_reach" -e "graph_seed_reach" src/prme`):

- `candidates.py:572` copies the count onto each kept candidate; `candidates.py:805-806` and `candidates.py:862`
  carry it through the merge, for graph candidates only.
- `scoring.py:605` reads it, from the candidate, to rank the seed count list.
- `models.py:419` is the candidate field (excluded from dumps). `models.py:809`, `candidates.py:50`,
  `candidates.py:567-570`, `pipeline.py:1669-1670` and `pipeline.py:1753` are the status string
  (`neighborhood_limit` or `backend_error`), not the counts.
- `candidates.py:511`, `893`, `919` and `931-935` are the on and off flag, and `candidates.py:790` sets the
  empty default of a candidate from another path.

The other hits are docstrings in `storage/seed_walk.py`. No benchmark, diagnostic or receipt field reads the counts.

**Test.** `tests/test_graph_seed_count.py::TestSeedReach::test_the_count_of_each_target_is_a_plain_walks_on_random_graphs`
checks the new count against a plain breadth-first walk from each seed (the oracle, in the test file only) on 150
small random graphs for each store shape (`walk_links` and `get_edges`) and each `max_hops` (1, 2, 3): cycles, links
from a node to itself, repeated links, owner entities under each owner mark (some of them seeds), a fact with an owner
mark, links to nodes outside the search, a seed among the targets, a target with no link and an unknown id. It checks
the count with and without the search depths, for every node and for the targets alone. Five broken versions of the
rule (an owner as a middle node, an owner next to the seed, a 3-hop path that replaces a nearer one, a seed counted
as reaching itself, a self link counted as a step) each fail it. The limit test now also checks the one warning.

## Bar 1: same output

Each side runs in its own process from its own tree, on its own fresh copy of the pack, through `gate_config`
(current defaults, environment ignored, organizer off) with `--set`. 20 questions (P8's), reference time 2026-11-05
UTC, user `robot-1`. `seed-reach-cost/same-output/` and `seed-reach-cost/speed/`.

| Pack | Setting | Ranked | Packed | Context | Masked stored receipt | Log fields | Replay | Raw receipt |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| m3 | hosted seed count (3 rounds) | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |
| m3 | `memory_mode=address_first` (default) | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |
| m3 | `memory_mode=classic` | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |
| m3 | `read_profile=robot` | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |
| n20000-s1 (flat) | hosted seed count (3 rounds) | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |
| n20000-s1 | `memory_mode=address_first` | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |
| n20000-s1 | `memory_mode=classic` | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |
| n20000-s1 | `read_profile=robot` | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |

The raw receipt differs only in `source_files_sha256`, whose `candidates` entry hashes `candidates.py`; it is the only
source entry that changes. Tokens used and candidate counts per path are 20/20 on every row. On m3 the same 3
questions report `neighborhood_limit` on both sides under every setting. The current defaults turn the seed count on
too, so the default rows run the change; the flat pack's questions name no entity in it, so its graph path has no
seeds and the change does not run there.

Probe receipts (`probes.sh`, `probe_receipts.py`): each probe's cached pack was copied once per side and measured with
`chat_probe measure`, no model call. Masked stored receipts and log fields are identical on `defects-v1` 7/7,
`chat-v1` 18/18 and `member-v1` 12/12 (`seed-reach-cost/probe-receipts.txt`); the raw receipts differ only in
`source_files_sha256`.

Offline evidence gate (`gates.sh`, current defaults, LongMemEval-S controls regenerated 2026-10-14): every row of the
first table on LoCoMo (1,536 questions with evidence, 1,540 for projected accuracy) and LongMemEval-S (470 and 500)
is +0.0 points, interval +0.0 to +0.0, 0 wins, 0 losses; no question lost or gained all its evidence among the
candidates, and candidates per question, records per context and the session expansion counts are the same
(`seed-reach-cost/gate/compare-main-vs-change.md`).

## Bar 2: speed

`pairs.py --rounds 3` with the hosted seed count setting: both sides read the same 20 questions in turns, three
rounds, 60 pairs, the order flipped each pair, one reader process per side kept open, each on its own fresh pack
copy. The 1-minute load average was recorded before every pair. The question groups were set before the run, from
Part A on `main`, by prme-manager's decision: the bar is judged on the 9 questions that run the count; the 3 over
the limit and the 8 with no count call are reported on their own. The interval is a bootstrap of the median
(10,000 resamples, seed 26), resampling pairs and, separately, questions with all their pairs.

| m3 group | Pairs | Median paired saving | 95% interval (pairs) | 95% interval (questions) | Change faster |
|---|---:|---:|---|---|---:|
| Count runs (the bar) | 27 | **100.1 ms** | 74.2 to 135.0 ms | 63.7 to 137.0 ms | 27 of 27 |
| All 20 | 60 | 44.2 ms | 15.5 to 58.7 ms | 8.5 to 79.4 ms | 47 of 60 |
| Over the limit | 9 | 2.1 ms | -25.7 to 48.7 ms | -5.8 to 6.1 ms | 5 of 9 |
| No count call | 24 | 6.5 ms | -0.8 to 18.4 ms | 0.5 to 22.5 ms | 15 of 24 |

| Pack | Load (min / median / max) | `main` p50 / p95 | Change p50 / p95 | Median paired saving | 95% interval (pairs) |
|---|---|---|---|---:|---|
| m3 | 11.8 / 12.9 / 13.2 | 341.1 / 498.0 ms | 292.7 / 390.7 ms | 44.2 ms | 15.5 to 58.7 ms |
| n20000-s1 (flat) | 15.9 / 17.3 / 17.3 | 69.0 / 106.2 ms | 69.4 / 103.9 ms | 0.2 ms | -0.6 to 0.9 ms |

On m3 the questions that run the count go from 424.0 to 313.7 ms at the median. The bar is met: the median paired
saving on those questions is at least 50 ms, and the flat pack is not slower beyond noise. The load average was high
(about 12 to 22) because other sessions ran on the machine; both sides shared it, pair by pair.

The one-round runs of bar 1 (load about 12 to 22) gave the same picture on m3: savings of 41.5 ms over all 20 and
85.2 ms on the count questions (`address_first`), 27.8 and 54.3 ms (`classic`), and 2.0 and 2.9 ms
(`read_profile=robot`, which keeps 40 graph candidates and makes a smaller walk). The flat pack's one-round savings
were -0.7, +0.3 and -0.1 ms.

An offline replay of the saved inputs of the m3 calls (`micro.py`, `dump.py`) put the Python part of the count at
52.9 ms at the median for the walk from every seed and 6.3 ms for the new rule, with equal results on every call.

## Bar 3: PostgreSQL

Skipped: no store method was added, so the PostgreSQL store and its SQL are unchanged. The PostgreSQL tests the read
path touches still ran, with PostgreSQL on (a localhost TCP address with a user and a password):
`test_pg_seed_walk.py` is in the bar 4 run below.

## Bar 4: privacy, determinism and replay

With PostgreSQL on and every model provider blocked: `test_graph_seed_count.py`, `test_seed_walk.py`,
`test_pg_seed_walk.py`, `test_graph_neighborhood_tenancy.py`, `test_privacy_source_routing.py`,
`test_privacy_fences.py`, `test_privacy_admission.py`, `test_privacy_source_reads.py`, `test_determinism_rebuild.py`
and `test_packing_receipt_compatibility.py`: 280 passed. The privacy read view (`AdmittedGraphView`) has no
`walk_links`, so it reads `get_edges` with the same filters as before, and the privacy fences are where they were.

The receipt's `source_files_sha256` changes: its `candidates` entry goes from `1f309b34...` to `1b6f8c87...`.
Receipts still replay (`replay_cross.py`, m3, current defaults, 20 questions): receipts made on `main` replay under
`main` 20/20 and under the change 20/20, and receipts made on the change replay under the change 20/20 and under
`main` 20/20 (`seed-reach-cost/replay/cross.jsonl`).

One note on the test runs: `test_graph_neighborhood_tenancy.py::test_neighborhood_route_reads_a_protected_store_through_the_admitted_view[duckdb]`
failed once, in the first run of a 10 file group without PostgreSQL. It passed in the next 2 group runs and in 8 runs
of its file. It tests the HTTP neighborhood route, which does not call `_seed_reach`. The error text was not kept.

## Full suite

`uv run pytest -n 8` on the change, with PostgreSQL on (a localhost TCP address with a user and a password) and every
model provider blocked: 9,293 passed, 233 skipped, 128 xfailed, 3 failed, in 16 minutes 49 seconds. None of the 3
comes from the change:

- `test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself` expects the checkout at
  `~/Sites/prism`; a worktree is somewhere else.
- `test_privacy_quality_sets.py::test_PV11_storage_parity_24_fixed_queries[duckdb]` and `[postgres]` failed with
  `PrivacyDenied: privacy_capability_unavailable` from the local analyzer's start (`local_keys.py:436`), at a load
  average near 17. The file passes on the change when run alone, 3 of 3. Task 24 saw the same start failure under load.

## What was not done

- No model call, no paid provider, no answer run: the change cannot change a context, so none applies.
- No mid tier: the offline gate and the probes show identical contexts.
- The limit was not raised, and the change was not timed with it lifted.
