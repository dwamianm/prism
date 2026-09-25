"""Scoped ranking profiles are gated, durable, applicable, and reversible."""
import asyncio
import hashlib
import json
from uuid import UUID, uuid4

import pytest

from prme import (
    FullRetrievalEvaluation,
    FullRetrievalEvaluationConfig,
    FullRetrievalQueryResult,
    FullRetrievalTrial,
    LearningConfig,
    LearningEvaluation,
    MemoryEngine,
    RankingMultipliers,
    StaleRankingProfileError,
)
from prme.models.learning import QueryLearningResult
from prme.retrieval.config import ScoringWeights
from prme.types import Scope
from tests import test_durable_ingestion
from tests.previous_defaults import previous_defaults

durable_config = test_durable_ingestion.config


@pytest.fixture
def config(durable_config):
    # Learned ranking profiles adjust the weighted formula: they keep the previous retrieval
    # defaults, which rank fusion and the reader format replaced on 2026-09-24.
    return previous_defaults(durable_config)


user = test_durable_ingestion.user


def _feature_hash(features) -> str:
    return hashlib.sha256(json.dumps(
        features, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode()).hexdigest()


def _canonical_sha256(value) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode()).hexdigest()


def _evidence(engine, multipliers, *, baseline=RankingMultipliers(), owner="owner"):
    proposal_queries = tuple(QueryLearningResult(
        group_id=f"group-{index:02d}",
        split="train" if index < 20 else "validation",
        request_ids=(UUID(int=100 + index),),
        baseline_pairwise_accuracy=.5,
        candidate_pairwise_accuracy=.5,
        baseline_judged_ndcg=.4,
        candidate_judged_ndcg=.6,
    ) for index in range(40))
    proposal = LearningEvaluation(
        user_id=owner, scopes=(Scope.PROJECT,), surface="results",
        config=LearningConfig(bootstrap_samples=100), input_checksum="a" * 64,
        feedback_ids=tuple(UUID(int=200 + index) for index in range(40)),
        receipt_checksums={UUID(int=100 + index): "b" * 64 for index in range(40)},
        multipliers=multipliers, decision="improved_on_observed_candidates",
        training_loss_before=1, training_loss_after=.5, validation_ndcg_gain=.2,
        validation_pairwise_gain=0, validation_gain_interval=(.2, .2),
        coverage={"feedback_records": 40, "requests_with_pairs": 40,
                  "training_queries": 20, "validation_queries": 20, "explicit_pairs": 40},
        exclusions={}, queries=proposal_queries,
    )
    trials = (
        FullRetrievalTrial(group_id="one", baseline_request_id=UUID(int=10),
                           candidate_request_id=UUID(int=11), relevant_node_ids=(UUID(int=20),)),
        FullRetrievalTrial(group_id="two", baseline_request_id=UUID(int=12),
                           candidate_request_id=UUID(int=13), relevant_node_ids=(UUID(int=21),)),
    )
    queries = tuple(FullRetrievalQueryResult(
        group_id=trial.group_id,
        baseline_request_ids=(trial.baseline_request_id,),
        candidate_request_ids=(trial.candidate_request_id,),
        baseline_recall=0, candidate_recall=1,
        baseline_ndcg=0, candidate_ndcg=1,
        baseline_mrr=0, candidate_mrr=1,
    ) for trial in trials)
    features = engine._retrieval_pipeline.execution_features()
    holdout_config = FullRetrievalEvaluationConfig(
        min_query_groups=2, bootstrap_samples=100,
    )
    receipt_checksums = {UUID(int=value): "e" * 64 for value in (10, 11, 12, 13)}
    input_checksum = _canonical_sha256({
        "user_id": owner,
        "scopes": [Scope.PROJECT.value],
        "proposal_input_checksum": proposal.input_checksum,
        "memory_artifact_sha256": "c" * 64,
        "baseline_multipliers": baseline.model_dump(mode="json"),
        "candidate_multipliers": multipliers.model_dump(mode="json"),
        "base_scoring": engine._config.scoring.model_dump(mode="json"),
        "config": holdout_config.model_dump(mode="json"),
        "trials": [trial.model_dump(mode="json") for trial in trials],
        "receipt_checksums": {
            str(key): value for key, value in sorted(
                receipt_checksums.items(), key=lambda item: str(item[0]),
            )
        },
    })
    holdout = FullRetrievalEvaluation(
        user_id=owner, scopes=(Scope.PROJECT,), config=holdout_config,
        proposal_input_checksum=proposal.input_checksum,
        memory_artifact_sha256="c" * 64, input_checksum=input_checksum,
        receipt_checksums=receipt_checksums,
        feature_identity=features, feature_identity_sha256=_feature_hash(features),
        base_scoring=engine._config.scoring,
        baseline_multipliers=baseline, candidate_multipliers=multipliers,
        decision="improved_full_retrieval",
        baseline_recall=0, candidate_recall=1, recall_gain=1,
        baseline_ndcg=0, candidate_ndcg=1, ndcg_gain=1,
        ndcg_gain_interval=(1, 1),
        baseline_mrr=0, candidate_mrr=1, mrr_gain=1,
        regression_fraction=0,
        coverage={"trials": 2, "query_groups": 2, "relevant_nodes": 2},
        trials=trials, queries=queries,
    )
    return proposal, holdout


