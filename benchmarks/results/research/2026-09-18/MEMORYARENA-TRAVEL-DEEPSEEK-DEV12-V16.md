# MemoryArena travel development V16

## Outcome

Call-local typed presentation metadata passed every preregistered development
selection rule. The candidate preserved 252 of 318 exact qualified reference
values, sent zero qualified arguments to tools, completed its audit, and passed
both broad non-inferiority gates. It advances to a fresh disjoint confirmation
cohort.

| Arm | Strict PS | Strict SPS | Strict SR | Passed slots | Input tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native full history | 11.5385 | 78.6919 | 0.0000 | 343 / 436 | 1,697,034 |
| PRME call-local typed values | 10.2564 | 80.0405 | 0.0000 | 350 / 436 | 1,394,078 |
| PRME minus native | -1.2821 | +1.3486 | 0.0000 | +7 | -17.85% |

All 156 registered traveler-arm executions completed with zero agent failures.
The strict PS and SPS deltas passed their registered -5-point floors.

## Targeted mechanism audit

The deterministic audit matched all 626 PRME tool executions to their saved
model request and model-visible result. The resolver observed 462 exact binding
uses: 70 qualified presentation values were replaced before execution and 392
arguments already used their unambiguous lookup form. Those uses produced 444
call-local result annotations across 47 travelers. Every annotation contained
only the exact source-backed presentation form used by that call; lookup values
remained outside general model context.

| Measure | V16 result | Registered rule |
| --- | ---: | ---: |
| Exact qualified outputs | **252 / 318** | at least 244 |
| Executed qualified arguments | **0** | at most 0 |
| Tool traces matched | **626 / 626** | complete |
| Binding uses | 462 | diagnostic |
| Guided tool results | 444 | diagnostic |

| Version | Exact qualified outputs | Model-requested qualified calls | Executed qualified arguments |
| --- | ---: | ---: | ---: |
| V11 pre-change | 239 / 318 | 55 | 55 |
| V12 canonical prompt | 235 / 318 | 161 | 161 |
| V13 final-answer prompt | 236 / 318 | 75 | 75 |
| V14 typed binding context | 244 / 318 | 0 | 0 |
| V15 execution-only resolution | 234 / 318 | 84 | 0 |
| V16 call-local presentation metadata | **252 / 318** | 67 | **0** |

V16 improves the targeted output measure by 13 values over V11 and eight over
V14 while preserving safe execution. Unlike V14, it does not place every
presentation/lookup pair in the retrieved prompt. Unlike V15, it supplies the
canonical presentation form after the corresponding tool call whether the
model requested the presentation or already used the lookup value.

## Decision

Advance this exact candidate to one fresh confirmation cohort that excludes
every group present in prior MemoryArena travel registrations. Confirmation
must retain complete execution, zero executed qualified arguments, and both
broad non-inferiority gates. The 244/318 development threshold is tied to this
development cohort and cannot be copied numerically to a different cohort;
confirmation must instead report its full qualified-value denominator and a
paired no-annotation comparator or an equivalent causal check before claiming
the output gain reproduced.

The generic typed-value API remains opt-in. `binding_uses` reports both replaced
and already-correct lookup values with provenance, so applications can render
call-local presentation metadata without exposing unrelated bindings. PRME does
not automatically modify arbitrary tool results or generated answers.

## Evidence identity

- Registration SHA-256:
  `57d7646cb99b180f983c0bbbe00aae181f23582c4c643c68f233559a0e0f46a2`
- Paired result SHA-256:
  `8d0d5d109cf08eb6d0f987dafbc7dfe22b9657fdd3335dd603ceabc1b15b8a15`
- Targeted audit SHA-256:
  `5381a5f157df79c5edffca429b5449834e2277e70ad7937a4ca0a0f18f17e9bd`
- Person checkpoints SHA-256:
  `a28604b4d5c23a4649ab108d4f3c598ec4e979c6d0a00b872d48f61c5796262c`
- Registered PRME revision:
  `50f5cf6726568b70e47f6f9cd0d73826d6fd0195`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`
- Actor: `deepseek-v4.1-flash:cloud` through Ollama native chat, temperature
  zero, seed 17, thinking disabled, 8,192 output-token cap

## Limits

This repeatedly examined development cohort supports candidate selection, not
universal quality claims. The remote model weights are not pinned, exact-string
scoring is narrower than itinerary validity, and one generation per arm does
not estimate variance. Native strict PS varied materially across V14, V15 and
V16, so the fresh confirmation remains necessary. The replacement and binding-
use audit proves exact argument handling and metadata delivery; it does not
prove caller-supplied lookup values are semantically correct for every tool.
