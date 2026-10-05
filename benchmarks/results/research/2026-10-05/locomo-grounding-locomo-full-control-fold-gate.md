# Offline evidence gate

Run: commit `8ed4e559b840dc03370b092f7c8d779dfd342ef2`, overrides `{"packing": {"fold_repeated_text": true}}`.

**Built packs, not the saved run.** Build `grounding-locomo-full-control` under `/Users/dmac/Sites/prism/data/extracted-packs-v1/grounding-locomo-full-control` (10 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 1540 | 0/1540 | 909.0 | 0.424 / 0.601 s | 80.4 | 95.8% | 0 | 1227/1536 (79.9%) | 19 | 74.8% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 80.4, 95.8%, 127/282.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 282 | 242/282 (85.8%) | 127/282 (45.0%) | 45.0% | 77.5 | 33.4% | 47.0% |
| open-domain | 96 | 69/92 (75.0%) | 48/92 (52.2%) | 52.2% | 108.5 | 25.3% | 58.7% |
| single-hop | 841 | 837/841 (99.5%) | 763/841 (90.7%) | 90.7% | 10 | 75.1% | 84.4% |
| temporal | 321 | 317/321 (98.8%) | 289/321 (90.0%) | 90.0% | 9 | 70.1% | 78.9% |

Packed records through session expansion: 22894 reached (18.5% of packed records), 685 found by no other path, 9714 scored by a session decay.

Aggregation: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 5, LEXICAL_AGG 41, VECTOR 41.

Annotated evidence turns cited by a packed extracted record: 317 of 2345 (13.5%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
