# Read path cost (2026-11-12)

Robots need a read under 50 ms at the 95th percentile. The address-first thesis measured about 220 ms per read
(`memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md`, finding F). This study measures where one `retrieve()` spends
its time with the current defaults (`memory_mode` classic), and removes the costs it can without changing any output.

## How it was measured

`benchmarks/diagnostics/read_cost.py`. It builds a synthetic home-robot store of a given size: routine patrol
observations, object moves, visitors and a few rare events, from `random.Random(1)`, admitted with
`ingest_fast_many()` and indexed with `process_pending()` (local embedding, no extraction, no model call). The
configuration is the evidence gate's (`gate_config`): current defaults, environment and `.env` ignored, the
opportunistic organizer off. Each run opens the saved pack, answers one warm-up question, then 60 questions three
times (180 timed reads per size). Each read is timed around `retrieve()` alone. The first round fingerprints every
read: ranked node ids and scores, packed node ids, rendered context, and the stored receipt with only its request id
replaced. `compare` fails on any difference.

Machine: Apple M5 Pro, 18 cores, Python 3.11.6, DuckDB 1.4.4, USearch 2.26.2. The machine was not idle (load
average about 3.6), as in daily use.

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
