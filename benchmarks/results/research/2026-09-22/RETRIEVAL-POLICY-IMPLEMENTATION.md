# Retrieval composition repairs from the registered study

Date: 2026-09-22. Branch: `fix/opt-in-retrieval-policies-2026-09-22`, isolated
from current `main` at `a66ee854325890c6bc28f6b515efeb6ed7df4deb` and from both
running benchmark worktrees. No default change, deployment, push or merge.
The user clarified that the 60%-remaining threshold means Codex usage allowance.
That meter is not exposed here; implementation began immediately after the
clarification, with no new study arms or research directions added afterward.
Already registered runs continue independently.

## What was implemented

1. `PRMEConfig.reranker_policy`: unchanged `legacy` default, plus explicit
   `score_envelope` and `anchored_score_envelope`. Both require the existing
   `enable_reranker=True` flag. The neural prefix now can retain its original
   score scale, preventing untouched tail scores from displacing it merely
   because neural blending shrank the prefix. The anchor variant prioritizes
   the strongest original ordinary multi-path record inside that prefix.
   Raw neural scores, score assignments, tail eligibility and UUID ties remain
   auditable; neither method claims calibrated probabilities.
2. `PRMEConfig.query_reformulation_merge_policy`: unchanged `new_only` default,
   plus explicit `max_signals` with `enable_query_reformulation=True`. Repeated
   candidate IDs can retain stronger alternate-query signals and distinct backend
   paths. Repeated queries do not create extra retrieval paths. Source snapshots
   must match exactly. No candidate-list mutation occurs before every alternate
   pass succeeds. Backend failures settle all passes and then propagate; ordinary
   provider empty-result fallback remains documented and separately observable.
3. Receipt schema 13 only when neural assignments occur. Versions 1–12 retain
   their original canonical representation and reject the new assignment.
   Both backends receive the configuration through normal engine construction;
   feature identity prevents a learned profile from silently crossing policies.
   Defaults and the independent temporal/Jev protocol remain unchanged.

These mechanisms are the previously registered research policies, now available
through the package configuration rather than a benchmark-only injection.
The signal-merge trial is still pending, so its implementation is experimental,
not an answer-quality recommendation. See [configuration and semantics](../../../../docs/EXPERIMENTAL-RETRIEVAL-POLICIES.md).

## Complete experimental evidence

All results below use the user-authorized Ollama `deepseek-v4.1-flash:cloud`,
pinned digest `e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8`,
official LongMemEval prompts/scoring, 3,996 effective memory tokens and the
registered 8,192 reader / 64 judge output limits. These are development data.
Original GPT access failures and earlier truncations are retained separately.

| Comparison | Answers | Paired difference, percentage points | Interpretation |
|---|---:|---:|---|
| Original production | 437/500 | Reference | Fixed registered control |
| Existing enabled reranker vs production | 284/500 | −30.6 [−35.0,−26.2] | Large score-composition defect and source displacement |
| Unconditional episode routing vs production | 345/500 | −18.4 [−22.2,−14.8] | Reject that unconditional default candidate |
| First envelope repair | 428/500 | Primary unavailable | Newly registered control failed closed; complete candidate cannot replace it |
| Anchor envelope vs its new control | 430/500 vs 429/500 | +0.2 [−1.6,+2.0] | 11 wins/10 losses; no established answer improvement |
| Marginal episode-bonus packing vs its new control | 433/500 vs 433/500 | 0.0 [−1.8,+1.8] | 10 wins/10 losses; reject as a default candidate |

The [ten-arm original analysis](opt-in-successor-v2-analysis-10-complete.json)
records exact configurations, artifact identities, categories, context usage,
latency, provider failures, omissions and paired intervals. It includes the
negative episode/reranker interaction arms and inactive evidence flags. The
[anchor primary](opt-in-anchored-rank-v1-answer-result.json),
[control](opt-in-anchored-rank-v1-reader-control-result.json) and
[candidate](opt-in-anchored-rank-v1-reader-candidate-result.json) bind its exact
new executions. The [first repair primary](opt-in-rank-envelope-v2-answer-result.json) remains invalid because its [new control failed](opt-in-rank-envelope-v2-reader-control-result.json). The [marginal primary](opt-in-marginal-packing-v2-answer-result.json) retains its complete tie.