async def test_profile_is_persisted_before_use_and_applied_to_exact_scope(config, user):
    profile_id = uuid4()
    change_id = uuid4()
    multipliers = RankingMultipliers(lexical=2)
    async with MemoryEngine.open(config) as engine:
        await engine.store("blue telescope", user_id=user, scope=Scope.PROJECT)
        proposal, holdout = _evidence(engine, multipliers, owner=user)
        profile = await engine.create_ranking_profile(
            proposal, holdout, user_id=user, profile_id=profile_id,
        )
        assert not (await engine.get_ranking_profile_status(
            str(profile_id), user_id=user,
        )).active
        # The caller profile UUID is an idempotent request identity; the first
        # server timestamp survives retries.
        assert await engine.create_ranking_profile(
            proposal, holdout, user_id=user, profile_id=profile_id,
        ) == profile
        state = await engine.activate_ranking_profile(
            str(profile_id), user_id=user, change_id=change_id,
        )
        assert await engine.activate_ranking_profile(
            str(profile_id), user_id=user, change_id=change_id,
        ) == state

        response = await engine.retrieve(
            "telescope", user_id=user, scope=Scope.PROJECT,
        )
        assert response.metadata.ranking_profile_id == profile_id
        assert response.metadata.ranking_profile_status == "applied"
        assert response.metadata.ranking_multipliers == multipliers
        receipt = await engine.get_retrieval_receipt(
            str(response.metadata.request_id), user_id=user,
        )
        assert receipt.execution.parameters["ranking_profile"] == {
            "status": "applied", "profile_id": str(profile_id), "reason": None,
        }

        explicit = await engine.retrieve(
            "telescope", user_id=user, scope=Scope.PROJECT,
            ranking_multipliers=RankingMultipliers(graph=2),
        )
        assert explicit.metadata.ranking_profile_status == "request_override"
        assert explicit.metadata.ranking_profile_reason == "explicit_multipliers"
        assert explicit.metadata.ranking_profile_id is None

        changed = ScoringWeights(
            w_semantic=.2, w_lexical=.25, w_graph=.2,
            w_recency=.15, w_salience=.1, w_confidence=.1,
        )
        inapplicable = await engine.retrieve(
            "telescope", user_id=user, scope=Scope.PROJECT, weights=changed,
        )
        assert inapplicable.metadata.ranking_profile_status == "inapplicable"
        assert inapplicable.metadata.ranking_profile_reason == "base_scoring_mismatch"
        assert inapplicable.metadata.ranking_multipliers is None
        assert (await engine.retrieve(
            "telescope", user_id=user, scope=Scope.PERSONAL,
        )).metadata.ranking_profile_status == "none"
        assert await engine.get_ranking_profile(str(profile_id), user_id="foreign") is None

    async with MemoryEngine.open(config) as engine:
        active = await engine.get_active_ranking_profile(
            user_id=user, scopes=[Scope.PROJECT],
        )
        assert active is not None and active.profile_id == profile_id
        assert (await engine.retrieve(
            "telescope", user_id=user, scope=Scope.PROJECT,
        )).metadata.ranking_profile_status == "applied"


async def test_versioned_activation_deactivation_and_rollback(config, user):
    first_id, second_id, stale_id = uuid4(), uuid4(), uuid4()
    first_multipliers = RankingMultipliers(lexical=2)
    second_multipliers = RankingMultipliers(lexical=3)
    async with MemoryEngine.open(config) as engine:
        proposal, holdout = _evidence(engine, first_multipliers, owner=user)
        await engine.create_ranking_profile(
            proposal, holdout, user_id=user, profile_id=first_id,
        )
        await engine.activate_ranking_profile(str(first_id), user_id=user)

        stale_proposal, stale_holdout = _evidence(
            engine, RankingMultipliers(graph=2), owner=user,
        )
        await engine.create_ranking_profile(
            stale_proposal, stale_holdout, user_id=user, profile_id=stale_id,
        )
        with pytest.raises(ValueError, match="evaluated baseline"):
            await engine.activate_ranking_profile(str(stale_id), user_id=user)

        proposal, holdout = _evidence(
            engine, second_multipliers, baseline=first_multipliers, owner=user,
        )
        await engine.create_ranking_profile(
            proposal, holdout, user_id=user, profile_id=second_id,
            baseline_profile_id=first_id,
        )
        await engine.activate_ranking_profile(str(second_id), user_id=user)
        assert (await engine.get_active_ranking_profile(
            user_id=user, scopes=[Scope.PROJECT],
        )).profile_id == second_id

        rollback_id = uuid4()
        rolled_back = await engine.rollback_ranking_profile(
            str(first_id), user_id=user, scopes=[Scope.PROJECT], change_id=rollback_id,
        )
        assert rolled_back.action == "rollback"
        assert await engine.rollback_ranking_profile(
            str(first_id), user_id=user, scopes=[Scope.PROJECT], change_id=rollback_id,
        ) == rolled_back

        deactivate_id = uuid4()
        deactivated = await engine.deactivate_ranking_profile(
            user_id=user, scopes=[Scope.PROJECT], change_id=deactivate_id,
        )
        assert deactivated.action == "deactivate"
        assert await engine.deactivate_ranking_profile(
            user_id=user, scopes=[Scope.PROJECT], change_id=deactivate_id,
        ) == deactivated
        assert await engine.get_active_ranking_profile(
            user_id=user, scopes=[Scope.PROJECT],
        ) is None
        history = await engine.list_ranking_profile_history(
            user_id=user, scopes=[Scope.PROJECT],
        )
        assert [item.action for item in history] == [
            "deactivate", "rollback", "activate", "activate",
        ]


