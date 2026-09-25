# Offline evidence gate comparison

Before: commit `7825ec201849255bad1ff70c406ae6b70028bfea`, current defaults.
After: commit `7825ec201849255bad1ff70c406ae6b70028bfea`, overrides `{"packing": {"lexical_k": 50, "vector_k": 50}}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 84.0% | 85.1% | +1.1 pp | -0.1 pp to +2.3 pp | 53 | 36 | 1447 |
| locomo | All evidence packed with memory text | 84.0% | 85.1% | +1.1 pp | -0.1 pp to +2.3 pp | 53 | 36 | 1447 |
| locomo | Evidence recall | 89.2% | 90.0% | +0.8 pp | -0.1 pp to +1.8 pp | 69 | 59 | 1408 |
| locomo | All evidence among the candidates | 99.3% | 90.6% | -8.7 pp | -10.2 pp to -7.3 pp | 0 | 133 | 1403 |
| locomo | Projected accuracy | 77.6% | 78.5% | +0.8 pp | -0.0 pp to +1.6 pp | 53 | 36 | 1451 |
| locomo | All evidence packed, multi-hop | 49.6% | 51.8% | +2.1 pp | -2.1 pp to +6.4 pp | 22 | 16 | 244 |
| locomo | All evidence packed, open-domain | 57.6% | 56.5% | -1.1 pp | -6.5 pp to +4.3 pp | 3 | 4 | 85 |
| locomo | All evidence packed, single-hop | 95.2% | 96.7% | +1.4 pp | +0.1 pp to +2.7 pp | 22 | 10 | 809 |
| locomo | All evidence packed, temporal | 92.2% | 92.2% | +0.0 pp | -2.2 pp to +2.2 pp | 6 | 6 | 309 |
| longmemeval | All evidence packed | 94.7% | 87.4% | -7.2 pp | -9.8 pp to -4.7 pp | 3 | 37 | 430 |
| longmemeval | All evidence packed with memory text | 94.7% | 87.4% | -7.2 pp | -9.8 pp to -4.7 pp | 3 | 37 | 430 |
| longmemeval | Evidence recall | 97.3% | 92.7% | -4.6 pp | -6.6 pp to -2.9 pp | 5 | 41 | 424 |
| longmemeval | All evidence among the candidates | 100.0% | 98.5% | -1.5 pp | -2.8 pp to -0.4 pp | 0 | 7 | 463 |
| longmemeval | Projected accuracy | 91.4% | 87.0% | -4.4 pp | -5.9 pp to -3.0 pp | 3 | 37 | 460 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 72 |
| longmemeval | All evidence packed, multi-session | 89.3% | 79.3% | -9.9 pp | -15.7 pp to -5.0 pp | 0 | 12 | 109 |
| longmemeval | All evidence packed, single-session-assistant | 96.4% | 100.0% | +3.6 pp | +0.0 pp to +8.9 pp | 2 | 0 | 54 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 70.0% | -30.0 pp | -46.7 pp to -13.3 pp | 0 | 9 | 21 |
| longmemeval | All evidence packed, single-session-user | 96.9% | 96.9% | +0.0 pp | -4.7 pp to +4.7 pp | 1 | 1 | 62 |
| longmemeval | All evidence packed, temporal-reasoning | 93.7% | 81.9% | -11.8 pp | -17.3 pp to -6.3 pp | 0 | 15 | 112 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 554.8 to 168.0 | 0.192 / 0.240 s to 0.100 / 0.133 s | 84.5 to 83.8 | 96.6% to 96.6% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 492.0 to 156.8 | 0.179 / 0.249 s to 0.068 / 0.159 s | 39.9 to 24.8 | 82.6% to 86.8% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 4.8 to 6.4, after 3.8 to 6.0.

- locomo questions that lost all their evidence among the candidates: 133 (conv-26-q0004, conv-26-q0015, conv-26-q0034, conv-26-q0038, conv-26-q0042, conv-26-q0048, conv-26-q0069, conv-26-q0075, conv-30-q0002, conv-30-q0018, conv-30-q0023, conv-30-q0031, conv-30-q0044, conv-30-q0074, conv-41-q0003, conv-41-q0006, conv-41-q0008, conv-41-q0010, conv-41-q0011, conv-41-q0014, ...).
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL 41, LEXICAL_AGG 41, VECTOR 41.
- longmemeval questions that lost all their evidence among the candidates: 7 (6d550036, 1a8a66a6, 75832dbd, gpt4_7abb270c, eac54add, 6e984302, gpt4_68e94288).
- longmemeval questions that gained all their evidence among the candidates: 0.
- longmemeval before: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 114.
- longmemeval after: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 180 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL 180, LEXICAL_AGG 114, VECTOR 180.

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
- locomo questions that left the current-state path: 112 (conv-41-q0012, conv-41-q0015, conv-41-q0023, conv-41-q0025, conv-41-q0028, conv-41-q0029, conv-41-q0030, conv-41-q0032, conv-41-q0035, conv-41-q0036, conv-41-q0037, conv-41-q0044, conv-41-q0057, conv-41-q0088, conv-41-q0101, conv-41-q0106, conv-41-q0136, conv-41-q0150, conv-42-q0010, conv-42-q0014, ...).
- longmemeval questions that entered the current-state path: 0.
- longmemeval questions that left the current-state path: 1 (75499fd8).
The JSON report lists every question.

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
