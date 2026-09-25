# Offline evidence gate

Run: commit `7825ec201849255bad1ff70c406ae6b70028bfea`, overrides `{"packing": {"lexical_k": 150, "vector_k": 150}}`.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 1540 | 0/1540 | 296.0 (50.4% of stored turns) | 0.134 / 0.175 s | 78.6 | 96.8% | 0 | 1300/1536 (84.6%) | 6 | 78.1% |
| longmemeval | 500 | 0/500 | 327.3 (66.6% of stored turns) | 0.121 / 0.225 s | 34.8 | 84.1% | 0 | 430/470 (91.5%) | 3 | 89.5% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 78.6, 96.8%, 147/282.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 282 | 246/282 (87.2%) | 147/282 (52.1%) | 52.1% | 19 | 56.5% | 51.6% |
| open-domain | 96 | 68/92 (73.9%) | 53/92 (57.6%) | 57.6% | 26.5 | 50.0% | 61.0% |
| single-hop | 841 | 837/841 (99.5%) | 799/841 (95.0%) | 95.0% | 3 | 88.6% | 87.6% |
| temporal | 321 | 317/321 (98.8%) | 301/321 (93.8%) | 93.8% | 2 | 84.1% | 81.6% |

Candidate recall per channel: the share of resolved evidence turns (pooled over questions) inside each channel's own top k, and in parentheses the share of annotated questions with all their evidence inside it. `vector_k` and `lexical_k` cut these rankings; count and list questions cut them at the widened limit, which only the last column applies, and either is the union of the two cuts. Lexical ranks only turns that share a term with the question. Graph, pinned records, the entity and aggregation lexical scans and session context are not counted. The rankings do not depend on the run's settings. The pool's "Evidence in top 25" above counts only returned turns. The JSON report has this table per category.

| Channel | Top 25 | Top 50 | Top 100 | Top 150 | Top 500 | All | This run's limits |
|---|---:|---:|---:|---:|---:|---:|---:|
| vector | 64.2% (65.6%) | 74.2% (73.5%) | 83.2% (82.0%) | 88.4% (87.5%) | 99.3% (98.8%) | 100.0% (99.4%) | 88.6% (87.8%) |
| lexical | 58.3% (61.7%) | 66.3% (68.7%) | 74.8% (75.3%) | 79.6% (78.9%) | 96.3% (94.9%) | 98.1% (97.3%) | 80.3% (79.5%) |
| either | 75.5% (76.8%) | 83.2% (83.3%) | 90.6% (89.7%) | 94.2% (93.1%) | 99.8% (99.2%) | 100.0% (99.4%) | 94.2% (93.2%) |

Aggregation: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 38 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL 32, LEXICAL_AGG 41, VECTOR 38.

Contexts that differ from the saved run: 1540 (conv-26-q0000, conv-26-q0001, conv-26-q0002, conv-26-q0003, conv-26-q0004, conv-26-q0005, conv-26-q0006, conv-26-q0007, conv-26-q0008, conv-26-q0009, conv-26-q0010, conv-26-q0011, conv-26-q0012, conv-26-q0013, conv-26-q0014, conv-26-q0015, conv-26-q0016, conv-26-q0017, conv-26-q0018, conv-26-q0019, ...). The JSON report lists every one.

## longmemeval

2026-09-23 baseline: 23.9 records per context, 32% memory text, all evidence packed for 403/470 annotated questions. This run: 34.8, 84.1%, 430/470.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| knowledge-update | 78 | 72/72 (100.0%) | 72/72 (100.0%) | 100.0% | 3 | 100.0% | 93.3% |
| multi-session | 133 | 121/121 (100.0%) | 105/121 (86.8%) | 86.8% | 5 | 84.8% | 86.0% |
| single-session-assistant | 56 | 56/56 (100.0%) | 52/56 (92.9%) | 92.9% | 1 | 98.2% | 90.9% |
| single-session-preference | 30 | 30/30 (100.0%) | 28/30 (93.3%) | 93.3% | 5.5 | 88.6% | 91.3% |
| single-session-user | 70 | 64/64 (100.0%) | 61/64 (95.3%) | 95.3% | 1 | 98.5% | 93.2% |
| temporal-reasoning | 133 | 127/127 (100.0%) | 112/127 (88.2%) | 88.2% | 4 | 89.3% | 87.7% |

Candidate recall per channel: the share of resolved evidence turns (pooled over questions) inside each channel's own top k, and in parentheses the share of annotated questions with all their evidence inside it. `vector_k` and `lexical_k` cut these rankings; count and list questions cut them at the widened limit, which only the last column applies, and either is the union of the two cuts. Lexical ranks only turns that share a term with the question. Graph, pinned records, the entity and aggregation lexical scans and session context are not counted. The rankings do not depend on the run's settings. The pool's "Evidence in top 25" above counts only returned turns. The JSON report has this table per category.

| Channel | Top 25 | Top 50 | Top 100 | Top 150 | Top 500 | All | This run's limits |
|---|---:|---:|---:|---:|---:|---:|---:|
| vector | 90.2% (86.2%) | 95.3% (92.6%) | 98.5% (97.9%) | 99.0% (98.5%) | 100.0% (100.0%) | 100.0% (100.0%) | 99.5% (99.1%) |
| lexical | 82.5% (78.7%) | 88.5% (84.7%) | 92.9% (90.2%) | 94.8% (91.7%) | 99.5% (99.1%) | 99.5% (99.1%) | 97.3% (95.3%) |
| either | 95.3% (92.8%) | 98.2% (96.8%) | 99.4% (99.1%) | 99.8% (99.6%) | 100.0% (100.0%) | 100.0% (100.0%) | 100.0% (100.0%) |

Aggregation: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 167 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL 103, LEXICAL_AGG 114, VECTOR 167.

Contexts that differ from the saved run: 500 (e47becba, 118b2229, 51a45a95, 58bf7951, 1e043500, c5e8278d, 6ade9755, 6f9b354f, 58ef2f1c, f8c5f88b, 5d3d2817, 7527f7e2, c960da58, 3b6f954b, 726462e0, 94f70d80, 66f24dbb, ad7109d1, af8d2e46, dccbc061, ...). The JSON report lists every one.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
