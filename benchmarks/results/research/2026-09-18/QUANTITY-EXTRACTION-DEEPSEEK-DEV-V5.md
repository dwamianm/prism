# Grounded quantity extraction development v5

**Decision:** Pass the registered development gate and advance v10 to the
preregistered Qwen confirmation.

**Date:** 2026-09-18

**Registration SHA-256:**
`5ebb5fb53dd648aa8a447bb59773f7bb7aa1c771be6391c0486d7510f2198cb4`

## Question

Does evidence-and-quantity-aware duplicate suppression retain all v9 coverage
while eliminating its duplicate conditional claim?

## Protocol

The preregistered assay reused the same 20 authored cases, twelve exact targets
and zero-tolerance gates. DeepSeek 4.1 Flash ran through Ollama with its alias
and digest frozen before calls. Every case used a fresh engine and checkpointed
state. Fresh outputs recorded `speech_act_v10` and prepared v12 plans.

V10 retains the source-derived decimal, dimensionless attribute, bounded
grounded-object quantity and conditional-action recovery from v7-v9. It
suppresses conditional fallback when a validated fact already has the same
evidence, predicate, polarity and quantity identity, even when its valid
condition is a different source substring.

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

The conditional case materialized exactly one quantified fact. All exact
currency, count, decimal, signed, compact-unit, negated and conditional targets
survived. Approximation, range, version, date, identifier, time, model-name,
address and ordinal controls remained quantity-free.

## Verification and artifacts

An independent pass verified the result self-hash, registration and state
hashes, all checkpoints, the successful exit, all metric totals, all 20 pass
flags, twelve total quantities, the v10 policy and exactly one conditional
quantity fact. The result identity is
`3f82da42a287f6f6235df0e42c93908b8bd46a130509128d859c3a8553f870fb`.
The complete summary is
[quantity-extraction-deepseek-dev-v5-results.json](quantity-extraction-deepseek-dev-v5-results.json).
Raw state and logs remain under
`data/benchmarks/quantity-extraction-deepseek-dev-v5/`.

This is authored development evidence. The preregistered distinct-model
confirmation remains required; this result is not held-out accuracy, answer
quality, complete real-world coverage or competitive evidence.
