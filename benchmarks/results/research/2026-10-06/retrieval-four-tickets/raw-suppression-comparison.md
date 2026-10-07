# Offline evidence gate comparison

Before: commit `55d3eb61a5c914a801225b69aed7b6c77568b496` with uncommitted changes, current defaults.
After: commit `55d3eb61a5c914a801225b69aed7b6c77568b496` with uncommitted changes, overrides `{"enable_read_supersedence": true}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 81.8% | 81.8% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 462 |
| locomo | All evidence packed with memory text | 81.8% | 81.8% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 462 |
| locomo | Evidence recall | 87.5% | 87.5% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 462 |
| locomo | All evidence among the candidates | 98.9% | 98.9% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 462 |
| locomo | Projected accuracy | 76.4% | 76.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 466 |
| locomo | All evidence packed, multi-hop | 50.5% | 50.5% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 101 |
| locomo | All evidence packed, open-domain | 58.6% | 58.6% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 29 |
| locomo | All evidence packed, single-hop | 95.2% | 95.2% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 230 |
| locomo | All evidence packed, temporal | 89.2% | 89.2% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 102 |
| longmemeval | All evidence packed | 78.9% | 78.9% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 38 |
| longmemeval | All evidence packed with memory text | 78.9% | 78.9% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 38 |
| longmemeval | Evidence recall | 89.2% | 89.2% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 38 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 38 |
| longmemeval | Projected accuracy | 81.2% | 81.2% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 42 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, multi-session | 60.0% | 60.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 10 |
| longmemeval | All evidence packed, single-session-assistant | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, single-session-user | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 4 |
| longmemeval | All evidence packed, temporal-reasoning | 55.6% | 55.6% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 9 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/466 to 0/466 | 490.5 to 490.5 | 0.172 / 0.221 s to 0.235 / 0.300 s | 80.8 to 80.8 | 96.7% to 96.7% | 0 to 0 |
| longmemeval | 0/42 to 0/42 | 488.6 to 488.6 | 0.177 / 0.223 s to 0.211 / 0.259 s | 41.1 to 41.1 | 82.5% to 82.5% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 8.2 to 6.4, after 5.5 to 7.5.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 9 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 9.
- locomo after: 9 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 9.
- longmemeval questions that lost all their evidence among the candidates: 0.
- longmemeval questions that gained all their evidence among the candidates: 0.
- longmemeval before: 13 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 8.
- longmemeval after: 13 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 8.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 101 | 0 | 0 |
| locomo | open-domain | 33 | 0 | 0 |
| locomo | single-hop | 230 | 0 | 0 |
| locomo | temporal | 102 | 0 | 0 |
| longmemeval | knowledge-update | 6 | 1 | 1 |
| longmemeval | multi-session | 13 | 1 | 1 |
| longmemeval | single-session-assistant | 5 | 0 | 0 |
| longmemeval | single-session-preference | 5 | 0 | 0 |
| longmemeval | single-session-user | 4 | 0 | 0 |
| longmemeval | temporal-reasoning | 9 | 3 | 3 |

- locomo questions that entered the current-state path: 0.
- locomo questions that left the current-state path: 0.
- longmemeval questions that entered the current-state path: 0.
- longmemeval questions that left the current-state path: 0.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 18643 (49.5%) / 1 / 9520 | 18643 (49.5%) / 1 / 9520 |
| locomo | multi-hop | 3981 (48.7%) / 0 / 2011 | 3981 (48.7%) / 0 / 2011 |
| locomo | open-domain | 1262 (47.7%) / 0 / 660 | 1262 (47.7%) / 0 / 660 |
| locomo | single-hop | 9423 (50.7%) / 1 / 4833 | 9423 (50.7%) / 1 / 4833 |
| locomo | temporal | 3977 (48.1%) / 0 / 2016 | 3977 (48.1%) / 0 / 2016 |
| longmemeval | all | 970 (56.2%) / 0 / 213 | 970 (56.2%) / 0 / 213 |
| longmemeval | knowledge-update | 139 (62.3%) / 0 / 32 | 139 (62.3%) / 0 / 32 |
| longmemeval | multi-session | 300 (59.9%) / 0 / 79 | 300 (59.9%) / 0 / 79 |
| longmemeval | single-session-assistant | 130 (57.8%) / 0 / 31 | 130 (57.8%) / 0 / 31 |
| longmemeval | single-session-preference | 108 (43.2%) / 0 / 23 | 108 (43.2%) / 0 / 23 |
| longmemeval | single-session-user | 94 (47.5%) / 0 / 15 | 94 (47.5%) / 0 / 15 |
| longmemeval | temporal-reasoning | 199 (60.3%) / 0 / 33 | 199 (60.3%) / 0 / 33 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
