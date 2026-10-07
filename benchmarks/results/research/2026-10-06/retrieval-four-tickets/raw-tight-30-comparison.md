# Offline evidence gate comparison

Before: commit `55d3eb61a5c914a801225b69aed7b6c77568b496` with uncommitted changes, current defaults.
After: commit `55d3eb61a5c914a801225b69aed7b6c77568b496` with uncommitted changes, overrides `{"packing": {"graph_max_candidates": 30, "lexical_k": 30, "vector_k": 30}}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 81.8% | 83.1% | +1.3 pp | -1.3 pp to +3.9 pp | 22 | 16 | 424 |
| locomo | All evidence packed with memory text | 81.8% | 83.1% | +1.3 pp | -1.3 pp to +3.9 pp | 22 | 16 | 424 |
| locomo | Evidence recall | 87.5% | 88.5% | +1.0 pp | -0.9 pp to +3.0 pp | 30 | 24 | 408 |
| locomo | All evidence among the candidates | 98.9% | 87.7% | -11.3 pp | -14.3 pp to -8.4 pp | 0 | 52 | 410 |
| locomo | Projected accuracy | 76.4% | 77.3% | +0.9 pp | -0.7 pp to +2.6 pp | 22 | 16 | 428 |
| locomo | All evidence packed, multi-hop | 50.5% | 54.5% | +4.0 pp | -4.0 pp to +11.9 pp | 11 | 7 | 83 |
| locomo | All evidence packed, open-domain | 58.6% | 55.2% | -3.4 pp | -17.2 pp to +6.9 pp | 1 | 2 | 26 |
| locomo | All evidence packed, single-hop | 95.2% | 96.1% | +0.9 pp | -2.2 pp to +3.9 pp | 8 | 6 | 216 |
| locomo | All evidence packed, temporal | 89.2% | 90.2% | +1.0 pp | -2.0 pp to +4.9 pp | 2 | 1 | 99 |
| longmemeval | All evidence packed | 78.9% | 71.1% | -7.9 pp | -18.4 pp to +0.0 pp | 0 | 3 | 35 |
| longmemeval | All evidence packed with memory text | 78.9% | 71.1% | -7.9 pp | -18.4 pp to +0.0 pp | 0 | 3 | 35 |
| longmemeval | Evidence recall | 89.2% | 82.4% | -6.8 pp | -13.4 pp to -1.4 pp | 0 | 6 | 32 |
| longmemeval | All evidence among the candidates | 100.0% | 89.5% | -10.5 pp | -21.1 pp to -2.6 pp | 0 | 4 | 34 |
| longmemeval | Projected accuracy | 81.2% | 76.6% | -4.6 pp | -10.7 pp to +0.0 pp | 0 | 3 | 39 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, multi-session | 60.0% | 50.0% | -10.0 pp | -30.0 pp to +0.0 pp | 0 | 1 | 9 |
| longmemeval | All evidence packed, single-session-assistant | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 60.0% | -40.0 pp | -80.0 pp to +0.0 pp | 0 | 2 | 3 |
| longmemeval | All evidence packed, single-session-user | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 4 |
| longmemeval | All evidence packed, temporal-reasoning | 55.6% | 55.6% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 9 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/466 to 0/466 | 490.5 to 138.5 | 0.172 / 0.221 s to 0.086 / 0.115 s | 80.8 to 81.3 | 96.7% to 96.7% | 0 to 0 |
| longmemeval | 0/42 to 0/42 | 488.6 to 111.2 | 0.177 / 0.223 s to 0.058 / 0.144 s | 41.1 to 21.7 | 82.5% to 87.8% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 8.2 to 6.4, after 4.4 to 6.4.

- locomo questions that lost all their evidence among the candidates: 52 (conv-26-q0004, conv-26-q0015, conv-26-q0034, conv-26-q0038, conv-26-q0042, conv-26-q0048, conv-26-q0055, conv-26-q0069, conv-26-q0075, conv-49-q0001, conv-49-q0007, conv-49-q0010, conv-49-q0011, conv-49-q0019, conv-49-q0020, conv-49-q0024, conv-49-q0025, conv-49-q0026, conv-49-q0028, conv-49-q0032, ...).
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 9 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 9.
- locomo after: 9 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 9 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL 9, LEXICAL_AGG 9, VECTOR 9.
- longmemeval questions that lost all their evidence among the candidates: 4 (ba358f49, gpt4_7abb270c, eac54add, 6e984302).
- longmemeval questions that gained all their evidence among the candidates: 0.
- longmemeval before: 13 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 8.
- longmemeval after: 13 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 13 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL 13, LEXICAL_AGG 8, VECTOR 13.

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
- locomo questions that left the current-state path: 17 (conv-50-q0011, conv-50-q0060, conv-50-q0063, conv-50-q0064, conv-50-q0076, conv-50-q0091, conv-50-q0095, conv-50-q0097, conv-50-q0103, conv-50-q0107, conv-50-q0124, conv-50-q0145, conv-50-q0147, conv-50-q0148, conv-50-q0149, conv-50-q0153, conv-50-q0157).
- longmemeval questions that entered the current-state path: 0.
- longmemeval questions that left the current-state path: 0.
The JSON report lists every question.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 18643 (49.5%) / 1 / 9520 | 19481 (51.4%) / 10384 / 13656 |
| locomo | multi-hop | 3981 (48.7%) / 0 / 2011 | 4261 (52.6%) / 2366 / 3107 |
| locomo | open-domain | 1262 (47.7%) / 0 / 660 | 1336 (50.6%) / 703 / 920 |
| locomo | single-hop | 9423 (50.7%) / 1 / 4833 | 9716 (51.7%) / 5179 / 6809 |
| locomo | temporal | 3977 (48.1%) / 0 / 2016 | 4168 (49.9%) / 2136 / 2820 |
| longmemeval | all | 970 (56.2%) / 0 / 213 | 704 (77.3%) / 93 / 227 |
| longmemeval | knowledge-update | 139 (62.3%) / 0 / 32 | 110 (84.0%) / 8 / 20 |
| longmemeval | multi-session | 300 (59.9%) / 0 / 79 | 212 (69.1%) / 6 / 32 |
| longmemeval | single-session-assistant | 130 (57.8%) / 0 / 31 | 95 (79.2%) / 18 / 41 |
| longmemeval | single-session-preference | 108 (43.2%) / 0 / 23 | 83 (74.1%) / 23 / 50 |
| longmemeval | single-session-user | 94 (47.5%) / 0 / 15 | 71 (85.5%) / 17 / 36 |
| longmemeval | temporal-reasoning | 199 (60.3%) / 0 / 33 | 133 (84.2%) / 21 / 48 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
