# Offline evidence gate comparison

Before: commit `f05b0ff0bcb293e1019f9c717f92dcca8d070168`, current defaults.
After: commit `f05b0ff0bcb293e1019f9c717f92dcca8d070168`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 75.7% | 79.5% | +3.8 pp | +2.5 pp to +5.0 pp | 81 | 23 | 1432 |
| locomo | All evidence packed with memory text | 75.7% | 79.5% | +3.8 pp | +2.5 pp to +5.0 pp | 81 | 23 | 1432 |
| locomo | Evidence recall | 81.9% | 85.7% | +3.8 pp | +2.7 pp to +4.9 pp | 141 | 32 | 1363 |
| locomo | All evidence among the candidates | 95.5% | 96.0% | +0.5 pp | +0.0 pp to +1.0 pp | 13 | 5 | 1518 |
| locomo | Projected accuracy | 72.0% | 74.6% | +2.6 pp | +1.7 pp to +3.4 pp | 81 | 23 | 1436 |
| locomo | All evidence packed, multi-hop | 37.6% | 42.9% | +5.3 pp | +1.4 pp to +8.9 pp | 23 | 8 | 251 |
| locomo | All evidence packed, open-domain | 44.6% | 52.2% | +7.6 pp | +1.1 pp to +14.1 pp | 9 | 2 | 81 |
| locomo | All evidence packed, single-hop | 87.9% | 91.4% | +3.6 pp | +2.0 pp to +5.2 pp | 38 | 8 | 795 |
| locomo | All evidence packed, temporal | 86.3% | 88.2% | +1.9 pp | -0.6 pp to +4.4 pp | 11 | 5 | 305 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 907.7 to 919.0 | 0.330 / 0.521 s to 0.368 / 0.605 s | 79.1 to 102.7 | 96.1% to 94.4% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 8.3 to 7.5, after 7.4 to 6.8.

- locomo questions that lost all their evidence among the candidates: 5 (conv-41-q0010, conv-42-q0059, conv-42-q0072, conv-43-q0026, conv-48-q0041).
- locomo questions that gained all their evidence among the candidates: 13 (conv-41-q0006, conv-41-q0032, conv-41-q0047, conv-42-q0134, conv-43-q0003, conv-43-q0011, conv-44-q0039, conv-44-q0051, conv-47-q0011, conv-49-q0011, conv-49-q0028, conv-50-q0019, conv-50-q0041).
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 5, LEXICAL_AGG 41, VECTOR 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 2, LEXICAL_AGG 41, VECTOR 41.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 0 |
| locomo | open-domain | 96 | 0 | 0 |
| locomo | single-hop | 841 | 0 | 0 |
| locomo | temporal | 321 | 0 | 0 |

- locomo questions that entered the current-state path: 12 (conv-43-q0011, conv-43-q0014, conv-43-q0018, conv-43-q0065, conv-43-q0083, conv-43-q0114, conv-43-q0127, conv-43-q0171, conv-44-q0032, conv-50-q0091, conv-50-q0095, conv-50-q0097).
- locomo questions that left the current-state path: 16 (conv-41-q0023, conv-41-q0028, conv-41-q0029, conv-41-q0030, conv-41-q0032, conv-41-q0035, conv-41-q0036, conv-41-q0037, conv-41-q0044, conv-41-q0057, conv-42-q0046, conv-42-q0060, conv-42-q0063, conv-42-q0081, conv-42-q0082, conv-42-q0164).
The JSON report lists every question.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 41688 (34.2%) / 445 / 11349 | 38925 (24.6%) / 452 / 16165 |
| locomo | multi-hop | 7725 (34.9%) / 57 / 2196 | 7198 (24.9%) / 54 / 3131 |
| locomo | open-domain | 2497 (32.1%) / 25 / 692 | 2269 (22.1%) / 42 / 955 |
| locomo | single-hop | 22977 (34.7%) / 249 / 6236 | 21314 (25.1%) / 217 / 8784 |
| locomo | temporal | 8489 (33.1%) / 114 / 2225 | 8144 (24.0%) / 139 / 3295 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
