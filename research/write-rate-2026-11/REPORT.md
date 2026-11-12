# Write rate of caller-stated facts: report

Task P2, prme-manager, 2026-11-11 and 2026-11-12. Branch `research-write-rate-2026-11`, cut from `d8cd0a26`
(`origin/address-first-memory`). The protocol was committed before any measurement (`PROTOCOL.md`, commit
`a65e6332`). Thesis section 5 (`memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md`) sets the robot budgets: accept
under 1 ms, searchable within 1 s.

## Verdict

**Not now.**

| Bar | Needed | Measured | Result |
|---|---|---|---|
| 1. Accept rate, sustained at 20,000 stored | 200 facts a second or more | 3.49 facts a second (M3, window 20,000 to 20,499) | fails |
| 2. Searchable within 1 s of accept | every fact, every size | 0 of 20 samples at 400, 4,000 and 20,500 stored; at most 2 of 2,000 facts committed within 1 s | fails |
| 3. Same records and results as the current path | apart from ids and times | records the same at 400 and 4,000; results the same at the level the current path matches itself; 20,000 not compared | holds on records at 400 and 4,000 |

The prototype makes the stated path 3.8 to 4.5 times faster. The path is still 33 times (at 400 stored) to 57 times
(at 20,000 stored) too slow for bar 1. The reads also miss the 1 s budget on their own: one `retrieve()` takes
1.3 s at 4,000 stored and 18.4 s at 20,500 stored, with no write running.

No small change makes caller-stated facts searchable fast enough for a robot. The per-fact derivation plan costs at
least about 136 ms a fact, even with the entity scan narrowed and one lexical commit a batch (M3b). A robot path needs
a write path that does not run one full plan for each fact, and a bounded read. That is a design task, not a small
change. See "Follow-up work".

A decision model is not needed for this task. The write path makes no judgment. No model was called in any run: the
extraction guard recorded 0 calls in every run, with the providers blocked.

## Question 1: what limits `ingest(assertions=...)`

The assertions themselves cost almost nothing. `_prepare_assertions` takes 0.02 ms a fact and `read_held` 0.01 ms.
The event append takes 1.5 ms. The time goes to the materialization path that every extracted claim takes.

M1, the current path, product defaults, per fact (`results/m1-cut-at-8000.json`):

| Stored | Facts/s | ms/fact | SQL/fact | Entities in store |
|---|---|---|---|---|
| 400 | 1.61 | 621 | 1,609 | 408 |
| 4,000 | 1.18 | 845 | 1,917 | 620 |

Top costs, with numbers:

1. **Entity lookup, two a fact (subject and object).** `EntityMerger.find_entity` reads every entity node of the
   owner and scope, in pages of 256, until the exact name matches. Inside a derivation plan, each page goes through
   `PlanningGraph.scan_nodes`, whose `legacy_check` calls `AdmissionCoordinator.require_legacy_inputs`. That runs
   `PrivacyRepository.records_for_objects` once per scanned node, with two SQL statements each (the second in
   `_object_identity`).
   - 400 stored: 472 ms of 621 ms a fact (76%). 729 privacy calls and 1,459 of 1,609 SQL statements a fact (91%).
   - 4,000 stored: 677 ms of 845 ms a fact (80%). 887 privacy calls and 1,774 of 1,917 SQL statements a fact (93%).
   - The cost grows with the owner's entity count, not with the fact count.
2. **Lexical stage.** `LexicalIndex.stage` commits the Tantivy writer and reloads the searcher once per plan, for one
   document: 71 ms a fact at 400, 74 ms at 4,000, 75 ms at 20,000.
3. **The rest of the plan and publish**, per fact: graph commit 11 to 14 ms, materialization queue drain 11 to 15 ms,
   mention links 8 to 12 ms, plan record 7 to 8 ms, embedding (`PlanningIndexes.prepare`) 8 to 9 ms, vector stage
   6 to 10 ms. About 150 to 180 SQL statements a fact outside the privacy reads (graph commit 23, plan record 18 to
   19, vector stage 13 to 18, lexical stage 13, extraction record 13).
