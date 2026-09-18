# MemoryArena travel development V15

## Outcome

Exact tool-boundary resolution passed its causal mechanism check and the paired
run passed both registered broad non-inferiority gates. It did not retain the
previous candidate's final-answer fidelity gain, so no fresh confirmation
cohort is authorized yet.

| Arm | Strict PS | Strict SPS | Strict SR | Passed slots | Input tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native full history | 6.4103 | 83.1213 | 0.0000 | 362 / 436 | 1,711,521 |
| PRME tool-boundary resolution | 17.9487 | 79.1946 | 0.0000 | 342 / 436 | 1,281,083 |
| PRME minus native | +11.5385 | -3.9267 | 0.0000 | -20 | -25.15% |

All 156 registered traveler-arm executions completed with zero agent failures.
The strict PS and SPS deltas both passed their preregistered -5-point floors.
The hosted alias produced a materially different native score from V14, which
reinforces the registered warning that one generation does not estimate model
variance.

## Tool-boundary mechanism

The deterministic checkpoint audit matched all 620 PRME tool executions to the
model-requested call saved in its trace. It found 88 exact source-backed
presentation-to-lookup substitutions across 10 travelers. Every declared
substitution exactly matched the observed argument mutation, and zero qualified
arguments reached the underlying tools.

| Measure | V15 result |
| --- | ---: |
| Resolver records matched to trace | 620 / 620 |
| Exact replacements | 88 |
| Travelers with replacements | 10 |
| Qualified arguments after resolution | **0** |

This is direct evidence that the typed representation solves the operational
failure it targets. The model saw the ordinary confirmed-plan context; lookup
values and binding instructions were absent from that context.

## Final-answer fidelity

The same complete audit retained the existing diagnostic over 318 changed
reference slots containing parenthesized qualifiers.

| Version | Exact qualified values | Model-requested qualified calls | Executed qualified arguments |
| --- | ---: | ---: | ---: |
| V11 pre-change | 239 / 318 | 55 | 55 |
| V12 canonical prompt | 235 / 318 | 161 | 161 |
| V13 final-answer prompt | 236 / 318 | 75 | 75 |
| V14 typed binding context | **244 / 318** | 0 | 0 |
| V15 tool-boundary resolution | 234 / 318 | 84 | **0** |

V15 prevents malformed execution without teaching the model to preserve the
canonical presentation in its answer. It therefore misses the V14 decision's
requirement to retain a targeted final-answer gain before selecting a fresh
confirmation cohort. The 318-slot diagnostic covers meals and accommodations,
not only the city values resolved at the tool boundary, so it is evidence about
answer fidelity rather than a direct resolver correctness check.

## Decision

Keep the generic typed-value API as a product candidate: it is opt-in,
source-bound, context-visible, ambiguity rejecting, fully audited, and now has
real workflow evidence across 88 substitutions. Do not treat the V15
MemoryArena adapter as a confirmed quality improvement and do not spend an
untouched cohort yet.

The next development candidate should retain the exact resolver and attach the
canonical presentation form only to the result of a tool call that used it.
That keeps lookup data out of general model context while giving the model the
specific output form after successful execution. It must execute zero qualified
arguments, preserve at least V14's 244/318 exact qualified values, and pass both
broad gates before confirmation.

## Evidence identity

- Registration SHA-256:
  `0d89b68e26133e2e1f5af55f9329b042a1726522c1bd79f59d4370e95c4852b3`
- Paired result SHA-256:
  `0ad6dce231ae3325c8c6b1582c65a88ce0ec0aef578fddc914d89b427b2712c6`
- Targeted audit SHA-256:
  `7d05611b3da18e0f73c602fa9e5b25a7480d5892c5b7d3a6b27828f34450d13e`
- Person checkpoints SHA-256:
  `3990b2c6f1aab31528baadf94adbb56e1808874944cd7c968d126afc38edb576`
- Registered PRME revision:
  `8242a63e6bc03b6911c49add3363f4c141e27c1f`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`
- Actor: `deepseek-v4.1-flash:cloud` through Ollama native chat, temperature
  zero, seed 17, thinking disabled, 8,192 output-token cap

## Limits

This repeatedly examined development cohort supports mechanism diagnosis, not
universal quality claims. The remote model weights are not pinned, exact-string
scoring is narrower than itinerary validity, and a single generation per arm
does not estimate variance. The stored replacement audit proves which exact
arguments changed; it does not prove the caller-supplied lookup value is
semantically correct for every external tool.
