# Offline evidence gate comparison

Before: commit `d810ee5552e388f85aac62f4cf8c6c6038f0b1c8`, current defaults.
After: commit `d810ee5552e388f85aac62f4cf8c6c6038f0b1c8`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 82.3% | 83.7% | +1.4 pp | +0.1 pp to +2.8 pp | 65 | 43 | 1428 |
| locomo | All evidence packed with memory text | 82.3% | 83.7% | +1.4 pp | +0.1 pp to +2.8 pp | 65 | 43 | 1428 |
| locomo | Evidence recall | 88.2% | 89.3% | +1.1 pp | -0.0 pp to +2.1 pp | 87 | 66 | 1383 |
| locomo | All evidence among the candidates | 96.4% | 98.3% | +1.9 pp | +1.2 pp to +2.6 pp | 30 | 1 | 1505 |
| locomo | Projected accuracy | 76.4% | 77.5% | +1.1 pp | +0.2 pp to +2.0 pp | 65 | 43 | 1432 |
| locomo | All evidence packed, multi-hop | 48.9% | 50.4% | +1.4 pp | -3.2 pp to +6.0 pp | 23 | 19 | 240 |
| locomo | All evidence packed, open-domain | 58.7% | 55.4% | -3.3 pp | -9.8 pp to +3.3 pp | 3 | 6 | 83 |
| locomo | All evidence packed, single-hop | 92.9% | 95.2% | +2.4 pp | +0.8 pp to +3.9 pp | 32 | 12 | 797 |
| locomo | All evidence packed, temporal | 90.7% | 91.0% | +0.3 pp | -1.9 pp to +2.5 pp | 7 | 6 | 308 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 924.7 to 868.2 | 0.334 / 0.475 s to 0.288 / 0.414 s | 95.2 to 88.6 | 95.2% to 94.5% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 1.7 to 1.6, after 2.3 to 1.7.

- locomo questions that lost all their evidence among the candidates: 1 (conv-44-q0051).
- locomo questions that gained all their evidence among the candidates: 30 (conv-26-q0015, conv-26-q0048, conv-41-q0003, conv-41-q0008, conv-41-q0044, conv-41-q0045, conv-41-q0064, conv-42-q0000, conv-42-q0072, conv-42-q0081, conv-43-q0026, conv-44-q0003, conv-44-q0033, conv-44-q0057, conv-47-q0002, conv-47-q0033, conv-48-q0023, conv-48-q0041, conv-48-q0048, conv-48-q0082, ...).
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 38, LEXICAL_AGG 41, VECTOR 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 32 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41, VECTOR 32.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 0 |
| locomo | open-domain | 96 | 0 | 0 |
| locomo | single-hop | 841 | 0 | 0 |
| locomo | temporal | 321 | 0 | 0 |

- locomo questions that entered the current-state path: 20 (conv-41-q0023, conv-41-q0028, conv-41-q0029, conv-41-q0032, conv-41-q0036, conv-41-q0037, conv-41-q0044, conv-41-q0057, conv-42-q0030, conv-42-q0046, conv-42-q0067, conv-42-q0070, conv-42-q0092, conv-42-q0163, conv-42-q0164, conv-43-q0061, conv-43-q0124, conv-43-q0128, conv-43-q0169, conv-50-q0076).
- locomo questions that left the current-state path: 0.
The JSON report lists every question.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 22629 (15.4%) / 696 / 12435 | 39404 (28.9%) / 619 / 25615 |
| locomo | multi-hop | 4339 (16.2%) / 107 / 2436 | 7430 (29.7%) / 79 / 5016 |
| locomo | open-domain | 1346 (14.1%) / 65 / 760 | 2328 (27.0%) / 40 / 1545 |
| locomo | single-hop | 12195 (15.6%) / 338 / 6621 | 21749 (29.5%) / 317 / 13786 |
| locomo | temporal | 4749 (14.9%) / 186 / 2618 | 7897 (27.2%) / 183 / 5268 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
