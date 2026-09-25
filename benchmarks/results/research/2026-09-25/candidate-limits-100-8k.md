# Offline evidence gate comparison

Before: commit `a6dd65d83f9bde34441296e34b379d52dda57439`, overrides `{"packing": {"token_budget": 8192}}`.
After: commit `a6dd65d83f9bde34441296e34b379d52dda57439`, overrides `{"packing": {"lexical_k": 100, "token_budget": 8192, "vector_k": 100}}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 89.6% | 90.8% | +1.2 pp | +0.1 pp to +2.3 pp | 46 | 28 | 1462 |
| locomo | All evidence packed with memory text | 89.6% | 90.8% | +1.2 pp | +0.1 pp to +2.3 pp | 46 | 28 | 1462 |
| locomo | Evidence recall | 93.9% | 94.4% | +0.5 pp | -0.3 pp to +1.4 pp | 53 | 48 | 1435 |
| locomo | All evidence among the candidates | 99.3% | 94.1% | -5.1 pp | -6.3 pp to -4.1 pp | 0 | 79 | 1457 |
| locomo | Projected accuracy | 81.3% | 82.2% | +0.9 pp | +0.2 pp to +1.6 pp | 46 | 28 | 1466 |
| locomo | All evidence packed, multi-hop | 66.0% | 70.6% | +4.6 pp | +0.4 pp to +8.9 pp | 25 | 12 | 245 |
| locomo | All evidence packed, open-domain | 67.4% | 62.0% | -5.4 pp | -12.0 pp to +0.0 pp | 2 | 7 | 83 |
| locomo | All evidence packed, single-hop | 97.4% | 98.8% | +1.4 pp | +0.5 pp to +2.5 pp | 16 | 4 | 821 |
| locomo | All evidence packed, temporal | 96.6% | 96.0% | -0.6 pp | -2.5 pp to +0.9 pp | 3 | 5 | 313 |
| longmemeval | All evidence packed | 97.2% | 93.4% | -3.8 pp | -6.0 pp to -1.9 pp | 3 | 21 | 446 |
| longmemeval | All evidence packed with memory text | 97.2% | 93.4% | -3.8 pp | -6.0 pp to -1.9 pp | 3 | 21 | 446 |
| longmemeval | Evidence recall | 98.2% | 96.3% | -1.9 pp | -3.3 pp to -0.5 pp | 3 | 21 | 446 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Projected accuracy | 92.9% | 90.6% | -2.3 pp | -3.6 pp to -1.2 pp | 3 | 21 | 476 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 72 |
| longmemeval | All evidence packed, multi-session | 94.2% | 88.4% | -5.8 pp | -10.7 pp to -1.7 pp | 0 | 7 | 114 |
| longmemeval | All evidence packed, single-session-assistant | 96.4% | 100.0% | +3.6 pp | +0.0 pp to +8.9 pp | 2 | 0 | 54 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 90.0% | -10.0 pp | -23.3 pp to +0.0 pp | 0 | 3 | 27 |
| longmemeval | All evidence packed, single-session-user | 98.4% | 98.4% | +0.0 pp | -4.7 pp to +4.7 pp | 1 | 1 | 62 |
| longmemeval | All evidence packed, temporal-reasoning | 97.6% | 89.8% | -7.9 pp | -12.6 pp to -3.1 pp | 0 | 10 | 117 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 554.8 to 234.0 | n/a to n/a | 165.7 to 165.9 | 97.3% to 97.3% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 492.0 to 256.2 | n/a to n/a | 69.6 to 47.7 | 84.9% to 87.9% | 0 to 0 |

Retrieval latency is shown only when both reports timed the same work after a warm-up search: reports from before issue #87 included the embedding model's load, and a plain baseline times its own ranking and packing.

- locomo questions that lost all their evidence among the candidates: 79 (conv-26-q0038, conv-26-q0042, conv-26-q0048, conv-26-q0069, conv-30-q0002, conv-30-q0044, conv-30-q0074, conv-41-q0003, conv-41-q0006, conv-41-q0008, conv-41-q0010, conv-41-q0028, conv-41-q0035, conv-41-q0045, conv-41-q0050, conv-41-q0064, conv-42-q0001, conv-42-q0034, conv-42-q0056, conv-42-q0059, ...).
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL 40, LEXICAL_AGG 41, VECTOR 41.
- longmemeval questions that lost all their evidence among the candidates: 0.
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
- locomo questions that left the current-state path: 79 (conv-41-q0012, conv-41-q0015, conv-41-q0023, conv-41-q0025, conv-41-q0028, conv-41-q0029, conv-41-q0030, conv-41-q0032, conv-41-q0035, conv-41-q0036, conv-41-q0037, conv-41-q0044, conv-41-q0057, conv-41-q0101, conv-41-q0150, conv-42-q0010, conv-42-q0027, conv-42-q0030, conv-42-q0046, conv-42-q0060, ...).
- longmemeval questions that entered the current-state path: 0.
- longmemeval questions that left the current-state path: 0.
The JSON report lists every question.

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
