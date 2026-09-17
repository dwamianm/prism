# MemoryArena travel development V12

## Outcome

The first canonical-value context contract passed the aggregate development
gate, but inspection rejected it before confirmation because the instruction
also changed tool-call behavior and did not improve the targeted qualifier class.

| Arm | Strict PS | Strict SPS | Strict SR | Passed slots | Input tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native full history | 7.6923 | 79.5806 | 0.0000 | 346 / 436 | 1,678,531 |
| PRME | 21.7949 | 78.3181 | 0.0000 | 341 / 436 | 1,370,527 |
| PRME minus native | +14.1026 | -1.2625 | 0.0000 | -5 | -18.35% |

All 156 traveler-arm executions completed with zero agent failures. The result
passed the registered -5-point PS and SPS floors and improved the aggregate
margin over V11. Because the hosted alias is mutable and each arm has one
generation, that movement cannot be assigned to the new instruction alone.

## Targeted inspection

The new contract described confirmed values as canonical and required verbatim
spelling, punctuation, spacing and parenthesized qualifiers. On development
slots whose changed reference value contained a parenthesized qualifier, V12
PRME passed 235 of 318 versus 239 of 318 in V11. This cohort did not reproduce
the confirmation's group-56 suffix collapse, and V12 did not show a general
qualifier gain.

The traces also show the model applying the contract to tool arguments. It first
queried values such as `Bemidji(Minnesota)` and `State College(Pennsylvania)`,
which the pinned tools do not accept, then retried the shorter tool form. The
intended policy concerns final-answer fidelity; changing tool calls adds cost and
can consume the bounded step budget.

## Decision

Do not spend a fresh confirmation cohort on V12. Refine the contract so tool
arguments use each tool's accepted form while the final answer restores exact
canonical values from confirmed records. The aggregate pass is retained as
evidence, but it is insufficient to validate the intended mechanism.

## Evidence identity

- Registration SHA-256:
  `228c89e7ecd7e85ffcb83191cd63b4762c229cd07d28062bdcf0a9237ca0d9b6`
- Result file SHA-256:
  `05e6c0d2dc594a3da9f719185907cd3905d08e680f42f34162562df6c1255b97`
- Canonical result SHA-256:
  `803b4baa386a9d0f7f618c53b04e9a11a80a9b0b882355c46c5a81fad8d0952d`
- Registered PRME revision:
  `9aa48e169fb37926f78c3c858bdefe5ec6d3b221`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`

## Limits

This is the repeatedly examined development cohort. The remote model weights are
not pinned, exact-string scoring is narrower than itinerary validity, and a
single generation per arm does not estimate model variance. V12 supports neither
a causal prompt claim nor a product-quality claim.
