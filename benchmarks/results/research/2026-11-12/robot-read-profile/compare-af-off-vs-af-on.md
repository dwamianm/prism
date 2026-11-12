# Offline evidence gate comparison

Before: commit `934d60fdc3efb4a375cb1643ba0ea39c54da2cd0` with uncommitted changes, overrides `{"memory_mode": "address_first"}`, LongMemEval-S controls regenerated at `/Users/dwamianm/Sites/prism/data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline` (manifest `0103b9509a35`).
After: commit `934d60fdc3efb4a375cb1643ba0ea39c54da2cd0` with uncommitted changes, overrides `{"memory_mode": "address_first", "read_profile": "robot"}`, LongMemEval-S controls regenerated at `/Users/dwamianm/Sites/prism/data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline` (manifest `0103b9509a35`).

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 84.1% | 79.4% | -4.8 pp | -6.2 pp to -3.4 pp | 28 | 101 | 1407 |
| locomo | All evidence packed with memory text | 84.1% | 79.4% | -4.8 pp | -6.2 pp to -3.4 pp | 28 | 101 | 1407 |
| locomo | Evidence recall | 89.2% | 85.0% | -4.2 pp | -5.4 pp to -3.0 pp | 33 | 154 | 1349 |
| locomo | All evidence among the candidates | 99.3% | 79.4% | -19.9 pp | -21.9 pp to -17.9 pp | 0 | 306 | 1230 |
| locomo | Projected accuracy | 77.7% | 74.6% | -3.1 pp | -4.1 pp to -2.1 pp | 28 | 101 | 1411 |
| locomo | All evidence packed, multi-hop | 50.0% | 37.9% | -12.1 pp | -16.3 pp to -7.8 pp | 5 | 39 | 238 |
| locomo | All evidence packed, open-domain | 57.6% | 47.8% | -9.8 pp | -17.4 pp to -3.3 pp | 2 | 11 | 79 |
| locomo | All evidence packed, single-hop | 95.4% | 93.9% | -1.4 pp | -3.1 pp to +0.1 pp | 17 | 29 | 795 |
| locomo | All evidence packed, temporal | 92.2% | 86.6% | -5.6 pp | -8.7 pp to -2.5 pp | 4 | 22 | 295 |
| longmemeval | All evidence packed | 94.7% | 87.9% | -6.8 pp | -9.4 pp to -4.3 pp | 4 | 36 | 430 |
| longmemeval | All evidence packed with memory text | 94.7% | 87.9% | -6.8 pp | -9.4 pp to -4.3 pp | 4 | 36 | 430 |
| longmemeval | Evidence recall | 97.3% | 93.0% | -4.3 pp | -6.2 pp to -2.5 pp | 4 | 41 | 425 |
| longmemeval | All evidence among the candidates | 100.0% | 92.1% | -7.9 pp | -10.4 pp to -5.5 pp | 0 | 37 | 433 |
| longmemeval | Projected accuracy | 91.4% | 87.3% | -4.1 pp | -5.6 pp to -2.7 pp | 4 | 36 | 460 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 72 |
| longmemeval | All evidence packed, multi-session | 89.3% | 77.7% | -11.6 pp | -17.4 pp to -5.8 pp | 0 | 14 | 107 |
| longmemeval | All evidence packed, single-session-assistant | 96.4% | 100.0% | +3.6 pp | +0.0 pp to +8.9 pp | 2 | 0 | 54 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 70.0% | -30.0 pp | -46.7 pp to -13.3 pp | 0 | 9 | 21 |
| longmemeval | All evidence packed, single-session-user | 96.9% | 98.4% | +1.6 pp | -3.1 pp to +6.2 pp | 2 | 1 | 61 |
| longmemeval | All evidence packed, temporal-reasoning | 93.7% | 84.3% | -9.4 pp | -15.0 pp to -4.7 pp | 0 | 12 | 115 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 554.8 to 40.0 | 0.133 / 0.176 s to 0.047 / 0.063 s | 84.5 to 40.0 | 96.6% to 94.8% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 490.1 to 40.0 | 0.144 / 0.204 s to 0.106 / 0.169 s | 39.7 to 23.9 | 82.6% to 87.1% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 4.7 to 5.2, after 5.4 to 17.2.

