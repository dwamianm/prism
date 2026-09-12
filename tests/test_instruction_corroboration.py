"""Similarity is not corroborating evidence for an instruction."""
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.types import EpistemicType, NodeType, Scope, SourceType
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

RULE = "Always use Python for data analysis."


@pytest.mark.parametrize("remention_enabled", [False, True])
@pytest.mark.parametrize("change", [
    {"content": "Never use Python for data analysis."},
    {"scope": Scope.PROJECT},
    {"node_type": NodeType.NOTE},
    {"epistemic_type": EpistemicType.CONDITIONAL},
    {"source_type": SourceType.SYSTEM_INFERRED},
    {"role": "assistant"},
])
async def test_similar_message_cannot_corroborate_an_unrelated_or_untrusted_instruction(config, user, monkeypatch, change, remention_enabled):
    config.reinforce_similarity_threshold = .5 if remention_enabled else None
    async with MemoryEngine.open(config) as engine:
        eid = await engine.store(RULE, user_id=user, node_type=NodeType.INSTRUCTION)
        original = (await engine.get_event_nodes(eid, user_id=user))[0]
        monkeypatch.setattr(engine._vector_index, "search", AsyncMock(return_value=[{"node_id": str(original.id), "score": 1.0}]))
        kwargs = {"content": RULE, "user_id": user, "node_type": NodeType.INSTRUCTION,
                  "scope": Scope.PERSONAL, "epistemic_type": EpistemicType.ASSERTED,
                  "source_type": SourceType.USER_STATED, "role": "user", **change}
        await engine.store(**kwargs)
        current = await engine.get_node(str(original.id), user_id=user)
        assert current.evidence_refs == original.evidence_refs
        assert current.confidence_base == original.confidence_base
        assert current.reinforcement_boost == original.reinforcement_boost


@pytest.mark.parametrize("remention_enabled", [False, True])
async def test_exact_explicit_instruction_repeat_records_corroborating_source(config, user, monkeypatch, remention_enabled):
    config.reinforce_similarity_threshold = .5 if remention_enabled else None
    async with MemoryEngine.open(config) as engine:
        eid = await engine.store(RULE, user_id=user, node_type=NodeType.INSTRUCTION)
        original = (await engine.get_event_nodes(eid, user_id=user))[0]
        monkeypatch.setattr(engine._vector_index, "search", AsyncMock(return_value=[{"node_id": str(original.id), "score": 1.0}]))
        repeated = await engine.store(RULE, user_id=user, node_type=NodeType.INSTRUCTION)
        current = await engine.get_node(str(original.id), user_id=user)
        assert current.reinforcement_boost == pytest.approx(original.reinforcement_boost + .15)
        assert str(current.evidence_refs[-1]) == repeated
        assert len(current.evidence_refs) == len(original.evidence_refs) + 1


async def test_ordinary_source_does_not_pay_for_instruction_similarity_search(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        search = AsyncMock(return_value=[])
        monkeypatch.setattr(engine._vector_index, "search", search)
        await engine.store("A routine source observation", user_id=user, node_type=NodeType.NOTE)
        search.assert_not_awaited()


@pytest.mark.parametrize("old_fields", [
    {"epistemic_type": EpistemicType.HYPOTHETICAL},
    {"source_type": SourceType.SYSTEM_INFERRED},
    {"event_time": "future"},
])
async def test_repetition_does_not_confirm_a_speculative_or_later_instruction(config, user, monkeypatch, old_fields):
    from datetime import datetime, timezone
    old_fields = dict(old_fields)
    if old_fields.get("event_time") == "future":
        old_fields["event_time"] = datetime(2099, 1, 1, tzinfo=timezone.utc)
    async with MemoryEngine.open(config) as engine:
        eid = await engine.store(RULE, user_id=user, node_type=NodeType.INSTRUCTION, **old_fields)
        original = (await engine.get_event_nodes(eid, user_id=user))[0]
        monkeypatch.setattr(engine._vector_index, "search", AsyncMock(return_value=[{"node_id": str(original.id), "score": 1.0}]))
        await engine.store(RULE, user_id=user, node_type=NodeType.INSTRUCTION)
        current = await engine.get_node(str(original.id), user_id=user)
        assert current.evidence_refs == original.evidence_refs
        assert current.confidence_base == original.confidence_base
        assert current.reinforcement_boost == original.reinforcement_boost


async def test_opt_in_remention_stays_in_the_source_scope(config, user, monkeypatch):
    config.reinforce_similarity_threshold = .5
    async with MemoryEngine.open(config) as engine:
        eid = await engine.store("Project Atlas uses Python", user_id=user, node_type=NodeType.FACT, scope=Scope.PROJECT)
        original = (await engine.get_event_nodes(eid, user_id=user))[0]
        monkeypatch.setattr(engine._vector_index, "search", AsyncMock(return_value=[{"node_id": str(original.id), "score": 1.0}]))
        await engine.store("Project Atlas uses Python", user_id=user, scope=Scope.PERSONAL)
        current = await engine.get_node(str(original.id), user_id=user)
        assert current.evidence_refs == original.evidence_refs
        assert current.confidence_base == original.confidence_base
