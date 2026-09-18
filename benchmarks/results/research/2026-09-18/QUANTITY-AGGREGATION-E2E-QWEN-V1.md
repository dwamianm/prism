# Grounded quantity aggregation end-to-end Qwen v1

**Decision:** Reject the registered explicit-query product path and preserve the
failure as the baseline for the next candidate.

**Date:** 2026-09-18

**Registration SHA-256:**
`8bc35bb7ac95f9d8a7565e0c2ad845db53cb9af88db69b5351d76b42b3404c72`

## Question

Can one durable PRME pack ingest four real LongMemEval charity sources and six
safety controls, then return the exact `$3,750` total through the public
`aggregate_quantities` API without admitting unrelated, negated, conditional,
approximate, incompatible-unit, or cross-owner values?

## Protocol

The preregistered assay used the local `prme-qwen3.5:35b-a3b-8k` artifact and
fresh `speech_act_v10` extraction. Ten sources were ingested synchronously into
one durable pack, with state checkpointed before and after every source. The
aggregation query selected `$` quantities and an explicit exact list of common
`raised` and `helped_raise` predicates. All model, prompt, implementation,
source, query, and expected-result identities were frozen before provider calls.

## Result

The run completed all ten cases with no provider failure, but failed the product
gate.

| Metric | Registered | Actual |
| --- | ---: | ---: |
| Completed sources | 10 | 10 |
| Source contracts passing | 10 | 9 |
| Charity contributions selected | 4 | 3 |
| Exact total | $3,750 | $3,500 |
| Minimum contribution | $250 | $500 |
| Cross-owner contribution | excluded | excluded |

The `$250` claim was extracted and stored correctly. Qwen named its predicate
`raised_amount_for_beneficiary`, which was not in the preregistered normalized
exact selector list, so the complete stored-set scan correctly classified it as
a selector mismatch. This is evidence of a developer-experience gap between
open-vocabulary extraction and exact structured aggregation, rather than an
arithmetic or storage failure.

The same complex source omitted the independent `5 kilometers` quantity after
its running claim failed grounding. That omission failed one source contract but
did not cause the charity total error. It is separate evidence that the bounded
quantity recovery added for existing grounded facts does not yet recover every
dropped measured action in a multi-claim source.

All substantive exclusion controls behaved safely: unrelated spending and the
negated amount were selector mismatches; the conditional amount was filtered by
its unresolved condition; the approximate amount remained quantity-free; `100
USD` was kept in a separate unit; and the other owner's `$10,000` was never
scanned. The registration expected the generic exclusion name
`epistemic_filtered`, while this path correctly reported the more specific
stable name `condition_filtered`. That protocol error accounts for one failed
check and does not change the missing `$250` diagnosis.

## Verification and next experiment

An independent pass verified the result self-hash, registration hash, state
hash, ten completed checkpoints, zero failed attempts, extracted predicates,
exact samples, total, exclusion counts, and complete-state marker. The result
identity is
`20fe398a7cf2bbbb8c3bfd36200090b18e1d13f93811ff639240ef8326cd6b5e`.
The complete result is
[quantity-aggregation-e2e-qwen-v1-results.json](quantity-aggregation-e2e-qwen-v1-results.json).
Raw state, pack, runner and log remain under
`data/benchmarks/quantity-aggregation-e2e-qwen-v1/`.

The next candidate should address the two localized gaps independently: recover
the dropped exact measured action under the existing source-grounding rules, and
test an auditable way to compose open-vocabulary predicates without weakening
polarity, epistemic, unit, or owner filters. The completed v1 result must not be
reinterpreted after those changes.
