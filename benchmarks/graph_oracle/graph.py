"""Write a gold graph into the graph store and read its claims back.

The graph store already holds what the gold graph needs, so nothing new is
added to the engine. An entity is an entity node. A claim between two entities is
one edge (``part_of`` for containment, ``relates_to`` for the rest) whose metadata
holds the relation label, polarity, status, role, time, the inferred flag and
the evidence memory ids: the *direct* shape. The *claim node* shape writes each
claim as a fact node instead (``has_fact`` from the subject, ``mentions`` to the
object, ``derived_from`` to each memory, ``supersedes`` between claims). A
literal claim (``has``) is always a claim node. A memory has ``mentions``
edges to every entity it names. Every edge carries a ``shape`` tag so one
store holds both shapes and a strategy picks one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from benchmarks.graph_oracle.gold import Claim, Gold, mentions
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.types import EdgeType, NodeType, Scope

DIRECT, CLAIM = "direct", "claim"
CONTAINMENT = {"located_in", "part_of"}


@dataclass
class Handles:
    """The node ids a written graph uses, both ways."""

    entity: dict[str, str] = field(default_factory=dict)   # gold entity id -> node id
    memory: dict[str, str] = field(default_factory=dict)   # gold memory id -> node id
    claim: dict[str, str] = field(default_factory=dict)    # gold claim id -> claim node id
    names: dict[str, tuple[str, str]] = field(default_factory=dict)  # node id -> (kind, gold id)

    def add(self, kind: str, gold_id: str, node_id: str) -> None:
        getattr(self, kind)[gold_id] = node_id
        self.names[node_id] = (kind, gold_id)


@dataclass(frozen=True)
class Edge:
    """A claim as a strategy reads it, whichever shape stored it."""

    id: str
    s: str
    r: str
    o: str | None
    attr: str
    value: str
    evidence: tuple[str, ...]
    polarity: str
    status: str
    role: str
    time: str
    inferred: bool
    shape: str  # direct, claim or both (a literal)

    @property
    def literal(self) -> bool:
        return self.o is None


@dataclass
class GraphData:
    """Everything a pack's gold graph says, decoded from its edges."""

    edges: list[Edge]
    mentions: dict[str, set[str]]                   # memory id -> entities it names
    named_in: dict[str, set[str]] = field(default_factory=dict)  # entity id -> memories that name it
    touching: dict[str, list[Edge]] = field(default_factory=dict)  # entity id -> claims on it

    def __post_init__(self) -> None:
        for memory_id, entities in self.mentions.items():
            for entity_id in entities:
                self.named_in.setdefault(entity_id, set()).add(memory_id)
        for edge in self.edges:
            self.touching.setdefault(edge.s, []).append(edge)
            if edge.o is not None:
                self.touching.setdefault(edge.o, []).append(edge)


def _node(owner: str, node_type: NodeType, content: str, metadata: dict) -> MemoryNode:
    return MemoryNode(user_id=owner, node_type=node_type, content=content, scope=Scope.PERSONAL, metadata=metadata,
                      confidence=0.9)


def _meta(claim: Claim, shape: str, evidence: list[str]) -> dict:
    meta = {"shape": shape, "relation": claim.r, "claim": claim.id, "polarity": claim.polarity, "status": claim.status,
            "role": claim.role, "time": claim.time, "inferred": claim.inferred, "evidence": evidence}
    if claim.attr:
        meta.update(attr=claim.attr, value=claim.value)
    if claim.supersedes:
        meta["supersedes"] = claim.supersedes
    return meta


