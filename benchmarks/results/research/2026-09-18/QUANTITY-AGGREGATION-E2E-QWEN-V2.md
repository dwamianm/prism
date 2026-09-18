# Grounded quantity aggregation end-to-end Qwen v2

**Decision:** Accept the explicit quantity-aggregation product path tested here.

**Date:** 2026-09-18

**Registration SHA-256:**
`ab5193e1fd2f41aa3eaabaaf60f11dfb85cb846dcb8b18541af687dc93cd28c5`

## Question

Do the two fixes localized by the failed v1 run close its product gap without
weakening quantity, polarity, condition, unit, or owner safety boundaries?

## Protocol

The preregistered confirmation repeated the same ten natural-language sources
in a fresh durable pack with the same local
`prme-qwen3.5:35b-a3b-8k` model artifact. Four real LongMemEval charity memories
had to total exactly `$3,750`. Six controls covered unrelated spending, a
negated amount, an unresolved conditional amount, an approximate amount, an
incompatible `USD` unit, and another owner's `$10,000`.

Fresh extraction used `speech_act_v11`, whose new deterministic fallback can
recover one source-leading exact measure from a bounded first-person completed
action. The aggregation query used the new explicit token-prefix selectors
`raised` and `helped_raise`. All sources, query semantics, expected values,
implementation hashes, prompt/schema hashes, model digest, and zero-tolerance
gates were frozen before provider calls. State was checkpointed around every
source.

## Result

The candidate passed every registered gate.

| Metric | v1 | v2 |
| --- | ---: | ---: |
| Completed sources | 10 / 10 | 10 / 10 |
| Source contracts passing | 9 / 10 | 10 / 10 |
| Charity contributions selected | 3 / 4 | 4 / 4 |
| Exact total | $3,500 | $3,750 |
| Minimum contribution | $500 | $250 |
| Aggregation checks failing | 7 | 0 |
| Provider failures | 0 | 0 |

The previously dropped `5 kilometers` action was admitted as one source-cited
observed fact with the exact verbatim quantity. The model still emitted the
charity predicate `raised_amount_for_beneficiary`; the token-bounded `raised`
selector included it and the `$250` contribution. The aggregate returned four
complete samples—`$250`, `$500`, `$1,000`, and `$2,000`—with four distinct
evidence events, exact decimal total `3750`, minimum `250`, and maximum `2000`.

Every safety control remained out of the total. Spending and the negative claim
failed selectors, the conditional claim reported `condition_filtered`, the
approximate claim remained quantity-free, `100 USD` reported `unit_mismatch`,
and the other owner's memory was not scanned. The response explicitly reported
`semantic_equivalence="normalized_exact_and_predicate_prefix"`; it did not
claim exact-only matching or infer synonyms.

## Causal and integrity checks

An independent v1-to-v2 comparison found identical value, unit, predicate,
polarity, and epistemic tuples in all nine other sources. The changed source
kept the same `$250` model claim and added only the bounded `5 kilometers`
fallback. This isolates the source-contract repair from the aggregation
composition change.

The independent verification also checked the result self-hash, registration
hash, state hash, ten completed checkpoints, zero failed attempts, v11/v12
policy pair on every source, all aggregation checks, four samples and their
evidence counts, coverage fields, exclusion names, and final decision. The
result identity is
`5d163b0bcea36d4e1299f26b86253ea0843993926d2f3de5f7741222aef4db9d`.
The complete result is
[quantity-aggregation-e2e-qwen-v2-results.json](quantity-aggregation-e2e-qwen-v2-results.json).
Raw state, pack, runner and log remain under
`data/benchmarks/quantity-aggregation-e2e-qwen-v2/`.

This is authored product-path evidence with one local model artifact. It does
not establish held-out extraction accuracy, exhaustive verb/unit coverage,
automatic predicate discovery, answer quality, or competitive leadership.
Predicate prefixes are caller-selected lexical composition and remain subject
to false inclusion if an application chooses an overbroad action prefix. A
later, separate
[frozen-pack confirmation](QUANTITY-AGGREGATION-TEXT-PLANNER-V2.md) established
only the documented fixed-shape natural-language planner; it did not establish
general semantic parsing.
