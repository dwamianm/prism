# MemoryAgentBench atomic bulk-ingestion development trial

**Completed:** 2026-09-14  
**Systems:** serial typed `store()` admission versus atomic raw batch admission  
**Reader:** local Ollama `qwen3.5:9b`, temperature 0, seed 42, thinking disabled  
**Questions:** first 20 registered Banking77 test-time-learning questions

## Result

Atomic batch admission reduced construction time for 5,897 records from
463.112 seconds to 47.401 seconds on the same local host. This is an observed
89.8% reduction, or 9.77 times the prior throughput. The batch run completed
19/20 answers, while the earlier serial run completed 20/20.

| Measurement | Serial store | Atomic batch | Observed change |
|---|---:|---:|---:|
| Construction time | 463.112 s | 47.401 s | -89.8% |
| Exact match | 20/20 | 19/20 | -1 answer |
| Mean packed context | 3,969.70 tokens | 3,965.05 tokens | -4.65 tokens |
| Mean query time | 5.097 s | 4.983 s | -0.115 s |

The performance result clears the operational goal for this change. The answer
result does not establish either quality equivalence or a systematic
regression: it is one fixed 20-question cohort and one local reader execution.
The two runs also created distinct immutable event identities and admission
times, so their packed contexts were structurally similar but not byte
identical. No quality claim is based on this timing trial.

## Protocol and verification

Both runs used the same pinned MemoryAgentBench and dataset revisions, source
splitting, 4,096-token compact context budget, episode routing configuration,
reader model, temperature, seed, reasoning setting, and numeric-label output
contract. Adapter schema 8 performed 5,897 sequential typed `store()` calls.
Schema 9 admitted the same ordered raw records with one `ingest_fast_many()`
transaction, then drained the existing bounded, restart-safe materialization
queue before publishing the pack.

The batch trial was registered before inference against PRME
`36afbc8ef28be20628661e4690e56d6959beaf9b`, MemoryAgentBench
`fe1735de8cf8b9908e1e3d3b5612afc815698062`, and dataset revision
`7ea066982b140a19337e17e60d45d4076e042faf`. Verification authenticated all 20
ordered result rows and captures, durable receipt checksums, ranking replay,
token recounts, scopes, completed pack state, and exact runtime source hashes.
The earlier serial arm was separately registered and verified at PRME
`8676c9c9600bd0cd592bb186cea2d0ee2513f7cc`.

## Decision

Keep atomic raw batch admission in the public engine, synchronous client, HTTP,
and MCP surfaces. It preserves append-only source durability and all-or-nothing
admission while removing most per-record transaction overhead. Keep adapter
schema 9 so future MemoryAgentBench work evaluates the public bulk-import path
and reports its actual quality rather than inheriting performance from a test
adapter shortcut.

The next reliability boundary for bulk imports is retry identity: a client that
loses the response after commit needs a safe way to recover the accepted batch
without duplicating source events.

Artifacts: [aggregate comparison](memoryagentbench-banking-bulk-dev20-comparison.json)
and [batch verification](memoryagentbench-banking-bulk-dev20-prme-verification.json).
