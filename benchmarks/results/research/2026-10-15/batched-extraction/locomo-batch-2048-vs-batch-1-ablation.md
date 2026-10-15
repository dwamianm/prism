# Offline evidence gate comparison

Before: commit `93935a165257a96bcf6613df6dc7e9316c545b22`, current defaults.
After: commit `93935a165257a96bcf6613df6dc7e9316c545b22`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 82.2% | 84.0% | +1.8 pp | +0.7 pp to +2.9 pp | 53 | 25 | 1458 |
| locomo | All evidence packed with memory text | 82.2% | 84.0% | +1.8 pp | +0.7 pp to +2.9 pp | 53 | 25 | 1458 |
| locomo | Evidence recall | 87.7% | 89.5% | +1.8 pp | +0.9 pp to +2.7 pp | 78 | 40 | 1418 |
| locomo | All evidence among the candidates | 97.3% | 98.4% | +1.1 pp | +0.6 pp to +1.6 pp | 17 | 0 | 1519 |
| locomo | Projected accuracy | 76.3% | 77.7% | +1.4 pp | +0.6 pp to +2.1 pp | 53 | 25 | 1462 |
| locomo | All evidence packed, multi-hop | 51.4% | 51.8% | +0.4 pp | -3.5 pp to +4.3 pp | 16 | 15 | 251 |
| locomo | All evidence packed, open-domain | 56.5% | 55.4% | -1.1 pp | -3.3 pp to +0.0 pp | 0 | 1 | 91 |
| locomo | All evidence packed, single-hop | 92.6% | 95.5% | +2.9 pp | +1.5 pp to +4.2 pp | 29 | 5 | 807 |
| locomo | All evidence packed, temporal | 89.4% | 90.7% | +1.2 pp | -0.9 pp to +3.4 pp | 8 | 4 | 309 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 931.8 to 853.2 | 0.394 / 0.581 s to 0.308 / 0.435 s | 90.5 to 88.4 | 94.4% to 94.5% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 4.8 to 6.8, after 4.9 to 4.3.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 17 (conv-26-q0048, conv-41-q0008, conv-41-q0028, conv-41-q0045, conv-41-q0064, conv-42-q0072, conv-42-q0081, conv-44-q0031, conv-47-q0033, conv-49-q0007, conv-49-q0019, conv-49-q0020, conv-49-q0051, conv-49-q0054, conv-49-q0077, conv-50-q0023, conv-50-q0058).
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL_AGG 41, VECTOR 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 28 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41, VECTOR 28.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 0 |
| locomo | open-domain | 96 | 0 | 0 |
| locomo | single-hop | 841 | 0 | 0 |
| locomo | temporal | 321 | 0 | 0 |

- locomo questions that entered the current-state path: 17 (conv-41-q0023, conv-41-q0025, conv-41-q0028, conv-41-q0029, conv-41-q0036, conv-41-q0037, conv-41-q0044, conv-41-q0057, conv-42-q0030, conv-42-q0070, conv-42-q0092, conv-42-q0115, conv-42-q0163, conv-43-q0124, conv-43-q0128, conv-43-q0169, conv-50-q0076).
- locomo questions that left the current-state path: 0.
The JSON report lists every question.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 27144 (19.5%) / 690 / 17524 | 40324 (29.6%) / 520 / 26224 |
| locomo | multi-hop | 5148 (20.2%) / 101 / 3538 | 7505 (30.0%) / 65 / 5050 |
| locomo | open-domain | 1653 (18.5%) / 51 / 1085 | 2403 (27.9%) / 32 / 1586 |
| locomo | single-hop | 15021 (20.0%) / 315 / 9409 | 22378 (30.4%) / 285 / 14172 |
| locomo | temporal | 5322 (17.8%) / 223 / 3492 | 8038 (27.7%) / 138 / 5416 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
