# Offline evidence gate comparison

Before: commit `725ddb34f370cb88c696221bb512c98998000d8f`, overrides `{"enable_reranker": true, "reranker_policy": "score_envelope", "reranker_prior_weight": 0, "reranker_top_k": 300}`.
After: commit `725ddb34f370cb88c696221bb512c98998000d8f`, overrides `{"enable_reranker": true, "reranker_policy": "score_envelope", "reranker_prior_weight": 0, "reranker_top_k": 300}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 86.1% | 86.1% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | All evidence packed with memory text | 86.1% | 86.1% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Evidence recall | 90.7% | 90.7% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | All evidence among the candidates | 99.3% | 99.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 79.0% | 79.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1540 |
| locomo | All evidence packed, multi-hop | 55.3% | 55.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 282 |
| locomo | All evidence packed, open-domain | 59.8% | 59.8% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 92 |
| locomo | All evidence packed, single-hop | 96.0% | 96.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 841 |
| locomo | All evidence packed, temporal | 94.7% | 94.7% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 321 |
| longmemeval | All evidence packed | 94.3% | 94.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | All evidence packed with memory text | 94.3% | 94.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Evidence recall | 96.3% | 96.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Projected accuracy | 91.1% | 91.1% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 500 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 72 |
| longmemeval | All evidence packed, multi-session | 90.9% | 90.9% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 121 |
| longmemeval | All evidence packed, single-session-assistant | 94.6% | 94.6% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 56 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 30 |
| longmemeval | All evidence packed, single-session-user | 98.4% | 98.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 64 |
| longmemeval | All evidence packed, temporal-reasoning | 90.6% | 90.6% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 127 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 555.0 to 555.0 | 0.421 / 0.523 s to 0.428 / 0.522 s | 86.7 to 86.7 | 96.6% to 96.6% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 492.1 to 492.1 | 1.736 / 1.979 s to 1.711 / 1.895 s | 37.8 to 37.8 | 82.9% to 82.9% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 4.8 to 5.4, after 3.6 to 3.6.

Before, cross-encoder reranking inside retrieval, p50 / p95 per question: locomo 0.270 / 0.328 s; longmemeval 1.586 / 1.799 s. Retrieval time includes it.

After, cross-encoder reranking inside retrieval, p50 / p95 per question: locomo 0.274 / 0.328 s; longmemeval 1.571 / 1.738 s. Retrieval time includes it.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.
- longmemeval questions that lost all their evidence among the candidates: 0.
- longmemeval questions that gained all their evidence among the candidates: 0.
- longmemeval before: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 114.
- longmemeval after: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 114.

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
- longmemeval questions that entered the current-state path: 0.
- longmemeval questions that left the current-state path: 0.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 59133 (44.3%) / 5 / 33598 | 59133 (44.3%) / 5 / 33598 |
| locomo | multi-hop | 10476 (44.3%) / 1 / 5962 | 10476 (44.3%) / 1 / 5962 |
| locomo | open-domain | 3623 (42.2%) / 1 / 2105 | 3623 (42.2%) / 1 / 2105 |
| locomo | single-hop | 32668 (45.6%) / 2 / 18463 | 32668 (45.6%) / 2 / 18463 |
| locomo | temporal | 12366 (41.6%) / 1 / 7068 | 12366 (41.6%) / 1 / 7068 |
| longmemeval | all | 11634 (61.6%) / 0 / 4525 | 11634 (61.6%) / 0 / 4525 |
| longmemeval | knowledge-update | 1812 (69.9%) / 0 / 689 | 1812 (69.9%) / 0 / 689 |
| longmemeval | multi-session | 3212 (72.4%) / 0 / 1301 | 3212 (72.4%) / 0 / 1301 |
| longmemeval | single-session-assistant | 1244 (38.2%) / 0 / 268 | 1244 (38.2%) / 0 / 268 |
| longmemeval | single-session-preference | 707 (52.4%) / 0 / 260 | 707 (52.4%) / 0 / 260 |
| longmemeval | single-session-user | 1580 (72.2%) / 0 / 748 | 1580 (72.2%) / 0 / 748 |
| longmemeval | temporal-reasoning | 3079 (60.7%) / 0 / 1259 | 3079 (60.7%) / 0 / 1259 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
