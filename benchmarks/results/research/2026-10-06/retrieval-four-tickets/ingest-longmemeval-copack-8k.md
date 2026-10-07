# Offline evidence gate

Run: commit `55d3eb61a5c914a801225b69aed7b6c77568b496` with uncommitted changes, overrides `{"packing": {"co_pack_sources": true, "token_budget": 8192}}`.

**Built packs, not the saved run.** Build `four-tickets-lme-sample` under `data/extracted-packs-v1/four-tickets-lme-sample` (2 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| longmemeval | 2 | 0/2 | 1222.5 | 0.424 / 0.539 s | 54.5 | 87.1% | 0 | 2/2 (100.0%) | 9.5 | 95.5% |

## longmemeval

2026-09-23 baseline: 23.9 records per context, 32% memory text, all evidence packed for 403/470 annotated questions. This run: 54.5, 87.1%, 2/2.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| single-session-user | 2 | 2/2 (100.0%) | 2/2 (100.0%) | 100.0% | 9.5 | 100.0% | 95.5% |

Source fidelity: 2/14 packed claims lack a source record in the same context (14.3%); 0 lack complete source text. Packed source-text tokens per context: 381.0. Claim totals can change through folding; these rates are not answer accuracy.

All annotated evidence present as complete source text: 100.0%.

Packed records through session expansion: 19 reached (17.4% of packed records), 0 found by no other path, 12 scored by a session decay.

Aggregation: 1 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 1 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 1, VECTOR 1.

Annotated evidence turns cited by a packed extracted record: 1 of 2 (50.0%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
