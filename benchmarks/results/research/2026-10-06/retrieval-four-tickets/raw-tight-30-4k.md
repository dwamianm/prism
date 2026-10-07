# Offline evidence gate

Run: commit `55d3eb61a5c914a801225b69aed7b6c77568b496` with uncommitted changes, overrides `{"packing": {"graph_max_candidates": 30, "lexical_k": 30, "vector_k": 30}}`.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 466 | 0/466 | 138.5 (28.1% of stored turns) | 0.086 / 0.115 s | 81.3 | 96.7% | 0 | 384/462 (83.1%) | 6 | 77.3% |
| longmemeval | 42 | 0/42 | 111.2 (22.8% of stored turns) | 0.058 / 0.144 s | 21.7 | 87.8% | 0 | 27/38 (71.1%) | 4 | 76.6% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 81.3, 96.7%, 55/101.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 101 | 68/101 (67.3%) | 55/101 (54.5%) | 54.5% | 14 | 60.2% | 53.1% |
| open-domain | 33 | 19/29 (65.5%) | 16/29 (55.2%) | 55.2% | 20.5 | 58.3% | 67.6% |
| single-hop | 230 | 225/230 (97.8%) | 221/230 (96.1%) | 96.1% | 3 | 90.8% | 88.4% |
| temporal | 102 | 93/102 (91.2%) | 92/102 (90.2%) | 90.2% | 2 | 88.3% | 79.6% |

Source fidelity: 0/0 packed claims lack a source record in the same context (n/a); 0 lack complete source text. Packed source-text tokens per context: 0.0. Claim totals can change through folding; these rates are not answer accuracy.

All annotated evidence present as complete source text: 83.1%.

Packed records through session expansion: 19481 reached (51.4% of packed records), 10384 found by no other path, 13656 scored by a session decay.

Candidate recall per channel: the share of resolved evidence turns (pooled over questions) inside each channel's own top k, and in parentheses the share of annotated questions with all their evidence inside it. `vector_k` and `lexical_k` cut these rankings; count and list questions cut them at the widened limit, which only the last column applies, and either is the union of the two cuts. Lexical ranks only turns that share a term with the question. Graph, pinned records, the entity and aggregation lexical scans and session context are not counted. The rankings do not depend on the run's settings. The pool's "Evidence in top 25" above counts only returned turns. The JSON report has this table per category.

| Channel | Top 25 | Top 50 | Top 100 | Top 150 | Top 500 | All | This run's limits |
|---|---:|---:|---:|---:|---:|---:|---:|
| vector | 60.9% (63.0%) | 72.1% (70.8%) | 82.0% (79.7%) | 87.7% (85.7%) | 100.0% (98.9%) | 100.0% (98.9%) | 65.3% (65.8%) |
| lexical | 55.8% (59.5%) | 65.4% (68.2%) | 75.2% (75.5%) | 80.7% (78.4%) | 96.8% (95.0%) | 97.1% (95.5%) | 58.6% (61.9%) |
| either | 72.8% (74.2%) | 82.4% (82.5%) | 91.0% (89.6%) | 94.1% (92.2%) | 100.0% (98.9%) | 100.0% (98.9%) | 76.3% (76.8%) |

Aggregation: 9 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 9 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL 9, LEXICAL_AGG 9, VECTOR 9.

Contexts that differ from the saved run: 466 (conv-26-q0000, conv-26-q0001, conv-26-q0002, conv-26-q0003, conv-26-q0004, conv-26-q0005, conv-26-q0006, conv-26-q0007, conv-26-q0008, conv-26-q0009, conv-26-q0010, conv-26-q0011, conv-26-q0012, conv-26-q0013, conv-26-q0014, conv-26-q0015, conv-26-q0016, conv-26-q0017, conv-26-q0018, conv-26-q0019, ...). The JSON report lists every one.

## longmemeval

2026-09-23 baseline: 23.9 records per context, 32% memory text, all evidence packed for 403/470 annotated questions. This run: 21.7, 87.8%, 27/38.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| knowledge-update | 6 | 5/5 (100.0%) | 5/5 (100.0%) | 100.0% | 2.5 | 100.0% | 90.7% |
| multi-session | 13 | 9/10 (90.0%) | 5/10 (50.0%) | 50.0% | 8 | 73.3% | 66.1% |
| single-session-assistant | 5 | 5/5 (100.0%) | 5/5 (100.0%) | 100.0% | 2 | 100.0% | 95.5% |
| single-session-preference | 5 | 5/5 (100.0%) | 3/5 (60.0%) | 60.0% | 7 | 85.7% | 69.9% |
| single-session-user | 4 | 4/4 (100.0%) | 4/4 (100.0%) | 100.0% | 1 | 100.0% | 95.5% |
| temporal-reasoning | 9 | 6/9 (66.7%) | 5/9 (55.6%) | 55.6% | 6 | 88.2% | 67.0% |

Source fidelity: 0/0 packed claims lack a source record in the same context (n/a); 0 lack complete source text. Packed source-text tokens per context: 0.0. Claim totals can change through folding; these rates are not answer accuracy.

All annotated evidence present as complete source text: 71.1%.

Packed records through session expansion: 704 reached (77.3% of packed records), 93 found by no other path, 227 scored by a session decay.

Candidate recall per channel: the share of resolved evidence turns (pooled over questions) inside each channel's own top k, and in parentheses the share of annotated questions with all their evidence inside it. `vector_k` and `lexical_k` cut these rankings; count and list questions cut them at the widened limit, which only the last column applies, and either is the union of the two cuts. Lexical ranks only turns that share a term with the question. Graph, pinned records, the entity and aggregation lexical scans and session context are not counted. The rankings do not depend on the run's settings. The pool's "Evidence in top 25" above counts only returned turns. The JSON report has this table per category.

| Channel | Top 25 | Top 50 | Top 100 | Top 150 | Top 500 | All | This run's limits |
|---|---:|---:|---:|---:|---:|---:|---:|
| vector | 81.0% (73.7%) | 94.9% (89.5%) | 98.7% (97.4%) | 98.7% (97.4%) | 100.0% (100.0%) | 100.0% (100.0%) | 88.6% (84.2%) |
| lexical | 64.6% (63.2%) | 74.7% (63.2%) | 83.5% (73.7%) | 87.3% (76.3%) | 100.0% (100.0%) | 100.0% (100.0%) | 73.4% (65.8%) |
| either | 84.8% (76.3%) | 94.9% (89.5%) | 98.7% (97.4%) | 100.0% (100.0%) | 100.0% (100.0%) | 100.0% (100.0%) | 91.1% (86.8%) |

Aggregation: 13 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 13 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL 13, LEXICAL_AGG 8, VECTOR 13.

Contexts that differ from the saved run: 42 (58bf7951, ad7109d1, c8c3f81d, 36580ce8, 0a995998, gpt4_15e38248, gpt4_7fce9456, 00ca467f, gpt4_ab202e7f, 80ec1f4f_abs, 06878be2, 195a1a1b, 54026fce, 6b7dfb22, 38146c39, 51c32626, 720133ac, ba358f49, bc149d6b, 37f165cf, ...). The JSON report lists every one.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
