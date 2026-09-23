# Reranker score-scale repair development study

Date: 2026-09-22. Branch: `research/reranker-score-repair-2026-09-22`.
Parent research branch: `research/opt-in-interactions-2026-09-22` at `5650161`.
Production stays at `a66ee85`; no merge, deployment or default change is authorized.

The complete current reranker arm fell from 437/500 to 284/500 answers under
the registered Ollama DeepSeek reader/judge. A code and artifact audit found
that the top-100 neural blends were compared with an unchanged-score tail by
session expansion and balanced packing. A unit regression reproduces a tail
record displacing the entire reranked prefix solely because of this scale
mismatch. This motivates a repair; it does not establish a repaired quality win.

The research-only `RankEnvelopeReranker` sorts the original prefix score values
and assigns them in the existing neural-blend ranking order. It retains the
raw neural output, original composite score and assignment lineage, and leaves
the tail's scores and lack of neural judgment intact. Equal assigned values use
canonical UUID ties. This is an ordinal ranking policy, not trained calibration
or a relevance probability. Ordinary session expansion and packing then run on
the resulting common scale. No public configuration field activates the class.

The only package changes in this isolated branch allow replay of an explicit
`neural_rank_assignment` operation, whose `coefficient` stores the assigned
ranking value. A preceding `neural_blend` retains the actual model output.
Receipts with assignments require schema 13 and an execution descriptor.
Ordinary execution still emits schema 12, and old receipt fields, canonical
bytes and checksums remain unchanged. No source claim or provenance is rewritten.

The complete source assay was registered to reproduce all 500 original baseline and
reranker contexts, returned IDs and scores from private copies of the verified
packs. The repair reuses each case's exact original neural inference for
identical query/document pairs; no annotation or answer enters ranking.
New receipts and complete raw-score/assignment traces are retained. Treatment
retrieval timings exclude a second inference and cannot be advertised as a
serving-speed improvement. Any replay, provider/backend, receipt or budget
failure invalidates the full assay; the original failed-quality arm remains.

If both complete-source count and mean source fraction improve over the broken
reranker, the study freezes all 500 repaired contexts and a new baseline-context
repeat before answer generation. Primary answer comparison uses that new control;
the original 437/500 baseline is never replaced. An improvement over the broken
reranker alone does not justify promotion. Both control and candidate must finish
and authenticate, every category/loss remains reported, and a production proposal
would still need an untouched cohort and release review.

The [scheduling amendment](opt-in-rank-envelope-scheduling-amendment-v2.json)
starts local source replay earlier using spare host capacity. Version 1 was
withdrawn before any source case or answer call; its idle waiter alone was
stopped. The [version 2 registration](opt-in-rank-envelope-v2-registration.json)
retains identical evaluation and source-summary functions, algorithm, models,
cohort, budgets and selection gates. Hosted answers still wait for the original
historical matrix to finish. Source and answer work never alter that matrix's
source hashes or its results. Fresh-ingestion arms and the queued
MemoryAgentBench stage continue in the parent worktree.

Validation: 97 tests passed with one expected backend-specific skip, and Ruff's
correctness checks passed. The source evaluator also passed an authored
end-to-end exact control replay and deliberately failed on changed context.

The [isolated installed-wheel validation](rank-envelope-installed-validation-v1.json)
also passed the same 97 tests with one expected skip, including live PostgreSQL.
All 163 installed Python files matched the frozen source files. The wheel was
installed in a separate target directory without modifying the benchmark
environment's dependencies. This closes the source-only packaging limitation
for these targeted checks; it is not a full release gate or a quality result.

## Complete source result and frozen answer follow-up

The [complete registered source assay](opt-in-rank-envelope-v2-source-result.json)
authenticated all 500 baseline and original-reranker replays and all 500 repair
contexts, without hosted answer calls. Complete verbatim annotated source sets
were retained in **403/470 baseline, 223/470 original reranker, and 407/470 repair**
cases. Mean source fractions were 0.914645, 0.566170 and 0.915248 respectively.
The original answer matrix's node-presence metric was 224/470 for the reranker;
a [one-case representation audit](opt-in-reranker-source-metric-discrepancy-v1.json)
explains the difference: one assistant node was serialized as an ID/type/confidence
fallback without its source text. Neither metric nor any answer score is replaced.

