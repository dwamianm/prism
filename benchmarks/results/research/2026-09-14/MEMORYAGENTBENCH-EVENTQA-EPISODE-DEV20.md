# MemoryAgentBench EventQA episode-routing development comparison

**Completed:** 2026-09-14  
**Systems:** PRME with two-stage episode routing versus the pinned MemoryAgentBench BM25 control  
**Reader:** local Ollama `qwen3.5:9b`, temperature 0, seed 42, thinking disabled  
**Questions:** first 20 preregistered EventQA accurate-retrieval questions  
**Scoring:** the benchmark's official `substring_exact_match`

## Result

PRME answered 19 of 20 questions correctly and BM25 answered all 20. There were
no PRME wins, one loss and 19 ties, a negative 5-point observed difference. The
question-bootstrap 95% interval is -15 to 0 points and the exact two-sided
McNemar p-value is 1.0. This small, previously inspected development slice does
not establish equivalence or a population-level accuracy difference.

| System | Exact | Retrieved context / question | Reader input / question |
|---|---:|---:|---:|
| PRME episode route | 19/20 (95%) | 3,974.95 tokens | 4,555.55 tokens |
| BM25 | 20/20 (100%) | 41,738.05 tokens | 41,968.00 tokens |

PRME used 90.48% fewer retrieved-context tokens and 89.15% fewer total reader
input tokens. Its memory context stayed below the declared 4,096-token budget
on every query. Mean end-to-end query time was 4.51 seconds for PRME and 63.22
seconds for BM25. The arms ran sequentially on one local host, so these timings
are descriptive rather than a controlled latency claim.

The earlier flat-session PRME arm on the same 20 questions and reader scored
16/20. The episode arm changed three of those failures to successes with no
losses, an observed +15 points; the question-bootstrap interval is 0 to +30
points and exact McNemar p is 0.25. That before/after diagnostic crosses PRME
revisions and query clocks and is not the registered PRME-versus-BM25 comparison.

## Technique and protocol

The PRME arm set `episode_context_top_k=2`, `episode_context_local_k=8`, and
`episode_context_score_decay=0.95`. Adapter schema 8 retained each of the 17
upstream source chunks as a separate session-scoped episode. PRME reconstructed
381 ordered records, routed candidate-backed episode text with deterministic
BM25, selected local records with a second BM25 pass, and reserved that evidence
during packing. It made no additional model calls.

Both arms were registered before inference against PRME
`6279c4b62f3e65d7d7171c5d868dbf23b72b46e4`, upstream MemoryAgentBench
`fe1735de8cf8b9908e1e3d3b5612afc815698062`, and dataset revision
`7ea066982b140a19337e17e60d45d4076e042faf`. Registrations bind the same 17
source chunks, 20 question and answer identities, derived retrieval questions,
preprocessing versions, reader prompt, output contract, and generation controls.
The run used the upstream reader contract without response rewriting.

PRME persisted and replayed a schema-8 receipt for every query. Post-run receipt
inspection found 219 `episode_decay` score promotions across all 20 queries;
every promoted candidate was included in its packed context. The PRME verifier
authenticated receipt checksums, ranking replays, scopes, candidate identities,
episode settings, and rendered contexts. The BM25 verifier independently rebuilt
all 200 registered lexical selections. The paired comparator accepted both
result hashes and the complete cross-arm identity.

A first execution attempt resolved the benchmark adapter from the frozen
worktree but PRME's core models from an older editable checkout. Schema-8 receipt
verification rejected that attempt before its result was inspected. Its scratch
pack and output were discarded, the import path was corrected to the frozen
worktree's `src` directory, and the complete arm was rerun under the unchanged
outcome-free registration. Follow-up code after the evaluated revision now binds
the complete receipt-reported retrieval source map so this class of runtime
drift fails by direct source-hash comparison.

The serving model's observed local inventory digest was
`6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`.
The registration binds the `qwen3.5:9b` tag rather than that digest.

## Remaining failure and decision

The one remaining PRME failure differs from the earlier flat omissions. The
highest answer-token-overlap record from the top routed episode ranked ninth and
was present in the packed context, while the reference answer was not stated
verbatim. This is a post hoc reference-assisted diagnostic, not a retrieval
policy or an additional score. The failure now points to multi-record evidence
composition or reader grounding rather than a missing routed episode.

The result supports the two-stage technique on this named EventQA development
slice. It does not support enabling the route by default across unrelated
workloads. The product option remains disabled by default until registered
Banking77 and DetectiveQA regressions show that reserving episode evidence does
not displace task-relevant records, followed by a larger cohort and another
reader family.

## Claim boundary

This comparison covers one development task, one local reader, and one lexical
control. BM25 is not a feature-equivalent memory product and used about ten times
the retrieved context. The result closes three of four measured flat-session
failures while retaining PRME's 4K budget. It does not establish general memory
quality leadership.

Artifacts: [paired comparison](memoryagentbench-eventqa-episode-dev20-comparison.json),
[PRME verification](memoryagentbench-eventqa-episode-dev20-prme-verification.json),
and [BM25 verification](memoryagentbench-eventqa-episode-dev20-bm25-verification.json).
