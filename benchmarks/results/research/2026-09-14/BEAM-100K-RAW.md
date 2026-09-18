# BEAM 100K raw predict-only result

Date: 2026-09-14  
Status: complete registered execution

PRME completed the registered public BEAM 100K raw-memory workflow through the
unmodified OSS client from `mem0ai/memory-benchmarks`. The run ingested all 94
two-turn chunks with no recorded failure and retrieved the requested 50
memories for every one of the 20 probing questions.

| Measurement | Result |
|---|---:|
| Conversations | 1/1 |
| Source chunks | 94/94 |
| Failed chunks | 0 |
| Questions | 20/20 |
| Ability types | 10/10 |
| Results per query | 50/50 |
| Median retrieval latency | 86.75ms |
| Mean retrieval latency | 110.54ms |
| Retrieval latency p95 | 152.50ms |
| Maximum retrieval latency | 473.50ms |

The ten selected abilities were abstention, contradiction resolution, event
ordering, information extraction, instruction following, knowledge update,
multi-session reasoning, preference following, summarization, and temporal
reasoning. Each had two completed questions. The raw profile used PRME's public
`store_with_receipt()` path, one DuckDB thread, and local FastEmbed
`BAAI/bge-small-en-v1.5`; it made no extraction-model calls.

## Reproducibility

The protocol was registered before system execution and bound to PRME commit
`670d603363aa235b60eb476c57996437b4d84547`, upstream commit
`4b61c5d31b9c668a12b4f5e78064248a02c82d2b`, the official public dataset
revision `3205395e897e7318c7b094ef4e6047b9b82dbb03`, the normalized dataset
SHA-256, launcher, service, validator, upstream client and runner, run identity,
question selection, top-k, and chunk size.

The independent validator found no source, dataset, execution, ingestion,
question, result-count, ownership, finite-score, or artifact-completeness error.
The execution-manifest SHA-256 is
`6a16f361a86fc5c21eca68f989940af505d77510e100a2427278cfe333053ca0`.
The machine-readable result retains hashes for the adapter manifest, ingestion
checkpoint, dataset, and every prediction artifact.

The first clean-checkout invocation omitted PRME's API extra and stopped at the
FastAPI import before starting the service or touching benchmark state. The
registered manifest and dataset remained unchanged. The documented launcher
command now installs the API extra explicitly; that invocation produced this
complete result.

## Claim boundary

This result establishes complete source ingestion and top-50 retrieval through
the official BEAM client on one 100K conversation. It is deliberately
predict-only: it does not score generated answers or rubric nuggets. Mem0's
published managed-service scores use a different system and model stack, so
they are not a matched baseline for this raw PRME run. A separately registered
answer and judge execution is required for answer-quality claims.

## Artifacts

- [registration](beam-100k-raw-v1-registration.json)
- [machine-readable result](beam-100k-raw-v1-results.json)
