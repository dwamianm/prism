# Scoped retrieval learning

PRME can fit, evaluate, persist, activate, inspect, deactivate, and roll back
ranking adjustments for one owner and one exact scope filter. The workflow has
two evidence gates. Neither gate mutates active retrieval by itself.

1. `evaluate_learning` fits bounded weight multipliers from explicit positive
   and negative judgments and validates them on separate query groups.
2. `evaluate_full_retrieval` compares fresh baseline and candidate retrievals
   on a separate fixed holdout with complete relevant-node identities.
3. `create_ranking_profile` accepts only evidence that passed both gates.
4. `activate_ranking_profile` changes the pointer for that owner and exact scope
   set. Each subsequent retrieval records whether the profile was applied.

## Collect explicit judgments

Save relevance only after a user or evaluator has judged a returned candidate.
Unlabelled candidates do not become negatives.

```python
from prme import MemoryClient, RelevanceSubmission, Scope

with MemoryClient("./memories") as memory:
    response = memory.retrieve(
        "Which database does Aster use?",
        user_id="alice",
        scope=Scope.PROJECT,
    )
    if not response.metadata.receipt_persisted:
        raise RuntimeError("retrieval receipt was not saved")

    submission = RelevanceSubmission(
        request_id=response.metadata.request_id,
        labels={response.results[0].node.id: True},
    )
    memory.record_relevance(submission, user_id="alice")
```

Collect both classes across distinct query groups. The default offline gate
requires at least 20 training and 20 validation groups. Reuse a
`RelevanceSubmission.feedback_id` only when retrying the same write.

## Fit the offline proposal

```python
with MemoryClient("./memories") as memory:
    proposal = memory.evaluate_learning(
        user_id="alice",
        scopes=[Scope.PROJECT],
    )

if proposal.decision != "improved_on_observed_candidates":
    print(proposal.decision, proposal.coverage, proposal.exclusions)
```

This first gate replays only judged candidates. Save the complete report. Its
`input_checksum` binds the feedback and receipt snapshot used by the next gate.

## Run a fixed full-retrieval holdout

Use queries that were not used to fit or validate the proposal. Freeze or copy
the complete memory pack, prevent writes and organizer maintenance, and use the
same `reference_time`, scope, filters, limit, scoring configuration, and packing
configuration for both arms. The candidate arm changes only
`ranking_multipliers`.

Each `FullRetrievalTrial` must list every relevant memory ID for that query,
including relevant memories omitted by both returned lists. Use one `group_id`
for repeats or known paraphrases. A caller-generated SHA-256 digest should bind
the frozen fixture or archive used for the run; PRME records that identity but
cannot prevent another process from changing the source pack.

```python
from datetime import datetime, timezone
from prme import FullRetrievalTrial

clock = datetime(2026, 9, 14, tzinfo=timezone.utc)

with MemoryClient("./frozen-holdout-pack") as memory:
    baseline = memory.retrieve(
        "What telescope do I use?",
        user_id="alice",
        scope=Scope.PROJECT,
        reference_time=clock,
        limit=20,
        include_cross_scope=False,
    )
    candidate = memory.retrieve(
        "What telescope do I use?",
        user_id="alice",
        scope=Scope.PROJECT,
        reference_time=clock,
        limit=20,
        include_cross_scope=False,
        ranking_multipliers=proposal.multipliers,
    )

    trial = FullRetrievalTrial(
        group_id="telescope-model",
        baseline_request_id=baseline.metadata.request_id,
        candidate_request_id=candidate.metadata.request_id,
        relevant_node_ids=(known_relevant_node_id,),
    )

    holdout = memory.evaluate_full_retrieval(
        [trial],
        user_id="alice",
        scopes=[Scope.PROJECT],
        proposal_input_checksum=proposal.input_checksum,
        memory_artifact_sha256=frozen_fixture_sha256,
        candidate_multipliers=proposal.multipliers,
    )
```

