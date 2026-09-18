# Packing length-penalty development study

All 119 questions completed at frozen commit `e227a5e`, with zero errors and native exit 0. The 30 registered arms evaluated 3,570 contexts. Both endpoints reproduced all 1,428 saved score/density controls exactly. Source candidates stayed unchanged; labels were joined only after packing. All evidence credits and category summaries were independently recomputed.

See the [registration](packing-length-dev-plan.json), [complete results](packing-length-dev-completion-e227a5e.json), and [source study](HYBRID-LEXICAL-STUDY.md).

Only the multi-path priority value changes: `score / max(full entry tokens, 1) ** alpha`. Alpha 0 is score ordering; alpha 1 is density. Instructions, pins, active tasks, fallback priority, tie-breaking, complete source text and whole-output token limits stay unchanged.

| Query policy | Tokens | alpha 0 | alpha .25 | alpha .5 | alpha .75 | alpha 1 |
|---|---:|---:|---:|---:|---:|---:|
| Native parser | 2,048 | 88.16% | 92.54% | 86.04% | 77.92% | 65.13% |
| Native parser | 4,096 | 93.49% | 95.03% | 90.06% | 82.68% | 74.85% |
| Native parser | 8,192 | 96.35% | 96.05% | 91.37% | 88.74% | 82.02% |
| Stopwords removed | 2,048 | 86.84% | 92.47% | 89.55% | 83.99% | 82.16% |
| Stopwords removed | 4,096 | 90.94% | 95.47% | 91.81% | 87.94% | 85.96% |
| Stopwords removed | 8,192 | 96.42% | 96.05% | 96.20% | 92.69% | 91.81% |

At 4K with the native parser, alpha .25 raises overall source recall from 74.85% to 95.03%. All six category means exceed their density baselines on this development cohort. Compared with score ordering, multi-session recall rises from 84.64% to 94.79%, but assistant recall falls from 100% to 77.78% (nine questions). Preference recall stays at 92.86% (seven questions). At 8K, alpha .25 slightly trails score ordering overall. Removing stopwords with alpha .25 raises 4K recall to 95.47%, but does not solve the assistant loss.

Decision: preserve production defaults and carry native-parser alpha .25 forward as a hypothesis for broader testing. It uses the existing query behavior and changes one comparator. Its development improvement does not establish downstream answer quality or justify promotion. The failed 381-question confirmation gate remains unchanged.

This is retrospective development evidence from previously examined questions, not an independent test. Question bootstrap intervals ignore shared histories and multiple comparisons across the grid. Neither retrieval nor providers were rerun: frozen candidate snapshots were repacked. All 30 arms and category results are retained, including regressions. No competitive quality claim follows.

Raw output: `data/benchmarks/packing-length-dev-e227a5e.json`, SHA-256 `bcf9640c01ce9e5be032defa1fb0ace4309676e1079173cfc521e1e3c4c4066f`.
