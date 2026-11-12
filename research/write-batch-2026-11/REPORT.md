# Batch write path for caller-stated facts: report

Task P5, prme-manager, 2026-11-12. Branch `research-write-batch-2026-11`, cut from `research-write-rate-2026-11` at
`0d44a7d6` (task P2). P2 measured 3.49 facts a second at 20,000 stored on its prototype (the filtered entity lookup).
The bar is 200 facts a second. Thesis section 5 (`memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md`) sets the robot
budgets: accept under 1 ms, searchable within 1 s.

## Verdict

**No. The batch write path does not reach 200 facts a second, at any store size measured.**

| Bar | Needed | Measured (batch path) | Result |
|---|---|---|---|
| 1. Write rate at 20,000 stored | 200 facts a second | 6.16 facts a second at 20,500 (13.40 at 400, 11.66 at 4,000) | fails, 32 times short |
| 2. Searchable within 1 s of accept | every fact | 0 of 19 samples at 200 a second offered; 0 of 16 at 5 a second | fails |
| 3. Same records as the current path | apart from ids and times | the same at 400 and 4,000; 20,000 not compared | holds on records at 400 and 4,000 |

The four changes together make the stated path 2.0 times faster than P2's prototype at 400 and 4,000 stored, and
1.7 times faster at 20,500. The privacy reads fall from 188 SQL statements a fact to 28 at 20,500 stored.

**The floor.** One derivation plan per fact still issues about 170 SQL statements and takes about 75 ms, even at
400 stored. One DuckDB statement on this machine takes 0.04 to 0.27 ms, so the statements alone cost 17 to 45 ms a
fact. 200 facts a second leaves 5 ms a fact. So no change that keeps one full plan per fact can reach the bar. Only
a working tier can reach it: facts accepted into memory at once, and the log and the indexes written later in
batches (thesis section 5). That is a design task.

A decision model is not needed for this task. The write path makes no judgment. No model was called in any run: the
extraction guard recorded 0 calls, with the providers blocked.

## The change

`enable_batch_write_path` (research branch only, default False, `src/prme/config.py`). It applies to every batch
flush, so `ingest_batch` of caller-stated facts uses it. It turns on four changes together:

1. **Filtered entity lookup.** P2's `enable_filtered_entity_lookup`: the entity lookup reads only the owner's entities
   whose text contains the name. The exact name and type match still decides.
2. **One privacy query per page of planning inputs.** `AdmissionCoordinator.require_legacy_page`
   (`src/prme/privacy/admission.py`) checks a page of events and nodes with one identity query per table and one
   `privacy_admissions` query (`PrivacyRepository.legacy_page`, `src/prme/storage/privacy.py`), instead of two
   statements per node. A marker on the caller's copy is refused before any read, as before. `PlanningGraph`
   (`src/prme/ingestion/planning.py`) checks all unread nodes of one `get_nodes` call as one page.
3. **One lexical commit per batch flush.** The lexical stage of each plan adds its documents to the open Tantivy
   writer with no commit and no reload (`LexicalIndex.stage(..., deferred=True)`). The identity check still runs,
   against the committed index and the documents already in the writer. `_finish_batch`
   (`src/prme/ingestion/pipeline.py`) commits once at the end of the flush, together with the deletes of the nodes
   the batch retired, then removes those nodes from the vector index.
4. **Claim merge through an indexed key lookup.** `find_repeated_claims(..., by_key=True)`
   (`src/prme/ingestion/claim_merge.py`) asks `DuckPGQGraphStore.fact_targets_by_key` for the subject's current
   claims whose predicate and object can match the claim key, instead of reading every claim of the subject. The
   query returns a superset (any non-ASCII value passes, because SQL cannot apply NFKC), and the exact copy checks
   still decide.

