# Fail-closed quantity text planner v2

**Decision:** Accept the narrow natural-language planner and executor tested
here.

**Date:** 2026-09-18

**Registration SHA-256:**
`c70f9129824fc2d8b0f44b56d67217be2e8eab2c2151603fd5d08f81a9552a4e`

## Question

Can PRME safely translate a small, explicit set of natural-language amount and
count questions into the exact quantity aggregation API while refusing wording
whose meaning it cannot preserve?

## Protocol

The preregistered assay copied the already completed Qwen v2 quantity pack and
bound its 22-file manifest before execution. It made no extraction, reader,
judge, or other provider calls. Five executable questions covered individual
fundraising, group fundraising, measured distance, spending, and the alternate
`What's the total amount ...` form. Six controls covered a trailing qualifier,
negation, future wording, a named subject, an unknown action, and an unsupported
unit.

Every executable case had to expose its exact structured query, preserve the
question's `I` or `we` subject, use the registered predicate-prefix family,
keep units separate, scan the complete owner-bound stored set, and reproduce
the expected values. Every unsupported case had to return a typed refusal with
no query, no aggregation, and zero calls to `scan_nodes`.

## Result

All registered gates passed.

| Gate | Result |
| --- | ---: |
| Executable questions | 5 / 5 |
| Unsupported questions | 6 / 6 |
| Storage scans for refusals | 0 |
| Provider calls | 0 |
| Source pack unchanged | yes |

The individual fundraising question selected only `I` claims and returned
separate `$` and `USD` groups: `$2,750` across three contributions and `100
USD` as one contribution. The group question selected only `we` and returned
`$1,000`. The distance and spending questions returned `5 kilometers` and
`$300`. This verifies that the planner does not silently merge individual and
group subjects or currencies.

Every response exposed the fixed assumptions: owner binding, exact first-person
subject, positive default epistemic filtering, token-bounded predicate-prefix
families, and no unit conversion. Qualifiers and unsupported semantics were not
dropped to make a query fit.

## Failure preservation and repairs

The v1 result remains preserved as a rejection. Its observed plans, totals,
subjects, units, and six refusal cases were correct, but its scorer included a
redundant displayed `unit` field on the actual side after already keying the
comparison by unit. This made all five otherwise matching result groups fail.
The runner comparison was corrected and failed runs now return a nonzero shell
status before v2 was registered.

Replaying the v1 artifact also exposed a public-model defect: aggregation
queries serialized the default all-scope selector as `scopes=[]` but rejected
that same explicit value during validation. Assertion and quantity queries now
round-trip their own JSON; empty scopes consistently mean all scopes while
duplicates remain invalid. That repair was committed before the v2
implementation hashes were frozen.

## Integrity and boundary

The result self-hash was independently recomputed as
`6cc28aee8bc231f66b528145051bf593099ad6054f20ba00189a62197a47a785`.
The frozen source-pack manifest hash was
`a4f569af5ed74e041b8d40a367e8eb33c878f3d8e656b6240c434a0887b00ad3`.
The complete result is
[quantity-aggregation-text-planner-v2-results.json](quantity-aggregation-text-planner-v2-results.json).
The rejected v1 registration and result remain beside it. The copied pack and
run log remain under
`data/benchmarks/quantity-aggregation-text-planner-v2/`.

This is deterministic product-path evidence on one authored frozen pack. It
does not establish held-out paraphrase coverage, general semantic parsing,
qualifier understanding, automatic unit conversion, answer prose quality, or
competitive leadership. The planner deliberately supports only its documented
fixed shapes; applications should use the structured aggregation API for
anything more specific.
