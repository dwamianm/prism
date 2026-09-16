"""In-memory derivation planning; no graph or index publication (RFC-0016)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from prme.models import Event, MemoryEdge, MemoryNode
from prme.models.derivation import DerivationPlan, PreparedEmbedding, PreparedLexicalDocument
from prme.storage.embedding import EmbeddingProvider, encode_texts
from prme.storage.graph_store import GraphStore
from prme.types import ACTIVE_LIFECYCLE_STATES, EdgeType, NodeType, Scope


class PlanningGraph:
    """Scoped overlay used by entity matching and replacement detection.

    Durable reads are snapshotted. Only existing nodes actually referenced by
    the output become commit dependencies; scanning an unrelated entity does
    not make its future updates invalidate the plan.
    """

    def __init__(self, graph: GraphStore, event: Event):
        self.graph = graph
        self.event = Event.model_validate_json(event.model_dump_json())
        self.nodes: dict[UUID, MemoryNode] = {}
        self.edges: list[MemoryEdge] = []
        self.replacements: list[MemoryEdge] = []
        self._read: dict[UUID, MemoryNode] = {}
        self._scan_page: dict[UUID, MemoryNode] = {}
        self._retired: set[UUID] = set()

    def _snapshot(self, node: MemoryNode) -> MemoryNode:
        if (node.user_id, node.scope) != (self.event.user_id, self.event.scope):
            raise ValueError("Planning cannot cross the source owner or scope")
        return MemoryNode.model_validate_json(node.model_dump_json())

    async def create_node(self, node: MemoryNode) -> str:
        node = self._snapshot(node)
        if node.id in self.nodes or node.id in self._read:
            raise ValueError("Duplicate planned node identity")
        self.nodes[node.id] = node
        return str(node.id)

    async def create_edge(self, edge: MemoryEdge) -> str:
        if edge.user_id != self.event.user_id or edge.provenance_event_id != self.event.id:
            raise ValueError("Planned edge must belong to and cite its source")
        if edge.edge_type == EdgeType.SUPERSEDES:
            raise ValueError("Plan replacements explicitly")
        self.edges.append(MemoryEdge.model_validate_json(edge.model_dump_json()))
        return str(edge.id)

    async def get_node(self, node_id: str, *, include_superseded: bool = False) -> MemoryNode | None:
        key = UUID(node_id)
        if key in self._retired and not include_superseded:
            return None
        node = self.nodes.get(key) or self._read.get(key) or self._scan_page.get(key)
        if node is None:
            node = await self.graph.get_node(node_id, include_superseded=include_superseded)
            if node is None or (node.user_id, node.scope) != (self.event.user_id, self.event.scope):
                return None
        if key not in self.nodes:
            self._read.setdefault(key, self._snapshot(node))
            node = self._read[key]
        return node.model_copy(deep=True)

    async def scan_nodes(
        self, *, user_id: str, scope: Scope | None = None, node_type: NodeType | None = None,
        after_id: str | None = None, limit: int = 100,
    ) -> list[MemoryNode]:
        if (user_id, scope) != (self.event.user_id, self.event.scope):
            raise ValueError("Planning scans require the source owner and scope")
        if limit <= 0:
            return []
        # Fetch enough durable entries to fill a page even when earlier
        # replacements in this plan hide some of them.
        durable = await self.graph.scan_nodes(
            user_id=user_id, scope=scope, node_type=node_type,
            after_id=after_id, limit=limit + len(self._retired),
        )
        candidates = {node.id: self._read.get(node.id) or self._snapshot(node) for node in durable}
        candidates.update(self.nodes)
        result = [node for node in candidates.values()
                  if node.id not in self._retired and node.lifecycle_state in ACTIVE_LIFECYCLE_STATES
                  and (node_type is None or node.node_type == node_type)
                  and (after_id is None or str(node.id) > after_id)]
        page = sorted(result, key=lambda n: str(n.id))[:limit]
        self._scan_page = {node.id: node for node in page if node.id not in self.nodes}
        return [node.model_copy(deep=True) for node in page]

    async def get_edges(self, *, source_id: str) -> list[MemoryEdge]:
        if await self.get_node(source_id) is None:
            return []
        durable = await self.graph.get_edges(source_id=source_id)
        return [edge.model_copy(deep=True) for edge in durable + self.edges
                if str(edge.source_id) == source_id and edge.user_id == self.event.user_id]

    async def supersede(self, old_node_id: str, new_node_id: str, *, evidence_id: str | None = None) -> None:
        old, new = await self.get_node(old_node_id), await self.get_node(new_node_id)
        if old is None or new is None or new.id not in self.nodes or evidence_id != str(self.event.id):
            raise ValueError("Invalid planned replacement")
        # Late-arriving historical information is retained without retiring a
        # fact that became effective later. Arrival time is not authority.
        old_effective = old.event_time or old.valid_from
        new_effective = new.event_time or new.valid_from
        if new_effective < old_effective:
            return
        self.replacements.append(MemoryEdge(
            source_id=new.id, target_id=old.id, edge_type=EdgeType.SUPERSEDES,
            user_id=self.event.user_id, provenance_event_id=self.event.id,
        ))
        self._retired.add(old.id)

    async def contradict(self, node_a_id: str, node_b_id: str, *, evidence_id: str | None = None) -> None:
        # The source-grounded ingestion rules only request named replacements.
        # Reject unsupported state mutation instead of dropping it silently.
        raise ValueError("Contestation transitions are not part of this derivation policy")

    async def references(self) -> tuple[MemoryNode, ...]:
        used = {key for edge in self.edges + self.replacements
                for key in (edge.source_id, edge.target_id)} - self.nodes.keys()
        references = []
        for key in sorted(used, key=str):
            node = self._read.get(key)
            if node is None:
                node = await self.get_node(str(key))
            if node is None:
                raise ValueError("Planned edge refers to an unavailable node")
            references.append(node.model_copy(deep=True))
        return tuple(references)


class PlanningIndexes:
    """Collect index inputs; embedding inference runs once after graph planning."""

    def __init__(self, graph: PlanningGraph):
        self.graph = graph
        self.vectors: dict[UUID, str] = {}
        self.lexical: dict[UUID, PreparedLexicalDocument] = {}

    async def index(self, node_id: str, content: str, user_id: str,
                    node_type: str | None = None, scope: str | None = None) -> int:
        key = UUID(node_id)
        node = self.graph.nodes.get(key)
        if node is None or node.user_id != user_id:
            raise ValueError("Only new owned nodes may receive planned index writes")
        if node_type is None:
            if key in self.vectors:
                raise ValueError("Duplicate planned embedding")
            self.vectors[key] = content
        else:
            if (node_type, scope) != (node.node_type.value, node.scope.value) or key in self.lexical:
                raise ValueError("Invalid planned lexical identity")
            self.lexical[key] = PreparedLexicalDocument(node_id=key, content=content)
        return 0

    async def prepare(
        self,
        provider: EmbeddingProvider,
        *,
        materialization_policy: Literal[
            "temporal_validity_v7", "speech_act_v8", "speech_act_v9"
        ] = (
            "speech_act_v9"
        ),
    ) -> DerivationPlan:
        # Snapshot before provider I/O, including referenced existing nodes.
        nodes = tuple(node.model_copy(deep=True) for node in self.graph.nodes.values())
        references = await self.graph.references()
        inputs = tuple(self.vectors.items())
        if {key for key, _ in inputs} != {node.id for node in nodes}:
            raise ValueError("Every planned node requires an embedding input")
        identity = (provider.model_name, provider.model_version, provider.dimension)
        vectors = await encode_texts(provider, [content for _, content in inputs]) if inputs else []
        if len(vectors) != len(inputs):
            raise ValueError("Embedding provider must return one vector per planned input")
        if identity != (provider.model_name, provider.model_version, provider.dimension):
            raise ValueError("Embedding configuration changed during planning")
        event = self.graph.event
        return DerivationPlan(
            materialization_policy=materialization_policy,
            event_id=event.id, user_id=event.user_id, scope=event.scope, content_hash=event.content_hash,
            nodes=nodes, references=references, edges=tuple(self.graph.edges),
            replacements=tuple(self.graph.replacements),
            embeddings=tuple(PreparedEmbedding(node_id=key, content=content,
                             model=identity[0], version=identity[1], dimension=identity[2], values=values)
                             for (key, content), values in zip(inputs, vectors)),
            lexical_documents=tuple(self.lexical.values()),
        )


class PlanningQueue:
    """Run only in-memory planning adapters; never submit durable work."""

    async def submit(self, operation, *, label: str):
        return await operation()
