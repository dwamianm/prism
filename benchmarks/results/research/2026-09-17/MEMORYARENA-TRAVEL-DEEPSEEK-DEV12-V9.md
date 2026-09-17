# MemoryArena travel development trial V9

## Outcome

The registered paired run completed all 156 traveler executions with zero agent
failures. Exact prior-traveler routing and typed confirmed-plan rendering lifted
PRME's strict constraint score by 35.0645 points over V7, but the run did not
pass the registered non-inferiority gate.

| Arm | Strict PS | Strict SPS | Strict SR | Input tokens | Agent failures |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native full history | 16.6667 | 86.7992 | 0.0000 | 1,683,141 | 0 |
| PRME | 16.6667 | 70.9605 | 8.3333 | 1,245,166 | 0 |
| PRME minus native | 0.0000 | -15.8386 | 8.3333 | -437,975 | |

PRME matched native's 13 of 78 strict person passes and was the only arm to
complete an entire group. It used 26.02% fewer input tokens. Constraint-level
accuracy remained materially lower: PRME passed 300 of 436 slots and native
passed 370.

## Residual analysis

Four PRME travelers produced complete named plans whose exact marker immediately
followed another token, usually `</think>` or `Final plan:`. The registered
extractor required the marker to start a line, so James (group 54), Adam (group
203), Chloe (group 210), and Harper (group 210) were checkpointed without a
plan. Seven later contexts consequently rendered one of these plans as
unavailable. This is a deterministic adapter defect rather than a retrieval or
model transport failure.

The next development run changes only that boundary rule: it selects the last
literal `=== <name>'s Plan ===` marker anywhere in the response. It still
requires the exact traveler name and marker and continues to discard preceding
reasoning. Regression tests cover a marker directly following `Final plan:`.
Because the four lost plans affect dependent travelers, a fresh paired run is
required; scores cannot be repaired after the fact.

Compared with the raw-trace V5 baseline, PRME strict SPS improved from 32.7156
to 70.9605. Compared with the generic projected V7 context it improved from
35.8960 to 70.9605. Native scores are not directly stable across these
single-generation cloud runs, which is why every registered comparison remains
paired.

## Evidence identity

- Registration SHA-256:
  `a7c4d56e2588d62ebfe87f26047ed4baed2cb74d3c39d3303a069c5582cfb4e2`
- Result SHA-256:
  `db7f0ae5acfe9f916bc73d8e9591c270eb084441929edb447246f0a754381fd1`
- Registered PRME source revision:
  `4c06ef84fed7166ee62580c8a409fd0838192e9f`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`
- Actor: `deepseek-v4.1-flash:cloud` through Ollama native chat, temperature
  zero, seed 17, thinking disabled

## Limits

This is the repeatedly examined 12-group development cohort. Remote cloud
weights are not pinned. Each arm used one generation, so the result does not
estimate model variance. Strict normalized-string scoring is deliberately
narrower than semantic itinerary validity. These results do not establish
universal superiority or product leadership.
