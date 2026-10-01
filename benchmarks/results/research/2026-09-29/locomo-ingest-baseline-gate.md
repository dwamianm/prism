# Offline evidence gate

Run: commit `09949aa76f2c107c78bf85f3028a60f97b5d2807`, current defaults.

**Built packs, not the saved run.** Build `ingest-baseline-locomo-deepseek` under `data/extracted-packs-v1/ingest-baseline-locomo-deepseek` (10 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 1540 | 0/1540 | 907.7 | 0.305 / 0.462 s | 79.1 | 96.1% | 0 | 1161/1536 (75.6%) | 19 | 71.9% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 79.1, 96.1%, 105/282.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 282 | 242/282 (85.8%) | 105/282 (37.2%) | 37.2% | 79 | 32.5% | 42.0% |
| open-domain | 96 | 69/92 (75.0%) | 41/92 (44.6%) | 44.6% | 113 | 27.0% | 55.5% |
| single-hop | 841 | 838/841 (99.6%) | 738/841 (87.8%) | 87.8% | 10 | 74.8% | 82.1% |
| temporal | 321 | 318/321 (99.1%) | 277/321 (86.3%) | 86.3% | 8 | 69.4% | 76.2% |

Packed records through session expansion: 41698 reached (34.2% of packed records), 474 found by no other path, 11371 scored by a session decay.

Aggregation: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 5, LEXICAL_AGG 41, VECTOR 41.

Annotated evidence turns cited by a packed extracted record: 1342 of 2345 (57.2%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
