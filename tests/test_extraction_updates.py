"""LLM ingestion preserves coexisting values and names replacement targets."""

from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
from prme.types import LifecycleState, NodeType
from tests.test_durable_ingestion import config, user  # noqa: F401


async def ingest_fact(engine, user_id, source, value, **kwargs):
    engine._pipeline._extraction_provider.extract = AsyncMock(return_value=ExtractionResult(
        entities=[ExtractedEntity(name="Alice", entity_type="person")],
        facts=[ExtractedFact(subject="Alice", predicate="uses", object=value,
                             evidence_quote=source, **kwargs)],
    ))
    await engine.ingest(source, user_id=user_id, wait_for_extraction=True)


@pytest.mark.parametrize("intent", [None, "assertion", "update"])
async def test_another_value_does_not_replace_or_contest_an_existing_value(config, user, intent):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await ingest_fact(engine, user, "Alice uses Python", "Python")
        await ingest_fact(engine, user, "Alice now also uses Rust", "Rust", temporal_intent=intent)
        nodes = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
        assert len(nodes) == 2
        assert all(n.lifecycle_state == LifecycleState.TENTATIVE for n in nodes)
        assert all(n.superseded_by is None for n in nodes)


async def test_explicit_replacement_retires_only_the_named_value(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await ingest_fact(engine, user, "Alice uses Python", "Python")
        await ingest_fact(engine, user, "Alice uses Java", "Java")
        await ingest_fact(engine, user, "Alice switched from Python to Rust", "Rust",
                          temporal_intent="update", replaces_object="Python")
        nodes = await engine.query_nodes(user_id=user, node_type=NodeType.FACT,
                                         lifecycle_states=list(LifecycleState))
        by_value = {n.metadata["object"]: n for n in nodes}
        assert by_value["Python"].lifecycle_state == LifecycleState.SUPERSEDED
        assert by_value["Python"].superseded_by == by_value["Rust"].id
        assert by_value["Java"].lifecycle_state == LifecycleState.TENTATIVE
        assert by_value["Rust"].lifecycle_state == LifecycleState.TENTATIVE


@pytest.mark.parametrize("epistemic", ["conditional", "hypothetical", "inferred", "unverified"])
async def test_speculative_replacement_preserves_current_state(config, user, epistemic):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await ingest_fact(engine, user, "Alice uses Python", "Python")
        await ingest_fact(engine, user, "Alice might switch from Python to Rust", "Rust",
                          temporal_intent="update", replaces_object="Python", epistemic_type=epistemic)
        nodes = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
        assert len(nodes) == 2
        assert all(n.lifecycle_state == LifecycleState.TENTATIVE for n in nodes)


async def test_fabricated_replacement_target_cannot_retire_existing_fact(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await ingest_fact(engine, user, "Alice uses Python", "Python")
        await ingest_fact(engine, user, "Alice now also uses Rust", "Rust",
                          temporal_intent="update", replaces_object="Python")
        nodes = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
        assert len(nodes) == 2
        assert all(n.lifecycle_state == LifecycleState.TENTATIVE for n in nodes)
        assert next(n for n in nodes if n.metadata["object"] == "Rust").metadata["replaces_object"] is None


async def test_negative_update_retires_same_known_positive_claim(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await ingest_fact(
            engine, user, "Alice uses Python", "Python", polarity="positive"
        )
        await ingest_fact(
            engine,
            user,
            "Alice no longer uses Python",
            "Python",
            polarity="negative",
            temporal_intent="update",
            replaces_object="Python",
        )
        nodes = await engine.query_nodes(
            user_id=user,
            node_type=NodeType.FACT,
            lifecycle_states=list(LifecycleState),
        )
        positive = next(node for node in nodes if node.metadata["polarity"] == "positive")
        negative = next(node for node in nodes if node.metadata["polarity"] == "negative")
        assert positive.lifecycle_state == LifecycleState.SUPERSEDED
        assert positive.superseded_by == negative.id
        assert negative.lifecycle_state == LifecycleState.TENTATIVE
