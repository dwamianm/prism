"""An organizer aggregate must not block a later extraction (dwamianm/prism#556).

``summarize`` and ``consolidate`` publish SUMMARY nodes whose ``evidence_refs``
copy the evidence of their sources, including the event id of a source whose
extraction is still pending. The extraction's legacy guard read every node that
mentions the event and mistook that aggregate for an unjournaled derivation, so
the extraction failed with ``MaterializationError`` on every retry for as long
as the aggregate stayed in the graph.
"""

from unittest.mock import AsyncMock
from uuid import UUID

from prme import MemoryEngine
from prme.ingestion.schema import ExtractedEntity, ExtractionResult
from prme.models.nodes import MemoryNode
from prme.types import NodeType, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401

# Far enough apart that only the consolidation test's cluster forms.
DISTINCT = [
    "The kettle boiled at nine",
    "Quarterly tax filing is due",
    "Swallows migrate south",
    "Sourdough starter needs feeding",
    "The lighthouse keeper works nights",
]

# The four sentences the default embedding provider clusters at the pinned
# consolidation threshold (see tests/test_organizer_incremental.py).
CLUSTER = [
    "Python is widely used for machine learning",
    "Python is a popular language for ML projects",
    "Python is commonly used in machine learning applications",
    "Python is the top choice for ML development",
]


def _recorded_extraction(*_args, **_kwargs):
    """A recorded-shaped extraction result: one entity, no claims."""
    return ExtractionResult(
        entities=[ExtractedEntity(name="Alex", entity_type="person")],
        facts=[], relationships=[],
    )


def _recorded_batch(turns, **_kwargs):
    """The batched form of the same recorded result, with a grounded entity."""
    return {"turns": [
        {"turn": turn.index,
         "entities": [{"name": max(turn.content.split(), key=len), "entity_type": "topic"}],
         "facts": [], "relationships": []}
        for turn in turns
    ]}


def _stall_the_extraction_provider(engine):
    """Queue extractions and answer them from the fake, never from a model.

    The suite's autouse fixture sets one call per turn, which extracts during
    ``store()``. The bug needs the extraction to still be queued when organize
    runs, so the engine is built with batching (see ``_pending_engine``) and
    its flush timer is parked: only ``process_extractions()`` drains the queue.
    """
    engine._pipeline._batch_flush_seconds = 10_000.0
    provider = engine._pipeline._extraction_provider
    provider.extract = AsyncMock(side_effect=_recorded_extraction)
    provider.extract_batch = AsyncMock(side_effect=_recorded_batch)


def _pending_engine(base):
    """The same store with batched extraction, so a turn stays queued."""
    return base.model_copy(update={"extraction_batch_target_tokens": 2048})


async def _store_pending(engine, contents, owner):
    """Store each memory with extraction queued and nothing drained yet."""
    event_ids = []
    for content in contents:
        receipt = await engine.store_with_receipt(
            content, user_id=owner, node_type=NodeType.FACT,
            scope=Scope.PERSONAL, extract=True,
        )
        event_ids.append(str(receipt.event_id))
    return event_ids


async def _assert_all_complete(engine, event_ids, owner):
    drained = await engine.process_extractions(user_id=owner, limit=100, budget_ms=30_000)
    assert drained.pending == 0
    for event_id in event_ids:
        # The derivation receipt is the published plan, so it, not the source
        # node's processing status, says the extraction materialized.
        receipt = await engine._event_store.get_derivation_receipt(event_id, user_id=owner)
        assert receipt is not None, event_id


async def test_a_summary_over_pending_extractions_does_not_block_them(config, user):  # noqa: F811
    async with MemoryEngine.open(_pending_engine(config)) as engine:
        _stall_the_extraction_provider(engine)
        event_ids = await _store_pending(engine, DISTINCT, user)

        organized = await engine.organize(
            user_id=user, jobs=["summarize"],
            budget_ms=30_000,
        )
        # The daily summary covers all five pending notes, so its evidence_refs
        # carry each of their event ids.
        assert organized.per_job["summarize"].nodes_modified == 1

        await _assert_all_complete(engine, event_ids, user)


async def test_a_consolidation_over_pending_extractions_does_not_block_them(config, user):  # noqa: F811
    pinned = config.model_copy(update={"organizer": config.organizer.model_copy(update={
        "consolidation_min_cluster_size": 3,
        "consolidation_similarity_threshold": 0.70,
    })})
    async with MemoryEngine.open(_pending_engine(pinned)) as engine:
        _stall_the_extraction_provider(engine)
        event_ids = await _store_pending(engine, CLUSTER, user)

        organized = await engine.organize(user_id=user, jobs=["consolidate"])
        assert organized.per_job["consolidate"].nodes_modified == 1

        await _assert_all_complete(engine, event_ids, user)


async def test_an_unjournaled_derived_node_still_blocks_the_extraction(config, user):  # noqa: F811
    """The guard keeps catching what it was written for (dwamianm/prism#556)."""
    async with MemoryEngine.open(_pending_engine(config)) as engine:
        _stall_the_extraction_provider(engine)
        [event_id] = await _store_pending(engine, ["The kettle boiled at nine"], user)
        # A non-SUMMARY node that names the event but has no journaled plan is
        # the legacy shape: an unjournaled derivation, not organizer provenance.
        await engine._graph_store.create_node(MemoryNode(
            user_id=user, node_type=NodeType.FACT, scope=Scope.PERSONAL,
            content="a claim no derivation plan records",
            evidence_refs=[UUID(event_id)],
        ))

        drained = await engine.process_extractions(user_id=user, limit=10, budget_ms=30_000)
        assert drained.pending == 1
        receipt = await engine._event_store.get_derivation_receipt(event_id, user_id=user)
        assert receipt is None
        status = await engine.extraction_status(event_id, user_id=user)
        assert status is not None and status.status != "complete"
