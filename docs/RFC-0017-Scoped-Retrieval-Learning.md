# RFC-0017: Scoped retrieval feedback and evaluated learning

**Status:** Implemented, including two-stage evaluation and scoped profile activation/rollback
**Date:** 2026-09-12
**Depends on:** RFC-0002, RFC-0004, RFC-0005, RFC-0009

## Evidence and intended outcome

The existing `FeedbackTracker` is memory-only. Its signals do not identify an
owner or retrieval request. The legacy `feedback_apply` job clears those signals
and changes engine-global weights using hardcoded rules that lack attribution to
candidate features. It also has no out-of-sample improvement gate. These are not
sufficient evidence of adaptive retrieval quality. That global job is now
excluded from default `organize()` and `end_session()` passes. It requires
explicit `organize(jobs=["feedback_apply"])` without a user scope. Scoped
requests fail before any maintenance or feedback consumption, including direct
job dispatch. This contains the legacy behavior; it does not implement scoped
learning or establish that the heuristic improves retrieval.

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

`receipt.replay_ranking()` recomputes scores using the recorded formula version
(1 for the weighted sum, 2 for the opt-in rank fusion of RFC-0005 Section 7.2,
in schema version 15 receipts) and the recorded sort policy: composite score/path/ID, a separately reranked prefix and
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

### Default balanced policy and receipt version 5

Balanced packing reserves the highest-scored ordinary multi-path candidate and
then applies the fixed quarter-length penalty. It uses the same priority tiers,
whole-source rendering and measured budget. It is the application default after
a complete 119-question answer trial improved correctness from 67 to 83, with
26 paired wins and 10 losses. A separately registered 381-question answer
confirmation improved correctness from 185 to 250, with 88 paired wins, 23
losses and no lower category total. The source partitions had already been
inspected and the custom local judge does not establish universal superiority or
authorize a learned profile. Density and score ordering remain explicit alternatives.

At introduction, only balanced pipeline retrievals needed the version 5
receipt. They require an explicit ordering and execution descriptor and retain
the existing score replay and exposure checks. Density/score pipeline receipts
still use version 4.
Versions 1–4 reject a balanced claim and preserve their canonical bytes and
feedback checksum references. The pre-change version 4 fixture bytes cover both
existing policies. Consumers must support version 5 before accepting balanced
receipts; no new packing replay guarantee is introduced.

### Context guidance and receipt version 6

Version 6 pipeline retrievals record
`packing.context_guidance_mode` as `"off"`, `"temporal"`, or `"all"`. The field
affects the exact context exposed to an answering model, even when a particular
query receives no prefix, so it is part of the durable packing configuration.
Version 6 requires the existing execution descriptor and an explicit ordering
and guidance mode. Its execution identity also hashes the context formatter.

Versions 1–5 always mean guidance was off. Their serializers omit the later
field, retain canonical bytes and feedback checksums, and reject claims that
guidance was enabled. Version 5 remains valid for historical balanced receipts;
versions 4 and 5 are no longer emitted by the current pipeline.

### Compact context and receipt version 7

Current pipeline retrievals use version 7 and explicitly record
`packing.context_format` as `"auditable"` or `"compact"`. Compact rendering
changes the exact model exposure while retaining all evidence-state and temporal
fields, so an omitted format cannot be inferred for a new receipt. Versions 1–6
always mean the historical auditable JSON-object renderer; their serializers
omit the later field and preserve canonical bytes and feedback checksums. Version
7 keeps the version 6 execution, ordering, and guidance requirements.

### Two-stage episode context and receipt version 8

Version 8 pipeline retrievals explicitly recorded
`packing.episode_context_top_k`, `packing.episode_context_local_k`, and
`packing.episode_context_score_decay`. The optional retrieval stage routes
candidate-backed `(scope, session_id)` episodes with deterministic BM25 and
promotes a bounded local evidence set. Any inherited score is represented as an
ordered `episode_decay` adjustment and remains reproducible through
`replay_ranking()`.

