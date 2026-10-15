# Offline evidence gate

Run: commit `58caddb1a88dd60d51fc97c15742aee6ec8cf34a`, current defaults.

**Built packs, not the saved run.** Build `ingest-baseline-locomo-deepseek-2026-10-15` under `/Users/dwamianm/Sites/prism/data/extracted-packs-v1/ingest-baseline-locomo-deepseek-2026-10-15` (10 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 1540 | 0/1540 | 929.7 | 0.412 / 0.600 s | 96.2 | 95.2% | 0 | 1253/1536 (81.6%) | 17 | 75.9% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 96.2, 95.2%, 135/282.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 282 | 248/282 (87.9%) | 135/282 (47.9%) | 47.9% | 61 | 36.5% | 48.9% |
| open-domain | 96 | 70/92 (76.1%) | 52/92 (56.5%) | 56.5% | 94 | 28.1% | 60.6% |
| single-hop | 841 | 839/841 (99.8%) | 777/841 (92.4%) | 92.4% | 8 | 73.0% | 85.6% |
| temporal | 321 | 316/321 (98.4%) | 289/321 (90.0%) | 90.0% | 8 | 71.9% | 78.9% |

Packed records through session expansion: 23334 reached (15.8% of packed records), 725 found by no other path, 12540 scored by a session decay.

Aggregation: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL_AGG 41, VECTOR 41.

Annotated evidence turns cited by a packed extracted record: 319 of 2345 (13.6%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
