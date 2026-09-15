"""Two-stage episode routing preserves bounded, replayable source evidence."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from prme.models.nodes import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.episode_context import expand_episode_context
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.scoring import score_and_rank
from prme.storage.engine import MemoryEngine
from prme.types import LifecycleState, NodeType, RepresentationLevel, Scope
from tests import test_durable_ingestion


config = test_durable_ingestion.config
user = test_durable_ingestion.user


NOW = datetime(2026, 9, 14, tzinfo=timezone.utc)


def candidate(
    content: str,
    *,
    session: str | None,
    semantic: float,
    scope: Scope = Scope.PROJECT,
) -> RetrievalCandidate:
    node = MemoryNode(
        id=uuid4(),
        user_id="owner",
        session_id=session,
        node_type=NodeType.NOTE,
        content=content,
        scope=scope,
        lifecycle_state=LifecycleState.STABLE,
        confidence=0.8,
        confidence_base=0.8,
        salience=0.5,
        salience_base=0.5,
        created_at=NOW,
        updated_at=NOW,
        last_reinforced_at=NOW,
    )
    return RetrievalCandidate(
        node=node,
        paths=["VECTOR"],
        path_count=1,
        semantic_score=semantic,
    )


def test_routes_an_episode_then_promotes_local_evidence_with_provenance():
    anchor = candidate(
        "The deployment discussion covered routine status updates.",
        session="launch-episode",
        semantic=0.95,
    )
    evidence = candidate(
        "Maya supplied the required launch approval.",
        session="launch-episode",
        semantic=0.05,
    )
    distractors = [
        candidate(
            f"Unrelated support history {index}.",
            session="support-episode",
            semantic=0.8 - index / 100,
        )
        for index in range(3)
    ]
    scored, _ = score_and_rank([anchor, evidence, *distractors], now=NOW)
    original = next(item for item in scored if item.node.id == evidence.node.id)

    expanded = expand_episode_context(
        scored,
        "Who supplied launch approval?",
        PackingConfig(
            episode_context_top_k=1,
            episode_context_local_k=2,
            episode_context_score_decay=0.95,
        ),
    )

    promoted = next(item for item in expanded if item.node.id == evidence.node.id)
    episode_anchor = next(item for item in scored if item.node.id == anchor.node.id)
    assert "EPISODE_CONTEXT" in promoted.paths
    assert promoted.path_count == original.path_count + 1
    assert promoted.composite_score == episode_anchor.composite_score * 0.95
    assert promoted.score_provenance is not None
    assert promoted.score_provenance.adjustments[-1].kind == "episode_decay"
    assert promoted.score_provenance.adjustments[-1].source_node_id == anchor.node.id
    assert promoted.score_provenance.replay_score() == promoted.composite_score


def test_episode_routing_is_disabled_and_does_not_cross_scope_partitions():
    project = candidate(
        "The release approval is in the project episode.",
        session="shared-name",
        semantic=0.4,
    )
    personal = candidate(
        "A personal note with release approval.",
        session="shared-name",
        semantic=0.9,
        scope=Scope.PERSONAL,
    )
    scored, _ = score_and_rank([project, personal], now=NOW)
    assert expand_episode_context(scored, "release approval", PackingConfig()) is scored
    assert (
        expand_episode_context(
            scored,
            "release approval",
            PackingConfig(episode_context_top_k=2),
        )
        is scored
    )


def test_packing_reserves_routed_episode_evidence_after_user_priorities():
    evidence = candidate(
        "The source evidence needed for the answer. " * 12,
        session="episode",
        semantic=0.1,
    )
    evidence.paths.append("EPISODE_CONTEXT")
    evidence.path_count += 1
    evidence.composite_score = 0.1
    competitor = candidate(
        "A much higher-scored but broad candidate. " * 12,
        session=None,
        semantic=0.99,
    )
    competitor.paths.append("LEXICAL")
    competitor.path_count += 1
    competitor.composite_score = 0.99
    base = PackingConfig(
        token_budget=10_000,
        overhead_tokens=0,
        min_fidelity=RepresentationLevel.FULL,
    )
    evidence_only = pack_context([evidence], base)
    config = base.model_copy(update={"token_budget": evidence_only.tokens_used})

    packed = pack_context([competitor, evidence], config)

    included = {
        item.node.id for values in packed.sections.values() for item in values
    }
    assert evidence.node.id in included
    assert competitor.node.id not in included
    assert packed.tokens_used <= config.token_budget


@pytest.mark.asyncio
async def test_pipeline_persists_episode_policy_and_replayable_promotions(config, user):
    config = config.model_copy(
        update={
            "packing": config.packing.model_copy(
                update={
                    "episode_context_top_k": 1,
                    "episode_context_local_k": 2,
                    "episode_context_score_decay": 0.95,
                }
            )
        }
    )
    async with MemoryEngine.open(config) as engine:
        await engine.store(
            "The launch review covered the ordinary rollout schedule.",
            user_id=user,
            session_id="launch-episode",
        )
        await engine.store(
            "Maya supplied the launch approval.",
            user_id=user,
            session_id="launch-episode",
        )
        await engine.store(
            "An unrelated support review was routine.",
            user_id=user,
            session_id="support-episode",
        )
        await engine.store(
            "The support queue closed on time.",
            user_id=user,
            session_id="support-episode",
        )

        response = await engine.retrieve(
            "Who supplied launch approval?",
            user_id=user,
            min_score=0,
            include_cross_scope=False,
        )

        routed = [
            candidate
            for candidate in response.results
            if "EPISODE_CONTEXT" in candidate.paths
        ]
        assert routed
        assert all(candidate.node.user_id == user for candidate in routed)
        receipt = await engine.get_retrieval_receipt(
            str(response.metadata.request_id), user_id=user
        )
        assert receipt is not None
        assert receipt.schema_version == 8
        assert receipt.packing.episode_context_top_k == 1
        assert receipt.packing.episode_context_local_k == 2
        assert receipt.packing.episode_context_score_decay == 0.95
        assert receipt.execution is not None
        episode_source = receipt.execution.features["source_files_sha256"][
            "episode_context"
        ]
        assert isinstance(episode_source, str) and len(episode_source) == 64
        assert receipt.replay_ranking() == tuple(
            candidate.node.id for candidate in response.results
        )