async def test_profile_evidence_and_retry_conflicts_fail_closed(config, user):
    async with MemoryEngine.open(config) as engine:
        proposal, holdout = _evidence(
            engine, RankingMultipliers(lexical=2), owner=user,
        )
        profile_id = uuid4()
        await engine.create_ranking_profile(
            proposal, holdout, user_id=user, profile_id=profile_id,
        )
        changed_proposal, changed_holdout = _evidence(
            engine, RankingMultipliers(graph=2), owner=user,
        )
        with pytest.raises(ValueError, match="different ranking profile"):
            await engine.create_ranking_profile(
                changed_proposal, changed_holdout,
                user_id=user, profile_id=profile_id,
            )
        change_id = uuid4()
        await engine.activate_ranking_profile(
            str(profile_id), user_id=user, change_id=change_id,
        )
        with pytest.raises(ValueError, match="different ranking profile transition"):
            await engine.deactivate_ranking_profile(
                user_id=user, scopes=[Scope.PROJECT], change_id=change_id,
            )

        for update, message in (
            ({"decision": "no_improvement"}, "decision does not match"),
            ({"input_checksum": "f" * 64}, "identify its proposal"),
        ):
            bad_proposal = proposal.model_copy(update=update)
            with pytest.raises(ValueError, match=message):
                await engine.create_ranking_profile(
                    bad_proposal, holdout, user_id=user,
                )


async def test_concurrent_activations_serialize_from_the_evaluated_baseline(config, user):
    async with MemoryEngine.open(config) as engine:
        profiles = []
        for value in (2, 3):
            proposal, holdout = _evidence(
                engine, RankingMultipliers(lexical=value), owner=user,
            )
            profiles.append(await engine.create_ranking_profile(
                proposal, holdout, user_id=user,
            ))
        outcomes = await asyncio.gather(*(
            engine.activate_ranking_profile(str(profile.profile_id), user_id=user)
            for profile in profiles
        ), return_exceptions=True)
        assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
        failures = [item for item in outcomes if isinstance(item, BaseException)]
        assert len(failures) == 1 and isinstance(failures[0], StaleRankingProfileError)
        history = await engine.list_ranking_profile_history(
            user_id=user, scopes=[Scope.PROJECT],
        )
        active = await engine.get_active_ranking_profile(
            user_id=user, scopes=[Scope.PROJECT],
        )
        assert len(history) == 1 and active is not None
        assert active.profile_id == history[0].active_profile_id


async def test_activation_rejects_changed_runtime_feature_identity(config, user):
    async with MemoryEngine.open(config) as engine:
        proposal, holdout = _evidence(
            engine, RankingMultipliers(lexical=2), owner=user,
        )
        value = holdout.model_dump(mode="json")
        value["user_id"] = user
        value["feature_identity"] = {**value["feature_identity"], "runtime": "changed"}
        value["feature_identity_sha256"] = _feature_hash(value["feature_identity"])
        holdout = FullRetrievalEvaluation.model_validate(value)
        profile = await engine.create_ranking_profile(
            proposal, holdout, user_id=user,
        )
        with pytest.raises(ValueError, match="feature_identity_mismatch"):
            await engine.activate_ranking_profile(str(profile.profile_id), user_id=user)


async def test_retrieval_reads_compact_active_pointer_without_loading_evidence(
    config, user, monkeypatch,
):
    async with MemoryEngine.open(config) as engine:
        await engine.store("compact active profile", user_id=user, scope=Scope.PROJECT)
        proposal, holdout = _evidence(
            engine, RankingMultipliers(lexical=2), owner=user,
        )
        profile = await engine.create_ranking_profile(proposal, holdout, user_id=user)
        await engine.activate_ranking_profile(str(profile.profile_id), user_id=user)

        async def evidence_load_forbidden(*args, **kwargs):
            raise AssertionError("retrieval must not load full profile evidence")

        monkeypatch.setattr(engine._ranking_profiles, "get", evidence_load_forbidden)
        response = await engine.retrieve(
            "compact", user_id=user, scope=Scope.PROJECT,
        )
        assert response.metadata.ranking_profile_id == profile.profile_id
        assert response.metadata.ranking_profile_status == "applied"
