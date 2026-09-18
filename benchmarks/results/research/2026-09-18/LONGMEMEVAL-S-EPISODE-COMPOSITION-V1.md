# LongMemEval-S episode composition v1

**Decision:** Reject the registered grid.  
**Date:** 2026-09-18  
**Registration revision:** `18aa627cbfd6c9c907d8d18e84ce6edbe4a675b8`

## Question

Can PRME's existing deterministic episode router improve complete source
coverage on the frozen 500-question LongMemEval-S workload before adding new
representations, a reranker, or Zep-style retrieval scopes?

## Protocol

The runner and five candidate configurations were committed before evaluation.
Every arm reopened the same frozen packs, reproduced the current candidate IDs,
scores, order and auditable control context, and used the same 4,096-token
budget with the existing 100-token reserve. The router saw only the query and
already authorized candidate records. It did not receive question types,
answers, reference sessions or labeled turns.

Candidates routed one, two or three `(scope, session_id)` episodes with BM25,
then promoted either four or eight query-relevant records per episode at the
existing `0.95` inherited-score decay. A candidate could advance only with zero
per-question session or turn recall losses, zero category mean loss, and more
questions containing every labeled turn. Selection among passing candidates
was fixed in advance.

The registration is
[longmemeval-s-episode-composition-v1-registration.json](longmemeval-s-episode-composition-v1-registration.json).

## Result

No candidate passed. All 500 control contexts replayed and all 3,000 arm
contexts stayed within the exact budget, but every episode configuration lost
source coverage.

| Arm | Complete turns | Mean turn recall | Turn wins / losses | Complete sessions | Session wins / losses | Changed contexts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Control | 403 | 0.91465 | — | 437 | — | — |
| 1 episode × 4 records | 391 | 0.89706 | 6 / 30 | 424 | 1 / 16 | 495 |
| 2 episodes × 4 records | 348 | 0.82369 | 13 / 92 | 391 | 7 / 61 | 500 |
| 3 episodes × 4 records | 324 | 0.77908 | 13 / 118 | 376 | 7 / 74 | 500 |
| 2 episodes × 8 records | 283 | 0.73305 | 12 / 157 | 313 | 5 / 144 | 500 |
| 3 episodes × 8 records | 300 | 0.75635 | 10 / 141 | 341 | 3 / 113 | 500 |

The smallest candidate packed 1,981 episode-promoted records across the cohort
and reduced mean packed records from 23.93 to 19.46. Larger configurations
packed up to 6,031 promoted records and reduced mean record count to roughly
12. The promoted records gain an additional retrieval path, placing them in the
packer's multi-path priority. This spends the fixed budget on routed episode
members and displaces relevant evidence elsewhere. Raising the episode quota
therefore amplifies the loss.

The result rejects configuration tuning within this architecture. No reader or
judge calls were made, because the source gate failed first.

## Verification and artifacts

All 500 case checksums, the registration hash, the complete case manifest and
the final summary were independently recomputed. The self-checking result
identity is
`127be4d451b63602ff3ed54b7e4b736e0be8b143bd1946dc661731e2530b13a9`.
The summary artifact is
[longmemeval-s-episode-composition-v1-results.json](longmemeval-s-episode-composition-v1-results.json).
The 500 complete per-case artifacts remain under
`data/benchmarks/longmemeval-s-episode-composition-v1/`.

## Consequence

Keep episode routing opt-in. Its earlier gains on small MemoryAgentBench
development cohorts do not generalize to this workload under the current
multi-path packing priority.

The next packing design should assign marginal utility to evidence groups
inside the packer rather than converting every routed member into a priority
record. It should preserve ordinary relevance, token cost, mandatory objects,
conflicts and provenance, apply diminishing returns within a session, and
retain an escape path for multi-session evidence. The complete LongMemEval-S
cohort is already development evidence, so any selected policy still requires
answer-quality evaluation and an untouched confirmation elsewhere.
