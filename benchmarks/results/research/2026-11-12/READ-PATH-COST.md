# Read path cost (2026-11-12)

Robots need a read under 50 ms at the 95th percentile. The address-first thesis measured about 220 ms per read
(`memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md`, finding F). This study measures where one `retrieve()` spends
its time with the current defaults (`memory_mode` classic), and removes the costs it can without changing any output.

## How it was measured

`benchmarks/diagnostics/read_cost.py`. It builds a synthetic home-robot store of a given size: routine patrol
observations, object moves, visitors and a few rare events, from `random.Random(1)`, admitted with
`ingest_fast_many()` and indexed with `process_pending()` (local embedding, no extraction, no model call). The
configuration is the evidence gate's (`gate_config`): current defaults, environment and `.env` ignored, the
opportunistic organizer off. Each run reads a fresh copy of the saved pack (schema version 2 of the tool; see "A
second baseline" below), answers one warm-up question, then 60 questions three times (180 timed reads per size). Each read is timed around `retrieve()` alone. The first round fingerprints every
read: ranked node ids and scores, packed node ids, rendered context, and the stored receipt with only its request id
replaced. `compare` fails on any difference.

Machine: Apple M5 Pro, 18 cores, Python 3.11.6, DuckDB 1.4.4, USearch 2.26.2. The machine was not idle (load
average about 3.6), as in daily use.

Setup trap: a fresh worktree for the `origin/main` side needs `uv sync --all-extras`, as the branch worktree has.
Without it `sentence-transformers` is missing, and every stored receipt differs in `execution.features.packages`
while nothing else changes. The bar (a) runs below used matching environments (`uv pip freeze` identical apart from
the project path).

## Baseline (origin/main e10ca299)

| Memories | Reads | p50 | p95 | Mean | Max |
|---|---|---|---|---|---|
| 3,000 | 180 | 209.8 ms | 276.2 ms | 213.7 ms | 355.4 ms |
| 20,000 | 180 | 230.3 ms | 295.6 ms | 228.4 ms | 331.4 ms |

Bar (b): p95 at 20,000 memories at most 147.8 ms.

Every read returned 1,000 ranked candidates (500 vector, 500 lexical) and packed about 136 records into the 4,000
token budget. The size of the store hardly matters: the cost is per candidate, not per stored memory.

### Where the time goes (20,000 memories, wall time per read)

| Step | ms per read |
|---|---|
| Candidate generation, all backends | 137.1 |
| of which: vector path, allowed-key check in DuckDB (`VectorIndex._fetch_allowed_keys`) | 73.9 |
| of which: loading the candidate records (`DuckPGQGraphStore.get_nodes`) | 50.8 |
| of which: USearch search itself | 4.9 |
| of which: query embedding (vector path less the two parts above) | 4.6 |
| of which: lexical search (Tantivy) | 3.2 |
| Scoring (`score_and_rank`) | 18.2 |
| Packing (`pack_context`) | 17.7 |
| Receipt: `make_receipt` 12.3 ms, `model_dump_json` 15.4 ms (twice: checksum and log) | 27.7 |

### Top 3 costs

1. **The vector path's allowed-key check, 74 ms.** USearch returns 1,500 keys (500 times the over-fetch factor 3).
   One DuckDB query checks them against the owner, lifecycle and model with `vector_key IN (?, ?, ...)`: 1,500
   bound parameters. A test on the 3,000 pack showed the parameters are the cost, not the join: the same query took
   66 ms with 1,500 parameters, 62 ms with one list parameter (`= ANY(?)`), and 4.8 ms with the keys written into
   the SQL as integer literals, with identical rows in identical order.
2. **Loading the candidate records, 51 ms.** `get_nodes` reads about 840 records per read with `id IN (?, ...)`: about
   500 to 840 bound parameters. With 500 ids, the query took 27.5 ms with parameters, 21.7 ms of it before any row
   was read (a `count(*)` of the same query), and 7.5 ms with the ids written as quoted UUID literals. Row decoding
   (`_row_to_node`) is only about 5 ms per read.
3. **Building and serializing the receipt, 28 ms**, plus about 3 ms of score-record validation inside scoring
   (pydantic `validate_components` and `ScoreTrace` construction run about 100,000 times per round of 60 reads).

Search itself (USearch, Tantivy, the embedding) is under 15 ms per read.

## A second baseline

The first runs read the saved pack itself. Every `retrieve()` stores its receipt, about 1.5 MB here, in the pack's
`operations` table, so each run left a larger store for the next one: after about 1,000 reads the 20,000 pack had
grown from 75 MB to 2.9 GB, and the ranking-profile lookup, which scanned those rows, took about 24 ms per read.
Runs on that growing store cannot be compared fairly. The tool now copies the saved pack for each run and deletes
the copy afterwards (schema version 2). The packs were rebuilt from the same seed (the same `text_sha256` at both
sizes), and origin/main was measured again on fresh copies. That run, `main2`, is the baseline for bar (b). The
rebuilt packs have new node ids, so `main2` fingerprints differ from the first `main` run; every later comparison is
against `main2`.

| Memories | Reads | p50 | p95 | Mean | Max |
|---|---|---|---|---|---|
| 3,000 | 180 | 222.1 ms | 293.2 ms | 225.9 ms | 369.0 ms |
| 20,000 | 180 | 244.0 ms | 317.3 ms | 246.5 ms | 411.0 ms |

Bar (b) on this baseline: p95 at 20,000 memories at most 158.7 ms (147.8 ms on the first baseline). The same code
measured about 7% slower than the first run, on a machine with a load average of 4 to 7.

## What changed

Three changes in `src/prme/storage/`. None of them touch the 24 files in `src/prme/retrieval/` whose hashes the
receipt records (`execution.features.source_files_sha256`), so receipts stay byte-identical.

1. `VectorIndex._fetch_allowed_keys` writes the 1,500 USearch keys into the SQL as integer literals
   (`str(int(key))`) instead of 1,500 bound parameters. The owner and lifecycle states stay bound.
2. `DuckPGQGraphStore._get_nodes_sync` writes node ids as quoted literals, but only when every id is exactly
   `str(UUID(id))`: 36 lowercase hexadecimal digits and hyphens, which cannot carry SQL. Any other string keeps the
   bound parameters, so it reads, and fails, exactly as before (DuckDB accepts uppercase, braced and hyphen-free
   forms through a parameter, and rejects text that is not a UUID).
3. `RankingProfileStore` finds the latest profile state by id first (`ORDER BY created_at DESC, id DESC LIMIT 1` in a
   subquery), and reads one payload, instead of reading every `RANKING_PROFILE_STATE_CHANGED` payload of the owner.
   The row returned is the same; the id is the primary key, so there is no tie. The SQL runs on DuckDB and
   PostgreSQL.

One more change was tried and reverted: serializing the receipt once in `pipeline.py` (about 8 ms per read). It
left the receipt's content unchanged, but `pipeline.py` is one of the hashed files, so `source_files_sha256` changed
and bar (a) failed.

## Results

| Run | Memories | p50 | p95 | Mean | Max | Identical to `main2` |
|---|---|---|---|---|---|---|
| `main2` | 3,000 | 222.1 ms | 293.2 ms | 225.9 ms | 369.0 ms | |
| cycle 2 | 3,000 | 120.6 ms | 186.2 ms (0.64x) | 125.0 ms | 302.2 ms | 60/60 |
| `main2` | 20,000 | 244.0 ms | 317.3 ms | 246.5 ms | 411.0 ms | |
| cycle 2 | 20,000 | 141.0 ms | 221.5 ms (0.70x) | 148.4 ms | 340.7 ms | 60/60 |

"Identical" means the same ranked node ids and scores, packed node ids, rendered context and stored receipt bytes
(request id replaced) on every question. The fingerprint digests are equal at both sizes
(`read-cost-main2.json`, `read-cost-cycle2.json`). Candidate generation fell from 128.6 ms to 34.1 ms per read at
20,000 memories; the vector path from 77.1 ms to 15.8 ms.

Cycle 1 (vector keys only) on the growing pack: p95 192.3 ms and 201.6 ms against 276.2 ms and 295.6 ms, 60/60
identical. Those runs read a larger store than their baseline, so they are recorded only as history.

## Bar (a)

Run after the two cycles, with no code change. Off side: `origin/main` e10ca299 in its own detached worktree. On
side: this branch at 455b6b7a. Providers blocked. Model identity `e04da138`, Ollama 0.40.16.

**Probes.** The defect, chat and member probes (member with `--speaker` and `enable_speaker_grounding=true`) were
built on each side from the saved extraction cache with `PRME_BENCH_CACHE_ONLY=1`: 0 model calls (14, 54 and 30
cache hits).

| Probe | Top 3, main | Top 3, branch | Top 5, main | Top 5, branch | Claims on the named owner, main and branch |
|---|---|---|---|---|---|
| `defects-v1` | 7/7 | 7/7 | 7/7 | 7/7 | 12 and 12 |
| `chat-v1` | 14/18 | 14/18 | 15/18 | 15/18 | 35 and 35 |
| `member-v1` | 10/12 | 10/12 | 11/12 | 11/12 | 7 and 7 |

Answer ranks and top results are identical on all 37 probes. Rebuilt packs differ in some lines lower in the
context, which change order (`chat-v1` 3 of 18 contexts, `member-v1` 3 of 12, and the `record_types` count of 3
`chat-v1` probes). That is the known engine-wide tie-break on random node ids, an open item that this branch does not
touch: `origin/main` built twice against itself shows the same kind of difference (`chat-v1` 1 of 18 contexts and
the same 3 `record_types` counts, `member-v1` 3 of 12, `defects-v1` 0 of 7).

**Same pack, each side's code.** To take the rebuild out, each probe pack built on `origin/main` was copied four
times and measured with `chat_probe measure` (no model): twice with the branch, twice with `origin/main`. Every
context is identical (37 of 37 in both branch runs), and every stored retrieval receipt is byte-identical with its
request id masked (37 of 37), as in the main-against-main control. This is the bar (a) evidence for the probes.

**Offline evidence gate.** `product_packing gate` with the regenerated LongMemEval-S controls, no `--set`, then
`gate-compare`: every row of the first table +0.0 pp, interval +0.0 to +0.0, 0 won and 0 lost (1,540 LoCoMo and
500 LongMemEval-S questions). No question lost or gained all its evidence among the candidates. All 2,040 rows are
identical except their timing, the context digest included. Both sides ran at the same time on one machine (load
average 15 to 23), so the gate's latency is not a clean comparison: p50 / p95 LoCoMo 180 / 265 ms to 123 / 203 ms,
LongMemEval-S 183 / 268 ms to 141 / 217 ms.

**Tests.** The targeted tests listed under bar (c), `test_determinism_rebuild.py` included: 97 passed, 27 skipped
(PostgreSQL), providers blocked.

Reports (ignored): `data/read-cost-v1/gate/` (`off`, `on`, `compare`) and `data/chat-probe-v1/<conversation>/`
`rc-M`, `rc-M2` (main twice) and `rc-B` (branch).

## Verdict

- Bar (a): **met.** The stage-timing questions are identical at both sizes (60/60 and 60/60). The probes, the
  same-pack receipts, the offline evidence gate and the determinism tests show no difference ("Bar (a)" above).
- Bar (b): **not met.** p95 at 20,000 memories is 221.5 ms, 0.70x of `main2`; the bar is 0.50x (158.7 ms).
- Bar (c): ruff passes on the changed files. Targeted tests pass with providers blocked: 97 passed, 27 skipped
  (PostgreSQL), in `test_determinism_rebuild.py`, `test_packing_receipt_compatibility.py`, `test_privacy_receipts.py`,
  `test_ranking_profiles.py`, `test_ranking_profile_interfaces.py`, `test_owner_scoped_graph_reads.py`,
  `test_vector_filtered_recall.py`, `test_vector_recovery.py`, `test_graph_neighborhood_tenancy.py` and
  `test_storage_cancellation.py`.
- Distance from 50 ms: p95 221.5 ms is 171.5 ms over, 4.4 times the target. p50 is 141.0 ms.

## Where the time goes now (20,000 memories, ms per read)

| Step | ms per read |
|---|---|
| Receipt: two `model_dump_json` calls (checksum, then log) 16.4, `make_receipt` 13.5, the 1.5 MB INSERT 8.9, `json.dumps` 2.0 | about 41 |
| Candidate generation: vector search 16.9 (allowed keys 7.2, USearch 5.0, query embedding 4.6), loading records 15 (841 rows, decoding 5.2), pinned candidates 7.7, graph seeds 4.8, lexical 3.2, merge 2.4 | 34.1 (stages run concurrently) |
| Scoring (`score_and_rank`), mostly score-record validation (`validate_components`, about 99,000 calls per round) | 18 to 22 |
| Packing (`pack_context`) | 18 to 20 |
| Python garbage collection | about 14 |

The tail (p95 221.5 ms against a mean of 148.4 ms) comes from garbage-collection pauses and machine load.

The receipt is now the largest cost. Its serializer runs on the whole 1.5 MB receipt twice per read, and
`make_receipt` validates the score records a second time. Removing all of the receipt and garbage-collection time
would still leave a mean of about 93 ms: 1,000 candidates scored and packed per read is the floor at the current
candidate limits (`vector_k=500`, `lexical_k=500`).

## Bounded alternative

A third cycle needs approval. Two options:

1. **Receipt, with one accepted field change.** Serialize the receipt once and reuse the text for the checksum and
   the log, and validate the score records once. The receipt content stays the same, but `source_files_sha256`
   changes because `pipeline.py` is hashed. If that one field difference is accepted, this saves about 16 to 25 ms
   per read. It does not reach bar (b) alone.
2. **A robot read profile.** Reaching 50 ms needs fewer candidates and a smaller receipt per read, which changes
   outputs. That is a design call: a setting that is off by default (for example under `memory_mode`
   `address_first`), measured on the probes and the gate like any other option.

Follow-up finding: every read stores a receipt of about 1.5 MB. At 1,000 reads that is 1.5 GB in `operations`.
A robot that reads often fills its disk with receipts, whatever the read time.
