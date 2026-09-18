# Preserve one relevance head before density packing

The fixed one-head experiment improved whole-source retention on the previously
examined 119-question development cohort. It preserves the highest-scored ordinary
multi-path candidate before applying the existing density comparator to the rest.
Instructions, pins and active tasks keep their original priority; no content is
truncated and every rendered context obeys its measured budget. This remains a
diagnostic, not a changed product default.

| Budget | Density recall | One-head recall | Wins / losses / ties |
|---|---:|---:|---:|
| 2,048 | 66.08% | 75.07% | 14 / 1 / 99 |
| 4,096 | 74.85% | 81.51% | 9 / 0 / 105 |
| 8,192 | 82.02% | 88.45% | 8 / 0 / 106 |

There are 114 labelled questions across 111 history/base-question groups; five
unlabelled questions receive null source recall. All 119 contexts were generated
in both arms at all three budgets. The 4K paired difference is +6.65 percentage
points, with a group-bootstrap 95% interval of +2.65 to +11.30. This descriptive
interval does not undo development-set reuse or selection of the hypothesis
after examining failures.

At 4K the assistant category improved from 0/9 to 7/9 sources retained. Two
multi-session questions also improved; knowledge-update, preference, user and
temporal categories had no changed per-question source recall. At 2K,
`case-0073` (multi-session) fell from one of three sources to none. Every category
and changed case is retained in the machine-readable results, including this loss.

The prior diagnosis reproduced the original assistant contexts exactly. All nine
labelled sources fit individually in 4K; seven were score rank 1 and the others
ranks 2 and 3. Density pushed them to queue positions 109–387. They cost 236–1,158
rendered entry tokens. The policy targets that length penalty; it does not infer
speaker identity from source length or use relevance labels during selection.

The three authored contract tests passed: whole qualified-source preservation,
pinned priority and oversized-source exclusion. The native study exited 0 and
reproduced all 357 baseline contexts. An independent JSON-entry measurement pass
checks all 714 rendered contexts, their budgets, whole-source membership and
per-case recall. This does not independently validate the relevance labels.

Next, run the unchanged answer-reader protocol on these exact contexts. Any
production change also needs broader regression evidence, especially preference
and multi-session tasks. The earlier 381-question pure-score confirmation failed
its preference guard; this new development experiment does not supersede that
failure, establish held-out quality or demonstrate competitive leadership.

Artifacts: [registration](packing-head1-dev-plan.json),
[complete summaries and changed cases](packing-head1-dev-results.json),
[measurement verification](packing-head1-dev-verification.json),
[assistant diagnosis](assistant-packing-diagnosis.json).
Implementation: `benchmarks/diagnostics/packing_head.py`; immutable full context
output: `data/benchmarks/packing-head1-dev-fc8e1f3.json` (hash in the results).
