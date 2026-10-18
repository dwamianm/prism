# Offline evidence gate

Run: commit `d810ee5552e388f85aac62f4cf8c6c6038f0b1c8`, current defaults.

**Built packs, not the saved run.** Build `full-per-turn-locomo-deepseek-d810ee55` under `/Users/dmac/Sites/prism/data/extracted-packs-v1/full-per-turn-locomo-deepseek-d810ee55` (10 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 1540 | 0/1540 | 924.7 | 0.334 / 0.475 s | 95.2 | 95.2% | 0 | 1264/1536 (82.3%) | 17 | 76.4% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 95.2, 95.2%, 138/282.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 282 | 251/282 (89.0%) | 138/282 (48.9%) | 48.9% | 61 | 36.0% | 49.5% |
| open-domain | 96 | 73/92 (79.3%) | 54/92 (58.7%) | 58.7% | 88 | 25.7% | 61.5% |
| single-hop | 841 | 839/841 (99.8%) | 781/841 (92.9%) | 92.9% | 8 | 73.5% | 86.0% |
| temporal | 321 | 318/321 (99.1%) | 291/321 (90.7%) | 90.7% | 7 | 72.0% | 79.3% |

Packed records through session expansion: 22629 reached (15.4% of packed records), 696 found by no other path, 12435 scored by a session decay.

Aggregation: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 38, LEXICAL_AGG 41, VECTOR 41.

Annotated evidence turns cited by a packed extracted record: 339 of 2345 (14.5%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
