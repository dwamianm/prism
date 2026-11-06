"""Retrieval leaves out a profile that quotes a retired memory (issue #462).

``consolidate_knowledge()`` writes the selected sources' text into the profile
and names them in ``source_node_ids``. Archiving, superseding or deleting one
of those records retires only the record: the profile node stays active and
kept reaching the packed context. The filter reads the profile's source list on
the retrieval path and leaves the profile out until it is rebuilt.
"""

import pytest

from prme import MemoryEngine
from prme.types import NodeType, RetrievalMode, Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

BUDGET = 2000

AURORA = (
    "Aurora deploys from main only after approval.",
    "Aurora keeps nightly backups for 30 days.",
)
BEACON = (
    "Beacon deploys from release branches after approval.",
    "Beacon keeps nightly backups for 90 days.",
)


async def build_profiles(engine, owner):
    """Publish one Aurora and one Beacon profile and return their source ids."""
    sources = {"Aurora": {}, "Beacon": {}}
    for name, texts in (("Aurora", AURORA), ("Beacon", BEACON)):
        for text in texts:
            event_id = await engine.store(text, user_id=owner, scope=Scope.PERSONAL)
            sources[name][text] = str(
                (await engine.get_event_nodes(event_id, user_id=owner))[0].id
            )
    assert await engine.consolidate_knowledge(
        user_id=owner, scope=Scope.PERSONAL,
        entity_names=["Aurora", "Beacon"], max_profile_tokens=BUDGET,
    ) == 2
    profiles = {
        node.metadata["entity_name"]: node
        for node in await engine.query_nodes(
            user_id=owner, scope=Scope.PERSONAL, node_type=NodeType.SUMMARY,
        )
        if (node.metadata or {}).get("entity_profile")
    }
    assert set(profiles) == {"Aurora", "Beacon"}
    return profiles, sources


async def retrieve(engine, owner, query, mode=RetrievalMode.DEFAULT):
    return await engine.retrieve(
        query, user_id=owner, scope=Scope.PERSONAL,
        include_cross_scope=False, retrieval_mode=mode,
    )


def result_ids(response):
    return {str(candidate.node.id) for candidate in response.results}


async def test_a_profile_with_only_active_sources_is_still_returned(config, user):
    async with MemoryEngine.open(config) as engine:
        profiles, _ = await build_profiles(engine, user)
        for name, query in (("Aurora", "Aurora deployment"), ("Beacon", "Beacon deployment")):
            response = await retrieve(engine, user, query)
            assert str(profiles[name].id) in result_ids(response), name


@pytest.mark.parametrize("retire", ["archive", "supersede"])
async def test_a_retired_quoted_memory_removes_only_its_own_profile(config, user, retire):
    async with MemoryEngine.open(config) as engine:
        profiles, sources = await build_profiles(engine, user)
        aurora, beacon = profiles["Aurora"], profiles["Beacon"]
        old = sources["Aurora"][AURORA[0]]
        assert str(old) in aurora.metadata["source_node_ids"]
        assert str(old) not in beacon.metadata["source_node_ids"]
        assert AURORA[0] in aurora.content
        assert str(aurora.id) in result_ids(await retrieve(engine, user, "Aurora deployment"))

        if retire == "archive":
            await engine.archive(old, user_id=user)
        else:
            event_id = await engine.store(
                "Aurora now deploys from release branches.",
                user_id=user, scope=Scope.PERSONAL,
            )
            replacement = str((await engine.get_event_nodes(event_id, user_id=user))[0].id)
            await engine.supersede(old, replacement, user_id=user)

        response = await retrieve(engine, user, "Aurora deployment and backups")
        assert str(aurora.id) not in result_ids(response)
        assert AURORA[0] not in response.bundle.render()
        # A profile whose own sources are active is unaffected.
        response = await retrieve(engine, user, "Beacon deployment and backups")
        assert str(beacon.id) in result_ids(response)


