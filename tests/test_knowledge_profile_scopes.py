"""Legacy entity profiles must preserve the source namespace on every rebuild."""
import pytest

from prme import MemoryClient, MemoryEngine
from prme.types import NodeType, Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def seed(engine, user):
    for scope, label in ((Scope.PERSONAL, "private"), (Scope.PROJECT, "team")):
        for i in range(2):
            await engine.store(f"Aurora {label} note {i}", user_id=user, scope=scope)


async def profiles(engine, user):
    return [node for node in await engine.query_nodes(user_id=user, node_type=NodeType.SUMMARY)
            if (node.metadata or {}).get("entity_profile")]


async def test_profiles_keep_source_scope_in_graph_and_search(config, user):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        assert await engine.consolidate_knowledge(user_id=user, entity_names=["Aurora"]) == 2
        actual = await profiles(engine, user)
        assert {node.scope for node in actual} == {Scope.PERSONAL, Scope.PROJECT}
        for node in actual:
            own, other = ("private", "team") if node.scope == Scope.PERSONAL else ("team", "private")
            assert own in node.content and other not in node.content
            assert any(row["node_id"] == str(node.id) for row in await engine._vector_index.search(
                "Aurora", user, scope=[node.scope.value]))
            retrieved = await engine.retrieve("Aurora", user_id=user, scope=node.scope, include_cross_scope=False)
            assert str(node.id) in {str(candidate.node.id) for candidate in retrieved.results}
            assert other not in retrieved.bundle.render()
    async with MemoryEngine.open(config) as engine:
        assert {node.scope for node in await profiles(engine, user)} == {Scope.PERSONAL, Scope.PROJECT}


async def test_profile_rebuild_does_not_consume_prior_profile_as_source(config, user):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(user_id=user, entity_names=["Aurora"])
        before = {node.scope: node.content for node in await profiles(engine, user)}
        await engine.consolidate_knowledge(user_id=user, entity_names=["Aurora"])
        after = await profiles(engine, user)
        assert len(after) == 2
        assert {node.scope: node.content for node in after} == before
        assert all(node.content.count("[Knowledge Profile:") == 1 for node in after)


async def test_nodes_from_different_scopes_cannot_jointly_qualify_for_profile(config, user):
    async with MemoryEngine.open(config) as engine:
        await engine.store("Aurora private note", user_id=user, scope=Scope.PERSONAL)
        await engine.store("Aurora team note", user_id=user, scope=Scope.PROJECT)
        assert await engine.consolidate_knowledge(user_id=user, entity_names=["Aurora"]) == 0
        assert await profiles(engine, user) == []


async def test_explicit_scope_only_replaces_that_scopes_profile(config, user):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await seed(engine, user + "-other")
        for owner in (user, user + "-other"):
            await engine.consolidate_knowledge(user_id=owner, entity_names=["Aurora"])
        before = {str(node.id): node.model_dump_json() for owner in (user, user + "-other")
                  for node in await profiles(engine, owner) if owner != user or node.scope != Scope.PROJECT}
        assert await engine.consolidate_knowledge(user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]) == 1
        for node_id, snapshot in before.items():
            assert (await engine.get_node(node_id)).model_dump_json() == snapshot


def test_sync_profile_scope(config, user):
    with MemoryClient(config=config) as client:
        for i in range(2):
            client.store(f"Aurora team note {i}", user_id=user, scope=Scope.PROJECT)
        assert client.consolidate_knowledge(user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]) == 1
        assert all(node.scope == Scope.PROJECT for node in client.query_nodes(user_id=user))


async def test_legacy_mixed_profile_is_retired_when_its_scope_has_no_source_evidence(config, user):
    async with MemoryEngine.open(config) as engine:
        for i in range(2):
            await engine.store(f"Aurora team note {i}", user_id=user, scope=Scope.PROJECT)
        await engine.store("Aurora team legacy profile", user_id=user, scope=Scope.PERSONAL,
                           node_type=NodeType.SUMMARY, metadata={"entity_profile": True, "entity_name": "Aurora"})
        legacy = (await profiles(engine, user))[0]
        # Automatic discovery must also review old profile names, even though
        # the original name's source count no longer meets the discovery floor.
        await engine.consolidate_knowledge(user_id=user)
        assert all(node.scope != Scope.PERSONAL for node in await profiles(engine, user))
        old = await engine.get_node(str(legacy.id), include_superseded=True)
        assert old.lifecycle_state.value == "archived"
        assert not await engine._vector_index.search("Aurora", user, scope=[Scope.PERSONAL.value])
        assert not (await engine.retrieve("Aurora", user_id=user, scope=Scope.PERSONAL,
                                         include_cross_scope=False)).results
        assert len(await engine.query_nodes(user_id=user, scopes=[Scope.PROJECT])) == 2


