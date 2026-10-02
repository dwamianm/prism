# Offline evidence gate comparison

Before: commit `f05b0ff0bcb293e1019f9c717f92dcca8d070168`, current defaults.
After: commit `f05b0ff0bcb293e1019f9c717f92dcca8d070168`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 75.7% | 75.5% | -0.2 pp | -0.6 pp to +0.2 pp | 3 | 6 | 1527 |
| locomo | All evidence packed with memory text | 75.7% | 75.5% | -0.2 pp | -0.6 pp to +0.2 pp | 3 | 6 | 1527 |
| locomo | Evidence recall | 81.9% | 81.7% | -0.2 pp | -0.6 pp to +0.1 pp | 5 | 7 | 1524 |
| locomo | All evidence among the candidates | 95.5% | 95.5% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 72.0% | 71.9% | -0.1 pp | -0.4 pp to +0.1 pp | 3 | 6 | 1531 |
| locomo | All evidence packed, multi-hop | 37.6% | 36.9% | -0.7 pp | -1.8 pp to +0.0 pp | 0 | 2 | 280 |
| locomo | All evidence packed, open-domain | 44.6% | 44.6% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 92 |
| locomo | All evidence packed, single-hop | 87.9% | 87.6% | -0.2 pp | -0.8 pp to +0.2 pp | 2 | 4 | 835 |
| locomo | All evidence packed, temporal | 86.3% | 86.6% | +0.3 pp | +0.0 pp to +0.9 pp | 1 | 0 | 320 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 907.7 to 907.5 | 0.330 / 0.521 s to 0.333 / 0.533 s | 79.1 to 79.0 | 96.1% to 96.1% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 8.3 to 7.5, after 9.5 to 12.0.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 5, LEXICAL_AGG 41, VECTOR 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 5, LEXICAL_AGG 41, VECTOR 41.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 0 |
| locomo | open-domain | 96 | 0 | 0 |
| locomo | single-hop | 841 | 0 | 0 |
| locomo | temporal | 321 | 0 | 0 |

- locomo questions that entered the current-state path: 0.
- locomo questions that left the current-state path: 0.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 41688 (34.2%) / 445 / 11349 | 41868 (34.4%) / 434 / 11420 |
| locomo | multi-hop | 7725 (34.9%) / 57 / 2196 | 7750 (35.1%) / 57 / 2195 |
| locomo | open-domain | 2497 (32.1%) / 25 / 692 | 2527 (32.5%) / 32 / 709 |
| locomo | single-hop | 22977 (34.7%) / 249 / 6236 | 23088 (34.9%) / 238 / 6290 |
| locomo | temporal | 8489 (33.1%) / 114 / 2225 | 8503 (33.2%) / 107 / 2226 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
