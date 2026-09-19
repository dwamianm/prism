# MemoryArena travel development V14

## Outcome

Typed presentation/lookup bindings fixed the targeted tool-boundary failure and
improved exact qualified-value retention, but the paired run missed its broad
complete-plan non-inferiority gate. The current MemoryArena integration is
therefore rejected before confirmation.

| Arm | Strict PS | Strict SPS | Strict SR | Passed slots | Input tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native full history | 19.2308 | 80.1451 | 0.0000 | 347 / 436 | 1,669,369 |
| PRME typed bindings | 14.1026 | 81.2467 | 0.0000 | 351 / 436 | 1,391,257 |
| PRME minus native | -5.1282 | +1.1016 | 0.0000 | +4 | -16.66% |

All 156 registered traveler-arm executions completed with zero agent failures.
The strict SPS gate passed, but strict PS fell 5.1282 points below native
history, just outside the preregistered -5-point floor. The overall gate failed.

## Targeted mechanism audit

The deterministic checkpoint audit covers every registered traveler and the
same 318 changed slots whose exact reference value contains a parenthesized
qualifier. It also inspects complete top-level string values in saved tool-call
arguments.

| Version | Exact qualified values | Qualified tool calls | Travelers affected |
| --- | ---: | ---: | ---: |
| V11 pre-change | 239 / 318 | 55 | 7 |
| V12 canonical prompt | 235 / 318 | 161 | 21 |
| V13 final-answer prompt | 236 / 318 | 75 | 11 |
| V14 typed bindings | **244 / 318** | **0** | **0** |

V14 gained five exact qualified values over V11 and eliminated the malformed
qualified tool calls observed in all three earlier versions. The integration
stored the source-backed presentation and lookup forms separately and exposed
both in context. It did not rewrite generated plans or tool calls. This is
positive evidence for the typed representation and execution-boundary design.

The aggregate failure still controls the product decision. The model route uses
a mutable hosted alias and one generation per arm, so the run cannot distinguish
a broad mechanism regression from generation variance. That uncertainty does
not authorize ignoring the registered gate.

## Decision

Do not spend a fresh confirmation cohort and do not merge the V14 MemoryArena
adapter behavior. Preserve the generic typed-binding implementation as an
unmerged candidate and test a narrower integration that applies exact lookup
resolution at the tool boundary without adding binding instructions to every
model context. A later candidate must retain the targeted gain, keep qualified
tool calls at or below V11, and pass both registered broad gates before using
fresh confirmation data.

## Evidence identity

- Registration SHA-256:
  `695fb2a310b62570585c679392bfb16ea026a97c0a7fcbc38d16d95ec62c0777`
- Paired result SHA-256:
  `8c2b4487413e3e078ab38ed57ca49f7bbc836b447e35e584006ca4b8232e08eb`
- Targeted audit SHA-256:
  `6d7f21ccbe3bb1e2eb7e7952bfad32dc4babbbd1b48baca10749861d9d2e66f1`
- Person checkpoints SHA-256:
  `ba70f52f6d42aae07bf31c9dd6c9df563bb20dab3c3595e324b202e30a115025`
- Registered PRME revision:
  `180d16bb36b2ea6975c98afaaa330f16d46482af`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`
- Actor: `deepseek-v4.1-flash:cloud` through Ollama native chat, temperature
  zero, seed 17, thinking disabled, 8,192 output-token cap

## Limits

This repeatedly examined development cohort supports mechanism diagnosis, not
universal quality claims. The remote model weights are not pinned, exact-string
scoring is narrower than itinerary validity, and a single generation per arm
does not estimate variance. Tool-call inspection covers complete top-level
string arguments in the saved traces.