Files: `src/prme/config.py`, `src/prme/ingestion/claim_merge.py`, `src/prme/ingestion/pipeline.py`,
`src/prme/ingestion/planning.py`, `src/prme/privacy/admission.py`, `src/prme/storage/duckpgq_graph.py`,
`src/prme/storage/engine.py`, `src/prme/storage/lexical_index.py`, `src/prme/storage/privacy.py`. Tests:
`tests/test_batch_write_path.py` (6 tests: the default is off, the batch path stores what the current path stores
and a reopen reads the same, the key filter keeps every exact copy, the page check refuses what the object check
refuses, the planning graph checks one page once, the deferred stage keeps the identity check).

## Bar 1: write rate

Windows on P2's kept packs (copied to `/tmp/p5` first): 200 facts at 400 and 4,000 stored, 500 facts at 20,500
stored. 100 facts a call, `ingest_batch(wait_for_extraction=True)`, then `process_pending` until nothing waits. So
a window's time runs until every fact is committed and searchable. Timers and the SQL counter were on, the same on
both paths. Every window ended with 0 pending, 0 failed, 0 lexical documents uncommitted and 0 model calls.

| Path | Stored | Facts/s | ms/fact | SQL/fact |
|---|---|---|---|---|
| batch | 400 | 13.40 | 74.65 | 172.0 |
| batch | 4,000 | 11.66 | 85.76 | 166.1 |
| batch | 20,500 | 6.16 | 162.26 | 171.9 |
| prototype (P2) | 400 | 6.58 | 151.89 | 185.0 |
| prototype (P2) | 4,000 | 5.89 | 169.89 | 209.0 |
| prototype (P2) | 20,500 | 3.56 | 281.02 | 332.1 |

The prototype numbers here match P2's own (6.07, 5.32 and 3.49 facts a second).

**Stages, batch path, ms per fact.** Stages nest, so the column sums exceed the total.

| Stage | 400 | 4,000 | 20,500 |
|---|---|---|---|
| publish | 29.2 | 31.9 | 60.1 |
| plan preparation | 14.2 | 20.1 | 42.1 |
| populate | 7.6 | 13.8 | 35.4 |
| claim merge (`find_repeated_claims`) | 2.0 | 7.1 | 28.0 |
| batch finish (`_finish_batch`) | 0.73 | 1.11 | 17.1 |
| vector snapshot save | 0.13 | 0.87 | 17.3 |
| materialization queue drain | 9.8 | 10.7 | 14.6 |
| graph commit (`commit_derivation`) | 9.2 | 8.9 | 11.9 |
| mention links | 6.9 | 8.0 | 11.5 |
| privacy page check (`require_legacy_page`) | 6.6 | 7.6 | 9.0 |
| vector stage | 8.9 | 6.9 | 8.6 |
| embedding (`PlanningIndexes.prepare`) | 6.0 | 5.6 | 6.0 |
| lexical stage (deferred) | 4.3 | 4.2 | 5.2 |
| entity lookup (`find_entity`) | 3.9 | 4.6 | 4.8 |
| one lexical commit (`commit_deferred`), per fact | about 0.5 to 0.8 | | |

What grows with the store: the claim merge (2 to 28 ms; at 20,500 the key query itself takes about 27 ms) and the
vector snapshot save inside the batch finish (17 ms a fact at 20,500). The snapshot save comes from
`VectorIndex.delete_by_node_id` on a directory pack: each delete of a retired node saves the whole USearch snapshot,
about 0.33 times a fact at 20,500. A batch could save once. That was not applied.

**SQL statements per fact, top parts.**

| Part | Batch 20,500 | Prototype 20,500 |
|---|---|---|
| privacy reads | 25.7 (`legacy_page`) + 2.4 (per-object check) | 187.6 (`records_for_objects` 97.1, `_object_identity` 90.5) |
| graph commit | 23.9 | 23.9 |
| plan record | 18.0 | 18.0 |
| vector stage | 13.0 | 13.0 |
| extraction record | 13.0 | 13.0 |
| lexical stage | 13.0 | 13.0 |
| work claim | 8.1 | 8.1 |

