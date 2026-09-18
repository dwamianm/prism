# Grounded quantity extraction development v2

**Decision:** Reject v2 as complete. It closed every deterministic safety and
decimal-transport failure from v1, but missed one dimensionless count because
the provider omitted the claim.

**Date:** 2026-09-18

**Registration SHA-256:**
`d0ac385b54852bea0852bb26201cf3e65313943ade40030aa88c28870397fe33`

## Question

Does source-text decimal recovery plus full-evidence approximation checking
close the five registered v1 failures without creating new unsafe quantities?

## Protocol

The preregistered assay reused the same 20 authored sources and zero-tolerance
gates as v1. It used the DeepSeek 4.1 Flash cloud model through Ollama, a fresh
engine for each source, and a checkpoint after every case. The registration
froze the revised prompt, schemas, grounding, pipeline, diagnostic code, model
alias and model digest before the provider calls.

Fresh extraction outputs recorded `speech_act_v7`; saved v6 output semantics
were not changed. V7 discards a provider JSON float and may reconstruct the
decimal only from one exact supported token in the grounded source phrase. It
also checks surrounding evidence for approximation and range cues.

## Result

All 20 calls completed with zero provider failures. Nineteen cases passed. The
run had no unexpected quantity, field mismatch or policy error. One of twelve
expected quantities was missing, so the registered all-or-nothing gate failed.

| Metric | Result |
| --- | ---: |
| Completed cases | 20 / 20 |
| Cases passing | 19 / 20 |
| Missing expected quantities | 1 |
| Unexpected quantities | 0 |
| Field mismatches | 0 |
| Policy errors | 0 |
| Failed provider attempts | 0 |

The three provider-emitted JSON decimals (`1.5 liters`, `-3.25 volts`, and
`$12.50`) survived with exact source-derived decimal strings. The clipped
`about $500` output was rejected from its surrounding evidence. The other
range, version, date, identifier, time, model-name, address and ordinal controls
also remained quantity-free.

The remaining failure was `My final score was 3.` The model returned no entity
or fact, only a summary. PRME therefore had no claim to which it could attach
the dimensionless quantity. This is now isolated from decimal parsing and
approximation safety. A follow-up may use a narrow deterministic recovery for
source-literal numeric attributes such as score/count, provided it retains the
ordinary entity, evidence and exactness checks.

## Verification and artifacts

An independent pass verified the result self-hash, registration and state
hashes, all 20 checkpointed cases, zero failed calls, the recorded failing exit,
the lone failed case and the recomputed metric totals. The result identity is
`1bfb8682572a06db6ed47c5604aeca8d44593a850e43d2c10fa535b36dda2bc0`.
The complete summary is
[quantity-extraction-deepseek-dev-v2-results.json](quantity-extraction-deepseek-dev-v2-results.json).
Raw state and logs remain under
`data/benchmarks/quantity-extraction-deepseek-dev-v2/`.

This is an authored development assay using one mutable hosted model alias. It
does not establish held-out extraction accuracy, answer quality, complete
real-world coverage or competitive leadership.
