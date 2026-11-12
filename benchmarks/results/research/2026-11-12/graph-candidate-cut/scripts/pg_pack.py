"""Task 24 bar 3: a small pack on PostgreSQL gives the same ranked ids before and after.

Run once with ``build`` (any tree), then ``read`` once per tree with that tree's
``src`` first on PYTHONPATH. Prints one JSON line per question: ranked ids with
scores and the graph candidate count. No model calls: extraction is off.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import prme
from prme.config import OrganizerConfig, PRMEConfig
from prme.models import MemoryEdge
from prme.storage.engine import MemoryEngine
from prme.types import EdgeType, NodeType, Scope

URL = os.environ["TASK24_PG_URL"]
USER = "pg-pack-user"
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
PEOPLE = ["Alice Moreno", "Ben Okafor", "Chloe Park", "Dmitri Volkov", "Elena Ruiz", "Farid Haddad"]
PLACES = ["Lisbon", "Kyoto", "Denver", "Oslo"]
QUESTIONS = [
    "What does Alice Moreno like to cook?",
    "Where did Ben Okafor travel?",
    "Who does Chloe Park work with?",
    "What did Dmitri Volkov and Elena Ruiz plan in Oslo?",
    "Tell me about Farid Haddad and Kyoto",
    "Which people visited Lisbon?",
    "What happened in Denver?",
    "What does Elena Ruiz prefer?",
]


def config() -> PRMEConfig:
    return PRMEConfig(database_url=URL, organizer=OrganizerConfig(opportunistic_enabled=False))


async def build() -> None:
    engine = await MemoryEngine.create(config())
    try:
        ids = {}
        for i, name in enumerate(PEOPLE + PLACES):
            event = await engine.store(name, user_id=USER, node_type=NodeType.ENTITY, scope=Scope.PERSONAL,
                                       event_time=T0 + timedelta(minutes=i), extract=False)
            ids[name] = await node_of(engine, name)
        facts = []
        for i in range(48):
            person = PEOPLE[i % len(PEOPLE)]
            place = PLACES[(i * 7) % len(PLACES)]
            text = [f"{person} likes to cook dish number {i} at home.",
                    f"{person} traveled to {place} in week {i}.",
                    f"{person} works with {PEOPLE[(i + 1) % len(PEOPLE)]} on project {i}.",
                    f"{person} planned trip {i} to {place} with {PEOPLE[(i + 2) % len(PEOPLE)]}."][i % 4]
            kind = NodeType.PREFERENCE if i % 4 == 0 else NodeType.FACT
            event = await engine.store(text, user_id=USER, node_type=kind, scope=Scope.PERSONAL,
                                       confidence=[.5, .7, .9][i % 3],
                                       event_time=T0 + timedelta(hours=i), extract=False)
            node = await node_of(engine, text)
            facts.append(node)
            for name in (person, place, PEOPLE[(i + 1) % len(PEOPLE)]):
                if name in text:
                    await engine._graph_store.create_edge(MemoryEdge(
                        source_id=ids[name], target_id=node, edge_type=EdgeType.HAS_FACT, user_id=USER))
        for a, b in zip(PEOPLE, PEOPLE[1:]):
            await engine._graph_store.create_edge(MemoryEdge(
                source_id=ids[a], target_id=ids[b], edge_type=EdgeType.RELATES_TO, user_id=USER))
        print(json.dumps({"entities": len(ids), "facts": len(facts)}))
    finally:
        await engine.close()


async def node_of(engine, text):
    nodes = await engine._graph_store.query_nodes(user_id=USER, content_equals_any=[text], limit=5)
    if len(nodes) != 1:
        raise SystemExit(f"expected one node for {text!r}, got {len(nodes)}")
    return nodes[0].id


async def read() -> None:
    print(json.dumps({"prme": prme.__file__}), file=sys.stderr)
    engine = await MemoryEngine.create(config())
    try:
        for question in QUESTIONS:
            response = await engine.retrieve(question, user_id=USER, reference_time=datetime(2026, 11, 5, tzinfo=timezone.utc))
            ranked = [(str(r.node.id), r.composite_score, r.graph_proximity, r.paths) for r in response.results]
            print(json.dumps({"q": question, "ranked": ranked,
                              "generated": response.metadata.candidates_generated}, default=str))
    finally:
        await engine.close()


if __name__ == "__main__":
    asyncio.run({"build": build, "read": read}[sys.argv[1]]())