Against baseline, the repair gains eight complete source sets and loses four:
**+0.85 percentage points**, post hoc paired 95% interval **[−0.64, +2.34]**.
Against the broken reranker, it gains 187 and loses three: **+39.15 points**,
interval **[+34.68, +43.62]**. These [source intervals](opt-in-rank-envelope-source-intervals-v1.json)
use all 470 applicable cases and 10,000 seed-20260922 question-bootstrap draws;
they are unadjusted descriptive intervals, not answer-quality or confirmation
results. Baseline category changes in complete source retention are +4
multi-session, +2 knowledge-update, −2 assistant, and zero elsewhere. Source
fraction's baseline difference is only +0.000603, interval [−0.009328, +0.009362].
The repair changes 383/500 contexts, averaging 3,962.58 memory tokens.
Treatment retrieval reuses neural outputs and is not an uncached latency result.

Both source gates passed. The exact 500 baseline-repeat and 500 repair contexts
and [control](opt-in-rank-envelope-v2-reader-control-registration.json)/
[candidate](opt-in-rank-envelope-v2-reader-candidate-registration.json)
registrations are frozen before inference. Answer generation still waits for
all eleven original historical arms to settle. A registered, tested memory
handoff released the completed source process only while idle after preparation;
a low-memory coordinator reauthenticates every source case and prepared context
and executes the unchanged original answer/statistics code under the same gate.

The first ownership check refused a passive multiprocessing resource tracker;
its attempted coordinator then failed for lack of a handoff record, before any
benchmark call. Both logs remain. A prospective bookkeeping amendment permits
only that passive child type; the subsequent authenticated transfer stopped
no source or reader case and changed no model, cohort, score or retry rule.
This is recorded orchestration failure, not a replaced benchmark run.

The repair recovers a serious integration regression. Its small and uncertain
source advantage over production, including assistant-memory losses, still
requires the frozen matched answer trial before a quality claim.

A subsequent [released-lane scheduling amendment](opt-in-answer-lane-scheduling-v1.json)
now allows the frozen repair answers to start after the first historical lane's
six arms settle/authenticate and its coordinator exits, while the second lane
continues. It preserves the same provider-process ceiling and runs rank before
any eligible marginal follow-up, one four-slot reader process at a time. Six
ownership/gating tests passed. The previous waiting coordinator was transferred
only while idle after all 500 contexts were reauthenticated; no answer case
had started. This supersedes the original all-eleven-arm wait, with no change
to source gates, contexts, readers, scoring or failure handling.

## Completed candidate answers; failed primary control

The [candidate](opt-in-rank-envelope-v2-reader-candidate-result.json) completed
and authenticated all 500 answers: **428/500 (85.6%)**. The new control repeat
failed closed after 274 completed cases, one reader truncation and 225 unstarted
cases. The [primary comparison](opt-in-rank-envelope-v2-answer-result.json)
therefore has no answer metrics. Its failure is retained, not replaced by the
original control. The failed request is the same one that truncated in the
temporal arm; it had previously completed normally in the original baseline.
See the [provider ledger](opt-in-finalized-provider-failures-v1.json).

The prespecified secondary comparison against the original 437/500 baseline
has seven wins and 16 losses: **−1.8 points**, paired 95% interval **[−3.8,0.0]**.
Among 383 changed contexts, there were five wins/nine losses; among 117 exact
context repeats, two wins/seven losses. The original baseline stays fixed.
The candidate used 1,000 successful reader/judge calls without retries,
2,274,593 input tokens and 139,705 output tokens.

| Category | Candidate | Change from original baseline |
|---|---:|---:|
| Single-session user | 69/70 | 0 |
| Single-session assistant | 50/56 | −3 |
| Single-session preference | 27/30 | 0 |
| Multi-session | 97/133 | −3 |
| Temporal reasoning | 111/133 | −4 |
| Knowledge update | 74/78 | +1 |
| Abstention, overlapping | 26/30 | +1 |

The all-case [post-hoc mechanism audit](opt-in-rank-answer-diagnostics-v1.json)
finds three answer gains among eight complete-source gains, and three answer
losses among four complete-source losses. It also retains every other source
and answer transition. Compared with the complete broken reranker arm, the
repair has 153 wins/nine losses: +28.8 points [24.4,33.2], a post-hoc comparison.
This is substantial recovery of an integration regression, **not an improvement
over production**. The fixed primary trial failed, the secondary production
comparison is negative, and no untouched confirmation has occurred. Keep this
implementation research-only; do not enable reranking by default.
