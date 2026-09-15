# MemoryAgentBench episode-routing regression trials

**Completed:** 2026-09-14  
**Systems:** PRME flat retrieval versus PRME with two-stage episode routing  
**Reader:** local Ollama `qwen3.5:9b`, temperature 0, seed 42, thinking disabled  
**Questions:** the first 20 registered Banking77 questions and first 20 registered DetectiveQA questions

## Result

Episode routing preserved the 20/20 Banking77 result and improved DetectiveQA
from 13/20 to 15/20. The Detective paired diagnostic had three gains, one loss
and 16 ties, an observed 10-point improvement. Its question-bootstrap 95%
interval was -10 to +30 points and its exact two-sided McNemar p-value was
0.625. The sample is too small to distinguish the observed gain from question
selection variance.

| Task | Flat PRME | Episode PRME | Paired change | Mean episode context |
|---|---:|---:|---:|---:|
| Banking77 | 20/20 (100%) | 20/20 (100%) | 0 wins, 0 losses | 3,969.70 tokens |
| DetectiveQA | 13/20 (65%) | 15/20 (75%) | 3 wins, 1 loss | 3,968.60 tokens |

Episode routing did not expand the packed context. Banking fell from 3,975.15
to 3,969.70 mean tokens and DetectiveQA fell from 3,979.70 to 3,968.60. Mean
query time was 5.10 seconds on Banking and 4.94 seconds on DetectiveQA. The arms
ran sequentially on one local host, so those timings are descriptive.

## Protocol and verification

Both episode arms used `episode_context_top_k=2`,
`episode_context_local_k=8`, and `episode_context_score_decay=0.95`. The adapter
retained upstream source chunks as separate session-scoped episodes only when
the feature was enabled. It stored 5,897 Banking records in 463.112 seconds and
1,620 DetectiveQA records across three packs in 143.557 seconds.

The Banking run evaluated PRME
`8676c9c9600bd0cd592bb186cea2d0ee2513f7cc`; the Detective run evaluated
`75344b796813b77c23b94d753bf07de307aaf697`. The latter revision differs only
by a benchmark-installer upgrade repair discovered before the Detective run
produced any score. Both revisions use the same episode-routing and retrieval
source hashes. Registrations bind the exact upstream and dataset revisions,
prepared source chunks, questions, answers, reader controls, adapter, and all
17 retrieval modules named by receipt execution metadata.

Post-run verification authenticated every result row, capture, durable receipt,
ranking replay, scope, context hash, token recount, episode setting, and runtime
source map. Receipt inspection found 220 `episode_decay` promotions on Banking
and 224 on DetectiveQA; all 444 promoted candidates were packed. The checked-in
verification artifacts contain aggregate metrics and cryptographic identities,
without questions, answers, contexts, or model outputs.

The first Detective attempt stopped before ingestion because the pinned checkout
contained both an older generated reader validator and the newer validator. The
old block rejected `choice-only-v1`. The installer now recognizes and replaces
the prior generated forms, adds managed markers, and remains idempotent on the
actual checkout. The failed attempt produced no scored data; Detective was
registered again against the repaired frozen revision before inference.

## Decision

These trials clear the named cross-task regression gate: the perfect Banking
slice stayed perfect, while both EventQA and DetectiveQA improved over their
earlier flat-session runs. They do not justify a global default from 60 inspected
questions and one reader. Episode routing remains opt-in while a larger frozen
cohort and another reader family test whether the gains persist and whether the
single Detective loss represents a systematic displacement risk.

The current evidence supports using episode routing when ingestion supplies
meaningful episode boundaries. It does not support manufacturing sessions for
unstructured streams or claiming general accuracy leadership.

Artifacts: [paired regression diagnostic](memoryagentbench-episode-regressions-dev20-comparison.json),
[Banking verification](memoryagentbench-banking-episode-dev20-prme-verification.json),
and [DetectiveQA verification](memoryagentbench-detective-episode-dev20-prme-verification.json).
