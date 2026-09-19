# MemoryArena travel confirmation V2

## Outcome

The fresh three-arm confirmation rejected call-local typed presentation
guidance. The candidate preserved 352 of 457 exact qualified reference values,
while the matched PRME control without result guidance preserved 361. The
candidate therefore lost nine values against the preregistered requirement to
gain at least five. It also missed the strict SPS non-inferiority floor against
native full history.

All 234 registered traveler-arm executions completed with zero agent failures.
The cohort contained 12 groups and 78 travelers and excluded every group used
by prior registered MemoryArena travel experiments.

| Arm | Strict PS | Strict SPS | Strict SR | Passed slots | Input tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native full history | 6.4103 | 83.7835 | 0.0000 | 427 / 516 | 1,730,708 |
| PRME without result guidance | 3.8462 | 71.3227 | 0.0000 | 395 / 516 | 1,380,229 |
| PRME with call-local guidance | 3.8462 | 76.1570 | 0.0000 | 395 / 516 | 1,432,777 |
| Candidate minus native | -2.5641 | **-7.6264** | 0.0000 | -32 | -17.21% |
| Candidate minus control | 0.0000 | +4.8344 | 0.0000 | 0 | +3.81% |

The candidate passed the registered -5-point strict PS floor and failed the
-5-point strict SPS floor. The no-guidance control is diagnostic; it was not a
separate broad acceptance comparator.

## Causal targeted audit

The audit matched all 234 checkpoints and every registered traveler in both
PRME arms. The candidate's annotations did not reproduce the development
cohort's output-fidelity improvement.

| Measure | Candidate | No-guidance control | Registered rule |
| --- | ---: | ---: | ---: |
| Exact qualified outputs | 352 / 457 | **361 / 457** | candidate minus control at least +5 |
| Candidate minus control | **-9** | - | at least +5 |
| Model-requested qualified calls | 31 | 39 | diagnostic |
| Executed qualified arguments | **0** | **10** | zero in each PRME arm |
| Binding uses | 563 | 551 | diagnostic |
| Exact replacements | 31 | 31 | diagnostic |
| Guided tool results | 540 | 0 | diagnostic |

The candidate sent no qualified arguments to the underlying tools. The
otherwise identical control sent ten across one traveler, so the registered
control-safety gate also failed.

That failure localizes a coverage gap rather than an incorrect exact
replacement. The registered adapter created a binding only when an entire
`Current City` field was one qualified value, such as `San Antonio(Texas)`.
It did not decompose compound route fields such as `from Seattle to
Dallas(Texas)`. In group 267, traveler 5, the model requested qualified Dallas
and Houston values from those route fields. The resolver correctly refused to
guess an unregistered mapping and the underlying tools received ten qualified
arguments. Source-backed San Antonio values in the same trace were replaced.

## Decision

Reject automatic call-local presentation guidance as a selected MemoryArena
candidate. It does not become a default, and the V16 development gain cannot
support a product-quality claim.

Retain the generic typed-value and exact-resolution API as an opt-in execution
capability. Its resolver replaced only registered complete values, exposed
provenance for both replacements and already-correct lookup values, and did not
rewrite free text. The confirmation does not show that result annotations
improve generated answers, and it shows that applications need auditable
binding coverage before claiming safe execution.

The next typed-value experiment should first prove complete source-backed
coverage of atomic values embedded in compound fields without model calls.
Any presentation restoration should operate on declared structured output
slots under exact binding provenance. Repeating the same free-form result
instruction on another cohort is not justified by this result.

## Evidence identity

- Registration SHA-256:
  `e069e9121ca455409c118c571925a6582781e3cfeb5d49f6f1f4e0f679707940`
- Paired result SHA-256:
  `4e95374a4c6f83b9f7ed24d33e12afce8711c94a5ca2512e37ab6d566363f55b`
- Targeted audit file SHA-256:
  `211295b4e917c393479842d5d9babddd7e7fdf7e5f44c10245753f51a994158c`
- Person checkpoints SHA-256:
  `a30efe8c579c1dddd0a35476995a1fb004783abee2c0481fc0458eb3c0749888`
- Registered PRME revision:
  `fe68468968475e0a016cc860fe8c45fb7010782d`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`
- Actor: `deepseek-v4.1-flash:cloud` through Ollama native chat, temperature
  zero, seed 17, thinking disabled, 8,192 output-token cap

## Limits

The remote model weights are not pinned, exact-string scoring is narrower than
semantic itinerary validity, and one generation per arm does not estimate
variance. Cyclic arm order reduces but cannot eliminate hosted-service drift.
The safety failure is evidence about this registered adapter's incomplete
binding coverage; it does not mean the resolver transforms unsupported values
incorrectly. The experiment does not compare PRME with another memory product
or establish broad product leadership.
