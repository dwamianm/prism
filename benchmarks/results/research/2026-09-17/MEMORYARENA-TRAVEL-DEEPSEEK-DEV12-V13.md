# MemoryArena travel development V13

## Outcome

The final-answer-only value-fidelity contract passed the aggregate development
gate, but targeted inspection rejected it before confirmation. It reduced the
tool-call regression introduced by V12 without improving the exact-value failure
that motivated the change.

| Arm | Strict PS | Strict SPS | Strict SR | Passed slots | Input tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native full history | 14.1026 | 80.5169 | 0.0000 | 347 / 436 | 1,778,183 |
| PRME | 20.5128 | 78.8985 | 8.3333 | 343 / 436 | 1,443,232 |
| PRME minus native | +6.4103 | -1.6183 | +8.3333 | -4 | -18.83% |

All 156 traveler-arm executions completed with zero agent failures and complete
registered coverage. The result passed the registered -5-point PS and SPS
floors. The hosted model alias is mutable and each arm has one generation, so
the aggregate movement from earlier runs cannot be assigned to the prompt
change.

## Targeted inspection

V13 told the model to use each tool's accepted argument form and limited
canonical spelling requirements to the final answer. On development slots whose
changed reference value contained a parenthesized qualifier, V13 PRME passed
236 of 318. The pre-change V11 policy passed 239 of 318 and V12 passed 235 of
318. V13 therefore did not validate a general qualifier-fidelity improvement.

The narrower contract reduced qualified tool calls from V12's 161 calls across
21 travelers to 75 calls across 11 travelers. That still exceeds V11's 55 calls
across seven travelers. Traces show the model first querying values such as
`State College(Pennsylvania)` and `New Orleans(Louisiana)`, receiving no results,
and retrying the unqualified tool form. The prompt distinction was not a reliable
execution boundary.

## Decision

Do not spend a fresh confirmation cohort on V13. Retain the complete result as a
negative mechanism test. A later candidate should represent canonical output
values and tool lookup values as separate typed data, or enforce the distinction
at the tool boundary, then demonstrate a targeted development gain before a new
confirmation registration. A benchmark-specific rewrite of final plans would
not establish a memory-system improvement.

## Evidence identity

- Registration SHA-256:
  `f728f5417d876136c64d55297251f0b1d7c565555b6048922e36e5408af30576`
- Result file SHA-256:
  `f16870d6444207cded71dbb18ba4f4d03c5306e07c5e4b9c9e5910500e74e926`
- Canonical result SHA-256:
  `726690b3ab78db9ef7a16541bb0cff1cfd70936f19eef9b1cab773bdb65222e0`
- Registered PRME revision:
  `30bf133f9abf0f4b09f3e3471fbe149a63b37417`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`

## Limits

This is the repeatedly examined development cohort. The remote model weights are
not pinned, exact-string scoring is narrower than itinerary validity, and a
single generation per arm does not estimate model variance. Passing the broad
gate does not override the failed targeted mechanism test.
