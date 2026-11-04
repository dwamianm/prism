# Offline evidence gate comparison

Before: commit `60c37730e398d47c70e12fc574895d93654cfeb0`, overrides `{"packing": {"token_budget": 8192}, "scoring": {"fusion": "weighted"}}`, LongMemEval-S controls regenerated at `data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline` (manifest `0103b9509a35`).
After: commit `60c37730e398d47c70e12fc574895d93654cfeb0`, overrides `{"packing": {"token_budget": 8192}}`, LongMemEval-S controls regenerated at `data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline` (manifest `0103b9509a35`).

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 81.5% | 89.6% | +8.1 pp | +6.8 pp to +9.7 pp | 130 | 5 | 1401 |
| locomo | All evidence packed with memory text | 81.5% | 89.6% | +8.1 pp | +6.8 pp to +9.7 pp | 130 | 5 | 1401 |
| locomo | Evidence recall | 87.7% | 93.9% | +6.2 pp | +5.2 pp to +7.3 pp | 178 | 8 | 1350 |
| locomo | All evidence among the candidates | 99.3% | 99.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 76.0% | 81.3% | +5.3 pp | +4.4 pp to +6.3 pp | 130 | 5 | 1405 |
| locomo | All evidence packed, multi-hop | 41.1% | 66.0% | +24.8 pp | +19.5 pp to +30.1 pp | 71 | 1 | 210 |
| locomo | All evidence packed, open-domain | 53.3% | 67.4% | +14.1 pp | +6.5 pp to +21.7 pp | 14 | 1 | 77 |
| locomo | All evidence packed, single-hop | 94.3% | 97.4% | +3.1 pp | +1.9 pp to +4.3 pp | 28 | 2 | 811 |
| locomo | All evidence packed, temporal | 91.6% | 96.6% | +5.0 pp | +2.5 pp to +7.5 pp | 17 | 1 | 303 |
| longmemeval | All evidence packed | 92.8% | 97.2% | +4.5 pp | +2.3 pp to +6.8 pp | 25 | 4 | 441 |
| longmemeval | All evidence packed with memory text | 92.8% | 97.2% | +4.5 pp | +2.3 pp to +6.8 pp | 25 | 4 | 441 |
| longmemeval | Evidence recall | 95.7% | 98.2% | +2.5 pp | +0.9 pp to +4.2 pp | 26 | 5 | 439 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Projected accuracy | 90.2% | 92.9% | +2.7 pp | +1.4 pp to +4.1 pp | 25 | 4 | 471 |
| longmemeval | All evidence packed, knowledge-update | 98.6% | 100.0% | +1.4 pp | +0.0 pp to +4.2 pp | 1 | 0 | 71 |
| longmemeval | All evidence packed, multi-session | 86.8% | 94.2% | +7.4 pp | +2.5 pp to +12.4 pp | 10 | 1 | 110 |
| longmemeval | All evidence packed, single-session-assistant | 80.4% | 96.4% | +16.1 pp | +7.1 pp to +26.8 pp | 9 | 0 | 47 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 30 |
| longmemeval | All evidence packed, single-session-user | 95.3% | 98.4% | +3.1 pp | +0.0 pp to +7.8 pp | 2 | 0 | 62 |
| longmemeval | All evidence packed, temporal-reasoning | 97.6% | 97.6% | +0.0 pp | -3.1 pp to +3.9 pp | 3 | 3 | 121 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 554.7 to 554.8 | 0.184 / 0.247 s to 0.192 / 0.257 s | 194.6 to 165.7 | 97.0% to 97.3% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 492.0 to 492.0 | 0.193 / 0.289 s to 0.195 / 0.284 s | 151.7 to 69.6 | 72.7% to 84.9% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 2.0 to 9.6, after 2.0 to 7.7.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.
- longmemeval questions that lost all their evidence among the candidates: 0.
- longmemeval questions that gained all their evidence among the candidates: 0.
- longmemeval before: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 91.
- longmemeval after: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 91.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 0 |
| locomo | open-domain | 96 | 0 | 0 |
| locomo | single-hop | 841 | 0 | 0 |
| locomo | temporal | 321 | 0 | 0 |
| longmemeval | knowledge-update | 78 | 11 | 11 |
| longmemeval | multi-session | 133 | 23 | 23 |
| longmemeval | single-session-assistant | 56 | 3 | 3 |
| longmemeval | single-session-preference | 30 | 1 | 1 |
| longmemeval | single-session-user | 70 | 7 | 7 |
| longmemeval | temporal-reasoning | 133 | 37 | 37 |

- locomo questions that entered the current-state path: 0.
- locomo questions that left the current-state path: 0.
- locomo questions whose current-state path these receipts cannot show (weighted scoring or no recency boost): 1540. They are in neither list.
- longmemeval questions that entered the current-state path: 0.
- longmemeval questions that left the current-state path: 0.
- longmemeval questions whose current-state path these receipts cannot show (weighted scoring or no recency boost): 500. They are in neither list.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 70071 (23.4%) / 179 / 36831 | 95042 (37.2%) / 254 / 64122 |
| locomo | multi-hop | 13025 (24.5%) / 25 / 7007 | 17499 (37.8%) / 39 / 11844 |
| locomo | open-domain | 4686 (25.1%) / 41 / 2800 | 5878 (36.5%) / 27 / 4083 |
| locomo | single-hop | 36935 (22.4%) / 84 / 17885 | 51884 (37.4%) / 75 / 34740 |
| locomo | temporal | 15425 (24.3%) / 29 / 9139 | 19781 (36.7%) / 113 / 13455 |
| longmemeval | all | 11343 (15.0%) / 0 / 4037 | 15386 (44.2%) / 0 / 4409 |
| longmemeval | knowledge-update | 1708 (14.8%) / 0 / 588 | 2304 (43.0%) / 0 / 606 |
| longmemeval | multi-session | 3012 (14.8%) / 0 / 1196 | 4056 (48.0%) / 0 / 1123 |
| longmemeval | single-session-assistant | 1173 (13.1%) / 0 / 254 | 1856 (42.7%) / 0 / 625 |
| longmemeval | single-session-preference | 758 (17.0%) / 0 / 279 | 962 (39.3%) / 0 / 285 |
| longmemeval | single-session-user | 1594 (15.3%) / 0 / 481 | 2117 (40.0%) / 0 / 648 |
| longmemeval | temporal-reasoning | 3098 (15.4%) / 0 / 1239 | 4091 (46.0%) / 0 / 1122 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