4. **Claim merge** reads every earlier claim of the subject (`claim_merge.find_repeated_claims`): 3 ms a fact at 400,
   17 ms at 4,000, 70 ms at 20,000 (each object holds about 40 claims at 20,000). The privacy read runs on each of
   those claims too.

Index work per fact: about 1 embedding call (1 to 1.4 texts; FastEmbed itself takes 2.2 to 2.7 ms), 2 to 2.4 vector
adds, 1 lexical document and 1 lexical commit.

Why the recorded figure is 4.6 facts a second and this run measures 1.61: the contract scene behind the CHANGELOG
figure (`tests/contract/world.py`) has 20 objects and 22 places, so about 42 entities. This world has 500 objects
and 120 places (408 entities at 400 stored). The entity scan is proportional to the entity count. This is the
likely reason; the contract scene was not timed again here. The timers and the SQL counter were on in M1, M3 and M3b,
the same on every side, and add some cost of their own.

M1 was stopped at 8,000 stored. Its rate fell with each 1,000 written: 2.26, 1.44, 1.33, 1.20, 1.18, 1.12, 1.12,
0.97 and 0.81 facts a second. The 12,500 facts left to 20,500 would have taken 4 to 5 more hours. So there is no
current-path window at 20,000, and M4 has no current-path pack at 20,000.

## Question 2: the prototype

**The change.** `enable_filtered_entity_lookup` (research branch only, default False). The entity lookup asks the
store only for the owner's entities whose text contains the name (`content_contains_any`, a case-insensitive `LIKE`
that both backends already support). The planning graph narrows its planned nodes the same way. The exact name and
type match still decides, in the same id order, so the entity found is the same. Files:
`src/prme/config.py`, `src/prme/ingestion/entity_merge.py`, `src/prme/ingestion/planning.py`,
`src/prme/ingestion/pipeline.py`, `src/prme/storage/engine.py`, test `tests/test_filtered_entity_lookup.py`.

Why this change and not one transaction and one index pass for a batch: M1 put 76 to 80% of each fact's time in the
entity scans. A batch in one transaction still runs two scans a fact. M3b below gives the upper bound for the index
pass part.

**M3, the prototype, measured as M1** (`results/m3.json`):

| Stored | Facts/s | ms/fact | SQL/fact | Current path (M1) |
|---|---|---|---|---|
| 400 | 6.07 | 165 | 185 | 1.61 facts/s |
| 4,000 | 5.32 | 188 | 209 | 1.18 facts/s |
| 7,000 to 8,000 (progress step) | 3.60 | | | 0.81 facts/s |
| 20,000 | 3.49 | 286 | 327 | not measured |

At 20,000 stored the top costs are: publish 152 ms (lexical stage 75, index eviction 21, vector snapshot save 15,
drain 15, graph commit 14, mention links 12), plan preparation 91 ms (privacy reads 75 ms over 88 calls, claim merge
70 ms; these nest), and the entity lookup 6.6 ms for two calls.

**M3b, measurement only: the prototype plus one lexical commit a batch of 100** (prme-manager's add 2,
`results/m3b.json`, 575 s in total, under the 20 minute limit). The lexical stage stages documents with no commit and
no reload, and the batch commits once. The identity check against committed documents is skipped, and nothing checks
what a retry would do, so this is an upper bound and not a candidate change.

| Stored | Facts/s | ms/fact | Lexical stage ms/fact |
|---|---|---|---|
| 400 | 7.34 | 136 | 7 (was 74) |
| 4,000 | 5.98 | 167 | 7 (was 79) |

The drain rose from 12 to 22 ms a fact, so the net gain is 29 ms a fact at 400 and 21 ms at 4,000.

## Searchable within 1 s

**M2, the current path, one `ingest(..., assertions=..., wait_for_extraction=False)` a fact, 100 facts at 400 stored**
(`results/m2-400.json`):

