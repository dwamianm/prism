# Offline evidence gate comparison

Before: commit `60c37730e398d47c70e12fc574895d93654cfeb0`, overrides `{"scoring": {"fusion": "weighted"}}`, LongMemEval-S controls regenerated at `data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline` (manifest `0103b9509a35`).
After: commit `60c37730e398d47c70e12fc574895d93654cfeb0`, overrides `{"packing": {"session_context_window": 0}}`, LongMemEval-S controls regenerated at `data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline` (manifest `0103b9509a35`).

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 69.5% | 81.6% | +12.2 pp | +10.3 pp to +14.0 pp | 218 | 31 | 1287 |
| locomo | All evidence packed with memory text | 69.5% | 81.6% | +12.2 pp | +10.3 pp to +14.0 pp | 218 | 31 | 1287 |
| locomo | Evidence recall | 75.8% | 87.4% | +11.6 pp | +9.9 pp to +13.2 pp | 299 | 41 | 1196 |
| locomo | All evidence among the candidates | 99.3% | 99.2% | -0.1 pp | -0.3 pp to +0.0 pp | 0 | 2 | 1534 |
| locomo | Projected accuracy | 67.8% | 76.0% | +8.2 pp | +6.8 pp to +9.6 pp | 218 | 31 | 1291 |
| locomo | All evidence packed, multi-hop | 19.9% | 48.9% | +29.1 pp | +23.4 pp to +34.8 pp | 85 | 3 | 194 |
| locomo | All evidence packed, open-domain | 40.2% | 55.4% | +15.2 pp | +6.5 pp to +23.9 pp | 17 | 3 | 72 |
| locomo | All evidence packed, single-hop | 84.3% | 92.6% | +8.3 pp | +5.9 pp to +10.6 pp | 86 | 16 | 739 |
| locomo | All evidence packed, temporal | 82.6% | 89.1% | +6.5 pp | +2.8 pp to +10.3 pp | 30 | 9 | 282 |
| longmemeval | All evidence packed | 87.4% | 94.3% | +6.8 pp | +4.3 pp to +9.4 pp | 36 | 4 | 430 |
| longmemeval | All evidence packed with memory text | 87.4% | 94.3% | +6.8 pp | +4.3 pp to +9.4 pp | 36 | 4 | 430 |
| longmemeval | Evidence recall | 91.8% | 96.9% | +5.2 pp | +3.2 pp to +7.1 pp | 40 | 4 | 426 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Projected accuracy | 87.0% | 91.1% | +4.1 pp | +2.6 pp to +5.6 pp | 36 | 4 | 460 |
| longmemeval | All evidence packed, knowledge-update | 95.8% | 100.0% | +4.2 pp | +0.0 pp to +9.7 pp | 3 | 0 | 69 |
| longmemeval | All evidence packed, multi-session | 77.7% | 88.4% | +10.7 pp | +5.0 pp to +17.4 pp | 14 | 1 | 106 |
| longmemeval | All evidence packed, single-session-assistant | 75.0% | 94.6% | +19.6 pp | +8.9 pp to +30.4 pp | 12 | 1 | 43 |
| longmemeval | All evidence packed, single-session-preference | 96.7% | 96.7% | +0.0 pp | -10.0 pp to +10.0 pp | 1 | 1 | 28 |
| longmemeval | All evidence packed, single-session-user | 95.3% | 96.9% | +1.6 pp | +0.0 pp to +4.7 pp | 1 | 0 | 63 |
| longmemeval | All evidence packed, temporal-reasoning | 91.3% | 94.5% | +3.1 pp | -0.8 pp to +7.1 pp | 5 | 1 | 121 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 554.7 to 550.2 | 0.177 / 0.242 s to 0.172 / 0.238 s | 101.7 to 83.3 | 96.2% to 96.6% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 492.0 to 492.0 | 0.188 / 0.280 s to 0.180 / 0.271 s | 83.1 to 38.0 | 69.0% to 83.2% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 1.8 to 11.3, after 1.8 to 11.3.

- locomo questions that lost all their evidence among the candidates: 2 (conv-41-q0044, conv-42-q0000).
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
| locomo | all | 48606 (31.0%) / 23 / 23834 | 0 (0.0%) / 0 / 0 |
| locomo | multi-hop | 9511 (34.8%) / 3 / 4629 | 0 (0.0%) / 0 / 0 |
| locomo | open-domain | 3165 (32.4%) / 1 / 1755 | 0 (0.0%) / 0 / 0 |
| locomo | single-hop | 25556 (29.6%) / 8 / 11487 | 0 (0.0%) / 0 / 0 |
| locomo | temporal | 10374 (31.2%) / 11 / 5963 | 0 (0.0%) / 0 / 0 |
| longmemeval | all | 8708 (21.0%) / 0 / 3136 | 0 (0.0%) / 0 / 0 |
| longmemeval | knowledge-update | 1364 (21.2%) / 0 / 484 | 0 (0.0%) / 0 / 0 |
| longmemeval | multi-session | 2380 (21.5%) / 0 / 931 | 0 (0.0%) / 0 / 0 |
| longmemeval | single-session-assistant | 829 (16.8%) / 0 / 207 | 0 (0.0%) / 0 / 0 |
| longmemeval | single-session-preference | 561 (23.8%) / 0 / 203 | 0 (0.0%) / 0 / 0 |
| longmemeval | single-session-user | 1137 (19.3%) / 0 / 355 | 0 (0.0%) / 0 / 0 |
| longmemeval | temporal-reasoning | 2437 (22.4%) / 0 / 956 | 0 (0.0%) / 0 / 0 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
