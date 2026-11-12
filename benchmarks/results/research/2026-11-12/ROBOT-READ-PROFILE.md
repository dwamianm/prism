# Robot read profile (2026-11-12)

Task 22 asks for a read that takes at most 200 ms at the 95th percentile on a store of 20,000 facts. With the
current defaults, one `retrieve()` on 20,500 stated facts takes about 640 to 960 ms at the 95th percentile depending
on machine load, and it stores a receipt of about 1.8 MB. This change adds an opt-in read profile that cuts both by
roughly a factor of ten: the receipt goes to 75 KB or less, and the bench store reads in 52 to 54 ms at the 95th
percentile. With the profile off, every read is byte-identical to `main`.

**The stated store has not yet been measured on a quiet machine.** Its best result in this pass is 224.8 ms at the
95th percentile, against the 200 ms target, on a machine that another session's full test suite was loading; the
same code measured 197.8 ms on an earlier, quieter pass. The bench store's result is wide and clean. Bar 1 is
recorded below as met on the bench store and open on the stated store.

## What changed

- `read_profile` (`PRME_READ_PROFILE`), `"default"` or `"robot"`, default `"default"`. It is a preset, like
  `memory_mode`: `"robot"` sets existing settings, each only when the configuration does not set it itself:
  `packing.vector_k=60`, `packing.lexical_k=60`, `packing.graph_max_candidates=40`, `packing.graph_max_hops=2` and
  `result_limit=40` (`src/prme/config.py`, `_apply_read_profile`).
- `result_limit` (`PRME_RESULT_LIMIT`), default unset. It is the result limit `retrieve()` applies when the caller
  passes no `limit` (`src/prme/storage/engine.py`, two lines). A `limit` the caller passes wins. This is the one new
  knob. The profile needs it because the receipt records every result before packing: about 1,000 on a large store,
  which is most of the receipt's size.
- Both fields are left out of a configuration dump at their defaults, so a configuration recorded before them still
  compares equal, and receipts with the profile off do not change.
- No ranking code changes. No file in `src/prme/retrieval/` changes, so `execution.features.source_files_sha256`
  is the same as on `main`. No model is called, nothing acts at write time, and no stored memory is rewritten. No
  `DERIVATION_VERSION` bump: nothing changes in what any source derives.
- `config_audit.py` gates `read_profile` like `memory_mode`. `documentation/configuration.md` has both rows, and
  the CHANGELOG has the entry.

## How the values were chosen

A sweep on the two stores below, each value an existing setting. The sweep ran on the stack worktree (main #562
plus the read-path cost branch, task 22 step 1); the values are settings, so the tree did not matter. The receipt and
the stored operation row grow with the result count; the read time grows with the candidate counts and the third
graph hop.

| Setting | Stated p95 | Bench p95 | Receipt median (stated) |
|---|---|---|---|
| Defaults | 546.6 ms | 119.1 ms | 1,811,470 B |
| Limit 50 | 445 ms | | 93 KB |
| Limit 50, candidates 100/100/50 | 392 ms | | |
| Same, two graph hops | 149 ms | 33 ms | |
| Same, one graph hop | 127 ms | | |
| Limit 40, candidates 100/100/50, two hops | 153 ms | | 75 KB |
| Limit 40, candidates 60/60/40, two hops (chosen) | 151.9 ms | 22.8 ms | 74,613 B |

The third graph hop is the largest single cost on the stated store. One hop is a little faster, but two hops keep
the two-step questions that the graph exists for. Candidates 60/60/40 leave about 5 ms of headroom over 100/100/50
and keep the largest stored row under 100 KB (80/80/40 had one row of 102,214 bytes).

## How it was measured

Both stores hold 20,000 memories or more, and every run reads a fresh copy:

