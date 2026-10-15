# Offline evidence gate comparison

Before: commit `58caddb1a88dd60d51fc97c15742aee6ec8cf34a`, current defaults.
After: commit `58caddb1a88dd60d51fc97c15742aee6ec8cf34a`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 81.6% | 81.5% | -0.1 pp | -0.9 pp to +0.8 pp | 22 | 23 | 1491 |
| locomo | All evidence packed with memory text | 81.6% | 81.5% | -0.1 pp | -0.9 pp to +0.8 pp | 22 | 23 | 1491 |
| locomo | Evidence recall | 87.3% | 87.6% | +0.3 pp | -0.4 pp to +1.0 pp | 39 | 29 | 1468 |
| locomo | All evidence among the candidates | 95.9% | 96.0% | +0.1 pp | +0.0 pp to +0.2 pp | 1 | 0 | 1535 |
| locomo | Projected accuracy | 75.9% | 75.9% | -0.1 pp | -0.7 pp to +0.5 pp | 22 | 23 | 1495 |
| locomo | All evidence packed, multi-hop | 47.9% | 47.5% | -0.4 pp | -2.8 pp to +2.1 pp | 6 | 7 | 269 |
| locomo | All evidence packed, open-domain | 56.5% | 57.6% | +1.1 pp | -3.3 pp to +5.4 pp | 3 | 2 | 87 |
| locomo | All evidence packed, single-hop | 92.4% | 92.4% | +0.0 pp | -1.1 pp to +1.1 pp | 11 | 11 | 819 |
| locomo | All evidence packed, temporal | 90.0% | 89.7% | -0.3 pp | -1.6 pp to +0.9 pp | 2 | 3 | 316 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 929.7 to 928.4 | 0.412 / 0.600 s to 0.367 / 0.524 s | 96.2 to 96.1 | 95.2% to 95.3% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 7.4 to 7.7, after 12.4 to 4.8.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 1 (conv-44-q0027).
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL_AGG 41, VECTOR 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL_AGG 41, VECTOR 41.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 0 |
| locomo | open-domain | 96 | 0 | 0 |
| locomo | single-hop | 841 | 0 | 0 |
| locomo | temporal | 321 | 0 | 0 |

- locomo questions that entered the current-state path: 1 (conv-41-q0037).
- locomo questions that left the current-state path: 0.
The JSON report lists every question.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 23334 (15.8%) / 725 / 12540 | 23155 (15.6%) / 713 / 12436 |
| locomo | multi-hop | 4369 (16.1%) / 99 / 2368 | 4363 (16.1%) / 99 / 2413 |
| locomo | open-domain | 1374 (14.2%) / 48 / 759 | 1347 (14.0%) / 58 / 726 |
| locomo | single-hop | 12621 (16.0%) / 370 / 6733 | 12550 (15.9%) / 345 / 6642 |
| locomo | temporal | 4970 (15.3%) / 208 / 2680 | 4895 (15.1%) / 211 / 2655 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