async def test_profile_matching_requires_complete_entity_name(config, user):
    async with MemoryEngine.open(config) as engine:
        await engine.store("Joanna works on storage.", user_id=user)
        await engine.store("Joanna uses PostgreSQL.", user_id=user)
        assert await engine.consolidate_knowledge(user_id=user, entity_names=["Ann"]) == 0
        assert await profiles(engine, user) == []


async def test_profiles_keep_distinct_qualifiers_after_shared_prefix(config, user):
    prefix = "Aurora's production deployment policy for the customer environment, after the security review, "
    texts = [prefix + "allows external network access.", prefix + "does not allow external network access."]
    async with MemoryEngine.open(config) as engine:
        for text in texts:
            await engine.store(text, user_id=user)
        assert await engine.consolidate_knowledge(user_id=user, entity_names=["Aurora"], max_profile_tokens=1000) == 1
        node = (await profiles(engine, user))[0]
        assert all(text in node.content for text in texts)


async def test_profiles_preserve_source_provenance_and_inferred_status(config, user):
    from prme.types import EpistemicType, SourceType

    async with MemoryEngine.open(config) as engine:
        for i in range(2):
            await engine.store(f"Aurora might use option {i} if the trial succeeds.", user_id=user,
                               epistemic_type=EpistemicType.CONDITIONAL, source_type=SourceType.USER_STATED)
        sources = await engine.query_nodes(user_id=user)
        assert await engine.consolidate_knowledge(user_id=user, entity_names=["Aurora"], max_profile_tokens=1000) == 1
        node = (await profiles(engine, user))[0]
        assert node.epistemic_type == EpistemicType.INFERRED
        assert node.source_type == SourceType.SYSTEM_INFERRED
        assert node.confidence == pytest.approx(min(engine._confidence_matrix.lookup_with_fallback(
            EpistemicType.INFERRED, SourceType.SYSTEM_INFERRED), *(source.confidence for source in sources)))
        assert set(node.evidence_refs) == {event for source in sources for event in source.evidence_refs}
        assert set(node.metadata['source_node_ids']) == {str(source.id) for source in sources}
        for source in sources:
            assert str(source.id) in node.content and source.content in node.content
        assert 'epistemic=conditional' in node.content
        assert 'source_type=user_stated' in node.content


async def test_profile_budget_counts_full_multilingual_text(config, user):
    from prme.retrieval.tokenization import count_tokens

    async with MemoryEngine.open(config) as engine:
        await engine.store("Aurora " + "記憶管理" * 140, user_id=user)
        await engine.store("Aurora prefers blue.", user_id=user)
        assert await engine.consolidate_knowledge(user_id=user, entity_names=["Aurora"], max_profile_tokens=250) == 1
        node = (await profiles(engine, user))[0]
        assert count_tokens(node.content, config.packing.tokenizer) <= 250
        assert "Aurora prefers blue." in node.content
        assert "記憶管理" not in node.content
        assert len(node.metadata['source_node_ids']) == 1
        assert node.metadata['source_count_available'] == 2
        assert node.metadata['source_count_included'] == 1
        assert node.metadata['tokens_used'] == count_tokens(node.content, config.packing.tokenizer)


@pytest.mark.parametrize('kwargs', [
    {'max_profile_tokens': -1}, {'max_profile_tokens': True}, {'max_profile_tokens': 1.5},
    {'entity_names': ['']}, {'entity_names': ['  ']}, {'entity_names': 'Aurora'},
])
async def test_invalid_profile_options_fail_before_storage(config, user, kwargs, monkeypatch):
    from unittest.mock import AsyncMock

    async with MemoryEngine.open(config) as engine:
        query = AsyncMock(side_effect=AssertionError('Invalid input reached storage'))
        monkeypatch.setattr(engine._graph_store, 'query_nodes', query)
        with pytest.raises(ValueError):
            await engine.consolidate_knowledge(user_id=user, **kwargs)
        query.assert_not_awaited()