Versions 1–7 always mean episode routing was disabled. Their serializers omit
the three later fields and preserve canonical bytes and feedback checksums.
Version 8 retains the version 7 execution, ordering, guidance, context-format,
and rendered-context requirements. Replay still covers returned candidates only;
it cannot reconstruct unseen candidates or the episode-routing corpus.

### Explicit current-update ranking and receipt version 9

Current pipeline retrievals use version 9. The scoring configuration and every
candidate's applied weights explicitly record
`current_update_multiplier`. When eligible, the actual post-score operation is
stored as a `current_update` adjustment, so `replay_ranking()` reproduces both
the score and ordering without rerunning text detection. Version 9 requires the
same execution, ordering, guidance, context-format, episode-routing, and rendered
context fields as version 8.

Versions 1–8 mean the separate current-update multiplier was disabled. Their
serializers omit the new field from both configured and applied weights and
preserve existing canonical bytes and feedback checksums. They cannot contain a
`current_update` adjustment. This version boundary lets older consumers reject
the new operation instead of silently interpreting a version 8 receipt under a
different scoring contract.

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

## Answer citation records

An application can append one owner-scoped citation set for an answer generated
from a saved retrieval. The record binds a caller answer ID, optional answer
digest, collection method, receipt checksum, rendered-context checksum and the
memory node IDs cited by the answer. Every nonempty citation must identify a
content-bearing entry actually included in that receipt's context. Current graph
state is irrelevant, so a citation remains auditable after graph changes or
archival. Empty citation sets explicitly distinguish “reported no citations”
from absent telemetry.

A caller-generated citation UUID makes exact retries idempotent across restart.
Conflicting reuse fails without overwriting the first record. Reads and pages
retain owner isolation on both storage backends. The public async/sync Python,
HTTP and MCP surfaces share this contract.

Citation method records whether use was model-reported, application-verified or
human-verified. It does not prove answer correctness. Missing citations are not
negative relevance labels, and uncited exposure is not causal proof that a memory
was unnecessary. No current organizer or scorer consumes these records.

## Controlled context ablation

`ablate_context(bundle, [node_id])` removes included records from the exact
packed bundle without mutating the input, rerunning retrieval, filling the freed
budget, or changing any retained entry or representation. Its frozen result
binds the removed IDs, both context hashes, the counterfactual bundle, and exact
token accounting. Empty, duplicate, or absent targets fail closed.

`assess_context_presence` accepts one target and a saved answer citation. The
target must be cited and the citation's context hash must match the ablation's
baseline. A caller supplies baseline and counterfactual correctness under a
named fixed reader/evaluator protocol. The result follows four observable tiers:
correct-to-wrong is load-bearing (`1.0`), correct-to-correct is cited but
non-flipping (`0.6`), wrong-to-correct is misleading (`-1.0`), and
wrong-to-wrong is noncuring (`0.0`). The result is inspectable but is not
persisted or consumed by ranking and retention.

This is an exact context-entry intervention, not the memory-bank deletion in
Hindsight Memory-PRM. Bank deletion can change graph traversal, candidate cutoffs,
session expansion, and the record that fills a freed budget. Context ablation is
useful causal evidence for a fixed rendered prompt, while a full reproduction of
bank-level presence credit still requires the retrieval-invariant experiment
described below.

The registered 37-question source-anchored development diagnostic provides an
initial causal check on this primitive. Removing the only annotated source from
the balanced context reduced a fixed reader from 29/37 to 7/37 correct, including
23 correct-to-wrong transitions. Six non-flips retained duplicate support and one
apparent cure exposed a source/reference inconsistency, confirming that neither
branch can be converted mechanically into a relevance label. The cohort had
already been examined, uses gold sources rather than model citations, and is not
a held-out bank-deletion result. The complete evidence is in
`benchmarks/results/research/2026-09-13/CONTEXT-ABLATION-ANSWER.md`.