async def test_a_deleted_quoted_memory_removes_its_profile(config, user):
    async with MemoryEngine.open(config) as engine:
        profiles, sources = await build_profiles(engine, user)
        aurora, beacon = profiles["Aurora"], profiles["Beacon"]
        await engine._graph_store.delete_node(str(sources["Aurora"][AURORA[0]]))

        response = await retrieve(engine, user, "Aurora deployment and backups")
        assert str(aurora.id) not in result_ids(response)
        assert AURORA[0] not in response.bundle.render()
        response = await retrieve(engine, user, "Beacon deployment and backups")
        assert str(beacon.id) in result_ids(response)


async def test_explicit_retrieval_keeps_a_profile_whose_source_is_retired(config, user):
    async with MemoryEngine.open(config) as engine:
        profiles, sources = await build_profiles(engine, user)
        await engine.archive(sources["Aurora"][AURORA[0]], user_id=user)

        response = await retrieve(engine, user, "Aurora deployment", mode=RetrievalMode.EXPLICIT)
        assert str(profiles["Aurora"].id) in result_ids(response)


async def test_explicit_retrieval_drops_a_profile_whose_source_was_deleted(config, user):
    async with MemoryEngine.open(config) as engine:
        profiles, sources = await build_profiles(engine, user)
        await engine._graph_store.delete_node(str(sources["Aurora"][AURORA[0]]))

        response = await retrieve(engine, user, "Aurora deployment", mode=RetrievalMode.EXPLICIT)
        assert str(profiles["Aurora"].id) not in result_ids(response)


async def test_a_rebuilt_profile_returns_once_its_sources_are_active(config, user):
    """The retired profile is left out until a rebuild publishes a fresh one."""
    async with MemoryEngine.open(config) as engine:
        profiles, sources = await build_profiles(engine, user)
        old_profile = profiles["Aurora"]
        await engine.archive(sources["Aurora"][AURORA[0]], user_id=user)
        assert str(old_profile.id) not in result_ids(await retrieve(engine, user, "Aurora deployment"))

        await engine.store(
            "Aurora alerts when a nightly backup fails.", user_id=user, scope=Scope.PERSONAL,
        )
        assert await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PERSONAL, entity_names=["Aurora"], max_profile_tokens=BUDGET,
        ) == 1
        rebuilt = [
            node for node in await engine.query_nodes(
                user_id=user, scope=Scope.PERSONAL, node_type=NodeType.SUMMARY,
            )
            if (node.metadata or {}).get("entity_profile")
            and node.metadata.get("entity_name") == "Aurora"
        ]
        assert len(rebuilt) == 1 and rebuilt[0].id != old_profile.id
        response = await retrieve(engine, user, "Aurora deployment and backups")
        assert str(rebuilt[0].id) in result_ids(response)
        assert AURORA[0] not in response.bundle.render()


async def test_a_profile_without_a_source_list_is_left_out(config, user):
    """A profile written before profile format version 2 cannot be checked."""
    async with MemoryEngine.open(config) as engine:
        await engine.store(
            "Aurora deploy policy from an older release.",
            user_id=user, scope=Scope.PERSONAL, node_type=NodeType.SUMMARY,
            metadata={"entity_profile": True, "entity_name": "Aurora"},
        )
        for mode in (RetrievalMode.DEFAULT, RetrievalMode.EXPLICIT):
            response = await retrieve(engine, user, "Aurora deploy policy", mode=mode)
            assert not any(
                (candidate.node.metadata or {}).get("entity_profile")
                for candidate in response.results
            ), mode
            assert any(
                item.reason.startswith("profile_sources_unknown")
                for item in response.excluded
            ), mode


