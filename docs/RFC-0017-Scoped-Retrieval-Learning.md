# RFC-0017: Scoped retrieval feedback and evaluated learning

**Status:** Receipts, relevance collection and offline proposal evaluation implemented; profile activation pending
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

### Score replay (receipt schema version 2)

New receipts record applied scoring weights for each candidate, the base trace,
and ordered neural-blend/session-decay operations. Session inheritance records
its source node and keeps that source's features even when the source is absent
from the returned set. Query-specific recency redistribution is captured after
it runs. Redistribution cannot exceed the available semantic/lexical weight.

`receipt.replay_ranking()` recomputes scores using formula version 1 and the
recorded sort policy: composite score/path/ID, a separately reranked prefix and
base-ranked tail, or score/ID after session expansion. Receipt validation checks
both score and order reproduction. This operation uses no model, current graph,
clock or query classifier. It describes only returned candidates; generation,
filtering, selection omissions, exposure bias and packing cannot be recovered
from these snapshots. It is not a full counterfactual retrieval evaluation.

Version 1 receipts keep their exact canonical JSON and checksum and continue
to accept relevance feedback. They lack sufficient score provenance for replay;
replay raises an explicit error rather than reconstructing features from current
graph state or assuming that configured weights were actually applied. The
version 2 fields are omitted entirely when serializing a version 1 receipt.
This migration preserves existing relevance-record checksum references.

### Full-pipeline trials and receipt version 3

The async engine, synchronous client and pipeline accept an explicit
`ranking_multipliers` request argument. The same bounded weight-adjustment helper
used in offline fitting runs after query-specific redistribution and before
composite scoring. Downstream neural prefix selection, session neighbors,
result selection and packing run afresh. Cross-scope hints use the same explicit
request adjustment, as they already do for a caller-supplied `weights` override.
HTTP `POST /v1/retrieve` and MCP `memory_retrieve` accept the same bounded
`ranking_multipliers` model. HTTP accepts `min_fidelity` alongside its existing
clock/filters. MCP exposes a timezone-aware `reference_time`, validity and
source-event time bounds, multiple scopes, epistemic mode, fidelity and cross-scope
controls. Its optional `include_context` returns the actual rendered bundle;
that bundle's token budget does not bound the surrounding tool-response JSON.
Authentication continues to determine the owner of retrievals and receipts.
No default weights or persisted profile are changed. Controlled comparisons
require a fixed pack, clock and filters with maintenance/writes excluded.

Version 3 introduced pipeline receipts that include `execution.parameters`
and `execution.features`. Parameters record the adjustment, original temporal
filters, cross-scope behavior and query-processing settings. Features record
reported embedding/reranker identities, storage implementations, dependency
versions and source-file hashes observed at pipeline creation; the current
reranker identity is read for each request. These observations are not model-weight
digests or guarantees about a remotely updated service. Complete applicability
checks remain necessary before automatic profile use.

The execution descriptor uses extensible JSON maps without adding default keys
when old snapshots are parsed. Version 1 and 2 serializers omit the descriptor
entirely, preserving old bytes/checksums and relevance references. Versions 2 and
3 both support score replay and offline evaluation. Full request re-execution
still requires the original memory artifact and matching feature environment.

### Packing policy and receipt version 4

New pipeline receipts use version 4 and record the explicit
`packing.multipath_ordering` value. Both density and score ordering retain the
same priority tiers, source fidelity and budget contract. Version 4 retains the
execution descriptor and score replay requirements introduced above. A serialized
version 4 receipt that omits its ordering is invalid.

Versions 1–3 always mean density ordering. Parsing fills that historical meaning
independently of any future application default; their serializers omit the new
field to preserve exact canonical bytes, checksums and existing feedback links.
Those schemas reject a score-ordering claim. Historical version 3 fixture bytes
were generated with the pre-option `e297d08` runtime. The backend regressions
check old receipt feedback and new policy recording across graph changes and
restart. Score replay still describes returned candidate order, not a replay of
packing or unseen candidates.

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

## Offline proposal evaluation

`MemoryEngine.evaluate_learning` and `MemoryClient.evaluate_learning` capture an
owner's relevance records in one bounded query and resolve their immutable
receipts in batches. Admissions after that first query do not enter the cut.
Exceeding `max_records` fails explicitly. Missing, ambiguous or corrupt receipts
also fail; snapshotting does not use mutable graph features or live providers.
The standalone `prme.retrieval.learning.evaluate_learning` accepts exported models.

The first implemented candidate multiplies each of the six applied additive
weights by a bounded positive factor and renormalizes the sum. Unity reproduces
the baseline exactly. Query-time features, temporal bonuses, epistemic factors,
relevance caps, score rounding, recorded neural blends and session inheritance
are retained. Zero-weight features stay zero. This is an experimental adjustment
family, not a claim that it is the optimal learning algorithm.

Training uses explicitly positive/negative pairs, a logistic pairwise loss and
regularization toward unity. A deterministic coordinate search uses a fixed step
schedule and bounds of 0.25–4. The pairwise loss follows the comparison objective
described in [the original RankNet paper](https://www.microsoft.com/en-us/research/publication/learning-to-rank-using-gradient-descent/);
this implementation is not RankNet's neural architecture or gradient optimizer.
Its bounded weight adjustment is a PRME experiment that requires further task
comparison. Loss parameters and acceptance thresholds are provisional and saved
in every report.

Repeated labels on the same request/candidate are collapsed. Conflicting labels
are excluded and counted; an unlabelled candidate never becomes a negative.
Requests need both explicit classes. Labels from other scope filters or relevance
surfaces are reported as excluded. Version 1 receipts remain usable for feedback
but are excluded from fitting because they lack replayable score provenance.

Queries are normalized for Unicode, whitespace and case; callers can additionally
group known paraphrases with `query_groups`. A fixed hash assigns each group to
training or validation independently of dataset size. Adding feedback therefore
cannot move an existing group into the other split. Each query group receives
equal objective and metric weight, regardless of its request or pair counts.
Default minimum coverage is 20 training and 20 validation groups. Validation
labels never select fitted weights or hyperparameters.

Reports retain input identities/checksums, configuration, exclusions, multipliers
and per-query metrics. Metrics rank only judged candidates: pairwise ordering
accuracy and judged NDCG@k. The offline improvement decision requires the configured
mean validation NDCG gain, a positive lower endpoint of a paired query bootstrap
95% interval, and no mean pairwise-accuracy regression. A failed gate retains the
proposed parameters for inspection while returning `no_improvement`; insufficient
coverage returns unity without fitting. Neither outcome changes active weights.

Even a positive offline result holds observed candidate membership, neural prefix
membership and session lineage fixed. It does not establish better candidate
generation, context selection, answers, unseen tasks or complete retrieval under
changed weights. Reusing validation results for later manual tuning also requires
a separate final holdout. Persisted profiles, complete-retrieval validation,
activation, deactivation and rollback remain required before production learning.

## Remaining profile activation requirements

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
- bind profile applicability to the feature/scorer/embedding/reranker versions
  actually evaluated, rather than assuming numeric features from different models
  are interchangeable;
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
