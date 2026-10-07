# Offline evidence gate

Run: commit `55d3eb61a5c914a801225b69aed7b6c77568b496` with uncommitted changes, overrides `{"packing": {"co_pack_sources": true, "token_budget": 8192}}`.

**Built packs, not the saved run.** Build `ingest-baseline` under `data/extracted-packs-v1/ingest-baseline` (1 packs, extraction model `prme-qwen3.5:35b-a3b-8k`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 152 | 0/152 | 716.8 | 0.551 / 0.675 s | 156.6 | 96.8% | 0 | 131/150 (87.3%) | 11 | 79.1% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 156.6, 96.8%, 21/32.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 32 | 31/32 (96.9%) | 21/32 (65.6%) | 65.6% | 42 | 42.5% | 60.4% |
| open-domain | 13 | 11/11 (100.0%) | 8/11 (72.7%) | 72.7% | 35.5 | 40.0% | 69.4% |
| single-hop | 70 | 70/70 (100.0%) | 65/70 (92.9%) | 92.9% | 6 | 81.7% | 86.0% |
| temporal | 37 | 37/37 (100.0%) | 37/37 (100.0%) | 100.0% | 4 | 97.3% | 85.7% |

Source fidelity: 102/1150 packed claims lack a source record in the same context (8.9%); 7 lack complete source text. Packed source-text tokens per context: 381.2. Claim totals can change through folding; these rates are not answer accuracy.

All annotated evidence present as complete source text: 87.3%.

Packed records through session expansion: 4515 reached (19.0% of packed records), 74 found by no other path, 2528 scored by a session decay.

Aggregation: 3 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 3.

Annotated evidence turns cited by a packed extracted record: 14 of 201 (7.0%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
