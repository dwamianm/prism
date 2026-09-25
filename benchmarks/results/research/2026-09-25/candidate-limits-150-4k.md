# Offline evidence gate comparison

Before: commit `7825ec201849255bad1ff70c406ae6b70028bfea`, current defaults.
After: commit `7825ec201849255bad1ff70c406ae6b70028bfea`, overrides `{"packing": {"lexical_k": 150, "vector_k": 150}}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 84.0% | 84.6% | +0.7 pp | -0.7 pp to +2.0 pp | 59 | 49 | 1428 |
| locomo | All evidence packed with memory text | 84.0% | 84.6% | +0.7 pp | -0.7 pp to +2.0 pp | 59 | 49 | 1428 |
| locomo | Evidence recall | 89.2% | 89.5% | +0.3 pp | -0.7 pp to +1.3 pp | 79 | 71 | 1386 |
| locomo | All evidence among the candidates | 99.3% | 95.6% | -3.7 pp | -4.7 pp to -2.8 pp | 0 | 57 | 1479 |
| locomo | Projected accuracy | 77.6% | 78.1% | +0.4 pp | -0.5 pp to +1.3 pp | 59 | 49 | 1432 |
| locomo | All evidence packed, multi-hop | 49.6% | 52.1% | +2.5 pp | -2.1 pp to +7.1 pp | 25 | 18 | 239 |
| locomo | All evidence packed, open-domain | 57.6% | 57.6% | +0.0 pp | -5.4 pp to +5.4 pp | 3 | 3 | 86 |
| locomo | All evidence packed, single-hop | 95.2% | 95.0% | -0.2 pp | -1.8 pp to +1.2 pp | 19 | 21 | 801 |
| locomo | All evidence packed, temporal | 92.2% | 93.8% | +1.6 pp | -0.9 pp to +4.0 pp | 12 | 7 | 302 |
| longmemeval | All evidence packed | 94.7% | 91.5% | -3.2 pp | -5.1 pp to -1.5 pp | 1 | 16 | 453 |
| longmemeval | All evidence packed with memory text | 94.7% | 91.5% | -3.2 pp | -5.1 pp to -1.5 pp | 1 | 16 | 453 |
| longmemeval | Evidence recall | 97.3% | 94.9% | -2.4 pp | -3.7 pp to -1.2 pp | 1 | 16 | 453 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Projected accuracy | 91.4% | 89.5% | -1.9 pp | -3.0 pp to -0.9 pp | 1 | 16 | 483 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 72 |
| longmemeval | All evidence packed, multi-session | 89.3% | 86.8% | -2.5 pp | -5.8 pp to +0.0 pp | 0 | 3 | 118 |
| longmemeval | All evidence packed, single-session-assistant | 96.4% | 92.9% | -3.6 pp | -8.9 pp to +0.0 pp | 0 | 2 | 54 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 93.3% | -6.7 pp | -16.7 pp to +0.0 pp | 0 | 2 | 28 |
| longmemeval | All evidence packed, single-session-user | 96.9% | 95.3% | -1.6 pp | -4.7 pp to +0.0 pp | 0 | 1 | 63 |
| longmemeval | All evidence packed, temporal-reasoning | 93.7% | 88.2% | -5.5 pp | -10.2 pp to -1.6 pp | 1 | 8 | 118 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 554.8 to 296.0 | 0.192 / 0.240 s to 0.134 / 0.175 s | 84.5 to 78.6 | 96.6% to 96.8% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 492.0 to 327.3 | 0.179 / 0.249 s to 0.121 / 0.225 s | 39.9 to 34.8 | 82.6% to 84.1% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 4.8 to 6.4, after 6.4 to 3.1.

- locomo questions that lost all their evidence among the candidates: 57 (conv-26-q0042, conv-26-q0048, conv-26-q0069, conv-30-q0002, conv-30-q0074, conv-41-q0003, conv-41-q0006, conv-41-q0008, conv-41-q0010, conv-41-q0028, conv-41-q0035, conv-41-q0045, conv-41-q0050, conv-41-q0064, conv-42-q0001, conv-42-q0034, conv-42-q0056, conv-42-q0066, conv-42-q0074, conv-42-q0081, ...).
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 38 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL 32, LEXICAL_AGG 41, VECTOR 38.
- longmemeval questions that lost all their evidence among the candidates: 0.
- longmemeval questions that gained all their evidence among the candidates: 0.
- longmemeval before: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 114.
- longmemeval after: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 167 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL 103, LEXICAL_AGG 114, VECTOR 167.

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
- locomo questions that left the current-state path: 55 (conv-41-q0023, conv-41-q0025, conv-41-q0028, conv-41-q0029, conv-41-q0030, conv-41-q0032, conv-41-q0035, conv-41-q0036, conv-41-q0037, conv-41-q0044, conv-41-q0057, conv-41-q0150, conv-42-q0030, conv-42-q0046, conv-42-q0060, conv-42-q0061, conv-42-q0063, conv-42-q0067, conv-42-q0070, conv-42-q0081, ...).
- longmemeval questions that entered the current-state path: 0.
- longmemeval questions that left the current-state path: 0.
The JSON report lists every question.

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
