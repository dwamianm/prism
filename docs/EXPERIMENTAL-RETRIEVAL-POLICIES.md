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
The existing model, prefix limit and prior blend weight are unchanged. Direct
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
