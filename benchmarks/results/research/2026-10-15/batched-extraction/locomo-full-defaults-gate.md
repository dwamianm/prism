# Offline evidence gate

Run: commit `d810ee5552e388f85aac62f4cf8c6c6038f0b1c8`, current defaults.

**Built packs, not the saved run.** Build `full-defaults-locomo-deepseek-d810ee55` under `/Users/dmac/Sites/prism/data/extracted-packs-v1/full-defaults-locomo-deepseek-d810ee55` (10 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 1540 | 0/1540 | 868.2 | 0.288 / 0.414 s | 88.6 | 94.5% | 0 | 1286/1536 (83.7%) | 15 | 77.5% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 88.6, 94.5%, 142/282.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 282 | 267/282 (94.7%) | 142/282 (50.4%) | 50.4% | 50 | 39.0% | 50.5% |
| open-domain | 96 | 84/92 (91.3%) | 51/92 (55.4%) | 55.4% | 90 | 28.3% | 60.1% |
| single-hop | 841 | 839/841 (99.8%) | 801/841 (95.2%) | 95.2% | 6 | 79.4% | 87.7% |
| temporal | 321 | 320/321 (99.7%) | 292/321 (91.0%) | 91.0% | 6 | 74.6% | 79.6% |

Packed records through session expansion: 39404 reached (28.9% of packed records), 619 found by no other path, 25615 scored by a session decay.

Aggregation: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 32 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41, VECTOR 32.

Annotated evidence turns cited by a packed extracted record: 374 of 2345 (15.9%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
