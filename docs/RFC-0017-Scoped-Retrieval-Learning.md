# RFC-0017: Scoped retrieval feedback and evaluated learning

**Status:** Receipt and relevance-record foundation implemented; evaluated learning pending
**Date:** 2026-09-12
**Depends on:** RFC-0002, RFC-0004, RFC-0005, RFC-0009

## Evidence and intended outcome

The existing `FeedbackTracker` is memory-only. Its signals do not identify an
owner or retrieval request. The legacy `feedback_apply` job clears those signals
and changes engine-global weights using hardcoded rules that lack attribution to
candidate features. It also has no out-of-sample improvement gate. These are not
sufficient evidence of adaptive retrieval quality. Existing operator restrictions
on that global job remain necessary until it is replaced.

The intended replacement collects immutable, owner-scoped relevance judgments on
actual saved retrievals, evaluates proposed ranking changes against separated
queries, and activates only a versioned profile for the affected owner and scope.
No tenant feedback may update engine-global defaults. Learned profiles must
survive restart, retain their inputs, and support explicit rollback. A regression
or insufficient evidence must keep the existing profile. The legacy heuristic
is not the learning algorithm for this replacement.

## Retrieval receipt

A successful retrieval log can include a versioned receipt containing the exact
request, owner, scope filter, reference clock, scoring and packing configuration,
candidate identities, content hashes, score traces, and context membership and
representation. Candidate text is not duplicated. The context hash identifies the
rendered bundle. The receipt describes what the memory API returned; it does not
claim that an application sent that context to a model or that the model used it.

Retrieval remains available when logging fails, but metadata must state whether
a feedback receipt was persisted. Reading a missing, foreign or legacy request
without a receipt yields no receipt. Saved receipts remain snapshots if a graph
node changes or is archived later; reconstructing them from current graph state
would corrupt the training record.

## Explicit relevance records

`record_relevance(request_id, labels, user_id=..., feedback_id=...)` accepts
nonempty boolean labels for members of the saved response. `surface="results"`
judges returned candidates; `surface="context"` judges content-bearing entries
actually included in the bundle. Reference-only entries cannot receive positive
content-evidence credit. The detection method distinguishes explicit user labels
from structured evaluation. Neither label type implies an independently verified
fact, authorizes a graph mutation, or treats an unlabelled item as negative.

A caller-supplied feedback UUID makes retries idempotent within an owner. Reusing
it with different labels, a different request, surface or method must fail rather
than overwrite the original judgment. The server records its own admission time.
Records use append-only operation rows and are read through an owner boundary on
both storage backends. A failed validation writes nothing. Different feedback IDs
can preserve conflicting judgments; later training must state how it handles
repeated/contradictory judgments and must not count retries as independent votes.

## Remaining evaluated-learning requirements

Before a learned profile can be activated, the implementation must:

- retain enough recorded features/configuration to reproduce baseline ranking;
- separate training and validation by retrieval/query group, avoiding duplicate
  query leakage and documenting exposure/selection bias;
- use explicitly labelled comparisons, never infer negatives from missing labels;
- report coverage, baseline/candidate metrics and rejected proposals;
- require an improvement gate on separate observations and retain uncertainty;
- keep owner/scope boundaries during collection, fitting, activation and replay;
- persist the selected profile and input identities before using it;
- load the correct immutable profile per request without mutating shared weights;
- support inspection, deactivation and rollback with reproducible receipts.

Collection tests alone do not prove these requirements, learned quality, or
superiority to another memory system. Algorithm choice and gates require current
primary-source research and development-set evaluation before activation.

## Current technique comparison (reviewed 2026-09-12)

Learning to rank uses query/document relevance judgments together with recorded
features; judgment coverage and quality affect generalization. This supports
collecting explicit labels and contemporaneous features before fitting a ranker.
[Elastic's primary LTR documentation](https://www.elastic.co/docs/solutions/search/ranking/learning-to-rank-ltr)
describes that input contract and ranking objectives. For PRME, the first
comparison should include the unchanged scorer, a regularized pairwise linear
ranker, and a tree-based ranking model when data volume supports it. That is a
proposed experiment, not a validated model choice or improvement claim.

[MemRL v2](https://arxiv.org/html/2601.03192v2) first recalls semantically related
experiences, then selects using learned utility. Its updates use task-success
rewards, and its stability analysis assumes a fixed inference/evaluation policy
and stationary task distribution. This is relevant to agent experience memory,
but a relevance label does not by itself measure task success. Our inference is
that PRME needs a distinct outcome/exposure contract before applying that kind of
utility update; the existing `USED` signal cannot be substituted silently.

[MemQ v3](https://arxiv.org/html/2605.08374v3) propagates value through a provenance
DAG linking retrieved experiences to newly created memories, including future
utility. PRME's typed factual relationships are not that experience-creation
DAG. Adapting this technique would require explicit trajectory/outcome lineage,
credit-assignment tests, and a controlled task benchmark. Copying its update onto
ordinary `HAS_FACT` or similarity edges would not implement the reported method.
