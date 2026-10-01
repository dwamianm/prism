# Offline evidence gate

Run: commit `09949aa76f2c107c78bf85f3028a60f97b5d2807`, current defaults.

**Built packs, not the saved run.** Build `fact-text-w4-locomo-deepseek` under `data/extracted-packs-v1/fact-text-w4-locomo-deepseek` (10 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 1540 | 0/1540 | 922.2 | 0.315 / 0.530 s | 93.4 | 84.1% | 0 | 1177/1536 (76.6%) | 24 | 72.6% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 93.4, 84.1%, 106/282.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 282 | 235/282 (83.3%) | 106/282 (37.6%) | 37.6% | 93 | 25.7% | 42.3% |
| open-domain | 96 | 67/92 (72.8%) | 46/92 (50.0%) | 50.0% | 106.5 | 26.3% | 57.8% |
| single-hop | 841 | 835/841 (99.3%) | 745/841 (88.6%) | 88.6% | 10.5 | 72.1% | 82.8% |
| temporal | 321 | 318/321 (99.1%) | 280/321 (87.2%) | 87.2% | 10 | 68.8% | 76.9% |

Packed records through session expansion: 40454 reached (28.1% of packed records), 372 found by no other path, 15804 scored by a session decay.

Aggregation: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 1, LEXICAL_AGG 41, VECTOR 41.

Annotated evidence turns cited by a packed extracted record: 1288 of 2345 (54.9%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