Index work per fact (batch, 20,500): 1 embedding call, 1 lexical document, 2 vector adds.

**The SQL floor** (`sql_floor.py`, an in-memory DuckDB table of 20,000 rows, 2,000 statements of each kind):

| Statement | ms each | Statements in 5 ms |
|---|---|---|
| constant select | 0.042 | 119 |
| point lookup by key | 0.116 | 43 |
| one-row insert | 0.268 | 18 |

About 170 statements a fact cost 17 to 45 ms in SQL alone. The batch path is not limited by one slow part any more;
it is limited by the number of steps in one plan per fact.

**Measured, not applied: a two-statement key filter.** `fact_targets_by_key` is one join over edges and nodes. On a
subject with 65 `HAS_FACT` edges it takes 27 to 32 ms, because it scans all nodes. Two statements (the subject's
edges, then those nodes by id) took 8 to 9 ms on the same subject. That would save about 18 ms a fact at 20,500
stored. It does not change the verdict: the path would still be about 7 facts a second.

## Bar 2: searchable within 1 s

`stream.py` is P2's stream test with a `--path` choice. It offers `ingest_batch(wait_for_extraction=False)` calls and
polls `retrieve("Where is the {object} now?")` for sample facts in the same process. A fact is found when the
response holds its own claim. On the batch path a fact counts as committed when the batch finish that published it
returns.

**Flush timer facts.** `ingest_batch` without a wait starts its flush at once (`_spawn_flush`). It does not wait for
the 30 s batch flush timer (`BATCH_FLUSH_SECONDS = 30`). That timer is armed by `ingest()` without a wait, and
after a flush that leaves work unfinished. So the commit waits below are backlog and plan cost, not the timer.

**At 200 a second offered** (10 facts every 50 ms for 10 s, then 120 s to settle, 19 samples a run):

| Stored | Offered/s | Accept per fact p50 / p95 / max | Calls late p50 | Committed at stop | Commit wait p50 / p95 / max | Found | Within 1 s | First found | Ranks |
|---|---|---|---|---|---|---|---|---|---|
| 400 | 140.7 | 4.39 / 23.82 / 37.18 ms | 2.2 s | 1,210 of 2,000 (9.0/s) | 74.3 / 120.1 / 124.5 s | 16 | 0 | 2.59 s | 1 to 5 |
| 4,000 | 84.8 | 5.0 / 42.6 / 72.9 ms | 6.5 s | 1,000 of 2,000 (7.0/s) | 79.1 / 127.1 / 131.4 s | 14 | 0 | 3.76 s | 1 to 11 |
| 20,500 | 11.7 | 6.4 / 518.6 / 687.5 ms | 42.9 s | 280 of 2,000 (0.96/s) | 246.3 / 286.8 / 288.5 s | 11 | 0 | 24.86 s | 11 to 84 |

