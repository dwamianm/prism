# Offline evidence gate

Run: commit `f05b0ff0bcb293e1019f9c717f92dcca8d070168`, current defaults.

**Built packs, not the saved run.** Build `cached-locomo-control` under `data/extracted-packs-v1/cached-locomo-control` (3 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 466 | 0/466 | 860.2 | 0.353 / 0.614 s | 76.6 | 96.1% | 0 | 338/462 (73.2%) | 23 | 70.8% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 76.6, 96.1%, 38/101.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 101 | 83/101 (82.2%) | 38/101 (37.6%) | 37.6% | 78 | 34.6% | 42.2% |
| open-domain | 33 | 21/29 (72.4%) | 9/29 (31.0%) | 31.0% | 142 | 20.0% | 58.2% |
| single-hop | 230 | 230/230 (100.0%) | 204/230 (88.7%) | 88.7% | 11 | 75.5% | 82.9% |
| temporal | 102 | 101/102 (99.0%) | 87/102 (85.3%) | 85.3% | 8 | 70.6% | 76.1% |

Packed records through session expansion: 13361 reached (37.4% of packed records), 130 found by no other path, 3891 scored by a session decay.

Aggregation: 9 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 9 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 9, LEXICAL_AGG 9, VECTOR 9.

Annotated evidence turns cited by a packed extracted record: 406 of 746 (54.4%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
