# Grounded quantity extraction development v3

**Decision:** Pass the registered development gate and advance the candidate to
cross-model confirmation.

**Date:** 2026-09-18

**Registration SHA-256:**
`cd1621ccc0163dddcf2e831680a812f41d8704004bec687f5955609d689008cc`

## Question

Does the v8 extraction candidate retain every registered exact quantity while
excluding every unsupported numeric control after adding a narrow deterministic
recovery for model-omitted dimensionless attributes?

## Protocol

The preregistered assay used the same 20 authored sources and zero-tolerance
gates as v1 and v2. It used the DeepSeek 4.1 Flash cloud model through Ollama,
a fresh engine for each source, and a checkpoint after every case. The
registration froze the prompt, response schemas, grounding, pipeline,
diagnostic code, model alias and model digest before provider calls.

Fresh outputs recorded `speech_act_v8` and prepared `speech_act_v12` plans. V8
retains v7's source-derived decimal repair and surrounding approximation/range
checks. It also adds a deterministic recovery limited to complete user-authored
sentences whose subject is a literal score, count, rating or level and whose
object is one terminal exact dimensionless decimal. The recovered concept and
fact still pass normal evidence, quantity and closed-reference validation.

## Result

The candidate passed every registered gate.

| Metric | Result |
| --- | ---: |
| Completed cases | 20 / 20 |
| Cases passing | 20 / 20 |
| Expected quantities retained | 12 / 12 |
| Missing expected quantities | 0 |
| Unexpected quantities | 0 |
| Field mismatches | 0 |
| Policy errors | 0 |
| Failed provider attempts | 0 |

The dimensionless score materialized as a `final score` concept with object and
quantity source text `3`. The three provider-emitted JSON decimals survived as
exact source-derived strings. The approximation, range, version, date,
identifier, time, model-name, address and ordinal controls remained
quantity-free. Negated and conditional amounts preserved their registered
polarity and epistemic fields.

## Verification and artifacts

An independent pass verified the result self-hash, registration and state
hashes, all 20 checkpointed cases, the successful process exit, all metric
totals, all 20 per-case pass flags, all twelve materialized quantities and the
v8 extraction policy on the recovered dimensionless case. The result identity
is `f7049aed75ff29e1d588fba53c03f107bde41675aff9d9015dcbd69b4a91fabe`.
The complete summary is
[quantity-extraction-deepseek-dev-v3-results.json](quantity-extraction-deepseek-dev-v3-results.json).
Raw state and logs remain under
`data/benchmarks/quantity-extraction-deepseek-dev-v3/`.

This is an authored development assay using one mutable hosted model alias. A
distinct-model confirmation is required before accepting the candidate as
registered product evidence. It does not establish held-out extraction
accuracy, answer quality, complete real-world coverage or competitive
leadership.
