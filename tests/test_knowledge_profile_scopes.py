"""Legacy entity profiles must preserve the source namespace on every rebuild."""
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
