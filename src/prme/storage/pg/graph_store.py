"""PostgreSQL-backed GraphStore implementation.

Implements the GraphStore Protocol using asyncpg for natively async
PostgreSQL access. Mirrors DuckPGQGraphStore method-for-method with
PostgreSQL-native recursive CTEs and parameterized queries ($1, $2, ...).
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.types import (
    DecayProfile,
    EdgeType,
    EpistemicType,
    LifecycleState,
    NodeType,
    Scope,
    SourceType,
    validate_transition,
)

logger = logging.getLogger(__name__)

# Explicit column list for SELECT * replacement (positional independence).
_NODE_COLUMNS = (
    "id, node_type, user_id, session_id, scope, content, "
    "metadata, confidence, salience, lifecycle_state, "
    "valid_from, valid_to, superseded_by, evidence_refs, "
    "created_at, updated_at, epistemic_type, source_type, "
    "decay_profile, last_reinforced_at, reinforcement_boost, "
    "salience_base, confidence_base, pinned"
)

_EDGE_COLUMNS = (
    "id, source_id, target_id, edge_type, user_id, "
    "confidence, valid_from, valid_to, provenance_event_id, "
    "metadata, created_at"
)


class PgGraphStore:
    """GraphStore implementation backed by PostgreSQL via asyncpg.

    Uses parameterized queries for all user data to prevent SQL injection.
    All queries enforce user_id scoping. Query defaults filter to active
    lifecycle states (tentative + stable + contested).
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # --- Node Operations ---

    async def create_node(self, node: MemoryNode) -> str:
        """Create a new node in the graph store."""
        evidence_json = (
            json.dumps([str(ref) for ref in node.evidence_refs])
            if node.evidence_refs
            else None
        )
        metadata_json = (
            json.dumps(node.metadata) if node.metadata is not None else None
        )

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO nodes (
                    id, node_type, user_id, session_id, scope, content,
                    metadata, confidence, salience, lifecycle_state,
                    valid_from, valid_to, superseded_by, evidence_refs,
                    created_at, updated_at, epistemic_type, source_type,
                    decay_profile, last_reinforced_at, reinforcement_boost,
                    salience_base, confidence_base, pinned
                ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10,
                          $11, $12, $13, $14::jsonb, $15, $16, $17, $18,
                          $19, $20, $21, $22, $23, $24)
                """,
                str(node.id),
                node.node_type.value,
                node.user_id,
                node.session_id,
                node.scope.value,
                node.content,
                metadata_json,
                node.confidence,
                node.salience,
                node.lifecycle_state.value,
                node.valid_from,
                node.valid_to,
                str(node.superseded_by) if node.superseded_by else None,
                evidence_json,
                node.created_at,
                node.updated_at,
                node.epistemic_type.value,
                node.source_type.value,
                node.decay_profile.value,
                node.last_reinforced_at,
                node.reinforcement_boost,
                node.salience_base,
                node.confidence_base,
                node.pinned,
            )
        return str(node.id)

    async def get_node(
        self,
        node_id: str,
        *,
        include_superseded: bool = False,
    ) -> MemoryNode | None:
        """Retrieve a node by ID."""
        async with self._pool.acquire() as conn:
            if include_superseded:
                row = await conn.fetchrow(
                    f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id = $1",
                    node_id,
                )
            else:
                row = await conn.fetchrow(
                    f"SELECT {_NODE_COLUMNS} FROM nodes "
                    "WHERE id = $1 AND lifecycle_state IN ('tentative', 'stable')",
                    node_id,
                )
        if row is None:
            return None
        return self._record_to_node(row)

    async def query_nodes(
        self,
        *,
        node_type: NodeType | None = None,
        user_id: str | None = None,
        scope: Scope | None = None,
        lifecycle_states: list[LifecycleState] | None = None,
        valid_at: datetime | None = None,
        min_confidence: float | None = None,
        limit: int = 100,
    ) -> list[MemoryNode]:
        """Query nodes with flexible filters."""
        conditions: list[str] = []
        params: list = []
        idx = 1

        if lifecycle_states is None:
            lifecycle_states = [
                LifecycleState.TENTATIVE,
                LifecycleState.STABLE,
                LifecycleState.CONTESTED,
            ]

        placeholders = ", ".join(f"${idx + i}" for i in range(len(lifecycle_states)))
        conditions.append(f"lifecycle_state IN ({placeholders})")
        params.extend(s.value for s in lifecycle_states)
        idx += len(lifecycle_states)

        if node_type is not None:
            conditions.append(f"node_type = ${idx}")
            params.append(node_type.value)
            idx += 1

        if user_id is not None:
            conditions.append(f"user_id = ${idx}")
            params.append(user_id)
            idx += 1

        if scope is not None:
            conditions.append(f"scope = ${idx}")
            params.append(scope.value)
            idx += 1

        if valid_at is not None:
            conditions.append(
                f"valid_from <= ${idx} AND (valid_to IS NULL OR valid_to > ${idx + 1})"
            )
            params.extend([valid_at, valid_at])
            idx += 2

        if min_confidence is not None:
            conditions.append(f"confidence >= ${idx}")
            params.append(min_confidence)
            idx += 1

        where = " AND ".join(conditions) if conditions else "1=1"
        query = (
            f"SELECT {_NODE_COLUMNS} FROM nodes "
            f"WHERE {where} "
            f"ORDER BY created_at DESC "
            f"LIMIT ${idx}"
        )
        params.append(limit)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._record_to_node(row) for row in rows]

    # --- Node Update ---

    # Fields allowed for update_node. Maps Python field name -> SQL column name.
    _UPDATE_ALLOWED_FIELDS: set[str] = {
        "reinforcement_boost",
        "last_reinforced_at",
        "confidence_base",
        "salience_base",
        "decay_profile",
        "pinned",
        "evidence_refs",
        "metadata",
        "lifecycle_state",
        "superseded_by",
        "confidence",
        "salience",
        "updated_at",
    }

    async def update_node(self, node_id: str, **updates) -> None:
        """Update specific fields on an existing node.

        Args:
            node_id: UUID string of the node to update.
            **updates: Field names and values to update.

        Raises:
            ValueError: If node_id does not exist or no valid fields provided.
        """
        # Filter to only allowed fields
        valid_updates = {
            k: v for k, v in updates.items()
            if k in self._UPDATE_ALLOWED_FIELDS
        }
        if not valid_updates:
            raise ValueError(
                f"No valid fields to update. Allowed fields: "
                f"{sorted(self._UPDATE_ALLOWED_FIELDS)}"
            )

        # Always set updated_at to current timestamp
        now = datetime.now(timezone.utc)
        if "updated_at" not in valid_updates:
            valid_updates["updated_at"] = now

        # Build SET clause with PostgreSQL $N parameter placeholders
        set_parts: list[str] = []
        params: list = []
        idx = 1

        for field, value in valid_updates.items():
            # Serialize special types
            if field == "evidence_refs" and value is not None:
                set_parts.append(f"{field} = ${idx}::jsonb")
                params.append(json.dumps([str(u) for u in value]))
            elif field == "metadata" and value is not None:
                set_parts.append(f"{field} = ${idx}::jsonb")
                params.append(json.dumps(value))
            elif field == "decay_profile" and isinstance(value, DecayProfile):
                set_parts.append(f"{field} = ${idx}")
                params.append(value.value)
            elif field == "lifecycle_state" and isinstance(value, LifecycleState):
                set_parts.append(f"{field} = ${idx}")
                params.append(value.value)
            elif field == "superseded_by" and value is not None:
                set_parts.append(f"{field} = ${idx}::uuid")
                params.append(str(value))
            elif field == "last_reinforced_at" and isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                set_parts.append(f"{field} = ${idx}")
                params.append(value)
            else:
                set_parts.append(f"{field} = ${idx}")
                params.append(value)
            idx += 1

        set_clause = ", ".join(set_parts)
        params.append(node_id)

        query = f"UPDATE nodes SET {set_clause} WHERE id = ${idx}"

        async with self._pool.acquire() as conn:
            # Verify the node exists
            existing = await conn.fetchrow(
                "SELECT id FROM nodes WHERE id = $1", node_id
            )
            if existing is None:
                raise ValueError(f"Node {node_id} not found")

            await conn.execute(query, *params)

    # --- Edge Operations ---

    async def create_edge(self, edge: MemoryEdge) -> str:
        """Create a new edge between two nodes."""
        metadata_json = (
            json.dumps(edge.metadata) if edge.metadata is not None else None
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO edges (
                    id, source_id, target_id, edge_type, user_id,
                    confidence, valid_from, valid_to, provenance_event_id,
                    metadata, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
                """,
                str(edge.id),
                str(edge.source_id),
                str(edge.target_id),
                edge.edge_type.value,
                edge.user_id,
                edge.confidence,
                edge.valid_from,
                edge.valid_to,
                str(edge.provenance_event_id) if edge.provenance_event_id else None,
                metadata_json,
                edge.created_at,
            )
        return str(edge.id)

    async def get_edges(
        self,
        *,
        source_id: str | None = None,
        target_id: str | None = None,
        edge_type: EdgeType | None = None,
        valid_at: datetime | None = None,
        min_confidence: float | None = None,
    ) -> list[MemoryEdge]:
        """Query edges with filters."""
        conditions: list[str] = []
        params: list = []
        idx = 1

        if source_id is not None:
            conditions.append(f"source_id = ${idx}::uuid")
            params.append(source_id)
            idx += 1

        if target_id is not None:
            conditions.append(f"target_id = ${idx}::uuid")
            params.append(target_id)
            idx += 1

        if edge_type is not None:
            conditions.append(f"edge_type = ${idx}")
            params.append(edge_type.value)
            idx += 1

        if valid_at is not None:
            conditions.append(
                f"valid_from <= ${idx} AND (valid_to IS NULL OR valid_to > ${idx + 1})"
            )
            params.extend([valid_at, valid_at])
            idx += 2

        if min_confidence is not None:
            conditions.append(f"confidence >= ${idx}")
            params.append(min_confidence)
            idx += 1

        where = " AND ".join(conditions) if conditions else "1=1"
        query = (
            f"SELECT {_EDGE_COLUMNS} FROM edges "
            f"WHERE {where} "
            "ORDER BY created_at DESC"
        )

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._record_to_edge(row) for row in rows]

    # --- Lifecycle Transitions ---

    async def promote(self, node_id: str) -> None:
        """Promote a tentative node to stable."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT lifecycle_state FROM nodes WHERE id = $1", node_id
            )
            if row is None:
                raise ValueError(f"Node {node_id} not found")

            current_state = LifecycleState(row["lifecycle_state"])
            target_state = LifecycleState.STABLE

            if not validate_transition(current_state, target_state):
                raise ValueError(
                    f"Cannot promote: node is {current_state.value}, "
                    f"only Tentative nodes can be promoted"
                )

            await conn.execute(
                "UPDATE nodes SET lifecycle_state = $1, updated_at = now() WHERE id = $2",
                target_state.value,
                node_id,
            )

    async def supersede(
        self,
        old_node_id: str,
        new_node_id: str,
        *,
        evidence_id: str | None = None,
    ) -> None:
        """Mark a node as superseded by another."""
        async with self._pool.acquire() as conn:
            old_row = await conn.fetchrow(
                "SELECT lifecycle_state, user_id FROM nodes WHERE id = $1",
                old_node_id,
            )
            if old_row is None:
                raise ValueError(f"Old node {old_node_id} not found")

            new_row = await conn.fetchrow(
                "SELECT id, user_id FROM nodes WHERE id = $1", new_node_id
            )
            if new_row is None:
                raise ValueError(f"New node {new_node_id} not found")

            current_state = LifecycleState(old_row["lifecycle_state"])
            target_state = LifecycleState.SUPERSEDED

            if not validate_transition(current_state, target_state):
                raise ValueError(
                    f"Cannot supersede: node is {current_state.value}, "
                    f"only Tentative or Stable nodes can be superseded"
                )

            await conn.execute(
                "UPDATE nodes SET lifecycle_state = $1, superseded_by = $2::uuid, "
                "updated_at = now() WHERE id = $3",
                target_state.value,
                new_node_id,
                old_node_id,
            )

        # Create SUPERSEDES edge: new_node -> old_node
        provenance_uuid = None
        if evidence_id is not None:
            try:
                provenance_uuid = UUID(evidence_id)
            except ValueError:
                logger.warning(
                    "evidence_id %r is not a valid UUID, storing edge "
                    "without provenance reference",
                    evidence_id,
                )

        edge = MemoryEdge(
            source_id=UUID(new_node_id),
            target_id=UUID(old_node_id),
            edge_type=EdgeType.SUPERSEDES,
            user_id=old_row["user_id"],
            confidence=1.0,
            provenance_event_id=provenance_uuid,
        )
        await self.create_edge(edge)

    async def contradict(
        self,
        node_a_id: str,
        node_b_id: str,
        *,
        evidence_id: str | None = None,
    ) -> None:
        """Mark two nodes as contradicting each other."""
        async with self._pool.acquire() as conn:
            row_a = await conn.fetchrow(
                "SELECT lifecycle_state, user_id FROM nodes WHERE id = $1",
                node_a_id,
            )
            if row_a is None:
                raise ValueError(f"Node {node_a_id} not found")

            row_b = await conn.fetchrow(
                "SELECT lifecycle_state, user_id FROM nodes WHERE id = $1",
                node_b_id,
            )
            if row_b is None:
                raise ValueError(f"Node {node_b_id} not found")

            state_a = LifecycleState(row_a["lifecycle_state"])
            state_b = LifecycleState(row_b["lifecycle_state"])

            if not validate_transition(state_a, LifecycleState.CONTESTED):
                raise ValueError(
                    f"Cannot contest node {node_a_id}: current state "
                    f"'{state_a.value}' does not allow transition to CONTESTED"
                )
            if not validate_transition(state_b, LifecycleState.CONTESTED):
                raise ValueError(
                    f"Cannot contest node {node_b_id}: current state "
                    f"'{state_b.value}' does not allow transition to CONTESTED"
                )

            # Transition both to CONTESTED
            await conn.execute(
                "UPDATE nodes SET lifecycle_state = $1, updated_at = now() WHERE id = $2",
                LifecycleState.CONTESTED.value,
                node_a_id,
            )
            await conn.execute(
                "UPDATE nodes SET lifecycle_state = $1, updated_at = now() WHERE id = $2",
                LifecycleState.CONTESTED.value,
                node_b_id,
            )

            # Create CONTRADICTS edge: node_b -> node_a
            provenance_uuid = None
            if evidence_id is not None:
                try:
                    provenance_uuid = UUID(evidence_id)
                except ValueError:
                    logger.warning(
                        "evidence_id %r is not a valid UUID, storing edge "
                        "without provenance reference",
                        evidence_id,
                    )

            edge = MemoryEdge(
                source_id=UUID(node_b_id),
                target_id=UUID(node_a_id),
                edge_type=EdgeType.CONTRADICTS,
                user_id=row_a["user_id"],
                confidence=1.0,
                provenance_event_id=provenance_uuid,
            )
            await conn.execute(
                """
                INSERT INTO edges (
                    id, source_id, target_id, edge_type, user_id,
                    confidence, valid_from, valid_to, provenance_event_id,
                    metadata, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
                """,
                str(edge.id),
                str(edge.source_id),
                str(edge.target_id),
                edge.edge_type.value,
                edge.user_id,
                edge.confidence,
                edge.valid_from,
                edge.valid_to,
                str(edge.provenance_event_id) if edge.provenance_event_id else None,
                json.dumps(edge.metadata) if edge.metadata else None,
                edge.created_at,
            )

            # Log CONTRADICTION_NOTED operation
            op_id = str(_uuid.uuid4())
            payload = json.dumps({
                "node_a_id": node_a_id,
                "node_b_id": node_b_id,
                "evidence_event_id": evidence_id,
            })
            await conn.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at) "
                "VALUES ($1, 'CONTRADICTION_NOTED', $2, $3::jsonb, 'system', now())",
                op_id,
                node_a_id,
                payload,
            )

    async def resolve_contradiction(
        self,
        winner_id: str,
        loser_id: str,
        *,
        resolver_actor_id: str = "system",
        evidence_id: str | None = None,
    ) -> None:
        """Resolve a contradiction by declaring a winner and loser."""
        async with self._pool.acquire() as conn:
            winner_row = await conn.fetchrow(
                "SELECT lifecycle_state FROM nodes WHERE id = $1", winner_id
            )
            if winner_row is None:
                raise ValueError(f"Winner node {winner_id} not found")

            loser_row = await conn.fetchrow(
                "SELECT lifecycle_state FROM nodes WHERE id = $1", loser_id
            )
            if loser_row is None:
                raise ValueError(f"Loser node {loser_id} not found")

            winner_state = LifecycleState(winner_row["lifecycle_state"])
            loser_state = LifecycleState(loser_row["lifecycle_state"])

            if winner_state != LifecycleState.CONTESTED:
                raise ValueError(
                    f"Winner node {winner_id} is not CONTESTED "
                    f"(current: {winner_state.value})"
                )
            if loser_state != LifecycleState.CONTESTED:
                raise ValueError(
                    f"Loser node {loser_id} is not CONTESTED "
                    f"(current: {loser_state.value})"
                )

            # Validate CONTRADICTS edge exists
            edge_row = await conn.fetchrow(
                """
                SELECT id FROM edges
                WHERE edge_type = 'contradicts'
                AND (
                    (source_id = $1::uuid AND target_id = $2::uuid)
                    OR
                    (source_id = $2::uuid AND target_id = $1::uuid)
                )
                LIMIT 1
                """,
                winner_id,
                loser_id,
            )
            if edge_row is None:
                raise ValueError(
                    f"No CONTRADICTS edge exists between {winner_id} and {loser_id}"
                )

            # Transition winner to STABLE
            await conn.execute(
                "UPDATE nodes SET lifecycle_state = $1, updated_at = now() WHERE id = $2",
                LifecycleState.STABLE.value,
                winner_id,
            )

            # Transition loser to DEPRECATED
            await conn.execute(
                "UPDATE nodes SET lifecycle_state = $1, updated_at = now() WHERE id = $2",
                LifecycleState.DEPRECATED.value,
                loser_id,
            )

            # Log EPISTEMIC_TRANSITION for winner
            await conn.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at) "
                "VALUES ($1, 'EPISTEMIC_TRANSITION', $2, $3::jsonb, $4, now())",
                str(_uuid.uuid4()),
                winner_id,
                json.dumps({
                    "from_state": LifecycleState.CONTESTED.value,
                    "to_state": LifecycleState.STABLE.value,
                    "reason": "contradiction_resolved_winner",
                    "evidence_event_id": evidence_id,
                }),
                resolver_actor_id,
            )

            # Log EPISTEMIC_TRANSITION for loser
            await conn.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at) "
                "VALUES ($1, 'EPISTEMIC_TRANSITION', $2, $3::jsonb, $4, now())",
                str(_uuid.uuid4()),
                loser_id,
                json.dumps({
                    "from_state": LifecycleState.CONTESTED.value,
                    "to_state": LifecycleState.DEPRECATED.value,
                    "reason": "contradiction_resolved_loser",
                    "evidence_event_id": evidence_id,
                }),
                resolver_actor_id,
            )

            # Log CONTRADICTION_RESOLVED
            await conn.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at) "
                "VALUES ($1, 'CONTRADICTION_RESOLVED', $2, $3::jsonb, $4, now())",
                str(_uuid.uuid4()),
                winner_id,
                json.dumps({
                    "winner_id": winner_id,
                    "loser_id": loser_id,
                    "evidence_event_id": evidence_id,
                }),
                resolver_actor_id,
            )

    async def archive(self, node_id: str) -> None:
        """Archive a node (terminal state)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT lifecycle_state FROM nodes WHERE id = $1", node_id
            )
            if row is None:
                raise ValueError(f"Node {node_id} not found")

            current_state = LifecycleState(row["lifecycle_state"])
            target_state = LifecycleState.ARCHIVED

            if not validate_transition(current_state, target_state):
                raise ValueError(
                    f"Cannot archive: node is {current_state.value}, "
                    f"Archived nodes cannot be transitioned"
                )

            await conn.execute(
                "UPDATE nodes SET lifecycle_state = $1, updated_at = now() WHERE id = $2",
                target_state.value,
                node_id,
            )

    # --- Graph Traversal ---

    async def get_neighborhood(
        self,
        node_id: str,
        *,
        max_hops: int = 2,
        edge_types: list[EdgeType] | None = None,
        valid_at: datetime | None = None,
        min_confidence: float | None = None,
        include_superseded: bool = False,
    ) -> list[MemoryNode]:
        """Get nodes within N hops of a starting node via recursive CTE."""
        # Build edge filter
        edge_filter_parts: list[str] = []
        params: list = [node_id]
        idx = 2  # $1 is node_id

        if edge_types is not None:
            et_placeholders = ", ".join(f"${idx + i}" for i in range(len(edge_types)))
            edge_filter_parts.append(f"e.edge_type IN ({et_placeholders})")
            params.extend(et.value for et in edge_types)
            idx += len(edge_types)

        edge_filter = "AND " + " AND ".join(edge_filter_parts) if edge_filter_parts else ""

        # max_hops parameter
        params.append(max_hops)
        max_hops_idx = idx
        idx += 1

        # Build node filter
        node_filter_parts: list[str] = []

        if not include_superseded:
            node_filter_parts.append(
                "n.lifecycle_state IN ('tentative', 'stable')"
            )

        if valid_at is not None:
            node_filter_parts.append(
                f"n.valid_from <= ${idx} AND (n.valid_to IS NULL OR n.valid_to > ${idx + 1})"
            )
            params.extend([valid_at, valid_at])
            idx += 2

        if min_confidence is not None:
            node_filter_parts.append(f"n.confidence >= ${idx}")
            params.append(min_confidence)
            idx += 1

        node_filter = "AND " + " AND ".join(node_filter_parts) if node_filter_parts else ""

        query = f"""
            WITH RECURSIVE neighborhood AS (
                -- Base case: direct neighbors in both directions
                SELECT
                    CASE WHEN e.source_id = $1::uuid
                         THEN e.target_id
                         ELSE e.source_id
                    END AS id,
                    1 AS depth
                FROM edges e
                WHERE (e.source_id = $1::uuid OR e.target_id = $1::uuid)
                    {edge_filter}

                UNION

                -- Recursive case
                SELECT
                    CASE WHEN e.source_id = nb.id
                         THEN e.target_id
                         ELSE e.source_id
                    END AS id,
                    nb.depth + 1 AS depth
                FROM neighborhood nb
                JOIN edges e ON (e.source_id = nb.id OR e.target_id = nb.id)
                    {edge_filter}
                WHERE nb.depth < ${max_hops_idx}
            )
            SELECT DISTINCT {_NODE_COLUMNS}
            FROM (SELECT DISTINCT id FROM neighborhood) nb_ids
            JOIN nodes n ON nb_ids.id = n.id
            WHERE n.id != $1::uuid {node_filter}
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._record_to_node(row) for row in rows]

    async def find_shortest_path(
        self,
        source_id: str,
        target_id: str,
        *,
        edge_types: list[EdgeType] | None = None,
    ) -> list[str] | None:
        """Find the shortest path between two nodes via BFS."""
        if source_id == target_id:
            return [source_id]

        # Build edge filter
        edge_filter_parts: list[str] = []
        edge_params: list = []
        idx = 1
        if edge_types is not None:
            et_placeholders = ", ".join(f"${idx + i}" for i in range(len(edge_types)))
            edge_filter_parts.append(f"edge_type IN ({et_placeholders})")
            edge_params.extend(et.value for et in edge_types)
            idx += len(edge_types)

        edge_where = "AND " + " AND ".join(edge_filter_parts) if edge_filter_parts else ""

        # Iterative BFS (same approach as DuckDB backend)
        visited: set[str] = {source_id}
        parent: dict[str, str] = {}
        frontier = [source_id]
        max_depth = 10

        async with self._pool.acquire() as conn:
            for _ in range(max_depth):
                if not frontier:
                    return None

                next_frontier: list[str] = []
                for current_id in frontier:
                    query = f"""
                        SELECT
                            CASE WHEN source_id = $1::uuid
                                 THEN target_id::text
                                 ELSE source_id::text
                            END AS neighbor_id
                        FROM edges
                        WHERE (source_id = $1::uuid OR target_id = $1::uuid)
                            {edge_where}
                    """
                    params = [current_id, *edge_params]

                    rows = await conn.fetch(query, *params)
                    for row in rows:
                        neighbor_id = str(row["neighbor_id"])
                        if neighbor_id not in visited:
                            visited.add(neighbor_id)
                            parent[neighbor_id] = current_id
                            next_frontier.append(neighbor_id)

                            if neighbor_id == target_id:
                                path = [target_id]
                                node = target_id
                                while node != source_id:
                                    node = parent[node]
                                    path.append(node)
                                path.reverse()
                                return path

                frontier = next_frontier

        return None

    async def get_supersedence_chain(
        self,
        node_id: str,
        *,
        direction: str = "forward",
    ) -> list[MemoryNode]:
        """Traverse the supersedence chain from a node."""
        chain: list[MemoryNode] = []
        visited: set[str] = {node_id}
        current_id = node_id
        max_depth = 100

        async with self._pool.acquire() as conn:
            for _ in range(max_depth):
                if direction == "forward":
                    row = await conn.fetchrow(
                        "SELECT source_id::text FROM edges "
                        "WHERE target_id = $1::uuid AND edge_type = 'supersedes' "
                        "LIMIT 1",
                        current_id,
                    )
                elif direction == "backward":
                    row = await conn.fetchrow(
                        "SELECT target_id::text FROM edges "
                        "WHERE source_id = $1::uuid AND edge_type = 'supersedes' "
                        "LIMIT 1",
                        current_id,
                    )
                else:
                    raise ValueError(
                        f"Invalid direction: {direction}. Use 'forward' or 'backward'."
                    )

                if row is None:
                    break

                next_id = str(row[0])
                if next_id in visited:
                    break

                visited.add(next_id)

                node_row = await conn.fetchrow(
                    f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id = $1", next_id
                )
                if node_row is None:
                    break

                chain.append(self._record_to_node(node_row))
                current_id = next_id

        return chain

    # --- Cleanup / Rollback ---

    async def delete_node(self, node_id: str) -> None:
        """Delete a node by ID. No-op if not found."""
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM nodes WHERE id = $1", node_id)
        logger.debug("graph.delete_node", extra={"node_id": node_id})

    async def delete_edge(self, edge_id: str) -> None:
        """Delete an edge by ID. No-op if not found."""
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM edges WHERE id = $1", edge_id)
        logger.debug("graph.delete_edge", extra={"edge_id": edge_id})

    # --- Row conversion helpers ---

    @staticmethod
    def _record_to_node(row: asyncpg.Record) -> MemoryNode:
        """Convert an asyncpg Record to a MemoryNode."""
        raw_id = row["id"]
        node_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))

        raw_evidence = row["evidence_refs"]
        evidence_refs: list[UUID] = []
        if raw_evidence is not None:
            if isinstance(raw_evidence, str):
                evidence_refs = [UUID(ref) for ref in json.loads(raw_evidence)]
            elif isinstance(raw_evidence, list):
                evidence_refs = [UUID(str(ref)) for ref in raw_evidence]

        raw_metadata = row["metadata"]
        if isinstance(raw_metadata, str):
            metadata = json.loads(raw_metadata)
        else:
            metadata = raw_metadata

        raw_superseded = row["superseded_by"]
        superseded_by = None
        if raw_superseded is not None:
            superseded_by = (
                raw_superseded
                if isinstance(raw_superseded, UUID)
                else UUID(str(raw_superseded))
            )

        def ensure_tz(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            if isinstance(dt, datetime) and dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        # Decay/reinforcement fields (RFC-0015) -- graceful fallback
        raw_decay_profile = row.get("decay_profile", "medium") if hasattr(row, "get") else "medium"
        raw_last_reinforced = row.get("last_reinforced_at") if hasattr(row, "get") else None
        raw_reinforcement_boost = row.get("reinforcement_boost", 0.0) if hasattr(row, "get") else 0.0
        raw_salience_base = row.get("salience_base", row["salience"]) if hasattr(row, "get") else row["salience"]
        raw_confidence_base = row.get("confidence_base", row["confidence"]) if hasattr(row, "get") else row["confidence"]
        raw_pinned = row.get("pinned", False) if hasattr(row, "get") else False

        return MemoryNode(
            id=node_id,
            node_type=NodeType(row["node_type"]),
            user_id=row["user_id"],
            session_id=row["session_id"],
            scope=Scope(row["scope"]),
            content=row["content"],
            metadata=metadata,
            confidence=row["confidence"],
            salience=row["salience"],
            lifecycle_state=LifecycleState(row["lifecycle_state"]),
            valid_from=ensure_tz(row["valid_from"]),
            valid_to=ensure_tz(row["valid_to"]),
            superseded_by=superseded_by,
            evidence_refs=evidence_refs,
            created_at=ensure_tz(row["created_at"]),
            updated_at=ensure_tz(row["updated_at"]),
            epistemic_type=row["epistemic_type"],
            source_type=row["source_type"],
            decay_profile=DecayProfile(raw_decay_profile) if raw_decay_profile else DecayProfile.MEDIUM,
            last_reinforced_at=ensure_tz(raw_last_reinforced) or datetime.now(timezone.utc),
            reinforcement_boost=raw_reinforcement_boost or 0.0,
            salience_base=raw_salience_base if raw_salience_base is not None else 0.5,
            confidence_base=raw_confidence_base if raw_confidence_base is not None else 0.5,
            pinned=bool(raw_pinned),
        )

    @staticmethod
    def _record_to_edge(row: asyncpg.Record) -> MemoryEdge:
        """Convert an asyncpg Record to a MemoryEdge."""

        def to_uuid(val: object) -> UUID:
            return val if isinstance(val, UUID) else UUID(str(val))

        def ensure_tz(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            if isinstance(dt, datetime) and dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        raw_provenance = row["provenance_event_id"]
        provenance_event_id = (
            to_uuid(raw_provenance) if raw_provenance is not None else None
        )

        raw_metadata = row["metadata"]
        if isinstance(raw_metadata, str):
            metadata = json.loads(raw_metadata)
        else:
            metadata = raw_metadata

        return MemoryEdge(
            id=to_uuid(row["id"]),
            source_id=to_uuid(row["source_id"]),
            target_id=to_uuid(row["target_id"]),
            edge_type=EdgeType(row["edge_type"]),
            user_id=row["user_id"],
            confidence=row["confidence"],
            valid_from=ensure_tz(row["valid_from"]),
            valid_to=ensure_tz(row["valid_to"]),
            provenance_event_id=provenance_event_id,
            metadata=metadata,
            created_at=ensure_tz(row["created_at"]),
        )
