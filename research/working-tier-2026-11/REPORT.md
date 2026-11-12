# P6: a working tier for writes. Result

Status: every bar measured. Results in `results/`.

| Bar | What it asks | Result |
|---|---|---|
| 1 | accept p95 under 1 ms at 200 a second offered, 20,000 stored | PASS, p95 0.247 ms |
| 2 | every accepted fact found by address within 1 s | FAIL, 17 of 19 samples; 2 took 1.62 s and 5.22 s |
| 3 | same stored records as the current path | PASS at 500 and at 4,050 records |
| 4 | kill mid-stream, reopen, lose nothing | PASS, 60 of 60 |
| 5 | `retrieve()` p95 100 to 200 ms at 20,000; the tier's added ms | FAIL, base read 22 s at the branch point and 0.67 s on the `934d60fd` base; tier adds 20 to 53 ms p50, 74 to 192 ms p95 |
| 6 | privacy refused and hidden as the current path does | PASS, 4 of 4 cases |

## Verdict

**In the engine, but not as this prototype stands, and not on its own.** Four bars pass. The two that fail both fail
for reasons outside the tier:

- **A stated fact can be accepted in a fraction of a millisecond** (p95 0.247 ms at 200 a second offered, 20,500
  stored), so the caller's side of bar 1 is reachable.
- **A fact is findable by address in 13 to 14 ms** once it is in the working table, against 2,563 ms for the stored
  address lookup at 20,500 (`probes/lookup_cost.py`, p50 over 5 calls, 20,500 nodes scanned). The table holds
  12,000 facts in 11.71 MB, so a full 20,000 working set costs about 20 MB.
- **The drain writes the same records as the current path**, table for table, at 500 and at 4,050 records.
- **Nothing accepted is lost** on a kill and reopen, and the restart closes P5's gap between the graph commit and
  the lexical commit.
- **Privacy is unchanged**: the same refusal rule, 4 of 4 cases.

What it does not give, and cannot give without an engine change:

- **Sustained 200 a second.** The drain runs the engine's own derivation, which costs about 0.29 to 0.34 facts a
  second at these sizes. The working tier moves that cost off the caller; it does not remove it. A backlog grows
  without bound at a 200 a second offer.
- **A read within 1 s.** The drain runs on the reader's event loop, and one derivation job holds that loop for up
  to 7.6 s. Reads are fast; the wait for the loop is not. Bar 2 fails on this, 17 of 19 samples.
- **A `retrieve()` inside 100 to 200 ms.** The tier adds nothing measurable to a read, but the base read is 22 s at
  20,500 stored. Bar 5 fails on the base read, and no write tier can fix that.

**No decision model is needed anywhere.** Every step is mechanical: validate the stated assertion, key it by
address, log it, append the event, derive in batches, drop it after both completions.

**The smallest engine change the result implies**, in order of what it buys:

1. **Let a read run beside the drain.** The privacy check and the working-table read are synchronous; give the
   store a read connection a plain thread can use, or make the derivation yield inside a job. This alone makes
   bar 2's 1 s reachable. `offloop.py` shows why the tier cannot do it alone: the shared connection and the
   `asyncio.Lock` are the engine's.
2. **Make the derivation cheaper or parallel.** At 0.29 to 0.34 facts a second, a 200 a second stream needs a drain
   about 600 times faster, or a bounded working set with a defined drop policy. This is the real work, and P5
   already showed that SQL trimming alone does not reach it.
3. **Make the stored read cheap enough for the robot goal.** At this branch point that is 22 s at 20,500 stored.
   The addendum shows it is 0.67 s at `934d60fd`, so this gap is closing and is now about 4 times, not 100.

The addendum also adds a fourth item: **make the working-table merge cheaper than a second full retrieval**. At
12,000 facts in the table the merge's p95 is 192 ms, which is the whole goal band, so a reading path that merges
cannot spend that.

## Machine caveat

Every number here was measured on this laptop while other sessions ran work of their own. Load average was 10 to 27
on 18 cores (6 performance cores) during the runs. The load came from `~/Sites/prism-flip`, `/tmp/task22/stack` and
other processes this session did not start and cannot stop. Every run reports its load average beside it.

The load matters most for the two failures, and it does not explain them. Bar 2 failed because one derivation job
held the event loop for 7.6 s, which load raises but does not create. Bar 5 failed because the base `retrieve()` is
22 s at 20,500 stored, and P3 measured 18.5 s on a quieter machine with the same shape. Neither margin is close
enough for load to decide it.

## Bar 6: privacy. PASS

Cases, each on a fresh store, `results/privacy-probe.json`:

| Case | Current path | Working tier |
|---|---|---|
| `marked-scope` (a fact row of the probe's own scope is marked, so no legacy check can pass) | write refused: `ExtractionError` > `MaterializationError` > `privacy_provider_unavailable` | `lookup`, `facts_by_address` and `retrieve_with_working` each refused: `privacy_provider_unavailable` |
| `marked-other-user` (a row of another owner is marked) | write proceeds | reads proceed; only the probe's own `green mug` returned |
| `other-user-table` (another owner's fact sits in the table with the same address) | write proceeds | reads return only the probe's own `red mug` |
| `other-scope-table` (the same fact in the `project` scope) | write proceeds | reads return only the probe's own `red mug` |

The rule is the current path's rule, unchanged: every read calls `PrivacyRepository.require_legacy_range` first, and
the table is keyed by `(user_id, scope)` so a foreign owner or scope never matches. The open question of "inputs
read" against "every node of the scope" is not decided here.

## Bar 3: the same records after the drain. PASS at 400 and at 4,000 stored

| Stored | Facts added | Current path | Working tier |
|---|---|---|---|
| 400 | 100 | 293.81 s, 0.34 facts/s | 0.048 s accept, 297.77 s drain, 5 batches, 0 left in the table |

At 500 records, `results/bar3-500.json`:

| Table | Current | Working | Same |
|---|---|---|---|
| events | 500 | 500 | yes |
| nodes | 1,444 | 1,444 | yes |
| edges | 2,002 | 2,002 | yes |
| operations | 1,500 | 1,500 | yes |
| lexical | 998 | 998 | yes |
| vectors | 1,442 | 1,442 | yes when `vector_key` is dropped |

`vector_key` is a position in the vector storage, not content. With it dropped, the 1,442 rows match on model,
version, dimension, staged text and vector bytes. Retrieval agrees too: 24 of 25 questions have the same top 10
results at 9 rounded places, and the expected result keeps the same rank on 25 of 25. One question differs in the
10th place.

At 4,050 records, `results/bar3-4050.json`: events, nodes, edges and vectors match (vectors again once `vector_key`
is dropped), lexical matches, and retrieval matches on 25 of 25 questions at the top 10 and the top 50 with 9
rounded places, and the expected result keeps the same rank on 25 of 25.

One table does not match by value: `DERIVATION_PREPARED` and `DERIVATION_COMMITTED` operations differ in exactly one
field, `plan_checksum` (100 rows of 12,150). That field is a hash of the whole `DerivationPlan`, and the plan embeds
`created_at`, so a plan built at a different second has a different checksum. The plans themselves match once ids and
times are masked. It is a time value, not content, so it stays inside "apart from ids and times".

**The 20,000 case was not run.** The bar allows it only if a current-path pack exists or is cheap to make. No 20,500
pack built by the current path exists, and at 0.34 facts a second the current path would need about 16 hours for
20,000 facts. P5's 0.29 facts a second at 20,500 says the same. Bar 3 is reported at 400 and 4,050 and the bar passes
on both.

## Bar 4: kill mid-stream and reopen. PASS

`results/crash-400-accept.json` and `results/crash-400-verify.json`.

- Accept phase: 60 facts accepted, 60 records in the log, then `os._exit(9)` with no drain.
- Verify phase, same directory reopened: replay loaded 60 facts, 3 drain batches, 188.2 s.
- `facts_stored` 60, `facts_derived` 60, `missing_events` empty, `missing_records` empty, 0 left in the working
  table, 0 model calls.

Zero accepted facts lost. The restart replay plus `_reset_stale_leases` re-derives a job a dead process left
`running`, so the 300 s lease does not delay it.

## Bar 1 and bar 2: accept latency, and findability within 1 s

`results/stream-working-20500.json`, 20,500 stored, the offer 10 facts every 50 ms for 10 s (200 a second planned).

**Bar 1: PASS.** `accept` p50 0.058 ms, p95 0.247 ms, p99 0.412 ms, max 82.164 ms, mean 0.148 ms, over 2,000 calls.
1,996 of 2,000 were under 1 ms. Offer rate achieved 163.96 facts a second against 200 planned, because the drain
kept the loop busy between offer ticks. Machine load average was 21 to 27 on 18 cores during the run.

**Bar 2: FAIL as measured. 17 of 19 samples, not 19 of 19.**

| Sample | After accept |
|---|---|
| 17 of 19 | 0.02 to 0.72 s |
| offered 1,000 | 1.62 s |
| offered 1,400 | 5.22 s |

**Cause, measured.** `loop_lag.py`, 20,500 stored, a 5 ms timer beside the drainer:

| Drain | Loop lag p50 | p95 | p99 | max | Ticks over 100 ms |
|---|---|---|---|---|---|
| 40 facts, 70.45 s | 15.8 ms | 24.5 ms | 38.7 ms | 3,939.7 ms | 10 of 2,647 |
| 1 fact | 16.2 ms | 22.4 ms | 62.1 ms | 7,626.1 ms | 2 of 387 |

A direct `lookup()` in the same window tracks the lag to the millisecond: p50 15.5 ms, max 3,939.4 ms in the 40-fact
run. The read's own cost is 1.5 ms, so the rest is waiting for the event loop while the drain runs on it. The
p50/p95 of 15 to 25 ms is the engine's per-event asyncio work, and the seconds-long maximum is one derivation job
that does not yield. A single job held the loop for 7.6 s, so a smaller drain batch does not bound the wait: the
block is inside one job, not between jobs.

**Two fix attempts, both bounded, neither makes bar 2 pass:**

1. Cap the drain's step budget (`start_drainer(step_budget_ms=...)`). Not run as a measured variant: the 1-fact
   measurement above already shows one job holding the loop for 7.6 s, and `process_extractions` checks its budget
   only between jobs, so no between-jobs cap can bind a single job.
2. Read the working table off the loop: the privacy check and the table read are synchronous, so a caller's own
   thread can do both while the loop is busy. `offloop.py` measures this beside a drain. The privacy check needs
   DuckDB, and its lock is an `asyncio.Lock`, so a plain thread cannot take it. A second DuckDB cursor on the same
   connection aborts the drainer's write transaction (`TransactionContext Error: cannot rollback - no transaction
   is active`). A thread-safe read therefore needs its own connection, which is a change to how the store hands out
   connections, not a change to the tier.

Bar 2 therefore fails for the design as built, and the cause is the shared event loop, not the working table. The fix
is outside the tier: the engine's derivation must yield inside a job, or the read must run on a connection of its
own. That is the smallest change the result implies, and it is also what would make the loop lag a reader sees
predictable.

## Bar 5: read cost. FAIL on the base read, not on the tier

The bar is `retrieve()` p95 of 100 to 200 ms at 20,000 facts. It fails, and it fails before the working tier does
anything: the engine's own `retrieve()` on `origin/main` takes about 22 s at 20,500 stored.

`results/reads-20500.json`, 20,500 stored, 3 questions, the drainer stopped so the loop was free, load average 15:

| Backlog | `retrieve()` p50 | p95 | with working table p50 | added p50 | address lookup p50 | table memory |
|---|---|---|---|---|---|---|
| 0 | 24,107.2 ms | 27,165.2 ms | 24,607.5 ms | -1,715.4 ms | 14.3 ms | 0 MB |
| 1,000 | 22,680.9 ms | 24,150.9 ms | 23,562.2 ms | -452.4 ms | 13.6 ms | 0.98 MB |
| 12,000 | 21,790.5 ms | 22,199.7 ms | 21,005.7 ms | -784.8 ms | 12.9 ms | 11.71 MB |

**The tier's added cost is zero within noise.** `added_ms` is negative at all three backlogs. The base read is 22 to
27 s on 3 samples, so the difference between the two reads is buried in run-to-run variation. What the tier does
cost is its own address lookup: 12.9 to 14.3 ms here, against 1.5 ms measured in the bar 2 run on the same 20,500
pack. Both numbers are far under 1 s, and both are a rounding error beside a 22 s read. The privacy check is the
extra cost: on this larger store it walks the scope before it answers.

**The table's memory is 11.71 MB at 12,000 facts and 0.98 MB at 1,000.** That is under 1 KB a fact, so a full 20,000
working set is about 20 MB. Memory is not the limit.

This matches P3, which measured `retrieve()` at 18,519 ms at 20,500 and named the base read as the thing that misses
the robot read goal. The working tier adds nothing to that and cannot fix it. A read inside 100 to 200 ms needs the
stored read itself to get about a hundred times cheaper, or a read path that does not run the full retrieval.

The 400-stored run, `results/reads-400.json`, 5 questions, load average 17:

| Backlog | `retrieve()` p50 | with working table p50 | added p50 | address lookup p50 | table memory |
|---|---|---|---|---|---|
| 0 | 595.2 ms | 636.3 ms | 18.9 ms | 1.85 ms | 0 MB |
| 200 | 666.6 ms | 658.5 ms | 51.3 ms | 1.55 ms | 0.2 MB |

Same reading at the small size: the added cost is under 52 ms, and the base read already misses the goal.

## Addendum, 2026-11-12 10:35 CST: bar 5 on the new base

`main` moved past this branch's start after the measurements above: commit `934d60fd` (#562, one graph walk from all
seeds in the graph candidate stage). #563 (read-path cost) had not landed when this addendum ran. The branch is
rebased onto `934d60fd` and every number below is from that tree, same pack, same machine.

**Bar 5 still fails, but the base read is about 30 times faster.** The bar is `retrieve()` p95 of 100 to 200 ms at
20,000 facts. The new base reads in 0.62 to 1.13 s. That is still 3 to 6 times over the top of the goal.

`retrieve()` on the new base, `results/addendum-reads-repeat.json`, 20,500 stored, 25 questions, backlog 0, load
average 12.3:

| Backlog | `retrieve()` p50 | p95 | p99 | with working table p50 | added p50 | added p95 | address lookup p50 | table memory |
|---|---|---|---|---|---|---|---|---|
| 0 | 623.3 ms | 668.5 ms | 697.7 ms | 659.4 ms | 35.5 ms | 99.2 ms | 13.4 ms | 0 MB |
| 1,000 | 654.0 ms | 769.7 ms | 769.7 ms | 683.9 ms | 40.2 ms | 74.6 ms | 13.8 ms | 0.98 MB |
| 12,000 | 817.0 ms | 1,125.5 ms | 1,125.5 ms | 854.4 ms | 53.4 ms | 192.2 ms | 15.2 ms | 11.71 MB |

The 1,000 and 12,000 rows come from `results/addendum-reads-backlog.json`, 20 questions, load average 11.7. The
12,000 row was the noisiest run of the day: its p95 is 308 ms above its p50 while every other row's is under 120 ms,
so read its p95 as an upper bound. A separate 9-question run, `results/addendum-reads-20500.json`, reads the same
way: `retrieve()` p50 618.7 / 665.9 / 714.3 ms and added p50 47.4 / 21.8 / 20.0 ms at the three backlogs.

**What changed in the reading:**

- **The tier's added cost is now measurable and positive.** On the old base it was negative within noise, because a
  22 s read swamped a 35 ms difference. A 623 ms read does not. The added cost is 20 to 53 ms at p50 and 74 to
  192 ms at p95. Its own address lookup, timed on its own, is 13.8 to 15.2 ms, so the merge step costs more than the
  lookup alone: about 20 ms at p50 that the separate timer does not see. The separate timer runs the lookup in its
  own call after a fresh `retrieve()`; inside `retrieve_with_working` the lookup runs straight after a retrieval
  that just read the same scope. The report does not separate those two parts, and the p95 of the pair is what a
  caller pays either way.
- **At 12,000 facts in the table the tier's p95 alone is 192 ms**, which is the whole top of the 100 to 200 ms goal
  band. A caller that must hit the band cannot spend it on a merge step.
- **Table memory is unchanged**: 0.98 MB at 1,000 facts, 11.71 MB at 12,000, so about 20 MB for a full 20,000.

**The stored address lookup did not move.** `probes/lookup_cost.py` on the new base, 10 calls, 20,500 nodes scanned:
p50 2,649.8 ms, p95 2,721.6 ms, status `unknown`. It was 2,563 ms on the old base. #562 changes candidate
generation in `retrieve()`, not `get_assertion_state`, so no change was expected, and none was measured.

**What this means for the verdict.** The verdict above holds and one line of it gets stronger. The base read is no
longer an unchallenged 22 s wall: two commits took it down 30 times, so the 100 to 200 ms goal is now a gap of
roughly 4 times rather than 100 times, and it looks reachable by more of the same work. Bar 5 still fails today,
which is what the bar asks. The tier's own cost also stops being free: at 12,000 facts it consumes the goal band at
p95, so a working-tier read has to make the merge cheaper than a second full retrieval.


## Scratch folders

Every pack and probe store sits under `/tmp/p6`, 11 GB in total:

| Folder | Size | What it is |
|---|---|---|
| `probe` | 4.5G | privacy and bar 3 probe stores |
| `compare` | 1.2G | the two bar 3 packs, current path and working tier |
| `reads` | 1.0G | bar 5 stores at each backlog |
| `stream` | 979M | bar 1 and bar 2 run |
| `offloop` | 950M | the failed off-loop read probe |
| `lookup` | 950M | the 20,500-fact pack the last runs read |
| `lag` | 949M | the loop lag probe |
| `build` | 467M | bar 3 build |
| `cur4050.duckdb` | 181M | the current-path 4,050 pack |
| `privacy-probe` | 91M | bar 6 stores, one per case |
| `crash-400` | 37M | bar 4 kill and reopen |
| `smoke` to `smoke5` | 185M | short smoke runs |
| `peek400` | 25M | the 400-stored pack |
| `pack` | 792K | a small pack copy |
| `offloop.out` | 4K | the failed probe's log |

Deleting this is the owner's call. Nothing in the repository needs it. The results files under `results/` hold every
number in this report.

## What the drain costs

The tier's drain runs the engine's unmodified `process_pending` and `process_extractions`, so it carries the current
path's per-fact cost and adds nothing to it: 100 facts took 297.77 s through the tier against 293.81 s through
`ingest_batch`. That is about 0.34 facts a second, against a 200 a second offer. **Sustained 200 a second needs a
faster drain. The working tier alone does not give it, and the report's verdict must say so plainly.**