The single-pair example shows the data flow and returns `insufficient_data` under
the default gate. Accumulate at least 20 independent query groups before creating
a profile. The default final gate also requires a mean NDCG gain of at least
0.01, a positive paired-bootstrap lower endpoint, no mean recall loss, and
regressions on at most 10% of groups.
The evaluator rejects receipt reuse and mismatched owners, scopes, clocks,
filters, limits, feature identities, base scoring, or execution parameters.

## Persist and activate the profile

```python
with MemoryClient("./memories") as memory:
    profile = memory.create_ranking_profile(
        proposal,
        holdout,
        user_id="alice",
    )
    change = memory.activate_ranking_profile(
        str(profile.profile_id),
        user_id="alice",
    )

    response = memory.retrieve(
        "What telescope do I use?",
        user_id="alice",
        scope=Scope.PROJECT,
    )
    assert response.metadata.ranking_profile_status == "applied"
    assert response.metadata.ranking_profile_id == profile.profile_id
```

Creation validates the owner, exact scope set, proposal checksum, multiplier
agreement, and both positive decisions. Activation also requires the feature
identity and base scoring configuration used by the holdout. A profile evaluated
from base scoring can activate only while base scoring is active. A later profile
must name and evaluate from its currently active `baseline_profile_id`.

Every activation, deactivation, and rollback is an append-only transition.
Supply a stable `change_id` UUID when a lost acknowledgement may be retried.
Concurrent changes use an expected-current-profile check, so a stale caller must
inspect current state and retry deliberately.

```python
with MemoryClient("./memories") as memory:
    active = memory.get_active_ranking_profile(
        user_id="alice", scopes=[Scope.PROJECT]
    )
    status = memory.get_ranking_profile_status(
        str(profile.profile_id), user_id="alice"
    )
    history = memory.list_ranking_profile_history(
        user_id="alice", scopes=[Scope.PROJECT]
    )

    memory.deactivate_ranking_profile(
        user_id="alice", scopes=[Scope.PROJECT]
    )
    memory.rollback_ranking_profile(
        str(profile.profile_id),
        user_id="alice",
        scopes=[Scope.PROJECT],
    )
```

The active pointer survives restart. Profile lookup uses the exact normalized
scope set; an unfiltered request and a project-scoped request have independent
pointers. An explicit per-request `ranking_multipliers` value takes precedence
and records `ranking_profile_status="request_override"`. If the current runtime
feature identity or base scoring no longer matches, PRME uses base scoring and
records `ranking_profile_status="inapplicable"` plus the reason.

Ranking multipliers reweight the weighted formula's additive weights, so they
do not apply to rank fusion (`ScoringWeights.fusion="rrf"`), which is the
default scoring; set `PRME_SCORING__FUSION=weighted` to use profiles. Under rank
fusion a profile is inapplicable with reason `rank_fusion_scoring`, a request
with non-neutral `ranking_multipliers` is rejected before retrieval (HTTP 422),
and learning evaluation counts rank-fused receipts under
`rank_fusion_receipt_records` instead of fitting them.

## HTTP and MCP

The HTTP API exposes:

- `POST /v1/learning/evaluate`
- `POST /v1/learning/evaluate-full-retrieval`
- `POST /v1/learning/profiles`
- `GET /v1/learning/profiles` and `GET /v1/learning/profiles/{profile_id}`
- `GET /v1/learning/profiles/active` and `GET /v1/learning/profiles/history`
- `POST /v1/learning/profiles/{profile_id}/activate`
- `POST /v1/learning/profiles/deactivate`
- `POST /v1/learning/profiles/rollback`

MCP provides the corresponding `memory_evaluate_learning`,
`memory_evaluate_full_retrieval`, `memory_create_ranking_profile`, get/list,
active/history, activate/deactivate, and rollback tools. Authenticated HTTP and
MCP deployments bind `user_id` to the credential as they do for retrieval and
feedback.

## Evidence boundary

A positive profile establishes improvement under its recorded judged-candidate
and full-retrieval protocols. It does not establish answer quality, truth,
causality, transfer to another model/index identity, or superiority on another
task distribution. Use an answer-level holdout before making task-quality claims.