- Accept: p50 4.4 ms, p95 18.6 ms, max 156 ms a call.
- The first commit came 31 s after its accept: with batching on and no wait, `ingest()` arms the batch flush timer
  (`BATCH_FLUSH_SECONDS = 30`, `src/prme/ingestion/pipeline.py`).
- Commit wait p50 92 s, max 153 s. 0.65 facts committed a second.
- 20 of 20 samples were found after their commit, at rank 1 to 3, 32 to 149 s after accept. 0 within 1 s.

**Stream test, the prototype, `ingest_batch` of 10 every 50 ms (200 a second planned) for 10 s, not waiting**
(`results/stream-prototype-*.json`). The run waits 120 s after the last offer, then stops.

| Stored | Offered/s | Accept per fact p50 / p95 / max | Committed at stop | Commit wait p50 / max | Samples found within 1 s |
|---|---|---|---|---|---|
| 400 | 102 | 5.3 / 29.8 / 48.7 ms | 585 of 2,000 (4.2/s) | 74 / 135 s | 0 of 20 (12 found, first after 1.5 s) |
| 4,000 | 105 | 4.2 / 37.8 / 61.5 ms | 518 of 2,000 (3.7/s) | 77 / 135 s | 0 of 20 (12 found, first after 2.2 s) |
| 20,500 | 33 | 2.1 / 388 / 465 ms | 47 of 2,000 (0.24/s) | 99 / 178 s | 0 of 20 (10 found, first after 19.1 s) |

- The calls could not keep the planned 200 a second: a call that commits a plan holds the process, so later calls
  started late (p50 5.7 s late at 400).
- The backlog grew at every size: at stop, the oldest waiting fact had waited at least 135 s.
- At 20,500 stored the numbers measure the reads more than the writes. Each sample's `retrieve()` took about 18 s in
  the same process, so the offer and the commits waited behind it. The read time alone is below.

**Read latency on an idle store** (not one of the protocol's measurements, added to explain the 20,500 stream test,
`results/read-latency.json`). Five "Where is the {object} now?" questions after one warm-up, opportunistic organizer
off, no write running:

| Stored | `retrieve()` median | Results returned |
|---|---|---|
| 400 | 0.39 s | 892 to 935 |
| 4,000 | 1.31 s | 863 to 953 |
| 20,500 | 18.39 s | 941 to 1,067 |

So bar 2 cannot hold at 4,000 stored or more through `retrieve()` on any write path, until the read is bounded.

The thesis budget of accept under 1 ms is not a bar. Measured accept: 4.4 ms p50 a call for one fact (M2), and 4.2
to 5.3 ms p50 a fact in calls of 10 (stream test, 42 to 53 ms a call).

## Bar 3: equivalence (M4)

Two builds through the current path (current-a, current-b) and one through the prototype, of the same observations,
compared at 400 and 4,000 stored (`m4_compare.py`, `m4_checks.py`, `results/m4-*.json`). Every UUID is replaced by a
label, every build clock is masked, and the build clocks are set to one fixed time before the questions are asked.
Per prme-manager, the judgment is on records, not on whole `retrieve()` responses.

**Records.** The same on both pairs at both sizes:

| Stored | Events | Nodes | Edges | Vectors | Lexical documents | Operations, checksums dropped and references sorted |
|---|---|---|---|---|---|---|
| 400 | 400 | 1,208 | 1,600 | 1,208 | 800 | 1,200, same |
| 4,000 | 4,000 | 8,620 | 16,115 | 8,505 | 7,885 | 12,000, same |

The operations log differs, current against current too, in two fields only: the order of a plan's `references`
(the entities the plan read, in the order the lookups returned them) and the plan checksums, which are digests over
the plan.

**Results, 25 questions a size** (20 "Where is the {object} now?", 5 "What is on the {place}?"):

| Comparison | Stored | Whole response | Top 10, exact | Top 10, scores to 9 places | Top 50, scores to 9 places | Bundle | Expected rank |
|---|---|---|---|---|---|---|---|
| current-a against current-b | 400 | 0 | 19 | 25 | 24 | 7 | 25 |
| current-a against prototype | 400 | 0 | 19 | 25 | 24 | 8 | 25 |
| current-a against current-b | 4,000 | 0 | 25 | 24 | 24 | 3 | 25 |
| current-a against prototype | 4,000 | 0 | 24 | 25 | 25 | 3 | 25 |

