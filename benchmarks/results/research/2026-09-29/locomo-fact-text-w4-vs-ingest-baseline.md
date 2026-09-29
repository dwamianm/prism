# Offline evidence gate comparison

Before: commit `09949aa76f2c107c78bf85f3028a60f97b5d2807`, current defaults.
After: commit `09949aa76f2c107c78bf85f3028a60f97b5d2807`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 75.6% | 76.6% | +1.0 pp | -0.3 pp to +2.3 pp | 58 | 42 | 1436 |
| locomo | All evidence packed with memory text | 75.6% | 76.6% | +1.0 pp | -0.3 pp to +2.3 pp | 58 | 42 | 1436 |
| locomo | Evidence recall | 81.8% | 83.4% | +1.6 pp | +0.6 pp to +2.7 pp | 102 | 59 | 1375 |
| locomo | All evidence among the candidates | 95.5% | 94.7% | -0.8 pp | -1.4 pp to -0.2 pp | 5 | 17 | 1514 |
| locomo | Projected accuracy | 71.9% | 72.6% | +0.7 pp | -0.2 pp to +1.5 pp | 58 | 42 | 1440 |
| locomo | All evidence packed, multi-hop | 37.2% | 37.6% | +0.4 pp | -3.9 pp to +4.6 pp | 18 | 17 | 247 |
| locomo | All evidence packed, open-domain | 44.6% | 50.0% | +5.4 pp | -1.1 pp to +12.0 pp | 8 | 3 | 81 |
| locomo | All evidence packed, single-hop | 87.8% | 88.6% | +0.8 pp | -0.6 pp to +2.4 pp | 23 | 16 | 802 |
| locomo | All evidence packed, temporal | 86.3% | 87.2% | +0.9 pp | -1.2 pp to +3.4 pp | 9 | 6 | 306 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 907.7 to 922.2 | 0.305 / 0.462 s to 0.315 / 0.530 s | 79.1 to 93.4 | 96.1% to 84.1% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 2.0 to 4.5, after 2.0 to 4.9.

- locomo questions that lost all their evidence among the candidates: 17 (conv-26-q0056, conv-30-q0002, conv-30-q0003, conv-41-q0010, conv-41-q0035, conv-41-q0040, conv-42-q0001, conv-42-q0056, conv-42-q0059, conv-43-q0026, conv-43-q0034, conv-43-q0067, conv-47-q0003, conv-48-q0041, conv-48-q0059, conv-49-q0014, conv-49-q0130).
- locomo questions that gained all their evidence among the candidates: 5 (conv-44-q0027, conv-44-q0051, conv-47-q0011, conv-49-q0028, conv-50-q0019).
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 5, LEXICAL_AGG 41, VECTOR 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 1, LEXICAL_AGG 41, VECTOR 41.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 0 |
| locomo | open-domain | 96 | 0 | 0 |
| locomo | single-hop | 841 | 0 | 0 |
| locomo | temporal | 321 | 0 | 0 |

- locomo questions that entered the current-state path: 4 (conv-43-q0083, conv-44-q0032, conv-50-q0091, conv-50-q0095).
- locomo questions that left the current-state path: 18 (conv-41-q0023, conv-41-q0025, conv-41-q0028, conv-41-q0029, conv-41-q0030, conv-41-q0032, conv-41-q0035, conv-41-q0036, conv-41-q0037, conv-41-q0044, conv-41-q0057, conv-41-q0136, conv-41-q0150, conv-42-q0046, conv-42-q0081, conv-42-q0082, conv-42-q0085, conv-43-q0035).
The JSON report lists every question.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 41698 (34.2%) / 474 / 11371 | 40454 (28.1%) / 372 / 15804 |
| locomo | multi-hop | 7721 (34.9%) / 60 / 2196 | 7763 (28.9%) / 60 / 3316 |
| locomo | open-domain | 2493 (32.1%) / 28 / 687 | 2353 (25.7%) / 33 / 924 |
| locomo | single-hop | 22992 (34.7%) / 262 / 6257 | 21933 (28.1%) / 214 / 8437 |
| locomo | temporal | 8492 (33.1%) / 124 / 2231 | 8405 (28.2%) / 65 / 3127 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
