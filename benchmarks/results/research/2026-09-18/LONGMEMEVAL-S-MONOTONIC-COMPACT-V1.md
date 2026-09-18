# LongMemEval-S monotonic compact packing v1

**Source-retention decision:** advanced to paired answer-quality evaluation.
The subsequent registered answer trial failed 76/119 to 72/119, so the policy
is now rejected for confirmation and product integration. See the
[answer report](LONGMEMEVAL-S-MONOTONIC-ANSWER-DEV-V2.md).

## Why this experiment exists

Direct compact packing fit more records but changed balanced density ordering.
Its registered 381-question confirmation lost labeled turns on six questions,
so it was rejected as a default. The follow-up composition first reserves every
record selected by ordinary auditable packing at the same representation, plus
any guidance included by the control. It then uses compact serialization and
spends only the remaining budget on additional candidates. If preservation is
impossible, it falls back to the auditable control.

All 500 LongMemEval-S questions had already been inspected when this policy was
designed. The complete cohort is therefore development evidence, not an
independent holdout.

## Registered gates and result

The registered run reopened all 500 frozen PRME packs and reproduced every
saved auditable context, candidate identity, and score before comparing the
candidate. It passed every gate:

- replay failures: 0;
- whole-context budget violations: 0;
- questions losing a control record: 0;
- questions losing included control guidance: 0;
- compact-to-auditable fallbacks: 0;
- per-question labeled-turn recall losses: 0;
- per-question required-session recall losses: 0;
- categories with lower mean turn recall: 0.

Result identity:
`17bb8c5ad7cb3b243b9409c3fe70c498a92f928fbdb92fe6b9c0052add260c59`.

## Effect size

Across all 500 questions, median packed records rose from 24 to 33 and mean
records from 23.93 to 32.52. Among the 470 answerable questions:

- complete required-session coverage rose from 437 to 442;
- complete labeled-turn coverage rose from 403 to 409;
- mean required-session recall rose from 0.96262 to 0.96667;
- mean labeled-turn recall rose from 0.91465 to 0.92128;
- labeled-turn recall had 7 wins, 0 losses, and 463 ties;
- required-session recall had 6 wins, 0 losses, and 464 ties.

Every question used at most 3,996 memory tokens after the registered 100-token
reserve. All 500 saved case checksums, formats, budgets, and preservation
invariants were independently revalidated after completion.

## Boundary

This establishes a source-retention improvement on the complete frozen
LongMemEval-S workload. It does not establish answer improvement, an independent
holdout result, Zep parity, or universal superiority. The implementation remains
experimental and is not wired into the public configuration or receipt schema.
The paired reader trial used exact frozen contexts, counterbalanced arm order
and a separately calibrated judge. It failed answer non-inferiority despite the
source-retention gain. This source result must not be used by itself to justify
a default or opt-in product policy.