- **stated**: 20,500 stated facts (`m3-pack`, write-rate study, task P3/21), 25 questions ("where is X now", "what is
  on P").
- **bench**: 20,000 benchmark-shaped memories (`data/read-cost-v1/packs/n20000-s1`, `read_cost.py`, task 18/19), 60
  questions.

Configuration: the evidence gate's (`gate_config`): current defaults, environment and `.env` ignored, the
opportunistic organizer off, plus `memory_mode` and the profile. Providers were blocked in every run. Code:
`main` `3fc87120` plus this change (after PR #563 merged; the profile carries over unchanged). Machine: Apple M5
Pro, 18 cores, Python 3.11.6. No model was called.

The machine was busy for every run of this second pass: a full test suite of another session, and its fast cycle
after that, held the 1-minute load average between 7.4 and 18. Two consequences, both stated in the tables:

- The bar 1 table gives every number with the load average recorded next to it, and a stated-store rerun was made
  later, when the load briefly fell to 7.6; the load rose again during it.
- The offline gate runs below were made under the same load, so their retrieval seconds are not compared.

**Bar 1** (`scripts/interleaved.py`): two engines open side by side, off and on, each on its own copy of the pack.
Each question is read on both in a pair, and the order flips each pair, so load drift hits both sides. 3 rounds:
180 pairs on bench, 75 on stated. The 1-minute load average was recorded before every pair. The machine was not
idle; the load is in the table.

## Results

### Bar 1: time (target: p95 at most 200 ms with the profile on)

| Store | Mode | Off p50 | Off p95 | On p50 | On p95 | Load average (min, median, max) |
|---|---|---|---|---|---|---|
| stated | classic | 652.5 ms | 964.4 ms | 160.6 ms | **213.7 ms** | 9.59, 10.57, 11.71 |
| stated | address_first | 664.8 ms | 970.0 ms | 196.2 ms | **246.6 ms** | 8.76, 10.43, 12.29 |
| bench | classic | 135.5 ms | 266.8 ms | 25.4 ms | **52.4 ms** | 9.71, 10.69, 11.73 |
| bench | address_first | 97.8 ms | 143.6 ms | 39.8 ms | **54.3 ms** | 9.65, 9.86, 10.04 |

The bench store meets the target in both modes at this load. The stated store does not: 213.7 ms in `classic` and
246.6 ms in `address_first`, against 200 ms, at a load average of 9 to 12.

A stated-store rerun started when the 1-minute load average fell to 7.6, but the load rose again during it and
averaged 7.4 to 8.7 (median 8.0):

| Store | Mode | Off p50 | Off p95 | On p50 | On p95 | Load average (min, median, max) |
|---|---|---|---|---|---|---|
| stated | classic | 708.2 ms | 1065.2 ms | 170.4 ms | **228.2 ms** | 7.53, 10.02, 12.29 |
| stated | address_first | 596.6 ms | 819.6 ms | 163.7 ms | **224.8 ms** | 7.44, 8.01, 8.73 |

So bar 1 is met on the bench store and **not met on the stated store in this pass**: 224.8 ms and 228.2 ms at
best, against 200 ms. The earlier pass of the same code and scripts on `main` `934d60fd` met it on both, at 157.0 ms
and 197.8 ms, the second by 2.2 ms only.

The two passes differ, and load does not explain all of it. In `classic`, the bench store's off-side p95 was
275.8 ms then and 266.8 ms now, which is flat, while its on-side rose from 38.9 to 52.4 ms, 35 per cent. The stated
store's off-side rose from 643.4 to 964.4 ms and its on-side from 157.0 to 213.7 ms. One reading is that a short read
is proportionally more exposed to a loaded machine than a long one: a 25 ms read that loses 13 ms to scheduling has
lost half its time, while a 135 ms read that keeps its threads busy averages the same contention out. That is a
hypothesis, not a measurement, and it is not proven here.

What the numbers do support: on the stated store, the on-side p95 is between a fifth and a quarter of the off-side
p95 of the same run, in every run of both passes, so the profile's own saving is large and steady. The bench rows,
where the off side is flat, are the clean evidence that the profile reaches the target: 52.4 ms and 54.3 ms against
200 ms, at a load average of 9.7 to 11.7. A stated-store run on a quiet machine is still owed, and until it exists
the stated target is open.

### Bar 2: profile off is byte-identical to `main`

`scripts/robot_read.py`, 1 round, `main` `3fc87120` against this change with the profile off. The fingerprint is
the ranked ids and scores, packed ids, the rendered context's digest and the stored receipt's digest (request id
replaced).

| Store | classic | address_first |
|---|---|---|
| bench | 60/60 identical | 60/60 identical |
| stated | 25/25 | 25/25 |

The probe packs give the same result: each probe pack's main build (`data/chat-probe-v1/<conversation>/rc-M`) measured
with the profile off on this change, against the same pack measured with `main`, has every stored retrieval receipt
byte-identical (`defects-v1` 7/7, `chat-v1` 18/18, `member-v1` 12/12). The offline evidence gate with the profile off
is below (bar 5).

### Bar 3: receipt size (target: under 100 KB per read)

| Store | Mode | Receipt median, off | Receipt median, on (max) | Stored row median, on (max) |
|---|---|---|---|---|
| stated | classic | 1,811,470 B | 74,613 B (75,796) | 95,121 B (96,952) |
| stated | address_first | 1,707,471 B | 75,113 B (76,269) | 95,056 B (98,808) |
| bench | classic | 1,414,941 B | 71,543 B (76,175) | 89,569 B (95,814) |
| bench | address_first | 142,953 B | 22,596 B (72,560) | 25,631 B (81,059) |

Every figure in this table is from the `3fc87120` runs and agrees with the earlier `934d60fd` runs to within 2 KB.

Met: every receipt is under 100 KB, and so is every stored operation row (the receipt plus its log fields). With
the profile off, 1,000 reads on the stated store add about 1.9 GB to `operations`; with it on, about 95 MB.

### Bar 4: determinism and replay

- Every read in bar 1 replays: `receipt.replay_ranking()` gives the returned ranking for 60 of 60 bench reads and 25
  of 25 stated reads, on both sides and in both modes.
- `tests/test_read_profile.py` (new) checks that the profile sets each option, that an option the configuration
  sets itself is kept, that `default` changes nothing (the dump included), that a robot read in both memory modes
  returns at most 40 results and replays from its receipt, that a caller's `limit` wins, and that `result_limit` in
  the configuration gives the same results and context as the same limit passed per read.
- `tests/test_determinism_rebuild.py`, `tests/test_packing_receipt_compatibility.py`,
  `tests/test_score_provenance.py`, `tests/test_rank_fusion.py` and `tests/test_read_profile.py` pass with
  `PRME_READ_PROFILE=default` and with `PRME_READ_PROFILE=robot`: 107 passed, 4 skipped, both ways (194 s and 71 s).
  With the variable set for the whole run, the profile's own tests clear it first, so they check the default profile
  wherever they run.
- The bench bar 2 runs (`main` `3fc87120` against this change, profile off, both memory modes, 60 reads each) are
  byte-identical on all 60, and the stated runs on all 25, both modes: eight sides, no difference.
- Full suite on the pre-rebase tree: 7,627 passed, 1,728 skipped, 127 xfailed, 20 failed. Nineteen were caused by my
  own `PRME_BENCH_CACHE_ONLY=1` flag, which makes a live extraction call raise `CacheOnlyViolation`; rerun without
  the flag, all 19 pass. The twentieth, `tests/test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself`,
  asserts the checkout is the main clone and fails in any worktree; the full suite was not rerun after the rebase.

### Bar 5: quality (reported, not a pass condition)

The profile cuts the result list to 40, and the packed context holds only results, so a record ranked below 40
cannot reach the reader. The rows below show what that costs.

**Probes** (`scripts/step2_probes.sh`): each probe pack's main build, copied four times and measured with no model,
with the build's own settings plus the side's settings. Records and tokens are per context, median.

| Probe | Side | Top 3 | Top 5 | Answer in context | Records (max) | Tokens | Claims on the named owner |
|---|---|---|---|---|---|---|---|
| `defects-v1` | classic off | 7/7 | 7/7 | 7/7 | 15 (15) | 531 | 12 |
| `defects-v1` | classic on | 7/7 | 7/7 | 7/7 | 14 (15) | 500 | 12 |
| `defects-v1` | address_first off | 7/7 | 7/7 | 7/7 | 15 (15) | 531 | 12 |
| `defects-v1` | address_first on | 7/7 | 7/7 | 7/7 | 14 (15) | 500 | 12 |
| `chat-v1` | classic off | 14/18 | 15/18 | 17/18 | 90 (90) | 3,070 | 35 |
| `chat-v1` | classic on | 14/18 | 16/18 | 17/18 | 28.5 (33) | 1,100 | 35 |
| `chat-v1` | address_first off | 15/18 | 16/18 | 17/18 | 90 (90) | 3,073 | 35 |
| `chat-v1` | address_first on | 15/18 | 16/18 | 17/18 | 28.5 (33) | 1,122 | 35 |
| `member-v1` | classic off | 10/12 | 11/12 | 12/12 | 54 (54) | 1,779 | 7 |
| `member-v1` | classic on | 10/12 | 11/12 | 12/12 | 33.5 (37) | 1,135 | 7 |
| `member-v1` | address_first off | 11/12 | 12/12 | 12/12 | 52 (52) | 1,805 | 7 |
| `member-v1` | address_first on | 11/12 | 12/12 | 12/12 | 33.5 (36) | 1,188 | 7 |

No probe loses its answer from the top 3, the top 5 or the context. `chat-v1` classic gains one probe in the top 5
with the profile on. The context shrinks: on `chat-v1`, 90 records and about 3,070 tokens become about 29 records
and 1,100 tokens. Claims on the named owner are a property of the stored pack, so they do not change.

**Offline evidence gate** (`scripts/step2_gates.sh`): `product_packing gate` over the saved LoCoMo and
LongMemEval-S packs, no answer model, then `gate-compare`. Four runs: classic off and on, `address_first` off and on.
No row is a pass condition here; they say what the 40-record cut costs.

These four runs and their three compares were made on `main` `934d60fd` and are carried over unchanged. That is
sound because PR #563 (merged as `3fc87120`) touched only `benchmarks/diagnostics/read_cost.py` and its tests, not
the retrieval or packing code the gate reads, and its own `gate-compare` of `main` against this change with the
profile off was +0.0 points on every first-table row, with 0 wins and 0 losses.

| Benchmark | Side | Candidates | Records per context | Context tokens | All evidence packed | All evidence among candidates | Projected accuracy |
|---|---|---|---|---|---|---|---|
| LoCoMo | classic off | 554.8 | 84.5 | 3,989 | 84.0% | 99.3% | 77.6% |
| LoCoMo | classic on | 40.0 | 40.0 | 2,030 | 79.4% | 79.4% | 74.6% |
| LoCoMo | classic on, multi-hop | 40.0 | 40.0 | 2,030 | 37.9% (107/282) | 37.9% | 42.5% |
| LoCoMo | address_first off | 554.8 | 84.5 | 3,989 | 84.1% | 99.3% | 77.74% |
| LoCoMo | address_first on | 40.0 | 40.0 | 2,031 | 79.4% | 79.4% | 74.6% |
| LongMemEval-S | classic off | 492.0 | 39.9 | 3,991 | 94.7% | 100.0% | 91.4% |
| LongMemEval-S | classic on | 40.0 | 23.9 | 3,963 | 88.1% | 92.1% | 87.4% |
| LongMemEval-S | classic on, multi-session | 40.0 | 23.9 | 3,963 | 77.7% (94/121) | 82.6% | 80.6% |
| LongMemEval-S | address_first off | 490.1 | 39.7 | 3,991 | 94.7% | 100.0% | 91.39% |
| LongMemEval-S | address_first on | 40.0 | 23.9 | 3,963 | 87.9% | 92.1% | 87.3% |

The loss is real and is the price of the profile, and `gate-compare` gives its intervals. In `classic`, on LoCoMo,
all evidence packed falls by 4.6 points (95% interval -6.1 to -3.3) and the projected accuracy by 3.0 (-4.0 to
-2.1); on LongMemEval-S the same is 6.6 (-9.1 to -4.0) and 4.0 (-5.5 to -2.6). In `address_first`, the falls are 4.8
(-6.2 to -3.4) and 3.1 (-4.1 to -2.1) on LoCoMo, and 6.8 (-9.4 to -4.3) and 4.1 (-5.6 to -2.7) on LongMemEval-S. All
evidence among the candidates falls from 99.3% to 79.4% on LoCoMo and from 100.0% to 92.1% on LongMemEval-S: the
candidate list itself is cut to 40, so evidence below that rank is never a candidate. The worst categories are
LongMemEval-S single-session-preference (100.0% to 73.3% in `classic`, 70.0% in `address_first`), LoCoMo multi-hop
(49.6% to 37.9%, 50.0% to 37.9%) and LongMemEval-S multi-session (89.3% to 77.7%). Evidence that ranks below 40
cannot reach the reader. A caller that needs the wider read passes `limit` or leaves the profile off.

The `main`-against-profile-off comparison (`scripts/step2_gates.sh`, third `gate-compare`) is the bar 2 evidence at
the gate: every first-table row is +0.0 points with 0 wins and 0 losses (1,536 annotated LoCoMo and 470 annotated
LongMemEval-S questions; the projected accuracy row reads 1,540 and 500). Only the latency rows differ, and those
runs were made under different load (3.9 to 5.7 before, 17.3 to 13.2 after), so they are not compared.

## Not in this change, and follow-ups

- The profile is `[HYPOTHESIS]`: its values come from one sweep on two synthetic stores, not from a benchmark answer
  run. No answer run was made.
- The 40-record context drops evidence ranked below 40. The gate rows above show how much on the two benchmarks.
  A robot that needs wider recall can set `PRME_RESULT_LIMIT` or pass `limit` per read, at the cost of a larger
  receipt.
- **The stated-store timing target is open.** The best stated-store result in this pass is 224.8 ms, measured at a
  load average of 7.4 to 8.7 on a machine another session was loading. The earlier pass measured 197.8 ms on the
  same code. A stated-store run on a quiet machine is owed before the bar is closed either way.
- The receipt is still built from 1,000 scored candidates when a caller passes a large `limit`; serializing the
  receipt once (study `READ-PATH-COST.md`, bounded alternative 1) would cut about 16 to 25 ms more on the stated
  store, but it changes `source_files_sha256` and is a separate decision. It would not close the gap on its own.
- PostgreSQL was not measured.
