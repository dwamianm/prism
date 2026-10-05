# Offline evidence gate comparison

Before: commit `d48769a0c98bcd03ec0576266409e20b9aa98034`, current defaults.
After: commit `8ed4e559b840dc03370b092f7c8d779dfd342ef2`, overrides `{"packing": {"fold_repeated_text": true}}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 77.9% | 80.9% | +2.9 pp | +2.1 pp to +3.8 pp | 47 | 2 | 1487 |
| locomo | All evidence packed with memory text | 77.9% | 80.9% | +2.9 pp | +2.1 pp to +3.8 pp | 47 | 2 | 1487 |
| locomo | Evidence recall | 84.4% | 86.8% | +2.4 pp | +1.7 pp to +3.2 pp | 79 | 4 | 1453 |
| locomo | All evidence among the candidates | 96.0% | 96.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 73.5% | 75.4% | +1.9 pp | +1.4 pp to +2.5 pp | 47 | 2 | 1491 |
| locomo | All evidence packed, multi-hop | 42.6% | 49.3% | +6.7 pp | +4.3 pp to +9.9 pp | 19 | 0 | 263 |
| locomo | All evidence packed, open-domain | 48.9% | 53.3% | +4.3 pp | +1.1 pp to +8.7 pp | 4 | 0 | 88 |
| locomo | All evidence packed, single-hop | 90.0% | 91.3% | +1.3 pp | +0.5 pp to +2.3 pp | 13 | 2 | 826 |
| locomo | All evidence packed, temporal | 85.7% | 89.1% | +3.4 pp | +1.6 pp to +5.6 pp | 11 | 0 | 310 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 963.1 to 963.1 | 0.340 / 0.523 s to 0.438 / 0.644 s | 104.7 to 95.7 | 91.4% to 94.0% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 4.4 to 3.9, after 4.5 to 9.9.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 4, LEXICAL_AGG 41, VECTOR 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 4, LEXICAL_AGG 41, VECTOR 41.

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
| locomo | all | 37166 (23.1%) / 553 / 14375 | 20540 (13.9%) / 787 / 10508 |
| locomo | multi-hop | 6812 (23.0%) / 61 / 2763 | 3774 (13.9%) / 98 / 1991 |
| locomo | open-domain | 2146 (20.7%) / 47 / 855 | 1225 (12.7%) / 71 / 658 |
| locomo | single-hop | 20403 (23.5%) / 280 / 7826 | 11157 (14.2%) / 401 / 5633 |
| locomo | temporal | 7805 (22.6%) / 165 / 2931 | 4384 (13.8%) / 217 / 2226 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
