"""Graph reads given a user never step onto, return or name another user's node.

#438: the neighborhood walk passed through another user's node to reach the
user's own nodes. #443: a node's ``superseded_by`` could name another user's
node on node responses. #444: supersedence walks followed links into another
user's nodes. The rule now lives in both graph stores' own reads, and
``find_cross_user_links`` (``prme cross-user-links``) lists the links that
cross users so an operator can repair an old store.

Links that cross users come from older stores (an unscoped merge before the
merge path refused nodes of different users) or direct graph writes, which
is how these tests make them.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

import httpx
from mcp.shared.memory import create_connected_server_and_client_session

from prme import MemoryEngine
from prme.api.app import create_app
from prme.config import APIConfig, MCPConfig
from prme.models.derivation import node_checksum
from prme.mcp.server import create_mcp_server
from prme.models import MemoryEdge, MemoryNode
from prme.organizer.oscillation import OscillationDetector
from prme.retrieval.candidates import _generate_graph_candidates
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import QueryAnalysis, RetrievalCandidate
from prme.retrieval.replaced_sources import suppress_replaced_sources
from prme.types import EdgeType, EpistemicType, LifecycleState, NodeType, QueryIntent, RetrievalMode
from tests.test_durable_ingestion import config, user  # noqa: F401


def _postgres(settings) -> bool:
    return bool(settings.database_url)


def _json_out(text: str):
    """The JSON document a CLI command printed, without the log lines around it."""
    lines = text.splitlines()
    start = lines.index("{")
    return json.loads("\n".join(lines[start:lines.index("}", start) + 1]))


async def _nodes(engine, **specs) -> dict[str, MemoryNode]:
    nodes = {}
    for key, (content, owner, kind) in specs.items():
        nodes[key] = MemoryNode(content=content, user_id=owner, node_type=kind)
        await engine._graph_store.create_node(nodes[key])
    return nodes


async def _edge(engine, source, target, owner, edge_type=EdgeType.RELATES_TO, created_at=None):
    edge = MemoryEdge(source_id=source.id, target_id=target.id, edge_type=edge_type, user_id=owner,
                      **({} if created_at is None else {"created_at": created_at}))
    await engine._graph_store.create_edge(edge)
    return edge


async def _point(engine, node, successor):
    """Retire ``node`` in favor of ``successor`` the way an old unscoped merge did."""
    await engine._graph_store.update_node(
        str(node.id), superseded_by=str(successor.id), lifecycle_state=LifecycleState.SUPERSEDED,
    )


# --- Traversal (#438) ---------------------------------------------------------------------------


async def _bridged(engine, owner, other):
    """A1 -- B1 -- A2, where B1 is another user's node, and A1 -- A3 of the owner's own."""
    nodes = await _nodes(
        engine,
        acme=("Acme", owner, NodeType.ENTITY),
        bridge=("Acme owes Globex money", other, NodeType.FACT),
        far=("Globex ships widgets", owner, NodeType.FACT),
        near=("Acme makes anvils", owner, NodeType.FACT),
    )
    await _edge(engine, nodes["acme"], nodes["bridge"], owner)
    await _edge(engine, nodes["bridge"], nodes["far"], other)
    await _edge(engine, nodes["acme"], nodes["near"], owner)
    return nodes


async def test_the_walk_never_passes_through_another_users_node(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = await _bridged(engine, user, other)
        graph = engine._graph_store
        start = str(nodes["acme"].id)

        # Unscoped, the walk is the operator read it always was.
        unscoped = {str(n.id): d for n, d in await graph.get_neighborhood_with_depth(start, max_hops=2)}
        assert unscoped == {str(nodes["bridge"].id): 1, str(nodes["far"].id): 2, str(nodes["near"].id): 1}

        # Scoped, it neither returns the other user's node nor reaches A2 through it.
        scoped = await graph.get_neighborhood_with_depth(start, max_hops=2, user_id=user)
        assert [(str(n.id), d) for n, d in scoped] == [(str(nodes["near"].id), 1)]
        assert [n.id for n in await graph.get_neighborhood(start, max_hops=3, user_id=user)] == [nodes["near"].id]
        assert [n.id for n in await engine.get_neighborhood(start, user_id=user, max_hops=2)] == [nodes["near"].id]

        # A starting node another user owns has no neighborhood for this user.
        assert await graph.get_neighborhood_with_depth(str(nodes["bridge"].id), user_id=user) == []
        # The other user's own walk stops at the owner's nodes the same way.
        theirs = await graph.get_neighborhood_with_depth(str(nodes["bridge"].id), max_hops=3, user_id=other)
        assert theirs == []

        # Retrieval's graph expansion no longer reaches A2 at hop 2 through B1.
        candidates = await _generate_graph_candidates(
            QueryAnalysis(query="What about Acme?", intent=QueryIntent.SEMANTIC, entities=["Acme"],
                          retrieval_mode=RetrievalMode.DEFAULT),
            graph, user, PackingConfig(),
        )
        assert {str(c["node"].id) for c in candidates} == {str(nodes["near"].id)}


async def test_the_neighborhood_route_stops_at_another_users_node(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = await _bridged(engine, user, other)
        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get(f"/v1/nodes/{nodes['acme'].id}/neighborhood", params={"max_hops": 2})
            assert response.status_code == 200
            assert [n["id"] for n in response.json()["nodes"]] == [str(nodes["near"].id)]


async def test_a_walk_still_passes_an_edge_whose_node_was_deleted(config, user):  # noqa: F811
    """A deleted node belongs to nobody: the scoped walk reads a store without crossing links as before."""
    if _postgres(config):
        return  # PostgreSQL deletes a node's edges with it, so the walk has nothing to pass.
    async with MemoryEngine.open(config) as engine:
        nodes = await _nodes(
            engine,
            start=("Acme", user, NodeType.ENTITY),
            gone=("Deleted", user, NodeType.FACT),
            far=("Globex", user, NodeType.ENTITY),
        )
        await _edge(engine, nodes["start"], nodes["gone"], user)
        await _edge(engine, nodes["gone"], nodes["far"], user)
        await engine._graph_store.delete_node(str(nodes["gone"].id))
        graph = engine._graph_store
        start = str(nodes["start"].id)
        unscoped = [(n.id, d) for n, d in await graph.get_neighborhood_with_depth(start, max_hops=2)]
        assert unscoped == [(nodes["far"].id, 2)]
        assert [(n.id, d) for n, d in await graph.get_neighborhood_with_depth(
            start, max_hops=2, user_id=user)] == unscoped


# --- Supersedence chains (#437, #444) -----------------------------------------------------------


async def test_a_forked_chain_follows_the_users_branch(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = await _nodes(
            engine,
            old=("Ships on Monday", user, NodeType.FACT),
            foreign=("Ships on Friday", other, NodeType.FACT),
            mine=("Ships on Tuesday", user, NodeType.FACT),
            newest=("Ships on Wednesday", user, NodeType.FACT),
        )
        # The crossing link is written first, so an unscoped LIMIT 1 tends to pick it.
        await _edge(engine, nodes["foreign"], nodes["old"], other, EdgeType.SUPERSEDES)
        await _edge(engine, nodes["mine"], nodes["old"], user, EdgeType.SUPERSEDES)
        await _edge(engine, nodes["newest"], nodes["mine"], user, EdgeType.SUPERSEDES)
        graph = engine._graph_store
        old = str(nodes["old"].id)

        forward = await graph.get_supersedence_chain(old, direction="forward", user_id=user)
        assert [n.id for n in forward] == [nodes["mine"].id, nodes["newest"].id]
        assert [n.id for n in await engine.get_supersedence_chain(old, user_id=user)] == [
            nodes["mine"].id, nodes["newest"].id]
        backward = await graph.get_supersedence_chain(str(nodes["newest"].id), direction="backward", user_id=user)
        assert [n.id for n in backward] == [nodes["mine"].id, nodes["old"].id]

        # A chain that only continues through another user's node ends before it.
        tail = await _nodes(engine, theirs=("Ships on Saturday", other, NodeType.FACT))
        await _edge(engine, tail["theirs"], nodes["newest"], other, EdgeType.SUPERSEDES)
        assert [n.id for n in await graph.get_supersedence_chain(old, user_id=user)] == [
            nodes["mine"].id, nodes["newest"].id]
        # Another user's starting node has no chain for this user.
        assert await graph.get_supersedence_chain(str(nodes["foreign"].id), user_id=user) == []
        # Unscoped, the chain still follows a link that crosses users.
        unscoped = await graph.get_supersedence_chain(str(nodes["newest"].id))
        assert [n.id for n in unscoped] == [tail["theirs"].id]


async def test_a_fork_of_the_users_own_links_follows_the_newest_link(config, user):  # noqa: F811
    """As the admitted view of a protected store does (get_edges reads newest first)."""
    async with MemoryEngine.open(config) as engine:
        nodes = await _nodes(
            engine,
            old=("Ships on Monday", user, NodeType.FACT),
            earlier=("Ships on Tuesday", user, NodeType.FACT),
            later=("Ships on Thursday", user, NodeType.FACT),
        )
        at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # The newer link is written first, so the write order cannot decide.
        await _edge(engine, nodes["later"], nodes["old"], user, EdgeType.SUPERSEDES, at + timedelta(days=1))
        await _edge(engine, nodes["earlier"], nodes["old"], user, EdgeType.SUPERSEDES, at)
        for _ in range(3):
            chain = await engine._graph_store.get_supersedence_chain(str(nodes["old"].id), user_id=user)
            assert [n.id for n in chain] == [nodes["later"].id]


async def test_the_oscillation_check_reads_only_the_users_chain(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = await _nodes(
            engine,
            first=("I prefer dark mode for my editor", user, NodeType.PREFERENCE),
            foreign=("I switched to light mode for my editor", other, NodeType.PREFERENCE),
            latest=("I prefer dark mode for my editor", user, NodeType.PREFERENCE),
        )
        # latest replaced the other user's node, which replaced first: dark, light, dark.
        await _edge(engine, nodes["latest"], nodes["foreign"], user, EdgeType.SUPERSEDES)
        await _edge(engine, nodes["foreign"], nodes["first"], other, EdgeType.SUPERSEDES)
        detector = OscillationDetector()
        latest = str(nodes["latest"].id)
        assert await detector.detect_oscillations(engine._graph_store, latest)  # the unscoped read
        assert await detector.detect_oscillations(engine._graph_store, latest, user_id=user) == []

        before = (await engine.get_node(latest, include_superseded=True)).confidence_base
        await engine._check_oscillation(latest, user)
        assert (await engine.get_node(latest, include_superseded=True)).confidence_base == before


# --- superseded_by never names another user's node (#443) ---------------------------------------


async def _pointed(engine, owner, other):
    """The owner's claim retired in favor of another user's node, and one retired in favor of their own."""
    nodes = await _nodes(
        engine,
        crossed=("Lives in Austin", owner, NodeType.FACT),
        foreign=("Lives in Denver", other, NodeType.FACT),
        retired=("Works at Acme", owner, NodeType.FACT),
        current=("Works at Globex", owner, NodeType.FACT),
    )
    await _point(engine, nodes["crossed"], nodes["foreign"])
    await _point(engine, nodes["retired"], nodes["current"])
    return nodes


async def test_node_reads_for_a_user_never_name_another_users_node(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = await _pointed(engine, user, other)
        graph = engine._graph_store
        crossed, retired = str(nodes["crossed"].id), str(nodes["retired"].id)
        superseded = [LifecycleState.SUPERSEDED]

        # Unscoped reads return the stored pointer.
        assert (await graph.get_node(crossed, include_superseded=True)).superseded_by == nodes["foreign"].id
        assert (await engine.get_node(crossed, include_superseded=True)).superseded_by == nodes["foreign"].id

        # Every owner-scoped read clears it, and keeps the pointer to the user's own node.
        reads = {
            "get_node": [await graph.get_node(crossed, include_superseded=True, user_id=user),
                         await graph.get_node(retired, include_superseded=True, user_id=user)],
            "engine.get_node": [await engine.get_node(crossed, include_superseded=True, user_id=user),
                                await engine.get_node(retired, include_superseded=True, user_id=user)],
            "get_nodes": await graph.get_nodes([crossed, retired], include_superseded=True, user_id=user),
            "engine.get_nodes": await engine.get_nodes([crossed, retired], include_superseded=True, user_id=user),
            "query_nodes": await graph.query_nodes(user_id=user, lifecycle_states=superseded),
            "engine.query_nodes": await engine.query_nodes(user_id=user, lifecycle_states=superseded),
            "scan_nodes": await graph.scan_nodes(user_id=user, lifecycle_states=superseded),
            "engine.scan_nodes": await engine.scan_nodes(user_id=user, lifecycle_states=superseded),
        }
        for name, found in reads.items():
            by_id = {str(n.id): n.superseded_by for n in found}
            assert by_id == {crossed: None, retired: nodes["current"].id}, name

        # A condition evaluated on the crossed node returns it without the pointer.
        conditional = MemoryNode(content="Lives in Austin if the job moves", user_id=user,
                                 node_type=NodeType.FACT, epistemic_type=EpistemicType.CONDITIONAL,
                                 metadata={"condition": "the job moves"})
        await graph.create_node(conditional)
        await _point(engine, conditional, nodes["foreign"])
        evaluated = await engine.evaluate_condition(str(conditional.id), "true", user_id=user)
        assert evaluated.id == conditional.id and evaluated.superseded_by is None

        # The nodes a neighborhood returns clear it too.
        await _edge(engine, nodes["current"], nodes["crossed"], user)
        near = await graph.get_neighborhood(str(nodes["current"].id), max_hops=1,
                                            include_superseded=True, user_id=user)
        assert [(n.id, n.superseded_by) for n in near] == [(nodes["crossed"].id, None)]

        # The other user's node reads as not found for this user.
        assert await graph.get_node(str(nodes["foreign"].id), user_id=user) is None
        assert await graph.get_nodes([str(nodes["foreign"].id)], user_id=user) == []
        # The chain read for the user starts at the crossed node and has nowhere to go.
        assert await engine.get_supersedence_chain(crossed, user_id=user) == []


async def test_event_nodes_never_name_another_users_node(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.ingest_fast("Lives in Austin", user_id=user)
        source = await engine.get_event(event_id)
        claim = MemoryNode(content="Lives in Austin", user_id=user, node_type=NodeType.FACT,
                           evidence_refs=[source.id])
        foreign = MemoryNode(content="Lives in Denver", user_id=other, node_type=NodeType.FACT)
        for node in (claim, foreign):
            await engine._graph_store.create_node(node)
        await _point(engine, claim, foreign)
        found = {n.id: n for n in await engine._graph_store.get_event_nodes(event_id, user_id=user)}
        assert found[claim.id].superseded_by is None


async def test_an_active_node_with_a_pointer_reads_as_stored(config, user):  # noqa: F811
    """Only a direct write leaves an active node with a pointer; planning compares such reads with the row."""
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = await _nodes(
            engine,
            active=("Lives in Austin", user, NodeType.FACT),
            foreign=("Lives in Denver", other, NodeType.FACT),
        )
        await engine._graph_store.update_node(str(nodes["active"].id), superseded_by=str(nodes["foreign"].id))
        graph = engine._graph_store
        raw = await graph.get_node(str(nodes["active"].id))
        scanned = [n for n in await graph.scan_nodes(user_id=user) if n.id == nodes["active"].id]
        assert raw.superseded_by == nodes["foreign"].id
        assert [node_checksum(n) for n in scanned] == [node_checksum(raw)]
        if not _postgres(config):  # the shared PostgreSQL database lists other tests' links too
            links = await engine.find_cross_user_links()
            assert [p.node_id for p in links.pointers] == [str(nodes["active"].id)]


async def test_the_api_node_responses_never_name_another_users_node(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = await _pointed(engine, user, other)
        # A chain of the user's own that lists the crossed node.
        await _edge(engine, nodes["current"], nodes["crossed"], user, EdgeType.SUPERSEDES)
        settings = config.model_copy(update={"api": APIConfig(user_keys={user: "owner-key"})})
        app = create_app(settings)
        app.state.engine = engine
        headers = {"Authorization": "Bearer owner-key"}
        foreign = str(nodes["foreign"].id)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            for path, params in (
                (f"/v1/nodes/{nodes['crossed'].id}", {}),
                ("/v1/nodes", {"state": "superseded"}),
                ("/v1/nodes/scan", {"state": "superseded"}),
                (f"/v1/nodes/{nodes['current'].id}/chain", {"direction": "backward"}),
            ):
                response = await client.get(path, params=params, headers=headers)
                assert response.status_code == 200, (path, response.text)
                assert foreign not in response.text, path
                assert str(nodes["crossed"].id) in response.text, path
            response = await client.get(f"/v1/nodes/{nodes['retired'].id}", headers=headers)
            assert response.json()["superseded_by"] == str(nodes["current"].id)


async def test_mcp_and_cli_node_output_never_name_another_users_node(config, user, capsys):  # noqa: F811
    from prme.cli import cmd_chain, cmd_node, cmd_nodes

    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = await _pointed(engine, user, other)
    crossed, foreign = str(nodes["crossed"].id), str(nodes["foreign"].id)

    settings = config.model_copy(update={"mcp": MCPConfig(user_id=user)})
    server = create_mcp_server(settings)
    async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as session:
        await session.initialize()
        result = await session.call_tool("memory_get_node", {"node_id": crossed})
        node = json.loads(result.content[0].text)
        assert node["id"] == crossed and node["superseded_by"] is None
        result = await session.call_tool("memory_scan_nodes", {"lifecycle_states": ["superseded"]})
        assert crossed in result.content[0].text and foreign not in result.content[0].text

    if _postgres(config):
        return  # The CLI opens directory and single-file packs.
    for command, extra in ((cmd_node, {"node_id": crossed}), (cmd_chain, {"node_id": crossed}),
                           (cmd_nodes, {"type": None, "state": "superseded", "limit": 100})):
        await command(argparse.Namespace(db_path=config.db_path, format="json", user_id=user, **extra))
        assert foreign not in capsys.readouterr().out, command.__name__


async def test_replaced_source_suppression_ignores_a_pointer_to_another_users_node(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        note = MemoryNode(content="Jo's birthday is March 3.", user_id=user, node_type=NodeType.NOTE)
        await engine._graph_store.create_node(note)
        claim = MemoryNode(content="Jo's birthday is March 3.", user_id=user, node_type=NodeType.FACT,
                           evidence_refs=[note.id])
        note_with_source = note.model_copy(update={"evidence_refs": [note.id]})
        foreign = MemoryNode(content="Jo's birthday is March 13.", user_id=other, node_type=NodeType.FACT)
        for node in (claim, foreign):
            await engine._graph_store.create_node(node)
        await _point(engine, claim, foreign)
        kept, excluded = await suppress_replaced_sources(
            [RetrievalCandidate(node=note_with_source, composite_score=1.0, paths=["VECTOR"], path_count=1)],
            graph_store=engine._graph_store, user_id=user,
        )
        assert [c.node.id for c in kept] == [note.id] and excluded == []


# --- Finding links that cross users (#438) ------------------------------------------------------


async def test_find_cross_user_links_counts_and_lists_them(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        before = await engine.find_cross_user_links()
        nodes = await _bridged(engine, user, other)
        pointed = await _pointed(engine, user, other)
        # An edge between the owner's nodes that another user owns crosses too.
        odd = await _edge(engine, nodes["acme"], nodes["far"], other)

        links = await engine.find_cross_user_links(limit=1000)
        if _postgres(config):
            # The shared PostgreSQL test database holds other tests' crossing
            # links, which parallel workers may be writing too.
            assert links.edge_count >= before.edge_count + 3
            assert links.pointer_count >= before.pointer_count + 1
            return
        assert links.edge_count == before.edge_count + 3
        assert links.pointer_count == before.pointer_count + 1
        mine = [e for e in links.edges if user in (e.owner, e.source_owner, e.target_owner)]
        assert {(e.source_id, e.target_id) for e in mine} == {
            (str(nodes["acme"].id), str(nodes["bridge"].id)),
            (str(nodes["bridge"].id), str(nodes["far"].id)),
            (str(nodes["acme"].id), str(nodes["far"].id)),
        }
        assert next(e for e in mine if e.edge_id == str(odd.id)).owner == other
        assert [(p.node_id, p.owner, p.superseded_by, p.successor_owner)
                for p in links.pointers if p.owner == user] == [
            (str(pointed["crossed"].id), user, str(pointed["foreign"].id), other)]

        limited = await engine.find_cross_user_links(limit=1)
        assert (limited.edge_count, limited.pointer_count) == (links.edge_count, links.pointer_count)
        assert len(limited.edges) == 1 and len(limited.pointers) == 1


async def test_a_store_without_crossing_links_reports_none(config, user):  # noqa: F811
    if _postgres(config):
        return  # The shared PostgreSQL test database holds other tests' crossing links.
    async with MemoryEngine.open(config) as engine:
        nodes = await _nodes(engine, a=("Acme", user, NodeType.ENTITY), b=("Globex", user, NodeType.ENTITY))
        await _edge(engine, nodes["a"], nodes["b"], user)
        await _point(engine, nodes["a"], nodes["b"])
        links = await engine.find_cross_user_links()
        assert (links.edge_count, links.pointer_count, links.edges, links.pointers) == (0, 0, [], [])


async def test_the_cli_reports_crossing_links(config, user, capsys):  # noqa: F811
    from prme.cli import build_parser, cmd_cross_user_links

    if _postgres(config):
        return  # The CLI opens directory and single-file packs.
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = await _bridged(engine, user, other)
        pointed = await _pointed(engine, user, other)

    args = build_parser().parse_args(["cross-user-links", config.db_path, "--format", "json", "--limit", "1"])
    await cmd_cross_user_links(args)
    report = _json_out(capsys.readouterr().out)
    assert (report["edge_count"], report["pointer_count"]) == (2, 1)
    assert len(report["edges"]) == 1
    assert report["pointers"] == [{
        "node_id": str(pointed["crossed"].id), "owner": user,
        "superseded_by": str(pointed["foreign"].id), "successor_owner": other,
    }]

    await cmd_cross_user_links(argparse.Namespace(db_path=config.db_path, format="table", limit=100))
    table = capsys.readouterr().out
    assert "Edges that cross users:    2" in table
    assert str(nodes["bridge"].id) in table
    assert "Pointers that cross users: 1" in table