The backlog grew at every size. At stop the oldest waiting fact had waited at least 126 s (400), 132 s (4,000) and
290 s (20,500). P2's prototype under the same test committed 4.2, 3.7 and 0.24 facts a second, first found after
1.5, 2.2 and 19.1 s. (P2's report says 20 samples; its sample lists hold 19.)

**At 5 a second offered** (5 facts every 1 s for 30 s, then 60 s to settle, 16 samples a run). This rate is below the
write rate, so the backlog does not grow at 400. All 150 facts committed in every run.

| Path | Stored | Accept per fact p50 / p95 | Commit wait p50 / p95 / max | Committed within 1 s | Found p50 (min to max) | Within 1 s | Ranks |
|---|---|---|---|---|---|---|---|
| batch | 400 | 3.92 / 23.86 ms | 0.68 / 1.61 / 1.91 s | 110 of 150 | 1.57 s (1.04 to 2.58) | 0 of 16 | 1 to 4 |
| batch | 4,000 | 29.41 / 97.32 ms | 15.55 / 17.88 / 17.99 s | 5 of 150 | 14.69 s (2.31 to 18.61) | 0 of 16 | 1 to 10 |
| prototype | 400 | 20.13 / 53.82 ms | 4.15 / 5.52 / 5.99 s | 7 of 150 | 3.57 s (1.07 to 5.49) | 0 of 16 | 1 to 3 |
| prototype | 4,000 | 27.04 / 50.11 ms | 17.39 / 23.09 / 23.88 s | 3 of 150 | 14.62 s (2.32 to 23.57) | 0 of 16 | 1 to 10 |

- At 400 stored the batch path commits most facts within 1 s (110 of 150). No sample was found within 1 s. The
  fastest sample was found 1.04 s after its accept.
- At 4,000 stored one `retrieve()` takes 1.31 s on an idle store (P2, `read-latency.json`), and the poller's reads
  in the same process slow the writes. So "found within 1 s" cannot hold at 4,000 on any write path until the read
  is bounded (task 21, prme-fixer-sidekick).
- The accept budget of under 1 ms is not met either: 3.9 to 29 ms p50 a fact.

## Bar 3: the same records

`build.py` built the same observations through the batch path to 4,000 stored, with closed copies at 400 and 4,000.
`compare.py` reuses P2's comparison unchanged (`m4_compare.py`, `m4_checks.py`): every UUID replaced by a label,
build clocks masked and set to one fixed time, the same 25 questions. Judged on records, per prme-manager. Side a is
P2's `current-a` pack, built through the current path.

**Records: the same at both sizes** (`results/bar3-current-a-vs-batch.json`).

| Stored | Events | Nodes | Edges | Vectors | Lexical documents | Operations (checksums dropped, references sorted) |
|---|---|---|---|---|---|---|
| 400 | 400, same | 1,208, same | 1,600, same | 1,208, same | 800, same | 1,200, same |
| 4,000 | 4,000, same | 8,620, same | 16,115, same | 8,505, same | 7,885, same | 12,000, same |

**Results, for reference.**

| Comparison | Stored | Whole response | Top 10, exact | Top 10, scores to 9 places | Top 50, scores to 9 places | Bundle | Expected rank |
|---|---|---|---|---|---|---|---|
| current-a against current-b (P2) | 400 | 0 | 19 | 25 | 24 | 7 | 25 |
| current-a against batch | 400 | 0 | 0 | 25 | 22 | 0 | 25 |
| current-a against current-b (P2) | 4,000 | 0 | 25 | 24 | 24 | 3 | 25 |
| current-a against batch | 4,000 | 0 | 3 | 11 | 11 | 1 | 21 |
| current-a against batch, lexical index rewritten as one segment | 4,000 | 0 | 7 | 18 | 18 | 2 | 24 |

The batch path matches the current path less often than the current path matches itself. At 4,000 stored 4
questions moved the expected record by one place (3 to 2 once, 7 to 6 three times, for example the old screwdriver
and the leather lamp).

**Diagnosis.** The records are the same, so the difference is in how the index stores them.

- The Tantivy segment layouts differ. current-a at 4,000: one segment of 7,885 documents, 0 deleted. Batch at 4,000:
  segments of 7,690 (5 deleted), 100 and 100 documents. At 400: current-a 697, 100, 1, 1 and 1; batch one segment
  of 800.
- BM25 statistics are kept per segment, so the same document gets a slightly different score: the top score for
  "mug" is 3.132073 on current-a and 3.13269 on batch.
- `relex.py` rewrote a copy of the batch pack's lexical index as one segment of its live documents, in stored order
  (7,885 documents, 0 deleted). Against that copy the 4,000 results improve (table, last row:
  `results/bar3-diagnosis-relexed-4000.json`) but are still below current against current. So the segment layout and
  the deleted markers explain part of the gap, not all of it. The rest was not looked into.
- The 20,500 comparison was not run. It is optional, and there is no current-path pack at 20,000 (P2's M1 stopped
  at 8,000).

So bar 3 holds on records at 400 and 4,000. The results differ more than current against current, and a product
change would need a lexical layout that does not depend on the commit pattern, or a test that shows the rank change
is harmless.

## Privacy

**Statement: I cannot tell whether checking only the inputs read keeps the rule.**

Evidence (`privacy_probe.py`, `results/privacy-probe.json`). Each case is a fresh store per path. A stored row gets a
privacy marker it cannot satisfy, so any legacy input check that reads that row refuses. Then one stated fact is
written.

| Case | Current path | Batch path |
|---|---|---|
| marked earlier claim of the subject, another claim key | refused, `privacy_provider_unavailable` (1 fact node after) | not refused (2 fact nodes after) |
| marked earlier claim of the subject, the same claim key | refused | refused |
| marked entity whose name the fact does not hold | refused | not refused (2 fact nodes after) |

- The page check itself refuses what the object check refuses, on the same inputs
  (`test_privacy_page_check_refuses_what_the_object_check_refuses`).
- The difference is what is read. The key filter and the filtered entity lookup read fewer rows, so fewer rows are
  checked. A marked row that the fact does not depend on no longer blocks the write.
- For the batch path: the `require_legacy_inputs` docstring says "Recheck the actual participating identities of an
  ordinary provider edge". The rows the batch path skips do not participate in the plan.
- Against it: `docs/PRIVACY-POLICIES.md` (lines 84 to 115) says legacy group lookups refuse when protected data is
  in the selected owner or scope. That is a range check. It is used only by the group helper, and the current path's
  full scans happened to give the same effect for every write.
- The rule's owner must decide which reading is meant. Until then, the batch path's privacy result is unknown, not
  safe.

## Other facts

- **PostgreSQL falls back.** `legacy_page` returns None when the store has a pool (PostgreSQL), and the page check
  then runs the per-object check. `fact_targets_by_key` exists only on the DuckDB graph store, so PostgreSQL reads
  every claim of the subject as before. Only the entity filter and the one lexical commit apply there. No PostgreSQL
  run was made (no database on this machine).
- **Durability gap.** The graph commits each plan before the batch's one lexical commit. A crash between them loses
  the batch's lexical documents. `index_compaction` only removes stale entries and does not add missing ones, so the
  documents stay missing until `prme rebuild`. A single-file pack rebuilds its lexical index on open, so it does not
  have this gap. A product change needs a repair step (for example, recheck the last batch's plans on open).
- **Hang on a cancelled batch flush.** If a batch flush task is cancelled after the write queue's consumer has stopped
  (for example in `asyncio.run` teardown with flushes still running), the `finally` in `_flush` awaits
  `_finish_batch`, which waits on the write queue forever. The stream harness exits from inside the loop with
  `os._exit` to avoid it (`stream.py`, `run_and_exit`). The engine prototype does not fix it. A product change must
  either skip the queue on cancellation or close the batch before the queue stops.
- **Flaky test.** `tests/test_privacy_source_reads.py::test_AC01_ordinary_reads_return_admitted_memory_without_evidence_open[duckdb]`
  failed once in a targeted run. It passed with the change stashed, 3 times alone, once in the full targeted set,
  and in the final run. It is recorded as flaky, not looked into.
- **Fix cycles.** One fix-and-validate cycle was used: the stream harness hang (fixed with `run_and_exit`, and it
  worked). The second was not used.

## Tests

On the final tree, providers blocked:

```sh
OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY= .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_filtered_entity_lookup.py tests/test_claim_merge.py tests/test_durable_ingestion.py \
  tests/test_extraction_window.py tests/test_lexical_shutdown.py tests/test_batch_write_path.py \
  tests/test_batched_extraction.py tests/test_privacy_source_reads.py tests/test_config_audit.py \
  tests/test_structured_assertions.py tests/test_derivation_version.py
```

215 passed, 111 skipped (PostgreSQL, no database here). An earlier run of the first seven files with
`PRME_ENABLE_BATCH_WRITE_PATH=true` gave 117 passed, 88 skipped and 2 failed; the 2 failures are the tests that
assert the option is off by default, as expected. `DERIVATION_VERSION` is unchanged, because the derived records do
not change.

## Commands

```sh
cd ~/Sites/prism-write-batch
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY= PYTHONPATH=.
R=~/Sites/prism-write-rate/data/write-rate-2026-11 O=research/write-batch-2026-11/results S=research/write-batch-2026-11
# Windows (also --stored 4000 with $R/m4/current-a/4000/pack, and $R/m3-pack/pack --stored 20500 --facts 500;
# the same with --path prototype)
uv run python $S/window.py --path batch --pack $R/m4/current-a/400/pack --stored 400 --facts 200 \
  --out $O/window-batch-400.json
# Streams at 200 a second (also 4000, and $R/m3-pack/pack --stored 20500)
uv run python $S/stream.py --path batch --pack $R/m4/current-a/400/pack --stored 400 --out $O/stream-batch-400.json
# Streams at 5 a second (batch and prototype, 400 and 4000)
uv run python $S/stream.py --path batch --pack $R/m4/current-a/400/pack --stored 400 --batch 5 --every-ms 1000 \
  --seconds 30 --settle 60 --out $O/stream5-batch-400.json
# Bar 3
uv run python $S/build.py --path batch --to 4000 --snapshots 400 4000 --dir /tmp/p5/build/batch
uv run python $S/compare.py --a $R/m4/current-a:current --b /tmp/p5/build/batch:batch --points 400 4000 \
  --out $O/bar3-current-a-vs-batch.json
uv run python $S/relex.py --source /tmp/p5/build/batch/4000/pack --dest /tmp/p5/build/batch-relex/4000/pack
uv run python $S/compare.py --a $R/m4/current-a:current --b /tmp/p5/build/batch-relex:batch --points 4000 \
  --out $O/bar3-diagnosis-relexed-4000.json
# Privacy probe and SQL floor
uv run python $S/privacy_probe.py
uv run python $S/sql_floor.py --out $O/sql-floor.json
```

The relex step in the measured run was an inline copy of the code now in `relex.py`; running `relex.py` again gives
the same layout (7,885 documents, one segment, 0 deleted).

## Limits of the run

- One sample per measurement. The observations and the stores are deterministic; the clock is not.
- The same machine as P2: Apple M5 Pro laptop, 18 cores, 48 GB, Python 3.11.6, DuckDB 1.4.4, tantivy-py 0.25.0,
  usearch 2.26.2, FastEmbed `BAAI/bge-small-en-v1.5`. A robot's own hardware is slower.
- The windows ran one at a time, with timers and the SQL counter on, the same on both paths. The timers add some cost
  of their own.
- Some stream runs overlapped the bar 3 build, the comparisons and the relex comparison. Each run uses mostly one
  core of 18, so the effect is small, but it is not zero.
- The streams poll `retrieve()` in the same process as the writes, as P2 did, so slow reads slow the writes.
- No PostgreSQL run.

## Follow-up work (not done, not part of this ticket)

1. A working tier (thesis section 5): accept into memory, write the log and the indexes in batches. This is the only
   way to the bar.
2. A privacy decision: is the legacy input check meant for the inputs read, or for every node of the scope?
3. The lexical layout difference in bar 3 (the part the one-segment rewrite does not explain).
4. One vector snapshot save per batch instead of one per delete (17 ms a fact at 20,500).
5. The two-statement key filter (about 18 ms a fact at 20,500).
6. The durability gap and the cancellation hang, before any product use of the one lexical commit.

## Files

- `batch.py`: shared parts (the three paths, the timers), on top of P2's `common.py`.
- `window.py` (bar 1), `stream.py` (bar 2), `build.py`, `compare.py` and `relex.py` (bar 3), `privacy_probe.py`,
  `sql_floor.py`.
- `results/`: the small JSON results. The packs and logs stay in `/tmp/p5/` (not in git).