def test_unverified_profiles_are_left_out_when_the_source_read_fails():
    from uuid import uuid4

    from prme.models.nodes import MemoryNode
    from prme.retrieval.models import RetrievalCandidate
    from prme.retrieval.profile_sources import exclude_unverified_profiles

    profile = RetrievalCandidate(node=MemoryNode(
        user_id="owner", node_type=NodeType.SUMMARY, scope=Scope.PERSONAL,
        content="[Knowledge Profile: Aurora] Aurora deploys from main.",
        metadata={"entity_profile": True, "entity_name": "Aurora",
                  "source_node_ids": [str(uuid4())]},
    ), paths=["VECTOR"], path_count=1)
    note = RetrievalCandidate(node=MemoryNode(
        user_id="owner", node_type=NodeType.NOTE, scope=Scope.PERSONAL,
        content="Aurora deploys from main.",
    ), paths=["VECTOR"], path_count=1)

    kept, excluded = exclude_unverified_profiles([profile, note])
    assert kept == [note]
    assert [item.reason for item in excluded] == ["profile_sources_unverified"]


async def test_a_suppressed_owner_profile_is_not_recorded_in_the_receipt(config, user):
    from prme.retrieval.owner_profile import OWNER_PROFILE_PATH

    on_config = config.model_copy(update={"enable_owner_profile_context": True})
    question = "Do you remember my deployment policy?"
    async with MemoryEngine.open(on_config) as engine:
        _, sources = await build_profiles(engine, user)

        async def ask(limit=None):
            response = await engine.retrieve(
                question, user_id=user, scope=Scope.PERSONAL,
                include_cross_scope=False, speaker="Aurora", limit=limit,
            )
            receipt = await engine.get_retrieval_receipt(
                str(response.metadata.request_id), user_id=user,
            )
            return response, receipt

        response, receipt = await ask()
        # The owner profile is placed after the top result.
        assert receipt.execution.features.get("owner_profile_context") == {"version": 1}
        assert any(OWNER_PROFILE_PATH in c.paths for c in response.results)

        # The result limit removes the placed profile too, so the receipt drops
        # the feature and the count there as well.
        limited, limited_receipt = await ask(limit=1)
        assert not any(OWNER_PROFILE_PATH in c.paths for c in limited.results)
        assert "owner_profile_context" not in limited_receipt.execution.features
        assert "OWNER_PROFILE" not in limited.metadata.candidates_generated

        await engine.archive(sources["Aurora"][AURORA[0]], user_id=user)
        response, receipt = await ask()
        assert not any(OWNER_PROFILE_PATH in c.paths for c in response.results)
        assert "owner_profile_context" not in receipt.execution.features
        assert "OWNER_PROFILE" not in response.metadata.candidates_generated


async def test_a_failed_source_read_leaves_profiles_out(config, user, monkeypatch):
    """The wrapper fails closed instead of serving a profile it could not check."""
    async with MemoryEngine.open(config) as engine:
        profiles, _ = await build_profiles(engine, user)
        original = engine._graph_store.get_nodes

        async def failing(node_ids, *, include_superseded=False, user_id=None):
            if include_superseded:
                raise RuntimeError("source read failed")
            return await original(node_ids, include_superseded=include_superseded, user_id=user_id)

        monkeypatch.setattr(engine._graph_store, "get_nodes", failing)
        response = await retrieve(engine, user, "Aurora deployment and backups")
        assert not any(
            str(candidate.node.id) == str(profiles["Aurora"].id)
            for candidate in response.results
        )
        assert any(
            item.reason == "profile_sources_unverified" for item in response.excluded
        )


async def test_cross_scope_hints_leave_out_a_profile_with_a_retired_source(config, user):
    wider = config.model_copy(update={
        "packing": config.packing.model_copy(update={"cross_scope_top_n": 10}),
    })
    async with MemoryEngine.open(wider) as engine:
        profiles, sources = await build_profiles(engine, user)
        await engine.store(
            "Aurora project deployment notes.", user_id=user, scope=Scope.PROJECT,
        )

        async def hints():
            response = await engine.retrieve(
                "Aurora deployment", user_id=user, scope=Scope.PROJECT,
                include_cross_scope=True,
            )
            return {str(candidate.node.id) for candidate in response.cross_scope_hints}

        # The profile is a hint while its quoted records are active.
        assert str(profiles["Aurora"].id) in await hints()
        await engine.archive(sources["Aurora"][AURORA[0]], user_id=user)
        assert str(profiles["Aurora"].id) not in await hints()
