# Offline evidence gate comparison

Before: commit `55d3eb61a5c914a801225b69aed7b6c77568b496` with uncommitted changes, current defaults.
After: commit `55d3eb61a5c914a801225b69aed7b6c77568b496` with uncommitted changes, overrides `{"enable_evidence_followup": true}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 81.8% | 79.4% | -2.4 pp | -4.3 pp to -0.6 pp | 4 | 15 | 443 |
| locomo | All evidence packed with memory text | 81.8% | 79.4% | -2.4 pp | -4.3 pp to -0.6 pp | 4 | 15 | 443 |
| locomo | Evidence recall | 87.5% | 85.6% | -1.9 pp | -3.3 pp to -0.5 pp | 8 | 23 | 431 |
| locomo | All evidence among the candidates | 98.9% | 98.9% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 462 |
| locomo | Projected accuracy | 76.4% | 74.9% | -1.5 pp | -2.8 pp to -0.4 pp | 4 | 15 | 447 |
| locomo | All evidence packed, multi-hop | 50.5% | 47.5% | -3.0 pp | -8.9 pp to +2.0 pp | 2 | 5 | 94 |
| locomo | All evidence packed, open-domain | 58.6% | 51.7% | -6.9 pp | -17.2 pp to +0.0 pp | 0 | 2 | 27 |
| locomo | All evidence packed, single-hop | 95.2% | 93.9% | -1.3 pp | -3.5 pp to +0.9 pp | 2 | 5 | 223 |
| locomo | All evidence packed, temporal | 89.2% | 86.3% | -2.9 pp | -6.9 pp to +0.0 pp | 0 | 3 | 99 |
| longmemeval | All evidence packed | 78.9% | 84.2% | +5.3 pp | +0.0 pp to +13.2 pp | 2 | 0 | 36 |
| longmemeval | All evidence packed with memory text | 78.9% | 84.2% | +5.3 pp | +0.0 pp to +13.2 pp | 2 | 0 | 36 |
| longmemeval | Evidence recall | 89.2% | 91.1% | +1.8 pp | +0.0 pp to +4.7 pp | 2 | 0 | 36 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 38 |
| longmemeval | Projected accuracy | 81.2% | 84.2% | +3.1 pp | +0.0 pp to +7.6 pp | 2 | 0 | 40 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, multi-session | 60.0% | 80.0% | +20.0 pp | +0.0 pp to +50.0 pp | 2 | 0 | 8 |
| longmemeval | All evidence packed, single-session-assistant | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, single-session-user | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 4 |
| longmemeval | All evidence packed, temporal-reasoning | 55.6% | 55.6% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 9 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/466 to 0/466 | 490.5 to 490.5 | 0.172 / 0.221 s to 0.274 / 0.350 s | 80.8 to 81.8 | 96.7% to 96.7% | 0 to 0 |
| longmemeval | 0/42 to 0/42 | 488.6 to 488.6 | 0.177 / 0.223 s to 0.276 / 0.341 s | 41.1 to 39.3 | 82.5% to 83.0% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 8.2 to 6.4, after 4.1 to 8.4.

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
| locomo | all | 18643 (49.5%) / 1 / 9520 | 17964 (47.1%) / 4 / 8612 |
| locomo | multi-hop | 3981 (48.7%) / 0 / 2011 | 3860 (46.5%) / 1 / 1846 |
| locomo | open-domain | 1262 (47.7%) / 0 / 660 | 1244 (46.7%) / 0 / 615 |
| locomo | single-hop | 9423 (50.7%) / 1 / 4833 | 8986 (47.8%) / 3 / 4279 |
| locomo | temporal | 3977 (48.1%) / 0 / 2016 | 3874 (46.3%) / 0 / 1872 |
| longmemeval | all | 970 (56.2%) / 0 / 213 | 933 (56.5%) / 0 / 197 |
| longmemeval | knowledge-update | 139 (62.3%) / 0 / 32 | 134 (61.2%) / 0 / 24 |
| longmemeval | multi-session | 300 (59.9%) / 0 / 79 | 286 (62.0%) / 0 / 74 |
| longmemeval | single-session-assistant | 130 (57.8%) / 0 / 31 | 126 (58.3%) / 0 / 25 |
| longmemeval | single-session-preference | 108 (43.2%) / 0 / 23 | 113 (46.7%) / 0 / 27 |
| longmemeval | single-session-user | 94 (47.5%) / 0 / 15 | 86 (46.2%) / 0 / 16 |
| longmemeval | temporal-reasoning | 199 (60.3%) / 0 / 33 | 188 (57.7%) / 0 / 31 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
