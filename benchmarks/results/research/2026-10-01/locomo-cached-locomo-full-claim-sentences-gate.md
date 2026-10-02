# Offline evidence gate

Run: commit `f05b0ff0bcb293e1019f9c717f92dcca8d070168`, current defaults.

**Built packs, not the saved run.** Build `cached-locomo-full-claim-sentences` under `/Users/dmac/Sites/prism/data/extracted-packs-v1/cached-locomo-full-claim-sentences` (10 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 1540 | 0/1540 | 919.0 | 0.368 / 0.605 s | 102.7 | 94.4% | 0 | 1221/1536 (79.5%) | 17 | 74.6% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 102.7, 94.4%, 121/282.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 282 | 249/282 (88.3%) | 121/282 (42.9%) | 42.9% | 66 | 35.6% | 45.7% |
| open-domain | 96 | 70/92 (76.1%) | 48/92 (52.2%) | 52.2% | 93 | 27.7% | 58.7% |
| single-hop | 841 | 839/841 (99.8%) | 769/841 (91.4%) | 91.4% | 8 | 74.2% | 84.9% |
| temporal | 321 | 317/321 (98.8%) | 283/321 (88.2%) | 88.2% | 8 | 72.8% | 77.6% |

Packed records through session expansion: 38925 reached (24.6% of packed records), 452 found by no other path, 16165 scored by a session decay.

Aggregation: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 2, LEXICAL_AGG 41, VECTOR 41.

Annotated evidence turns cited by a packed extracted record: 1139 of 2345 (48.6%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