The anchor source trial improved complete annotated source sets from 403/470 to
409/470: seven gains/one loss, +1.277 points [+0.213,+2.553]. Its mean source
fraction increased from 0.914645 to 0.922057. The
[source-paired artifact](opt-in-anchored-source-paired-v1.json) and
[source result](opt-in-anchored-rank-v1-source-result.json) retain checksums.
Source replay used cached neural judgments and reexecuted local query search;
its 2.144-second p50 / 13.186-second p95 are shared-host cached replay timings,
not uncached serving latency. Candidate contexts averaged 3,963.246 tokens
(p50 3,971; p95 3,996). It performed no new ingestion; the authenticated shared
[historical ingestion ledger](opt-in-shared-historical-ingestion-cost-v1.json)
contains 246,738 stored turns and 73,944.443 summed question-wall seconds. That
cost is counted once, not charged anew to each retrieval-only arm.

Each anchor answer arm completed 1,000 successful reader/judge requests with
1,000 attempts and no provider error or retry. Control used 2,273,010 input /
136,923 output tokens; candidate 2,271,603 / 136,268. Dollar cost is unobserved.
Category changes were +1 user, +1 update, −1 temporal, others tied; abstention
tied 24/30. No answer or category was relabeled.

The [complete error audit](opt-in-anchored-answer-audit-v1.json) authenticated
all 500 source cases and both reader/judge executions. Changed contexts had
eight wins/seven losses; identical contexts had three wins/three losses.
All [ten losses](ANCHORED-RANK-LOSS-REVIEW.md) are reviewed. Several ignored
retained evidence, one substituted an unsupported premise, and one passing
control supplied knowledge absent from its packed sources. The seven source
completeness gains yielded three answer wins; the sole completeness loss was
wrong in both new arms. Better source retention did not establish better answers.

Five [complete identical-input controls](opt-in-complete-control-repeats-v2.json)
scored 437, 435, 431, 433 and 429. There were 415 always-pass, 57 always-fail
and 28 variable questions. These repeats diagnose variation; none replaces a
registered primary control. The label-assisted 473/500 diagnostic is explicitly
nondeployable and outside the feature matrix, not a claimed product improvement.

## Validation

Source-tree authored checks: **39 passed**, including normal DuckDB and live
PostgreSQL engine configuration, scope/owner isolation, restart, feedback,
receipt replay, profile incompatibility, atomic failed merging and settling
alternate passes before error propagation.

Relevant existing regression checks: **182 passed, one expected skip**.
The skip is a local process-exit recovery case on PostgreSQL; live PostgreSQL
policy, receipt and profile cases ran. Coverage includes legacy reranking,
reformulation, receipt compatibility, balanced packing, learning gates, runtime
ranking and configuration. Logs:
[authored](implementation-authored-validation-v1.txt) and
[regressions](implementation-regression-validation-v1.txt).

The isolated installed-wheel run passed **221 tests with the same one expected
skip**; these repeat the same 221 source checks, not 442 distinct tests. It reports
one pytest plugin assertion-rewrite warning. See
[installed validation](implementation-installed-validation-v1.txt).
[Wheel identity](implementation-wheel-identity-v1.json) confirms all 163 packaged
Python files match the reviewed source and records its SHA-256. The initial
`python -m build` attempt failed because that frontend was absent; the project's
`uv build` succeeded. The first import-path probe rejected macOS's `/tmp` alias;
resolving both expected and actual paths corrected that check without a package
change. Both events are retained and are not benchmark answer failures.
Ruff F checks and `git diff --check` passed. This targeted evidence is not a
claim that the entire release suite ran.

## Recommendation and unfinished work

Keep the implemented policies opt-in. No studied combination currently earns
promotion to a production default. This conclusion follows the completed
negative/tied trials and uncertainty, not a claim that failed checks prove no
change is needed. The concrete score-scale and discarded-signal defects have
been implemented and tested on the isolated branch.

Continue the already registered fresh-ingestion control, supersedence, QA,
surprise and full-feature arms; score them only after complete authentication.
The signal-merge source gate remains active. MemoryAgentBench Banking, EventQA,
Conflict and Detective are queued behind the earlier stage; BEAM and MemoryArena
remain conditional. The failed temporal individual makes best-individual
selection unavailable under the fixed rule, not permission to select a successful
subset. Product alignment/Jev stays a separate explicit pair/review workflow.

To reach the product goal, the measured bottlenecks are answer-blind evidence
selection plus correct use of retained temporal/conflicting evidence. Preserve
source and semantic-failure diagnostics alongside answer scores. Any later
positive development candidate needs untouched confirmation and relevant release
checks before a separate explicit default-change review. No competitive leadership
claim is established by this study so far.
