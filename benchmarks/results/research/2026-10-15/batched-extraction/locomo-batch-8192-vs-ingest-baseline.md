# Offline evidence gate comparison

Before: commit `58caddb1a88dd60d51fc97c15742aee6ec8cf34a`, current defaults.
After: commit `93935a165257a96bcf6613df6dc7e9316c545b22`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 81.6% | 83.7% | +2.1 pp | +0.7 pp to +3.5 pp | 71 | 39 | 1426 |
| locomo | All evidence packed with memory text | 81.6% | 83.7% | +2.1 pp | +0.7 pp to +3.5 pp | 71 | 39 | 1426 |
| locomo | Evidence recall | 87.3% | 89.2% | +2.0 pp | +0.8 pp to +3.0 pp | 99 | 62 | 1375 |
| locomo | All evidence among the candidates | 95.9% | 98.5% | +2.6 pp | +1.8 pp to +3.5 pp | 41 | 1 | 1494 |
| locomo | Projected accuracy | 75.9% | 77.4% | +1.5 pp | +0.5 pp to +2.4 pp | 71 | 39 | 1430 |
| locomo | All evidence packed, multi-hop | 47.9% | 50.7% | +2.8 pp | -1.8 pp to +7.4 pp | 26 | 18 | 238 |
| locomo | All evidence packed, open-domain | 56.5% | 57.6% | +1.1 pp | -2.2 pp to +4.3 pp | 2 | 1 | 89 |
| locomo | All evidence packed, single-hop | 92.4% | 94.8% | +2.4 pp | +0.8 pp to +3.8 pp | 32 | 12 | 797 |
| locomo | All evidence packed, temporal | 90.0% | 91.0% | +0.9 pp | -1.6 pp to +3.4 pp | 11 | 8 | 302 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 929.7 to 857.1 | 0.412 / 0.600 s to 0.324 / 0.457 s | 96.2 to 88.4 | 95.2% to 94.5% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 7.4 to 7.7, after 4.3 to 5.5.

- locomo questions that lost all their evidence among the candidates: 1 (conv-44-q0051).
- locomo questions that gained all their evidence among the candidates: 41 (conv-26-q0015, conv-26-q0048, conv-26-q0069, conv-41-q0003, conv-41-q0008, conv-41-q0010, conv-41-q0028, conv-41-q0044, conv-41-q0045, conv-41-q0064, conv-42-q0000, conv-42-q0059, conv-42-q0072, conv-42-q0081, conv-43-q0026, conv-44-q0003, conv-44-q0027, conv-44-q0031, conv-44-q0033, conv-44-q0052, ...).
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL_AGG 41, VECTOR 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 32 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 4, LEXICAL_AGG 41, VECTOR 32.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 0 |
| locomo | open-domain | 96 | 0 | 0 |
| locomo | single-hop | 841 | 0 | 0 |
| locomo | temporal | 321 | 0 | 0 |

- locomo questions that entered the current-state path: 24 (conv-41-q0023, conv-41-q0028, conv-41-q0029, conv-41-q0032, conv-41-q0036, conv-41-q0037, conv-41-q0044, conv-41-q0057, conv-42-q0030, conv-42-q0046, conv-42-q0063, conv-42-q0067, conv-42-q0070, conv-42-q0081, conv-42-q0082, conv-42-q0092, conv-42-q0115, conv-42-q0163, conv-42-q0164, conv-43-q0061, ...).
- locomo questions that left the current-state path: 0.
The JSON report lists every question.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 23334 (15.8%) / 725 / 12540 | 40334 (29.6%) / 499 / 26138 |
| locomo | multi-hop | 4369 (16.1%) / 99 / 2368 | 7537 (30.1%) / 66 / 5090 |
| locomo | open-domain | 1374 (14.2%) / 48 / 759 | 2392 (27.8%) / 45 / 1583 |
| locomo | single-hop | 12621 (16.0%) / 370 / 6733 | 22358 (30.4%) / 240 / 14082 |
| locomo | temporal | 4970 (15.3%) / 208 / 2680 | 8047 (27.7%) / 148 / 5383 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
