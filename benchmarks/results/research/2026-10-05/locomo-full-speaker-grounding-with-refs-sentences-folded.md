# Offline evidence gate comparison

Before: commit `8ed4e559b840dc03370b092f7c8d779dfd342ef2`, overrides `{"packing": {"fold_repeated_text": true}}`.
After: commit `8ed4e559b840dc03370b092f7c8d779dfd342ef2`, overrides `{"packing": {"fold_repeated_text": true}}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 81.7% | 80.9% | -0.8 pp | -1.9 pp to +0.2 pp | 28 | 41 | 1467 |
| locomo | All evidence packed with memory text | 81.7% | 80.9% | -0.8 pp | -1.9 pp to +0.2 pp | 28 | 41 | 1467 |
| locomo | Evidence recall | 87.6% | 86.8% | -0.8 pp | -1.7 pp to +0.0 pp | 43 | 55 | 1438 |
| locomo | All evidence among the candidates | 96.1% | 96.0% | -0.1 pp | -0.5 pp to +0.3 pp | 4 | 6 | 1526 |
| locomo | Projected accuracy | 76.1% | 75.4% | -0.6 pp | -1.4 pp to +0.1 pp | 28 | 41 | 1471 |
| locomo | All evidence packed, multi-hop | 47.2% | 49.3% | +2.1 pp | -1.1 pp to +5.3 pp | 14 | 8 | 260 |
| locomo | All evidence packed, open-domain | 54.3% | 53.3% | -1.1 pp | -4.3 pp to +2.2 pp | 1 | 2 | 89 |
| locomo | All evidence packed, single-hop | 93.1% | 91.3% | -1.8 pp | -3.2 pp to -0.5 pp | 11 | 26 | 804 |
| locomo | All evidence packed, temporal | 90.0% | 89.1% | -0.9 pp | -2.8 pp to +0.6 pp | 2 | 5 | 314 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 920.2 to 963.1 | 0.414 / 0.603 s to 0.438 / 0.644 s | 95.7 to 95.7 | 95.2% to 94.0% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 4.5 to 7.6, after 4.5 to 9.9.

- locomo questions that lost all their evidence among the candidates: 6 (conv-30-q0003, conv-42-q0072, conv-42-q0134, conv-44-q0051, conv-47-q0011, conv-49-q0130).
- locomo questions that gained all their evidence among the candidates: 4 (conv-41-q0010, conv-43-q0026, conv-44-q0027, conv-44-q0052).
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 1, LEXICAL_AGG 41, VECTOR 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 4, LEXICAL_AGG 41, VECTOR 41.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 0 |
| locomo | open-domain | 96 | 0 | 0 |
| locomo | single-hop | 841 | 0 | 0 |
| locomo | temporal | 321 | 0 | 0 |

- locomo questions that entered the current-state path: 0.
- locomo questions that left the current-state path: 1 (conv-41-q0025).
The JSON report lists every question.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 23661 (16.1%) / 706 / 12574 | 20540 (13.9%) / 787 / 10508 |
| locomo | multi-hop | 4425 (16.4%) / 90 / 2422 | 3774 (13.9%) / 98 / 1991 |
| locomo | open-domain | 1374 (14.3%) / 61 / 740 | 1225 (12.7%) / 71 / 658 |
| locomo | single-hop | 12873 (16.4%) / 356 / 6733 | 11157 (14.2%) / 401 / 5633 |
| locomo | temporal | 4989 (15.5%) / 199 / 2679 | 4384 (13.8%) / 217 / 2226 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
