# LongMemEval-S current-PRME baseline: capture result

**Status:** complete memory-side baseline; reader and judge pending provider credit  
**Completed:** 2026-09-17  
**Registration:** `longmemeval-s-prme-baseline-v1-registration.json`  
**Capture result:** `longmemeval-s-prme-baseline-v1-capture.json`  
**Capture result identity:** `da16f20ad2427299eb5765d9a13f39f8a54b1aacc4bb6734b1f6a09284a8637c`

## Result

Unchanged PRME completed all 500 cleaned LongMemEval-S questions under the
registered direct-turn ingestion and default retrieval configuration. All 500
portable packs, 500 captured contexts and 1,000 retrieval receipts passed the
registered integrity and replay checks. Cold and warm retrieval produced the
same context and result order for every question.

| Capture gate | Result |
|---|---:|
| Questions | 500 / 500 |
| Failures | 0 |
| Durable replayable receipts | 1,000 |
| Cold/warm context mismatches | 0 |
| Verified pack trees | 500 / 500 |
| Input turns | 246,750 |
| Stored non-empty turns | 246,738 |
| Explicitly omitted empty, unlabeled turns | 12 |

The contexts nearly exhaust the registered 4,096-token budget after its
100-token caller reservation: median 3,976 tokens, mean 3,964.75, p95 3,996 and
maximum 3,996. The median context contains 24 complete records.

## Source evidence

The benchmark labels evidence for 470 answerable questions. The other 30 are
abstention questions.

| Evidence measure over packed context | Result |
|---|---:|
| Any required session | 467 / 470 (99.36%) |
| Every required session | 437 / 470 (92.98%) |
| Any required labeled turn | 451 / 470 (95.96%) |
| Every required labeled turn | 403 / 470 (85.74%) |

| Category | Questions | Complete sessions | Complete labeled turns |
|---|---:|---:|---:|
| Knowledge update | 72 | 68 (94.44%) | 67 (93.06%) |
| Multi-session | 121 | 110 (90.91%) | 90 (74.38%) |
| Single-session assistant | 56 | 56 (100%) | 49 (87.50%) |
| Single-session preference | 30 | 29 (96.67%) | 26 (86.67%) |
| Single-session user | 64 | 64 (100%) | 62 (96.88%) |
| Temporal reasoning | 127 | 110 (86.61%) | 109 (85.83%) |

## Gap localization

Candidate generation is not the observed source-recall bottleneck. Before
packing, the returned candidate set contained every required session and every
labeled evidence turn for all 470 answerable questions. The per-question
candidate set ranged from 396 to 599 records with a median of 490. Every one of
the 33 incomplete-session contexts and all 67 incomplete-turn contexts lost its
evidence only when selecting roughly 24 records for the fixed token budget.

This result changes the next experiment:

1. Do not add Zep-style retrieval scopes merely to raise candidate recall. The
   current candidate pool already contains all labeled evidence on this cohort.
2. Do not enable the cross-encoder reranker. The receipts localize the measured
   loss to budgeted selection, and PRME's prior reranker trials did not justify
   a default change.
3. Do not adopt newest-wins conflict semantics. Nothing in this result weakens
   PRME's explicit evidence and conflict model.
4. Test a packing-only, gold-label-free selection policy that distributes a
   fixed budget across independently relevant sessions or evidence groups while
   retaining score, token cost, pins, instructions, tasks, conflicts and exact
   provenance.

The whole 500-question cohort has now been inspected and is development
evidence for any policy motivated by this result. A policy selected here needs
a separately registered confirmation on another conversational-memory cohort
before it can replace the current default.

## Timing boundary

The registered five-worker capture measured local retrieval while other packs
were concurrently ingesting and embedding:

| Retrieval | p50 | p95 |
|---|---:|---:|
| Cold, after reopen | 379 ms | 651 ms |
| Immediate warm repeat | 301 ms | 572 ms |

These are reproducible workload measurements, not a direct comparison with
Zep's reported hot in-memory hosted latency. A controlled isolated latency arm
is required before making a serving-speed claim.

## Pending answer result

The registered reader and judge are both `gpt-5.4-2026-03-05` at medium
reasoning. The latest preflight on 2026-09-18 still returned HTTP 429 with
`credit_balance_exhausted`. No substitute reader will be reported as a matched
Zep comparison. The exact saved contexts make the answer and judge stages
restartable without repeating ingestion or retrieval when provider credit is
available.

