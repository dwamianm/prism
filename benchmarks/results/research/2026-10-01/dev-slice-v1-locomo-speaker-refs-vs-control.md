# Offline evidence gate comparison

Before: commit `f05b0ff0bcb293e1019f9c717f92dcca8d070168`, current defaults.
After: commit `f05b0ff0bcb293e1019f9c717f92dcca8d070168`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 73.2% | 72.5% | -0.6 pp | -1.5 pp to +0.0 pp | 0 | 3 | 459 |
| locomo | All evidence packed with memory text | 73.2% | 72.5% | -0.6 pp | -1.5 pp to +0.0 pp | 0 | 3 | 459 |
| locomo | Evidence recall | 80.6% | 80.1% | -0.4 pp | -1.2 pp to +0.1 pp | 1 | 3 | 458 |
| locomo | All evidence among the candidates | 94.2% | 94.2% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 462 |
| locomo | Projected accuracy | 70.8% | 70.3% | -0.5 pp | -1.1 pp to +0.0 pp | 0 | 3 | 463 |
| locomo | All evidence packed, multi-hop | 37.6% | 37.6% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 101 |
| locomo | All evidence packed, open-domain | 31.0% | 31.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 29 |
| locomo | All evidence packed, single-hop | 88.7% | 87.4% | -1.3 pp | -3.0 pp to +0.0 pp | 0 | 3 | 227 |
| locomo | All evidence packed, temporal | 85.3% | 85.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 102 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/466 to 0/466 | 860.2 to 859.8 | 0.353 / 0.614 s to 0.344 / 0.537 s | 76.6 to 76.5 | 96.1% to 96.1% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 9.2 to 12.5, after 15.0 to 14.5.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 9 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 9 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 9, LEXICAL_AGG 9, VECTOR 9.
- locomo after: 9 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 9 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 9, LEXICAL_AGG 9, VECTOR 9.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 101 | 0 | 0 |
| locomo | open-domain | 33 | 0 | 0 |
| locomo | single-hop | 230 | 0 | 0 |
| locomo | temporal | 102 | 0 | 0 |

- locomo questions that entered the current-state path: 0.
- locomo questions that left the current-state path: 0.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 13361 (37.4%) / 130 / 3891 | 13397 (37.6%) / 123 / 3897 |
| locomo | multi-hop | 2875 (36.9%) / 16 / 871 | 2880 (37.0%) / 16 / 873 |
| locomo | open-domain | 965 (38.6%) / 7 / 295 | 982 (39.2%) / 10 / 308 |
| locomo | single-hop | 6659 (38.0%) / 66 / 1896 | 6666 (38.1%) / 58 / 1883 |
| locomo | temporal | 2862 (36.3%) / 41 / 829 | 2869 (36.5%) / 39 / 833 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