async def write_graph(engine, gold: Gold, owner: str, memory_nodes: dict[str, str], *, shapes=(DIRECT, CLAIM)) -> Handles:
    """Write the gold graph for ``owner`` over memories already stored (``memory_nodes``: memory id -> node id)."""
    store = engine._graph_store
    handles = Handles()
    for memory_id, node_id in memory_nodes.items():
        handles.add("memory", memory_id, node_id)

    async def link(source: str, target: str, edge_type: EdgeType, meta: dict, confidence: float = 0.9) -> None:
        await store.create_edge(MemoryEdge(source_id=UUID(source), target_id=UUID(target), edge_type=edge_type,
                                           user_id=owner, confidence=confidence, metadata=meta))

    for entity in gold.entities.values():
        node = _node(owner, NodeType.ENTITY, entity.names[0], {
            "gold_id": entity.id, "entity_type": entity.type, "subtype": entity.subtype, "names": list(entity.names),
            "owner": entity.id == gold.owner_entity})
        handles.add("entity", entity.id, await store.create_node(node))
    for memory in gold.memories:
        for entity_id in mentions(gold, memory.text):
            await link(handles.memory[memory.id], handles.entity[entity_id], EdgeType.MENTIONS, {"shape": "both"})
    for claim in gold.claims:
        evidence = [handles.memory[memory_id] for memory_id in claim.ev]
        confidence = 0.6 if claim.inferred else 0.9
        if claim.o is not None and DIRECT in shapes:
            edge_type = EdgeType.PART_OF if claim.r in CONTAINMENT else EdgeType.RELATES_TO
            await link(handles.entity[claim.s], handles.entity[claim.o], edge_type, _meta(claim, DIRECT, evidence),
                       confidence)
        if claim.o is None or CLAIM in shapes:
            shape = "both" if claim.o is None else CLAIM
            text = (f"{claim.s} {claim.r} {claim.o}" if claim.o is not None else f"{claim.s} {claim.attr}: {claim.value}")
            node_id = await store.create_node(_node(owner, NodeType.FACT, text, {"gold_claim": claim.id}))
            handles.add("claim", claim.id, node_id)
            await link(handles.entity[claim.s], node_id, EdgeType.HAS_FACT, _meta(claim, shape, evidence), confidence)
            if claim.o is not None:
                await link(node_id, handles.entity[claim.o], EdgeType.MENTIONS, {"shape": CLAIM, "claim": claim.id})
            for memory_node in evidence:
                await link(node_id, memory_node, EdgeType.DERIVED_FROM, {"shape": shape})
    for claim in gold.claims:
        if claim.supersedes and claim.o is not None and CLAIM in shapes:
            await link(handles.claim[claim.id], handles.claim[claim.supersedes], EdgeType.SUPERSEDES, {"shape": CLAIM})
    return handles


def _decode_edge(edge, handles: Handles, shape: str, evidence: list[str] | None = None) -> Edge:
    meta = edge.metadata
    names = handles.names
    return Edge(
        meta["claim"], names[str(edge.source_id)][1], meta["relation"],
        None if meta["relation"] == "has" else names[str(edge.target_id)][1] if shape == DIRECT else None,
        meta.get("attr", ""), meta.get("value", ""),
        tuple(names[node_id][1] for node_id in (meta["evidence"] if evidence is None else evidence)),
        meta["polarity"], meta["status"], meta["role"], meta["time"], meta["inferred"], shape)


def decode(edges, handles: Handles) -> GraphData:
    """Decode a graph's edges into claims and mentions. Both shapes decode to the same claims."""
    decoded: list[Edge] = []
    claim_nodes: dict[str, dict] = {}
    seen_mentions: dict[str, set[str]] = {}
    for edge in edges:
        meta = edge.metadata or {}
        source, target = str(edge.source_id), str(edge.target_id)
        if meta.get("shape") == DIRECT:
            decoded.append(_decode_edge(edge, handles, DIRECT))
        elif edge.edge_type == EdgeType.HAS_FACT:
            if meta["shape"] == "both":
                decoded.append(_decode_edge(edge, handles, "both"))
            else:
                claim_nodes[target] = {"edge": edge, "o": None, "evidence": []}
        elif edge.edge_type == EdgeType.MENTIONS and handles.names[source][0] == "memory":
            seen_mentions.setdefault(handles.names[source][1], set()).add(handles.names[target][1])
    for edge in edges:
        source, target = str(edge.source_id), str(edge.target_id)
        if source in claim_nodes:
            if edge.edge_type == EdgeType.MENTIONS:
                claim_nodes[source]["o"] = handles.names[target][1]
            elif edge.edge_type == EdgeType.DERIVED_FROM:
                claim_nodes[source]["evidence"].append(target)
    for node in claim_nodes.values():
        meta = node["edge"].metadata
        decoded.append(Edge(meta["claim"], handles.names[str(node["edge"].source_id)][1], meta["relation"], node["o"],
                            "", "", tuple(handles.names[target][1] for target in node["evidence"]), meta["polarity"],
                            meta["status"], meta["role"], meta["time"], meta["inferred"], CLAIM))
    return GraphData(decoded, seen_mentions)