- No whole response matches, current against current too. Scores of the same record differ by about 1e-14
  (floating point order), equal scores are ordered by the random node id, and the number of results differs by a few
  on most questions (14 of 25 at 400 and 21 of 25 at 4,000, current against current; not looked into). The receipts
  follow the responses, so they differ where the responses differ.
- The prototype matches the current path at least as often as the current path matches itself, at every level.
- The tie order is the known random node-id tie-break; prme-fixer-sidekick has it (task 20, branch
  `stable-tie-break-2026-11`).
- At 4,000, the same pair gave 25 of 25 exact top 10 in one run and 24 of 25 to 9 places in the next run, on new
  scratch copies of the same packs. So `retrieve()` order is not fully reproducible on one pack either. Not looked
  into further.

## Candidate engine fix: the filtered entity lookup

This is separate from the verdict above. It is a candidate for the engine on its own, not for the robot budgets. It
was not built into the engine.

- **Gain.** 3.8 times at 400 stored (621 to 165 ms a fact), 4.5 times at 4,000 (845 to 188 ms), and 4.4 times in the
  7,000 to 8,000 step (0.81 to 3.60 facts a second). SQL statements a fact fall from 1,609 to 185 at 400 and from
  1,917 to 209 at 4,000. At 20,000 there is no current-path number to compare (M1 stopped at 8,000); the prototype's
  lookup costs 6.6 ms a fact there.
- **Model extraction pays the same cost** (prme-manager's add 1). `_populate` (`src/prme/ingestion/pipeline.py`,
  near line 1660) calls `find_or_create_entity` once per extracted entity. So chat `ingest()` runs the same full scan
  per entity, and it grows with the owner's entity count. This matters for hosted users with large stores.
- **What a fix must prove:**
  - the same entity is resolved as by the full scan: case and whitespace, names with `%`, `_` and `\`, a literal "I"
    or "we" (unresolved references), archived and other-owner copies, and more than one page of matches;
  - the same records, on the current path against the fix, as in M4, for `ingest(assertions=...)` and for model
    extraction (a recorded extraction replay);
  - that the privacy check still covers every input. The narrowed scan checks only the nodes it reads; the full
    scan checked every entity of the scope. A privacy review must confirm that the check is meant for inputs read;
  - the PostgreSQL backend (`LIKE ANY`), with its live test suite;
  - the fast tiers for an `ingest()` change (defect, chat and member probes, corrections probe, fast cycle) and the
    offline evidence gate (which replays `store()` packs, so it shows only that plain retrieval is unchanged);
  - `DERIVATION_VERSION` stays as it is, because the derived records do not change.

## Follow-up work (not done, not part of this ticket)

1. **A robot write path.** 200 facts a second leaves 5 ms a fact. The best per-plan path measured here is 136 ms a
   fact. A path that commits a batch of facts in one transaction and one index pass, or a working tier that keeps
   recent facts in memory and writes the log in batches (thesis section 5), is a design task.
2. **A bounded read.** `retrieve()` takes 1.3 s at 4,000 stored and 18.4 s at 20,500, and returns about 900 to 1,000
   results. Also, at 20,500 stored the newest claim for "Where is the {object} now?" ranked 13 to 84 in the stream
   test (rank 1 to 8 at 4,000 in M4).
3. **One privacy query per page** instead of one per node in `require_legacy_inputs` (88 calls a fact at 20,000 on
   the prototype, mostly from claim merge).
4. **One lexical commit per batch**, with a retry and identity design (M3b: 21 to 29 ms a fact).
5. **Claim merge** reads every earlier claim of the subject (70 ms a fact at 20,000).
6. **A deterministic tie-break** in retrieval (prme-fixer-sidekick, task 20), and the run-to-run order difference on
   one pack in M4.

## Commands

```sh
cd ~/Sites/prism-write-rate
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY= PYTHONPATH=.
R=research/write-rate-2026-11 D=data/write-rate-2026-11
# M1 (stopped at 8,000 stored; its windows were copied to m1-cut-at-8000.json)
.venv/bin/python $R/m1_current.py --pack $D/m1-pack --out $D/m1.json
# M3 and M3b
.venv/bin/python $R/m1_current.py --prototype --pack $D/m3-pack --out $D/m3.json
.venv/bin/python $R/m1_current.py --prototype --batch-lexical --total 4200 --windows 400:200 4000:200 \
  --pack $D/m3b-pack --out $D/m3b.json
