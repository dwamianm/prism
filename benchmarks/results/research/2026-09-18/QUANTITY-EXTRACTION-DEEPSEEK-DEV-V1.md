# Grounded quantity extraction development v1

**Decision:** Reject the first candidate as complete. Retain its target-linking
improvement and fix the two deterministic validation gaps before a fresh trial.

**Date:** 2026-09-18

**Registration SHA-256:**
`db430fa87dee50a790edd16dcaacc098b07b194f8abb80a6bc272ad104fc6adb`

## Question

Can fresh PRME extraction retain exact measured and counted values with their
semantic targets while excluding numeric strings that must never enter exact
aggregation?

## Protocol

The preregistered authored development assay used 20 fixed sources and the
DeepSeek 4.1 Flash cloud model through Ollama. Twelve exact quantity targets
covered currency, named currency, counts, decimal and signed measures,
dimensionless counts, multiple independent measures, negation and explicit
conditions. Eight controls covered approximations, ranges, software versions,
dates, identifiers, times, model names, addresses and ordinals.

Every case used a fresh engine and one extraction. State was checkpointed after
each case. A pass required all 20 cases, zero failed calls, all 12 exact targets,
no extra quantities, exact target/object, polarity and epistemic fields, and the
current `speech_act_v6` grounding plus `speech_act_v12` materialization policies.

## Result

All 20 calls completed with zero failed attempts. Fifteen cases passed. Four
expected quantities were missing and one unsafe quantity was present, so the
candidate failed the registered gate.

| Failure | Observed mechanism |
| --- | --- |
| `1.5 liters` | The provider emitted a JSON decimal number; strict result validation discarded the quantity rather than admit a binary float. |
| `-3.25 volts` | Same exact-decimal transport failure. |
| `$12.50` | Same exact-decimal transport failure. |
| dimensionless score `3` | The model produced an unsupported subject/object claim, which source grounding discarded. |
| `about $500` | The model copied only `$500` into `source_text`; the validator checked that clipped phrase and missed the approximation cue in the surrounding evidence. |

The candidate did preserve target-linked integer quantities, including two
independent measures, a negated amount and a conditional amount. It emitted no
quantity for the range, software version, date, identifier, clock time, model
name, address or ordinal controls.

## Diagnosis

The original charity-total localization improved from two of four grounded
currency values to four of four after the prompt required numeric claims to be
facts whose objects retain the exact amount and target. That is useful causal
evidence, but it does not override this broader failed gate.

The remaining safety and utility gaps should be closed in deterministic code:

- detect approximation and range cues around the cited quantity in the full
  evidence passage, even when the provider clips them from `source_text`;
- when strict structured output contains a JSON float, ignore the float and
  reconstruct the exact decimal string from the source-grounded quantified
  phrase before ordinary validation. Do not convert the float itself.

The dimensionless score miss is a model extraction failure and should remain a
known coverage limit unless broader evidence supports a safe recovery rule.

## Verification and artifacts

An independent pass verified the result self-hash, registration and state
hashes, all 20 checkpointed cases, zero failed attempts, the recorded failing
exit, and metrics recomputed from the saved materialized nodes. The result
identity is
`48f778c312fdb36b5195f8bb832803bc128b645050b56ff0a586bad7ed9c3659`.
The summary artifact is
[quantity-extraction-deepseek-dev-v1-results.json](quantity-extraction-deepseek-dev-v1-results.json).
Complete outputs remain under
`data/benchmarks/quantity-extraction-deepseek-dev-v1/`.

This is an authored development assay with one mutable hosted model alias. It
does not establish held-out extraction accuracy, answer quality, complete
real-world coverage or competitive leadership.
