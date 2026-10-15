# Offline evidence gate

Run: commit `58caddb1a88dd60d51fc97c15742aee6ec8cf34a`, current defaults.

**Built packs, not the saved run.** Build `ingest-baseline-locomo-deepseek-2026-10-15-repeat` under `/Users/dwamianm/Sites/prism/data/extracted-packs-v1/ingest-baseline-locomo-deepseek-2026-10-15-repeat` (10 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 1540 | 0/1540 | 928.4 | 0.367 / 0.524 s | 96.1 | 95.3% | 0 | 1252/1536 (81.5%) | 17.5 | 75.9% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 96.1, 95.3%, 134/282.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 282 | 249/282 (88.3%) | 134/282 (47.5%) | 47.5% | 65 | 36.1% | 48.6% |
| open-domain | 96 | 70/92 (76.1%) | 53/92 (57.6%) | 57.6% | 99 | 27.5% | 61.0% |
| single-hop | 841 | 839/841 (99.8%) | 777/841 (92.4%) | 92.4% | 8.5 | 73.3% | 85.6% |
| temporal | 321 | 316/321 (98.4%) | 288/321 (89.7%) | 89.7% | 8 | 71.9% | 78.7% |

Packed records through session expansion: 23155 reached (15.6% of packed records), 713 found by no other path, 12436 scored by a session decay.

Aggregation: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL_AGG 41, VECTOR 41.

Annotated evidence turns cited by a packed extracted record: 318 of 2345 (13.6%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