## Offline proposal evaluation

`MemoryEngine.evaluate_learning` and `MemoryClient.evaluate_learning` capture an
owner's relevance records in one bounded query and resolve their immutable
receipts in batches. Admissions after that first query do not enter the cut.
Exceeding `max_records` fails explicitly. Missing, ambiguous or corrupt receipts
also fail; snapshotting does not use mutable graph features or live providers.
The standalone `prme.retrieval.learning.evaluate_learning` accepts exported models.
HTTP `POST /v1/learning/evaluate` and MCP `memory_evaluate_learning` expose the
same evaluator through a credential-bound owner, typed scope/surface/configuration
inputs, explicit query groups, and the same fail-closed record bound.

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
a separate final holdout.

`evaluate_full_retrieval` provides that separate retrieval holdout primitive.
Each input pairs two freshly executed schema-3-or-later receipts and supplies the
complete set of relevant node identities, including relevant memories omitted
from either response. The evaluator verifies exact owner, scope, normalized
query, reference clock, temporal filters, result limits, base scoring, packing,
feature identity, and execution parameters outside the declared multiplier
change. Every pair in one holdout must share the same base scoring configuration
and feature identity. It rejects reused receipts and repeated normalized queries
assigned to different groups. A caller-supplied memory-artifact digest binds the fixed
experiment state; the caller remains responsible for excluding writes and
maintenance while producing each pair.

The final gate macro-averages repeats within an explicit query group, then
compares recall@k, NDCG@k and MRR across groups. Acceptance requires the minimum
number of independent groups, the configured mean NDCG gain, a positive paired
query-bootstrap lower endpoint, no mean recall loss, and a bounded fraction of
group regressions. Reports retain both receipt checksums, feature and memory
identity, proposal input checksum, per-group metrics, coverage, and uncertainty.
This evaluates new candidate generation and selection, but it does not establish
answer quality or authorize profile activation by itself.

## Scoped profile activation

An immutable `RankingProfile` can be created only when its offline proposal and
separate full-retrieval holdout both have positive decisions. Model validation
also binds the evidence to the same owner, exact normalized scope set, result
surface, proposal input checksum and candidate multipliers. A non-unity holdout
baseline must identify the immutable `baseline_profile_id` that supplied it.

Profile creation persists the complete proposal and holdout before use. Activation
requires the exact profile that served as the holdout baseline to be current and
rechecks the recorded feature identity and base scoring against the running
pipeline. This prevents a profile from being promoted over a concurrent change or
silently transferred to different scorer, storage, embedding or reranker features.

Retrieval resolves one active pointer for the owner and exact scope set without
mutating shared configuration. A matching profile supplies request-local
multipliers. Missing profiles retain base scoring. Runtime incompatibility also
retains base scoring and is reported as `inapplicable`; explicit request
multipliers take precedence and are reported as `request_override`. New receipts
and response metadata retain the profile identity, application status and reason.

Profiles and pointer changes use append-only checksummed operation records on
DuckDB and PostgreSQL. Activation, deactivation and rollback serialize changes
per owner/scope and reject stale expected state. Caller-supplied change UUIDs make
exact retries idempotent across restart. Inspection APIs expose immutable profiles,
the active pointer and transition history. See [the learning guide](LEARNING.md)
for Python, HTTP and MCP workflows.

Collection tests alone do not prove these requirements, learned quality, or
superiority to another memory system. Algorithm choice and gates require current
primary-source research and task-level evaluation before product-quality claims.

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

[Hindsight Memory-PRM](https://arxiv.org/abs/2608.29605) reports entry-level
presence credit derived from retrieval traces, answer citations and controlled
deletion-and-reanswer interventions, propagated across memory versions. PRME now
implements the narrower exact packed-context intervention above. A future
experiment must still reproduce bank deletion and re-retrieval under a fixed
reader and retrieval invariant, preserve failed and changed answers, and pass
held-out task and schema-transfer gates before any credit changes retention or
ranking.
