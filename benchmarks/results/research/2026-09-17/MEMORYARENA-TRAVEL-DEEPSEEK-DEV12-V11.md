# MemoryArena travel development trial V11

## Outcome

The registered paired run completed all 156 traveler executions with zero agent
failures and zero missing plans. PRME passed the registered development
non-inferiority gate.

| Arm | Strict PS | Strict SPS | Strict SR | Input tokens | Agent failures |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native full history | 12.8205 | 83.8333 | 0.0000 | 1,680,401 | 0 |
| PRME | 19.2308 | 78.8962 | 0.0000 | 1,313,888 | 0 |
| PRME minus native | +6.4103 | -4.9371 | 0.0000 | -366,513 | |

PRME passed 15 of 78 complete traveler plans versus native's 10 and used
21.81% fewer input tokens. Native retained the constraint-level lead, passing
360 of 436 strict slots versus PRME's 343. The SPS margin cleared the registered
-5 point floor by only 0.0629 points, so this is fragile development evidence,
not a superiority result.

## Corrected failure paths

The exact marker appeared immediately after `</think>` in four PRME responses
and one native response. All five were retained and scored. Under V9's line-start
rule those plans would have been dropped, including Adam's group-203 plan and
its downstream dependencies. Every V9 parser-loss traveler that recurred in
V11 was durably saved with a valid plan.

V11 also capped each cloud response at 8,192 output tokens and registered one
source-retained, no-tools final-answer continuation for an explicit length stop.
No V11 response reached the cap, so the continuation path was not exercised.
It remains a tested fail-safe rather than a contributor to this score. V10's
repeated incomplete response is preserved separately and carries no quality
result.

PRME's strict SPS improved from 70.9605 in complete V9 to 78.8962, while native
moved from 86.7992 to 83.8333. Because the hosted model alias does not pin remote
weights and each arm used one generation, the paired V11 result cannot assign
that change solely to the marker correction. The observed five same-line markers
do establish that the correction prevented real plan loss in this run.

## Decision

The registered rules required complete execution, PRME minus native strict PS
of at least -5 points, and strict SPS of at least -5 points. V11 passed all
three. This unlocks a fresh stratified confirmation cohort that excludes every
development and preflight group. No further policy tuning is permitted from the
confirmation result.

## Evidence identity

- Registration SHA-256:
  `bcd8154f60c191234facefa892a37ad0be6f02b38ce2f4c4620f653f4ec45494`
- Result SHA-256:
  `5c7eeb9af101c413aab400631daea89d7da5ddd97a24dca9c96ac8dbfb94039e`
- Registered PRME source revision:
  `7548f39c39fd982635f1b2ec9ac124f5f6541996`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`
- Actor: `deepseek-v4.1-flash:cloud` through Ollama native chat, temperature
  zero, seed 17, thinking disabled, 8,192 output-token cap

## Limits

This is the repeatedly examined 12-group development cohort. Remote cloud
weights are not pinned. Each arm used one generation, so the result does not
estimate model variance. Strict normalized-string scoring is deliberately
narrower than semantic itinerary validity. The result does not establish
universal superiority or product leadership.
