# LongMemEval-S grouped conversation packing v1

**Decision:** Advance both frozen grouped arms to a paired answer-quality
development trial. Do not expose or enable the policy in the product yet.

**Date:** 2026-09-18

**Registration revision:** `0d074d5ec7f8191eaac9d45f3623b07c211c398e`

## Question

Can PRME render related chat turns as a named conversation, sharing repeated
audit fields without obscuring record identity, and spend the saved space on
more evidence without displacing anything selected by the current packer?

## Protocol

The implementation, runner and gate were committed before aggregate evaluation.
The trial reopened all 500 frozen LongMemEval-S packs and reproduced the current
candidate IDs, scores, order and control context. All arms used the same 4,096
token budget with the existing 100-token caller reserve.

The same-set arm reserved the exact control node IDs and representations. It
grouped two or more records only when they shared an exact section, scope and
nonempty session ID. A named group header carried the common type, scope,
epistemic state, lifecycle and event time. Each turn retained its full UUID,
validity, source type, representation and complete text. Valid benchmark-stored
role and turn-index metadata supplied explicit conversation order. Singleton and
sessionless records retained the ordinary auditable JSON format.

The fill arm first reserved the same control evidence and included guidance,
then spent only the grouped context's remaining space on later candidates from
the unchanged balanced ordering. An exact whole-output token count enforced the
same ceiling. Neither packer received question types, references, labeled turns,
labeled sessions or answers.

## Result

Every case replayed and stayed within budget. Neither grouped arm lost a control
record or included guidance item. The same-set arm preserved the exact control
identity and representation map on all 500 questions. The fill arm had zero
per-question turn-recall losses, zero session-recall losses and zero category
mean losses. It increased the number of questions with complete labeled-turn
coverage, so the registered source gate passed.

| Arm | Complete turns | Mean turn recall | Complete sessions | Mean session recall | Mean records | Mean tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Control | 403 | 0.914645 | 437 | 0.962624 | 23.932 | 3964.750 |
| Grouped same set | 403 | 0.914645 | 437 | 0.962624 | 23.932 | 3690.976 |
| Grouped fill | 410 | 0.921348 | 438 | 0.963688 | 26.056 | 3968.506 |

Same-set grouping saved a mean 273.774 tokens and used fewer tokens on 499 of
500 questions. Fill added a mean 2.124 records. Labeled-turn recall improved on
eight questions and regressed on none; required-session recall improved on one
and regressed on none. Category means improved for multi-session,
single-session-assistant and temporal-reasoning questions and tied elsewhere.

The eight turn-recall gains were:

| Question | Category | Control | Grouped fill |
| --- | --- | ---: | ---: |
| `80ec1f4f` | multi-session | 0.500000 | 1.000000 |
| `gpt4_7fce9456` | multi-session | 0.666667 | 0.833333 |
| `2788b940` | multi-session | 0.800000 | 1.000000 |
| `e3038f8c` | multi-session | 0.750000 | 1.000000 |
| `67e0d0f2` | multi-session | 0.500000 | 1.000000 |
| `gpt4_f420262c` | temporal-reasoning | 0.800000 | 1.000000 |
| `gpt4_65aabe59` | temporal-reasoning | 0.666667 | 1.000000 |
| `eaca4986` | single-session-assistant | 0.000000 | 1.000000 |

## Verification and artifacts

An independent pass verified all 500 case checksums, the registration hash, the
complete case manifest, the result self-hash and all same-set identity maps.
The result identity is
`e6060d17af4811fb6618912579876722a6d1f3770b6dd8758f3bd8f9759515d5`.
The summary artifact is
[longmemeval-s-grouped-conversations-v1-results.json](longmemeval-s-grouped-conversations-v1-results.json).
Complete contexts, per-case evidence and the run log remain under
`data/benchmarks/longmemeval-s-grouped-conversations-v1/`.

## Boundary and next decision

This is source-retention evidence on the fully observed development cohort. It
does not show that a reader understands the grouped representation or that the
added records improve answers. It is not an independent holdout, a competitor
comparison or a Zep parity result.

The direct-turn benchmark carries exact role and turn-index metadata. Ordinary
PRME nodes do not yet preserve an equally strong role contract, so this private
benchmark hook must not become a public format by trusting arbitrary caller
metadata. First run a frozen paired answer trial with control, same-set and fill
contexts. Product integration, receipt versioning and role provenance remain
conditional on answer evidence and a separate-workload confirmation.
