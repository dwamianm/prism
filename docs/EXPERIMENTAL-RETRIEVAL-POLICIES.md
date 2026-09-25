# Experimental retrieval composition policies

These options expose fixes studied on the examined 500-question LongMemEval-S
cohort. They require explicit configuration, have no positive untouched answer
confirmation, and do not change production defaults. The implementation branch
is based on `main` at `a66ee85` and is separate from the frozen benchmark branches.

```python
from prme import PRMEConfig

config = PRMEConfig(
    enable_reranker=True,
    reranker_policy="anchored_score_envelope",
)
```

`reranker_policy` accepts `legacy` (the unchanged default), `score_envelope`,
and `anchored_score_envelope`. Setting a policy does not enable reranking.
The original neural-prefix blend can shrink its scores below the untouched tail,
allowing unjudged records to displace judged records during later packing.
The envelope policies reorder only the scored prefix and assign its original
sorted score multiset to that order. The tail retains its original scores and
has no neural judgment. Equal scores retain UUID tie-breaking.

The anchored variant first prioritizes the original highest-scored ordinary
multi-path candidate when it occurs in the reranked prefix. Instructions, pinned
records, maximum-salience records and active tasks are not ordinary anchors.
This is a priority, not a guarantee that any source fits or stays packed. The
remaining prefix follows neural order. Neither variant calibrates probabilities.
The policies change neither the model, the prefix limit nor the prior blend
weight; `reranker_prior_weight` (below) sets that weight. Direct
`CrossEncoderReranker(..., policy=...)` calls need candidates with replayable
scoring provenance for an envelope assignment; the normal pipeline supplies it.

Receipts preserve the neural blend and append `neural_rank_assignment`. Such
receipts use schema 13 and require an execution descriptor. Earlier schemas
cannot claim these assignments; their canonical bytes and checksums are
unchanged. Empty/no-assignment retrievals retain schema 12. The enabled policy
is part of the execution feature identity, so a learned ranking profile from
another policy cannot activate as if the pipelines matched. Score replay covers
returned candidates, not unseen candidates or full packing reconstruction.

```python
config = PRMEConfig(
    enable_query_reformulation=True,
    query_reformulation_merge_policy="max_signals",
)
```

`query_reformulation_merge_policy` accepts `new_only` (the unchanged default)
and `max_signals`. The latter combines alternative-query hits for an existing
candidate instead of discarding their retrieval signals: union distinct backend
paths and take the maximum semantic, normalized lexical and graph-proximity
components. Repeated queries do not count as additional backends. Source text,
owner, scope and clocks must match exactly for an identity. The ordinary scoring,
filtering and packing stages still run afterward. New identities retain the
normal candidate admission behavior.

Alternate-query backend failure, embedding mismatch, nonfinite signals or
conflicting source snapshots abort this opt-in retrieval before merging any
partial result. All alternate passes settle before a backend error propagates.
The existing provider helper still returns no alternatives on provider failure;
this option does not turn that compatibility behavior into provider success.
Benchmark observers must continue to count provider failures separately. The
provider, model, endpoint, credential and timeout come from `config.extraction`,
as in ordinary reformulation.
The merge policy is reported in execution parameters and, when enabled, feature
identity. It requires no new receipt schema because final scoring inputs already
have provenance. Neither option mutates the stored memory artifact.

## Cross-encoder rank order (issue #88)

`reranker_prior_weight` (`PRME_RERANKER_PRIOR_WEIGHT`, default 0.3) is the
weight of a reranked candidate's own score in the order of the reranked prefix;
the cross-encoder gets the rest. At 0.0 the prefix follows the cross-encoder
alone. With an envelope policy the prefix then takes its original scores in that
order: the model decides the ranks, and the scale of the scores (rank fusion's,
under the defaults) reaches session expansion and packing unchanged. Zep and
Hindsight use a cross-encoder after fusion the same way.

```python
config = PRMEConfig(
    enable_reranker=True,
    reranker_policy="score_envelope",
    reranker_prior_weight=0.0,
    reranker_top_k=300,
)
```

- The reranker runs after scoring, on the top `reranker_top_k` candidates of
  the scored pool, and needs the `reranker` extra and locally available model
  files. Rank fusion scores are the default, so this reranks the fused order.
- A weight other than 0.3 needs an envelope policy. `PRMEConfig` and the
  pipeline refuse it with `legacy`, where the prefix would carry raw model
  scores next to the tail's own scores: the scale failure the envelope policies
  fix.
- At 0.0 nothing but the model orders the prefix: the epistemic weight,
  node-type boost, temporal affinity and current-state recency that rank
  fusion multiplies in no longer change the order inside it, only the scores
  it is given. The evidence gate's packs hold raw turns, where the first two do
  not vary, so it cannot show that effect.
- The model reads the question and the record together up to its input limit
  (512 tokens for the default MiniLM model), so a long record is judged by its
  start.
- Packing keeps its tiers and ordering. Under the default balanced order a
  multi-path record's score is divided by the fourth root of its token cost, so
  the packed order inside that tier is not the model's order.
- Equal assigned scores still break ties by UUID, as in the envelope policies.
  Before session expansion, fused scores tie for about 0.1% (LoCoMo) and 0.5% to
  0.8% (LongMemEval-S) of the top 100 to 300 candidates, so this rarely moves
  a record.
- The model runs in the request, under one lock per engine. On a CPU a prefix
  of 300 long records takes seconds, so measure latency before using it on a
  shared server.
- Receipts need no new schema. Each reranked candidate's provenance records
  the weight as its `neural_blend` coefficient, and rank fusion receipts
  (versions 16 to 21) admit the rank assignment. A weight other than 0.3 is
  also recorded in `execution.parameters.reranker_prior_weight` and in the
  reranker's feature identity, so a ranking profile learned under another
  weight does not activate. At 0.3 both are omitted, and receipts keep their
  bytes.
- Model scores depend on the model files, library versions and hardware (CPU,
  Apple GPU or CUDA), so another machine can order near-ties differently. The
  receipt records the scores, so replaying it never reruns the model. The
  evidence gate records the versions, the device and the model revision of a
  reranker run.

## Evidence and limits

The [dated implementation report](../benchmarks/results/research/2026-09-22/RETRIEVAL-POLICY-IMPLEMENTATION.md)
links exact results and validation. The original enabled reranker scored 284/500
against production 437/500. The first envelope repair completed at 428/500,
but its newly registered primary control failed closed; the complete candidate
cannot repair that failed comparison. The anchor trial completed both new arms:
430/500 versus 429/500, 11 wins and 10 losses, +0.2 percentage points with a 95%
paired interval of [−1.6,+2.0]. Complete annotated source sets increased from
403/470 to 409/470, which did not establish an answer-quality gain. The signal
merge quality trial is registered separately and remains in progress.

Keep both options experimental. A default proposal needs a complete positive
answer comparison, untouched confirmation, relevant backend and regression
checks, and separate explicit review. No production flag, mainline merge or
release is authorized by this implementation.
