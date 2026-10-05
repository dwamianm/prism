# Offline evidence gate comparison

Before: commit `d48769a0c98bcd03ec0576266409e20b9aa98034`, current defaults.
After: commit `d48769a0c98bcd03ec0576266409e20b9aa98034`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 78.9% | 77.9% | -1.0 pp | -1.9 pp to -0.1 pp | 20 | 35 | 1481 |
| locomo | All evidence packed with memory text | 78.9% | 77.9% | -1.0 pp | -1.9 pp to -0.1 pp | 20 | 35 | 1481 |
| locomo | Evidence recall | 85.1% | 84.4% | -0.7 pp | -1.5 pp to +0.1 pp | 38 | 47 | 1451 |
| locomo | All evidence among the candidates | 96.1% | 96.0% | -0.1 pp | -0.5 pp to +0.3 pp | 4 | 6 | 1526 |
| locomo | Projected accuracy | 74.2% | 73.5% | -0.7 pp | -1.3 pp to -0.0 pp | 20 | 35 | 1485 |
| locomo | All evidence packed, multi-hop | 42.2% | 42.6% | +0.4 pp | -2.5 pp to +3.2 pp | 9 | 8 | 265 |
| locomo | All evidence packed, open-domain | 51.1% | 48.9% | -2.2 pp | -5.4 pp to +0.0 pp | 0 | 2 | 90 |
| locomo | All evidence packed, single-hop | 91.1% | 90.0% | -1.1 pp | -2.4 pp to +0.0 pp | 9 | 18 | 814 |
| locomo | All evidence packed, temporal | 87.2% | 85.7% | -1.6 pp | -3.4 pp to +0.0 pp | 2 | 7 | 312 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 920.2 to 963.1 | 0.328 / 0.511 s to 0.340 / 0.523 s | 103.3 to 104.7 | 94.3% to 91.4% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 7.9 to 4.4, after 4.4 to 3.9.

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
| locomo | all | 39795 (25.0%) / 451 / 16505 | 37166 (23.1%) / 553 / 14375 |
| locomo | multi-hop | 7280 (25.0%) / 58 / 3160 | 6812 (23.0%) / 61 / 2763 |
| locomo | open-domain | 2308 (22.5%) / 46 / 954 | 2146 (20.7%) / 47 / 855 |
| locomo | single-hop | 21827 (25.5%) / 214 / 8971 | 20403 (23.5%) / 280 / 7826 |
| locomo | temporal | 8380 (24.5%) / 133 / 3420 | 7805 (22.6%) / 165 / 2931 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