async def load_graph(engine, owner: str, handles: Handles) -> GraphData:
    """Read every edge of the owner's graph once and decode it."""
    edges = await engine._graph_store.get_edges(node_ids=list(handles.names))
    return decode([edge for edge in edges if edge.user_id == owner], handles)


class StoreSource:
    """Reads claims one frontier at a time from the graph store, for the direct shape.

    A strategy that walks the graph with this source pays for the reads a
    product would make: one ``get_edges`` call per hop, over every entity of
    the frontier, which returns the claims on those entities and the memories
    that name them. ``calls`` and ``edges_read`` count that cost.
    """

    def __init__(self, engine, owner: str, handles: Handles):
        self._store, self._owner, self._handles = engine._graph_store, owner, handles
        self.calls = 0
        self.edges_read = 0

    async def touching(self, entity_ids: list[str]) -> tuple[list[Edge], dict[str, set[str]]]:
        nodes = [self._handles.entity[entity_id] for entity_id in entity_ids]
        edges = [edge for edge in await self._store.get_edges(node_ids=nodes) if edge.user_id == self._owner]
        self.calls += 1
        self.edges_read += len(edges)
        claims: list[Edge] = []
        named: dict[str, set[str]] = {}
        for edge in edges:
            meta = edge.metadata or {}
            if meta.get("shape") in (DIRECT, "both") and meta.get("relation"):
                claims.append(_decode_edge(edge, self._handles, DIRECT if meta["shape"] == DIRECT else "both"))
            elif edge.edge_type == EdgeType.MENTIONS and self._handles.names[str(edge.source_id)][0] == "memory":
                named.setdefault(self._handles.names[str(edge.target_id)][1], set()).add(
                    self._handles.names[str(edge.source_id)][1])
        return claims, named


class MemorySource:
    """The same reads from a decoded graph held in memory, for the claim node shape and for sweeps."""

    def __init__(self, data: GraphData, shape: str = DIRECT, hidden: frozenset = frozenset()):
        self._data, self._shape, self._hidden = data, shape, hidden
        self.calls = 0
        self.edges_read = 0

    async def touching(self, entity_ids: list[str]) -> tuple[list[Edge], dict[str, set[str]]]:
        self.calls += 1
        claims = {edge.id: edge for entity_id in entity_ids for edge in self._data.touching.get(entity_id, ())
                  if edge.shape in (self._shape, "both") and edge.id not in self._hidden}
        named = {entity_id: set(self._data.named_in.get(entity_id, ())) for entity_id in entity_ids}
        self.edges_read += len(claims) + sum(len(memories) for memories in named.values())
        return list(claims.values()), named


def leaf_intent(data: GraphData) -> frozenset:
    """The claims a parent claim and containment already imply: "Alex visits Tokyo" when "Alex visits Japan" and "Tokyo is in Japan" hold."""
    wholes: dict[str, set[str]] = {}
    for edge in data.edges:
        if edge.r == "located_in" and edge.o:
            wholes.setdefault(edge.s, set()).add(edge.o)
    visits = {(edge.s, edge.o) for edge in data.edges if edge.r == "visits" and edge.polarity == "positive"}
    return frozenset(edge.id for edge in data.edges if edge.r == "visits" and edge.polarity == "positive"
                     and any((edge.s, whole) in visits for whole in wholes.get(edge.o, ())))
