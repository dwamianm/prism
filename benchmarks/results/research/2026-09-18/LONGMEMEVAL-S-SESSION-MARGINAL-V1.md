# LongMemEval-S session-marginal packing v1

**Decision:** Reject the registered grid; retain the lossless mild arm for an answer-quality diagnostic.

**Date:** 2026-09-18

**Registration revision:** `bf0da237b295ad49b0ca8938085bf39272d9fc64`

## Question

Can a deterministic diminishing-return policy broaden session coverage without
the evidence displacement caused by PRME's episode-priority router?

## Protocol

The implementation, runner and seven candidate configurations were committed
before evaluation. Every arm reopened the same 500 frozen LongMemEval-S packs,
reproduced current candidate IDs, scores, order and control context, and used the
same 4,096-token budget with the existing 100-token reserve. The packer received
no question types, references, labeled sessions, labeled turns or answers.

Within each ordinary evidence tier, the policy groups candidates by exact
`(scope, session_id)`. The first configured number of successfully packed
records from a group retain their existing utility. Each later record multiplies
its utility by a fixed decay. Required records, instructions, pins, active tasks,
explicit episode/evidence routes, representation fidelity and exact whole-output
budget checks retain their existing behavior. The dynamic selection uses a heap
over session heads and is deterministic in `O(n log n)` time.

A candidate could pass the preregistered gate only with zero per-question turn
or session recall losses, zero category mean loss, and more questions containing
every labeled turn. The registration is
[longmemeval-s-session-marginal-v1-registration.json](longmemeval-s-session-marginal-v1-registration.json).

## Result

All 500 control contexts replayed, all 4,000 arm contexts stayed within budget,
and every case completed. No candidate met the complete-turn gate.

| Arm | Complete turns | Mean turn recall | Complete sessions | Mean session recall | Mean unique sessions | Mean max records/session | Turn wins / losses | Changed contexts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Control | 403 | 0.914645 | 437 | 0.962624 | 11.172 | 6.008 | — | — |
| Free 1, decay 0.98 | 388 | 0.889255 | 441 | 0.966950 | 14.862 | 4.774 | 2 / 24 | 500 |
| Free 2, decay 0.98 | 390 | 0.894752 | 439 | 0.964468 | 13.148 | 4.906 | 4 / 20 | 497 |
| Free 4, decay 0.98 | 397 | 0.906135 | 438 | 0.963688 | 11.866 | 5.402 | 3 / 7 | 408 |
| Free 2, decay 0.95 | 375 | 0.871028 | 441 | 0.966312 | 14.542 | 3.964 | 4 / 41 | 498 |
| Free 4, decay 0.95 | 392 | 0.897730 | 438 | 0.964043 | 12.344 | 4.876 | 5 / 16 | 419 |
| Free 4, decay 0.90 | 385 | 0.888511 | 438 | 0.964043 | 12.698 | 4.428 | 5 / 24 | 423 |
| Free 8, decay 0.90 | 403 | 0.915709 | 437 | 0.962624 | 11.322 | 5.840 | 1 / 0 | 31 |

The broad settings did what they were designed to do: they increased unique
sessions and produced six session-recall wins with no session-recall losses at
the strongest breadth setting. They nevertheless displaced labeled turns within
sessions. Session breadth by itself is therefore an insufficient packing
objective.

The mild `free 8 / decay 0.90` arm was different. It changed only 31 contexts,
had one turn-recall win and no turn, session or category losses. Mean turn recall
rose by 0.001064, entirely in multi-session questions. The win added one of two
required turns to question `8e91e7d9`, raising turn recall from 0 to 0.5; it did
not recover the second required session and therefore did not increase the
number of fully covered questions. The arm is a source-recall Pareto improvement,
but the preregistered gate required a complete-question increase, so the recorded
grid decision remains rejection.

This distinction matters. The strict gate prevents weak source movements from
changing product behavior; it does not make a measured lossless improvement
invalid. The mild arm warrants an answer-quality diagnostic, with its original
rejection and development-only status preserved. It does not yet warrant a
public setting, receipt schema change or default change.

## Verification and artifacts

An independent pass recomputed all 500 case checksums in registration order,
the registration hash, complete case manifest and result self-hash. The final
identity is
`4c9b781a9ab334e6370633cbee9c0c8d7cbc3ad7668f7fcaceca4368e019bfae`.
The summary artifact is
[longmemeval-s-session-marginal-v1-results.json](longmemeval-s-session-marginal-v1-results.json).
The complete per-case artifacts and resumable run log remain under
`data/benchmarks/longmemeval-s-session-marginal-v1/`.

## Consequence

Reject aggressive session-diversity packing and keep the implementation behind
a private benchmark hook. Run one frozen paired answer diagnostic for the mild
arm across the complete cohort. Its 31 changed contexts make the causal surface
small, while unchanged prompt bodies can be reused. Promotion still requires
noninferior answer quality, no category regression, a separate workload
confirmation and a receipt-compatible public design.