# M4 packs (current-b the same as current-a; prototype with --path prototype)
.venv/bin/python $R/m4_build.py --path current --to 4000 --snapshots 400 --dir $D/m4/current-a
# M2 and the stream tests
.venv/bin/python $R/stream.py --mode single --facts 100 --settle 180 --pack $D/m4/current-a/400/pack --stored 400 \
  --out $D/m2-400.json
.venv/bin/python $R/stream.py --mode stream --prototype --pack $D/m4/prototype/400/pack --stored 400 \
  --out $D/stream-prototype-400.json
.venv/bin/python $R/stream.py --mode stream --prototype --pack $D/m4/prototype/4000/pack --stored 4000 \
  --out $D/stream-prototype-4000.json
.venv/bin/python $R/stream.py --mode stream --prototype --pack $D/m3-pack/pack --stored 20500 \
  --out $D/stream-prototype-20500.json
# M4 comparisons (and the same with --b $D/m4/prototype), then the checks
.venv/bin/python $R/m4_compare.py --a $D/m4/current-a --b $D/m4/current-b --points 400 \
  --out $D/m4-current-vs-current-400.json
.venv/bin/python $R/m4_checks.py --point 400 --out $D/m4-checks-400.json
# Read latency
.venv/bin/python $R/read_latency.py --out $D/read-latency.json
```

Targeted tests, with the prototype in the tree:
`uv run --all-extras pytest -q -x -p no:cacheprovider tests/test_filtered_entity_lookup.py tests/test_config_audit.py tests/test_structured_assertions.py tests/test_derivation_planning.py tests/test_derivation_version.py tests/contract`:
159 passed, 68 skipped (PostgreSQL, no database here), 63 xfailed (the contract scorecard's expected failures).

## Limits of the run

- One sample per measurement. The observations and the stores are deterministic; the clock is not.
- Apple M5 Pro laptop, 18 cores, 48 GB, Python 3.11.6, DuckDB 1.4.4, tantivy-py 0.25.0, usearch 2.26.2, FastEmbed
  `BAAI/bge-small-en-v1.5`. A robot's own hardware is slower, so these numbers are an upper bound for it.
- Runs overlapped on the same machine for part of the time (the M4 builds, M3b and the stream tests ran while M1 or M3
  ran). Each run uses mostly one core of 18, so the effect is small, but it is not zero. The M3 window at 20,000 ran
  with nothing else running (the other jobs were paused).
- The timers and the SQL counter add cost in M1, M3 and M3b. The M4 builds ran with no timers, but in parallel with
  other jobs, so they are not a clean rate.
- M4 at 20,000 was not run: there is no current-path pack at 20,000.
- The prototype used one of the two fix-and-validate cycles allowed; the M4 normalization used both of its cycles
  (the fixed build clocks are the second). `m4_checks.py` adds checks, not a third normalization.

## Files

- `PROTOCOL.md`: the protocol, committed before the measurements.
- `common.py`: the world, the counters and timers, the engine setup, the write helpers.
- `m1_current.py` (M1, M3, M3b), `stream.py` (M2 and the stream tests), `m4_build.py`, `m4_compare.py`,
  `m4_checks.py` (M4), `read_latency.py`.
- `results/`: the small JSON results. The packs and logs stay in `data/write-rate-2026-11/` (ignored by git).