- locomo questions that lost all their evidence among the candidates: 306 (conv-26-q0004, conv-26-q0007, conv-26-q0011, conv-26-q0015, conv-26-q0019, conv-26-q0024, conv-26-q0032, conv-26-q0034, conv-26-q0038, conv-26-q0042, conv-26-q0043, conv-26-q0047, conv-26-q0048, conv-26-q0050, conv-26-q0051, conv-26-q0055, conv-26-q0056, conv-26-q0065, conv-26-q0066, conv-26-q0069, ...).
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL 40, LEXICAL_AGG 41, VECTOR 41.
- longmemeval questions that lost all their evidence among the candidates: 37 (6d550036, gpt4_59c863d7, gpt4_d84a3211, gpt4_f2262a51, c4a1ceb8, gpt4_15e38248, 88432d0a, gpt4_7fce9456, d682f1a2, 2ce6a0f2, gpt4_31ff4165, gpt4_194be4b3, gpt4_ab202e7f, e3038f8c, 1a8a66a6, bf659f65, 75832dbd, d6233ab6, 1c0ddc50, 92a0aa75, ...).
- longmemeval questions that gained all their evidence among the candidates: 0.
- longmemeval before: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 91.
- longmemeval after: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 180 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL 180, LEXICAL_AGG 91, VECTOR 180.

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
| longmemeval | single-session-user | 70 | 7 | 5 |
| longmemeval | temporal-reasoning | 133 | 37 | 35 |

- locomo questions that entered the current-state path: 0.
- locomo questions that left the current-state path: 102 (conv-41-q0012, conv-41-q0015, conv-41-q0023, conv-41-q0025, conv-41-q0028, conv-41-q0029, conv-41-q0030, conv-41-q0032, conv-41-q0035, conv-41-q0036, conv-41-q0037, conv-41-q0044, conv-41-q0057, conv-41-q0101, conv-41-q0136, conv-41-q0150, conv-42-q0010, conv-42-q0027, conv-42-q0030, conv-42-q0034, ...).
- longmemeval questions that entered the current-state path: 0.
- longmemeval questions that left the current-state path: 0.
The JSON report lists every question.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 60045 (46.2%) / 8 / 33262 | 39184 (63.6%) / 16717 / 25752 |
| locomo | multi-hop | 10846 (46.1%) / 0 / 6056 | 7079 (62.8%) / 3034 / 4610 |
| locomo | open-domain | 3521 (42.6%) / 0 / 1963 | 2411 (62.8%) / 1150 / 1689 |
| locomo | single-hop | 33447 (47.2%) / 2 / 18488 | 21486 (63.9%) / 8816 / 13883 |
| locomo | temporal | 12231 (44.7%) / 6 / 6755 | 8208 (63.9%) / 3717 / 5570 |
| longmemeval | all | 10891 (54.8%) / 0 / 2431 | 8558 (71.7%) / 394 / 1402 |
| longmemeval | knowledge-update | 1689 (56.4%) / 0 / 364 | 1411 (71.2%) / 44 / 200 |
| longmemeval | multi-session | 2916 (59.4%) / 0 / 657 | 2328 (69.7%) / 26 / 228 |
| longmemeval | single-session-assistant | 1311 (53.9%) / 0 / 325 | 1011 (73.8%) / 101 / 269 |
| longmemeval | single-session-preference | 669 (45.6%) / 0 / 150 | 485 (67.6%) / 39 / 120 |
| longmemeval | single-session-user | 1425 (48.6%) / 0 / 306 | 1104 (69.4%) / 76 / 240 |
| longmemeval | temporal-reasoning | 2881 (56.2%) / 0 / 629 | 2219 (75.5%) / 108 / 345 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
