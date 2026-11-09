"""Archived entity profiles can be left out of a node listing (issue #432).

Every profile rebuild archives the previous SUMMARY node and keeps its
metadata, so a newest-first read across lifecycle states becomes a run of
retired profiles. The opt-in filter applies inside the query, before the
limit and the count, and the default read is unchanged.
"""

import httpx

from prme import MemoryEngine
from prme.api.app import create_app
from prme.models.nodes import MemoryNode
from prme.types import LifecycleState, NodeType, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401

ENTITY = "Maya Okafor"
ALL_STATES = [("state", state.value) for state in LifecycleState]


def _is_profile(node) -> bool:
    metadata = node.get("metadata") if isinstance(node, dict) else node.metadata
    return (metadata or {}).get("entity_profile") is True


async def _publish_six_profiles(engine, owner) -> list[str]:
    """Publish one profile, then rebuild it five times with a new memory."""
    memories = [
        f"{ENTITY} likes green tea",
        f"{ENTITY} lives in Lisbon",
    ]
    for content in memories:
        await engine.store(content, user_id=owner)
    assert await engine.consolidate_knowledge(
        user_id=owner, scope=Scope.PERSONAL, entity_names=[ENTITY],
    ) == 1
    for index in range(5):
        content = f"{ENTITY} keeps {index} plants"
        memories.append(content)
        await engine.store(content, user_id=owner)
        assert await engine.consolidate_knowledge(
            user_id=owner, scope=Scope.PERSONAL, entity_names=[ENTITY],
        ) == 1
    return memories


async def test_listing_and_counting_leave_archived_profiles_out(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        memories = await _publish_six_profiles(engine, user)
        archived_fact = MemoryNode(
            user_id=user, node_type=NodeType.FACT,
            content=f"{ENTITY} once lived in Porto",
            lifecycle_state=LifecycleState.ARCHIVED,
        )
        await engine._graph_store.create_node(archived_fact)
        states = list(LifecycleState)

        unfiltered = await engine.query_nodes(
            user_id=user, lifecycle_states=states, limit=1000,
        )
        filtered = await engine.query_nodes(
            user_id=user, lifecycle_states=states,
            exclude_archived_profiles=True, limit=1000,
        )

        assert len([n for n in unfiltered if _is_profile(n)]) == 6
        assert len([n for n in filtered if _is_profile(n)]) == 1
        assert len(unfiltered) == len(filtered) + 5
        assert set(memories) | {archived_fact.content} <= {
            n.content for n in filtered
        }

        assert await engine.count_nodes(
            user_id=user, lifecycle_states=states, exclude_archived_profiles=True,
        ) == len(filtered)
        assert await engine.count_nodes(
            user_id=user, lifecycle_states=states,
        ) == len(unfiltered)

        # The filter is applied before the limit: a post-limit filter would
        # return a short page because archived profiles crowd the newest rows.
        newest = await engine.query_nodes(
            user_id=user, lifecycle_states=states, limit=5,
        )
        assert any(n.lifecycle_state is LifecycleState.ARCHIVED for n in newest)
        page = await engine.query_nodes(
            user_id=user, lifecycle_states=states,
            exclude_archived_profiles=True, limit=5,
        )
        assert len(page) == 5
        assert not any(
            _is_profile(n) and n.lifecycle_state is LifecycleState.ARCHIVED
            for n in page
        )

        default = await engine.query_nodes(user_id=user, limit=1000)
        explicit_off = await engine.query_nodes(
            user_id=user, exclude_archived_profiles=False, limit=1000,
        )
        assert [n.model_dump_json() for n in default] == [
            n.model_dump_json() for n in explicit_off
        ]
        assert await engine.count_nodes(user_id=user) == await engine.count_nodes(
            user_id=user, exclude_archived_profiles=False,
        )


async def test_http_listing_and_count_accept_the_filter(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await _publish_six_profiles(engine, user)
        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test",
        ) as client:
            listing = [("user_id", user), ("limit", "1000"), *ALL_STATES]
            unfiltered = await client.get("/v1/nodes", params=listing)
            filtered = await client.get(
                "/v1/nodes",
                params=[*listing, ("exclude_archived_profiles", "true")],
            )
            assert unfiltered.status_code == 200
            assert filtered.status_code == 200
            assert len([
                n for n in unfiltered.json()["nodes"] if _is_profile(n)
            ]) == 6
            assert len([
                n for n in filtered.json()["nodes"] if _is_profile(n)
            ]) == 1
            assert filtered.json()["count"] == len(filtered.json()["nodes"])

            plain_stats = await client.get(
                "/v1/stats", params=[("user_id", user), *ALL_STATES],
            )
            filtered_stats = await client.get(
                "/v1/stats",
                params=[
                    ("user_id", user), *ALL_STATES,
                    ("exclude_archived_profiles", "true"),
                ],
            )
            assert plain_stats.status_code == 200
            assert filtered_stats.status_code == 200
            assert (
                filtered_stats.json()["node_count"]
                == plain_stats.json()["node_count"] - 5
            )

            invalid = await client.get("/v1/nodes", params={"state": "bogus"})
            assert invalid.status_code == 422
