# MemoryArena travel development trial V7

## Outcome

The registered paired run completed all 156 traveler executions with zero agent
failures. The corrected `traveler_final_plan_v2` projection preserved the base
trip request, retrieved complete named traveler plans, and avoided the one-large-
trace packing failure measured in V5. It did not pass the registered
non-inferiority gate.

| Arm | Strict PS | Strict SPS | Strict SR | Agent failures |
| --- | ---: | ---: | ---: | ---: |
| Native full history | 17.9487 | 74.1786 | 0.0000 | 0 |
| PRME | 0.0000 | 35.8960 | 0.0000 | 0 |
| PRME minus native | -17.9487 | -38.2826 | 0.0000 | |

The PRME strict slot score improved from 32.7156 in the raw-trace V5 baseline
to 35.8960. This is a real but small gain relative to the remaining gap. The
native arm stayed effectively flat (74.4751 in V5 and 74.1786 in V7).

## Residual analysis

The V7 contexts contained a median of four complete traveler plans and retained
the named plan dependencies in the examined queries. The model visibly cited
the retrieved meals, lodging, prices, ratings, and house rules. The dominant
remaining failure is presentation at the agent boundary:

- PRME supplied generic auditable JSON records, while the native arm framed the
  base itinerary as a confirmed, fixed travel plan and previous itineraries as
  plans for the same group.
- The PRME response more often contained reasoning text with `Day N` fragments.
  The pinned upstream parser treated some of those fragments as itinerary rows.
  Twenty-six PRME plans and ten native plans consequently failed the strict
  complete-structure check.
- A symmetric diagnostic that parses only the final
  `=== Name's Plan ===` block raises PRME strict SPS from 35.8960 to 57.6989 and
  native strict SPS from 74.1786 to 84.9773. This diagnostic is explanatory; it
  is not the registered result and does not change the failed gate.
- Even after that cleanup, first-traveler constraint accuracy is materially
  lower for PRME. This points to context semantics and formatting rather than
  retrieval depth: the first traveler depends only on the fixed base plan.

The next development ablation should therefore keep exact source events and the
same candidate retrieval, but render selected travel records as typed confirmed
plans, include only the requested traveler dependencies, and validate the final
plan boundary before parsing. It must be registered as a new run. An untouched
confirmation cohort remains reserved until a development run passes the gate.

## Evidence identity

- Registration SHA-256:
  `40ae73b06c70c0d1388975cb4c186b36deb640f18e6a55a0317fc99e829b90f1`
- Result SHA-256:
  `51c16786b0c44002d25abb5abaec56079fd382d1b1dbee25827fa42b4a7de579`
- Registered PRME source revision:
  `77ac874a71f6f5c44f05deb06ba5743e8307bee1`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`
- Actor: `deepseek-v4.1-flash:cloud` through Ollama native chat, temperature
  zero, seed 17, thinking disabled

## Limits

This is the previously examined 12-group development cohort. Remote cloud
weights are not pinned. Each arm used one generation, so the result does not
estimate model variance. Strict normalized-string scoring is deliberately
narrower than semantic itinerary validity. These results do not establish
universal superiority or product leadership.
