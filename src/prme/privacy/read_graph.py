"""The admitted-only graph view used by every protected candidate branch."""

from __future__ import annotations

from collections import defaultdict

from prme.ingestion.admitted_statements import project_admitted_edge
from prme.privacy.policy import PrivacyDenied
from prme.types import ACTIVE_LIFECYCLE_STATES, EdgeType, NodeType, Scope


class AdmittedGraphView:
    """Read-only facade. Unknown backend methods are deliberately not forwarded."""

    protected_reads = True

    def __init__(self, graph, context):
        self._graph = graph
        self.context = context

    def _owner(self, owner):
        if owner not in (None, self.context.user_id):
            raise PrivacyDenied("privacy_boundary_mismatch")
        return self.context.user_id

    async def get_node(self, node_id, *, include_superseded=False):
        await self.context.check()
        node = await self._graph.get_node(node_id, include_superseded=include_superseded)
        projected = await self.context.project_node(node)
        return await self.context.disclose(projected)

    async def get_nodes(self, node_ids, *, include_superseded=False):
        await self.context.check()
        result = []
        for node in await self._graph.get_nodes(node_ids, include_superseded=include_superseded):
            projected = await self.context.project_node(node)
            if projected is not None:
                result.append(projected)
        return await self.context.disclose(result)

    async def get_evidence_nodes(self, event_ids):
        """Selected runtime evidence is reachable only from evidence packing."""
        if self.context.source_access is None:
            return []
        if not self.context.evidence_lease_active:
            raise PrivacyDenied("privacy_denied")
        return await self.context.disclose([
            self.context.source_nodes[key].model_copy(deep=True)
            for key in dict.fromkeys(event_ids) if key in self.context.source_nodes
        ])

    async def scan_nodes(self, *, user_id=None, scope=None, node_type=None, lifecycle_states=None,
                         after_id=None, limit=100, operator_unscoped=False):
        owner = self._owner(user_id)
        if operator_unscoped:
            raise PrivacyDenied("privacy_boundary_mismatch")
        if limit <= 0:
            return []
        result = []
        cursor = after_id
        # Filtering never makes a short page look like end-of-history.
        while len(result) < limit:
            page_size = min(500, max(100, limit - len(result)))
            page = await self._graph.scan_nodes(
                user_id=owner, scope=scope, node_type=node_type, lifecycle_states=lifecycle_states,
                after_id=cursor, limit=page_size,
            )
            for node in page:
                projected = await self.context.project_node(node)
                if projected is not None:
                    result.append(projected)
                if len(result) == limit:
                    break
            if len(result) == limit or len(page) < page_size:
                break
            next_cursor = str(page[-1].id)
            if next_cursor == cursor:
                raise PrivacyDenied("privacy_invalid_result")
            cursor = next_cursor
        return await self.context.disclose(result)

    async def _all_nodes(self, *, scope=None, node_type=None, lifecycle_states=None):
        cursor = None
        while True:
            page = await self.scan_nodes(
                user_id=self.context.user_id, scope=scope, node_type=node_type,
                lifecycle_states=lifecycle_states, after_id=cursor, limit=500,
            )
            yield page
            if len(page) < 500:
                break
            cursor = str(page[-1].id)

    async def query_nodes(self, *, user_id=None, node_type=None, scope=None, scopes=None,
                          session_ids=None, lifecycle_states=None, valid_at=None,
                          min_confidence=None, min_salience=None, content_contains_any=None,
                          created_before=None, oldest_first=False, limit=100):
        self._owner(user_id)
        if scope is not None and scopes is not None:
            raise ValueError("scope and scopes are mutually exclusive")
        if limit <= 0:
            return []
        matches = []
        async for page in self._all_nodes(scope=scope, node_type=node_type, lifecycle_states=lifecycle_states):
            for node in page:
                if scopes is not None and node.scope not in {Scope(item) for item in scopes}:
                    continue
                if session_ids is not None and node.session_id not in session_ids:
                    continue
                if valid_at is not None and (node.valid_from > valid_at
                                            or (node.valid_to is not None and node.valid_to <= valid_at)):
                    continue
                if min_confidence is not None and node.confidence < min_confidence:
                    continue
                if min_salience is not None and node.salience < min_salience:
                    continue
                if created_before is not None and node.created_at > created_before:
                    continue
                if content_contains_any is not None and not any(
                    value.casefold() in node.content.casefold() for value in content_contains_any
                ):
                    continue
                matches.append(node)
        matches.sort(key=lambda item: item.created_at, reverse=not oldest_first)
        return await self.context.disclose(matches[:limit])

    async def count_nodes(self, *, user_id=None, lifecycle_states=None):
        self._owner(user_id)
        count = 0
        async for page in self._all_nodes(lifecycle_states=lifecycle_states):
            count += len(page)
        return await self.context.disclose(count)

    async def get_event_nodes(self, event_id, *, user_id):
        self._owner(user_id)
        if await self.context.record_for_event(event_id) is None:
            return []
        result = []
        for node in await self._graph.get_event_nodes(event_id, user_id=user_id):
            admitted = await self.context.project_node(node)
            if admitted is not None:
                result.append(admitted)
        return await self.context.disclose(result)

    async def get_edges(self, *, source_id=None, target_id=None, node_ids=None, edge_type=None,
                        valid_at=None, min_confidence=None, include_superseded=False):
        if source_id is None and target_id is None and node_ids is None:
            raise PrivacyDenied("privacy_unsupported_input")
        await self.context.check()
        edges = await self._graph.get_edges(
            source_id=source_id, target_id=target_id, node_ids=node_ids,
            edge_type=edge_type, valid_at=valid_at, min_confidence=min_confidence,
        )
        ids = list({str(identifier) for edge in edges for identifier in (edge.source_id, edge.target_id)})
        endpoints = {node.id: node for node in await self.get_nodes(ids, include_superseded=include_superseded)}
        admitted = []
        for edge in edges:
            source, target = endpoints.get(edge.source_id), endpoints.get(edge.target_id)
            if (edge.user_id != self.context.user_id or source is None or target is None
                    or source.scope != target.scope):
                continue
            if edge.provenance_event_id is None:
                continue
            origin = await self.context.record_for_event(str(edge.provenance_event_id))
            if origin is None or origin.snapshot.policy.scope != source.scope:
                continue
            admitted.append(project_admitted_edge(edge))
        return await self.context.disclose(admitted)

    async def get_neighborhood_with_depth(self, node_id, *, max_hops=2, edge_types=None,
                                          valid_at=None, min_confidence=None,
                                          include_superseded=False, include_unverified_aliases=False):
        seed = await self.get_node(node_id, include_superseded=include_superseded)
        if seed is None:
            return []
        from prme.retrieval.candidates import _unverified_alias
        found = {str(seed.id): (seed, 0)}
        frontier = {str(seed.id)}
        for depth in range(1, min(max_hops, 3) + 1):
            if not frontier:
                break
            edges = await self.get_edges(
                node_ids=sorted(frontier), valid_at=valid_at, min_confidence=min_confidence,
                include_superseded=include_superseded,
            )
            next_ids = set()
            for edge in edges:
                if edge_types is not None and edge.edge_type not in edge_types:
                    continue
                if not include_unverified_aliases and _unverified_alias(edge):
                    continue
                next_ids.update((str(edge.source_id), str(edge.target_id)))
            next_ids.difference_update(found)
            nodes = await self.get_nodes(sorted(next_ids), include_superseded=include_superseded)
            frontier = set()
            for node in nodes:
                if node.scope != seed.scope:
                    continue
                if valid_at is not None and node.node_type not in {NodeType.ENTITY, NodeType.PREFERENCE}:
                    if node.valid_from > valid_at or (node.valid_to is not None and node.valid_to <= valid_at):
                        continue
                found[str(node.id)] = (node, depth)
                frontier.add(str(node.id))
        return await self.context.disclose([value for key, value in found.items() if key != str(seed.id)])

    async def get_neighborhood(self, node_id, **kwargs):
        return [node for node, _ in await self.get_neighborhood_with_depth(node_id, **kwargs)]

    async def get_supersedence_chain(self, node_id, *, direction="forward"):
        if direction not in {"forward", "backward"}:
            raise ValueError("direction must be forward or backward")
        seed = await self.get_node(node_id, include_superseded=True)
        if seed is None:
            return []
        seen, result = {str(seed.id)}, []
        current = str(seed.id)
        for _ in range(100):
            selector = {"target_id": current} if direction == "forward" else {"source_id": current}
            edges = await self.get_edges(**selector, edge_type=EdgeType.SUPERSEDES, include_superseded=True)
            if not edges:
                break
            selected = edges[0]
            identifier = str(selected.source_id if direction == "forward" else selected.target_id)
            if identifier in seen:
                break
            node = await self.get_node(identifier, include_superseded=True)
            if node is None or node.scope != seed.scope:
                break
            result.append(node)
            seen.add(identifier)
            current = identifier
        return await self.context.disclose(result)

    async def get_session_neighbors(self, trigger_ids, *, user_id, window, scopes=None):
        self._owner(user_id)
        triggers = await self.get_nodes(trigger_ids)
        partitions = defaultdict(list)
        sessions = {node.session_id for node in triggers if node.session_id}
        async for page in self._all_nodes(lifecycle_states=list(ACTIVE_LIFECYCLE_STATES)):
            for node in page:
                if node.session_id in sessions and (scopes is None or node.scope in scopes):
                    partitions[node.scope, node.session_id].append(node)
        for values in partitions.values():
            values.sort(key=lambda node: (node.created_at, str(node.id)))
        result = {}
        for trigger in triggers:
            values = partitions[trigger.scope, trigger.session_id]
            position = next((i for i, node in enumerate(values) if node.id == trigger.id), None)
            result[str(trigger.id)] = [] if position is None else values[
                max(0, position - window):position + window + 1
            ]
        return await self.context.disclose(result)
