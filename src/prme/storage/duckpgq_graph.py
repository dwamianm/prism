"""DuckPGQ-backed GraphStore implementation with full graph operations.

Implements the GraphStore Protocol using DuckDB tables as the underlying
storage. All traversal operations use recursive CTEs on the edges table
as the primary implementation path. DuckPGQ SQL/PGQ is not available for
DuckDB 1.4.4 on osx_arm64, so recursive CTEs are the only code path.

Covers: node/edge CRUD, query_nodes/get_edges, lifecycle transitions
(promote, supersede, archive), graph traversal (neighborhood, shortest
path), and supersedence chain traversal.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any
from datetime import datetime, timezone
from uuid import UUID

import duckdb

from prme.privacy.journals import protected_mutation, journal_deadline
from prme.models.edges import MemoryEdge
from prme.storage._threading import run_to_completion
from prme.storage.seed_walk import WalkedNode, seed_walk_sql, walk_links_sql, walked_nodes
from prme.models.nodes import MemoryNode
from prme.storage.organizer_merge import MergeResult
from prme.storage.owner_reads import (
    CROSS_USER_EDGE_WHERE,
    CROSS_USER_POINTER_WHERE,
    CrossUserEdge,
    CrossUserLinks,
    CrossUserPointer,
    hide_foreign_successors,
    named_successors,
)
from prme.storage.alias_proposal import AliasProposalEvidence, AliasProposalResult
from prme.models.derivation import DerivationPlan, DerivationReceipt
from prme.models.extraction_work import ExtractionClaim
from prme.models.reingest import ReingestRecord
from prme.models.profile import ProfilePublication
from prme.models.consolidation import ConsolidationPublication
from prme.types import (
    ACTIVE_LIFECYCLE_STATES,
    DecayProfile,
    EdgeType,
    LifecycleState,
    NodeType,
    Scope,
    validate_transition,
)

logger = logging.getLogger(__name__)

# The opt-in archived-profile exclusion, shared by the listing and the count
# so the two cannot drift. The JSON type check matches only a boolean marker,
# as the Python facade and the rest of the codebase read it (issue #432).
ARCHIVED_PROFILE_EXCLUSION_SQL = (
    "NOT (node_type = 'summary' AND lifecycle_state = 'archived' "
    "AND COALESCE(json_type(metadata, '$.entity_profile'), 'NULL') = 'BOOLEAN' "
    "AND json_extract_string(metadata, '$.entity_profile') = 'true')"
)


def _uuid_literals(values: list[str]) -> str | None:
    """SQL literals for canonical UUID strings, or None if any value is not one.

    A value is written only when it is exactly ``str(UUID(value))``: 36
    lowercase hexadecimal digits and hyphens, so the text cannot carry SQL.
    """
    try:
        if all(isinstance(value, str) and str(UUID(value)) == value for value in values):
            return ", ".join(f"'{value}'" for value in values)
    except (ValueError, AttributeError, TypeError):
        pass
    return None


class DuckPGQGraphStore:
    """GraphStore implementation backed by DuckDB tables.

    Uses parameterized queries for all user data to prevent SQL injection.
    All queries enforce user_id scoping. Query defaults filter to active
    lifecycle states (tentative + stable + contested).
    """

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        conn_lock: asyncio.Lock | None = None,
    ) -> None:
        self._conn = conn
        self._conn_lock = conn_lock if conn_lock is not None else asyncio.Lock()

    # --- Node Operations ---

    async def reinforce_node(self, node_id: str, *, user_id: str | None, evidence_id: str | None, request_id: str | UUID | None = None) -> None:
        from prme.storage.reinforcement import reinforce_duckdb
        await reinforce_duckdb(self, node_id, user_id=user_id, evidence_id=evidence_id, request_id=request_id)

    async def apply_oscillation_penalty(
        self, node_id: str, chain_node_ids: list[str], *, user_id: str,
    ) -> bool:
        from prme.storage.oscillation_penalty import apply_duckdb
        return await apply_duckdb(
            self, node_id, chain_node_ids, user_id=user_id
        )

    async def evaluate_condition(self, node_id: str, state, **kwargs):
        from prme.storage.condition_evaluation import evaluate_condition_duckdb
        return await evaluate_condition_duckdb(self, node_id, state, **kwargs)

    async def commit_derivation(self, plan: DerivationPlan, *, claim: ExtractionClaim | None = None) -> DerivationReceipt:
        """Publish a journaled graph derivation and its receipt atomically."""
        from prme.storage.derivation import commit_duckdb
        return await commit_duckdb(self, plan, claim=claim)

    async def commit_reingest(self, plan: DerivationPlan, record: ReingestRecord) -> ReingestRecord:
        """Publish a re-derived source's plan, retire its earlier claims and mark it current atomically."""
        from prme.storage.reingest import commit_duckdb
        return await commit_duckdb(self, plan, record)

    async def profile_generation(self, key: str) -> int:
        from prme.storage.profile_publication import generation_duckdb
        return await generation_duckdb(self, key)

    async def publish_profile(self, plan: ProfilePublication) -> str:
        """Atomically replace a generated profile after index preparation."""
        from prme.storage.profile_publication import commit_duckdb
        return await commit_duckdb(self, plan)

    async def consolidation_generation(self, key: str) -> int:
        from prme.storage.consolidation_publication import generation_duckdb
        return await generation_duckdb(self, key)

    async def prepare_consolidation(
        self, plan: ConsolidationPublication
    ) -> ConsolidationPublication:
        from prme.storage.consolidation_publication import prepare_duckdb
        return await prepare_duckdb(self, plan)

    async def get_prepared_consolidation(
        self, node_id: str, *, user_id: str
    ) -> ConsolidationPublication | None:
        from prme.storage.consolidation_publication import get_prepared_duckdb
        return await get_prepared_duckdb(self, node_id, user_id=user_id)

    async def publish_consolidation(self, plan: ConsolidationPublication) -> str:
        from prme.storage.consolidation_publication import commit_duckdb
        return await commit_duckdb(self, plan)

    async def create_node(self, node: MemoryNode) -> str:
        """Create a new node in the graph store.

        Args:
            node: The MemoryNode to store.

        Returns:
            String UUID of the created node.
        """
        async with self._conn_lock:
            await run_to_completion(self._create_node_sync, node)
        return str(node.id)

    async def get_node(
        self,
        node_id: str,
        *,
        include_superseded: bool = False,
        user_id: str | None = None,
    ) -> MemoryNode | None:
        """Retrieve a node by ID.

        Args:
            node_id: String UUID of the node.
            include_superseded: If False, returns None for
                superseded/archived nodes.
            user_id: When given, another user's node reads as not found,
                and a retired node's ``superseded_by`` never
                names another user's node.

        Returns:
            The MemoryNode if found and visible, None otherwise.
        """
        def read() -> MemoryNode | None:
            node = self._get_node_sync(node_id, include_superseded)
            if node is None or user_id is None:
                return node
            if node.user_id != user_id:
                return None
            return self._owned_view_sync([node], user_id)[0]

        async with self._conn_lock:
            return await run_to_completion(read)

    async def get_nodes(
        self,
        node_ids: list[str],
        *,
        include_superseded: bool = False,
        user_id: str | None = None,
    ) -> list[MemoryNode]:
        """Retrieve multiple nodes by ID in a single round trip.

        Batch equivalent of get_node() with identical visibility
        semantics. Missing IDs are silently omitted.

        Args:
            node_ids: String UUIDs of the nodes to fetch.
            include_superseded: If False, superseded/archived nodes
                are omitted.
            user_id: When given, another user's nodes are omitted, and
                a retired node's ``superseded_by`` never
                names another user's node.

        Returns:
            List of found, visible MemoryNodes.
        """
        if not node_ids:
            return []

        def read() -> list[MemoryNode]:
            nodes = self._get_nodes_sync(node_ids, include_superseded)
            if user_id is None:
                return nodes
            return self._owned_view_sync(
                [node for node in nodes if node.user_id == user_id], user_id
            )

        async with self._conn_lock:
            return await run_to_completion(read)

    async def get_event_nodes(self, event_id: str, *, user_id: str) -> list[MemoryNode]:
        """Resolve provenance directly, independent of creation order and ranking."""
        if not user_id:
            raise ValueError("get_event_nodes requires user_id")
        evidence = json.dumps(str(UUID(event_id)))
        async with self._conn_lock:
            rows = await run_to_completion(lambda: self._conn.execute(
                "SELECT * FROM nodes WHERE user_id = ? AND json_contains(evidence_refs, ?::JSON) ORDER BY id",
                [user_id, evidence],
            ).fetchall())
            nodes = [self._row_to_node(row) for row in rows]
            return await run_to_completion(self._owned_view_sync, nodes, user_id)

    async def get_session_neighbors(
        self,
        trigger_ids: list[str],
        *,
        user_id: str,
        window: int,
        scopes: list[Scope] | None = None,
    ) -> dict[str, list[MemoryNode]]:
        """Fetch exact bounded neighborhoods without hydrating whole sessions."""
        if not user_id or window < 0:
            raise ValueError("get_session_neighbors requires user_id and a non-negative window")
        ids = list(dict.fromkeys(str(UUID(node_id)) for node_id in trigger_ids))
        if not ids:
            return {}

        id_slots = ",".join("?" for _ in ids)
        active = [state.value for state in ACTIVE_LIFECYCLE_STATES]
        state_slots = ",".join("?" for _ in active)
        scope_clause = ""
        anchor_params: list[Any] = [*ids, user_id, *active]
        if scopes:
            scope_slots = ",".join("?" for _ in scopes)
            scope_clause = f" AND scope IN ({scope_slots})"
            anchor_params.extend(scope.value for scope in scopes)
        sql = f"""
            WITH anchor_sessions AS (
                SELECT DISTINCT session_id, scope
                FROM nodes
                WHERE id IN ({id_slots})
                  AND user_id = ?
                  AND session_id IS NOT NULL
                  AND lifecycle_state IN ({state_slots})
                  {scope_clause}
            ), ranked AS (
                SELECT n.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY n.session_id, n.scope
                           ORDER BY n.created_at, n.id
                       ) AS session_rank
                FROM nodes n
                JOIN anchor_sessions a
                  ON n.session_id = a.session_id AND n.scope = a.scope
                WHERE n.user_id = ?
                  AND n.lifecycle_state IN ({state_slots})
            ), anchor_ranks AS (
                SELECT id AS trigger_id, session_id, scope, session_rank
                FROM ranked
                WHERE id IN ({id_slots})
            )
            SELECT a.trigger_id, r.* EXCLUDE (session_rank)
            FROM anchor_ranks a
            JOIN ranked r
              ON a.session_id = r.session_id
             AND a.scope = r.scope
             AND ABS(r.session_rank - a.session_rank) <= ?
            ORDER BY a.trigger_id, r.session_rank, r.id
        """
        params = [
            *anchor_params,
            user_id,
            *active,
            *ids,
            window,
        ]
        async with self._conn_lock:
            rows = await run_to_completion(
                lambda: self._conn.execute(sql, params).fetchall()
            )
        windows: dict[str, list[MemoryNode]] = {}
        for row in rows:
            windows.setdefault(str(row[0]), []).append(self._row_to_node(row[1:]))
        named = named_successors(node for nodes in windows.values() for node in nodes)
        if named:
            async with self._conn_lock:
                foreign = await run_to_completion(self._foreign_ids_sync, named, user_id)
            windows = {
                key: hide_foreign_successors(nodes, foreign) for key, nodes in windows.items()
            }
        return windows

    async def query_nodes(
        self,
        *,
        node_type: NodeType | None = None,
        user_id: str | None = None,
        scope: Scope | None = None,
        scopes: list[Scope] | None = None,
        session_ids: list[str] | None = None,
        lifecycle_states: list[LifecycleState] | None = None,
        valid_at: datetime | None = None,
        min_confidence: float | None = None,
        min_salience: float | None = None,
        content_contains_any: list[str] | None = None,
        content_equals_any: list[str] | None = None,
        created_before: datetime | None = None,
        oldest_first: bool = False,
        exclude_archived_profiles: bool = False,
        limit: int = 100,
    ) -> list[MemoryNode]:
        """Query nodes with flexible filters.

        Defaults to filtering for active states (tentative + stable + contested).

        Args:
            node_type: Filter by node type.
            user_id: Filter by user.
            scope: Filter by a single scope.
            scopes: Filter by multiple scopes (scope IN (...)).
            session_ids: Filter by session IDs (session_id IN (...)).
            lifecycle_states: Lifecycle filter (defaults to active).
            valid_at: Temporal validity filter.
            min_confidence: Minimum confidence threshold.
            min_salience: Minimum salience threshold.
            content_contains_any: Case-insensitive substring filters
                (matches content containing ANY of the strings).
            content_equals_any: Case-insensitive whole-content filters
                (matches trimmed, lowercased content equal to ANY value).
            created_before: Only return nodes with created_at <= this value.
            oldest_first: Order by created_at ASC instead of the default DESC.
            exclude_archived_profiles: Leave out archived entity profile
                SUMMARY nodes (metadata.entity_profile). Applied inside the
                query, before the limit.
            limit: Max results.

        Returns:
            List of matching MemoryNodes.
        """
        def read() -> list[MemoryNode]:
            nodes = self._query_nodes_sync(
                node_type,
                user_id,
                scope,
                scopes,
                session_ids,
                lifecycle_states,
                valid_at,
                min_confidence,
                min_salience,
                content_contains_any,
                content_equals_any,
                created_before,
                oldest_first,
                exclude_archived_profiles,
                limit,
            )
            return nodes if user_id is None else self._owned_view_sync(nodes, user_id)

        async with self._conn_lock:
            return await run_to_completion(read)

    async def count_nodes(
        self,
        *,
        user_id: str | None = None,
        lifecycle_states: list[LifecycleState] | None = None,
        exclude_archived_profiles: bool = False,
    ) -> int:
        """Count nodes matching the filters via SELECT COUNT(*).

        Defaults to counting active states (tentative + stable +
        contested), matching query_nodes().

        Args:
            user_id: Filter by user.
            lifecycle_states: Lifecycle filter (defaults to active).
            exclude_archived_profiles: Leave out archived entity profile
                SUMMARY nodes (metadata.entity_profile), matching
                query_nodes().

        Returns:
            Number of matching nodes.
        """
        async with self._conn_lock:
            return await run_to_completion(
                self._count_nodes_sync, user_id, lifecycle_states,
                exclude_archived_profiles,
            )

    # --- Incremental organizer reads (dwamianm/prism#517) ---

    async def scan_changed_nodes(
        self,
        *,
        user_id: str | None,
        lifecycle_states: list[LifecycleState],
        updated_after: datetime | None,
        updated_at_or_before: datetime,
        node_type: NodeType | None = None,
        after_id: str | None = None,
        limit: int = 500,
    ) -> list[MemoryNode]:
        def read() -> list[MemoryNode]:
            nodes = self._scan_changed_nodes_sync(
                user_id,
                lifecycle_states,
                updated_after,
                updated_at_or_before,
                node_type,
                after_id,
                limit,
            )
            return nodes if user_id is None else self._owned_view_sync(nodes, user_id)

        async with self._conn_lock:
            return await run_to_completion(read)

    def _scan_changed_nodes_sync(
        self,
        user_id: str | None,
        lifecycle_states: list[LifecycleState],
        updated_after: datetime | None,
        updated_at_or_before: datetime,
        node_type: NodeType | None,
        after_id: str | None,
        limit: int,
    ) -> list[MemoryNode]:
        """Page a scope in (updated_at, id) order with a keyset cursor."""
        conditions: list[str] = []
        params: list = []

        placeholders = ", ".join("?" for _ in lifecycle_states)
        conditions.append(f"lifecycle_state IN ({placeholders})")
        params.extend(state.value for state in lifecycle_states)

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)

        if node_type is not None:
            conditions.append("node_type = ?")
            params.append(node_type.value)

        # COALESCE keeps the keyset total for any legacy row without
        # updated_at. MemoryNode requires updated_at, so such a row still
        # fails loudly when it is materialized rather than being skipped.
        conditions.append("COALESCE(updated_at, created_at) <= ?")
        params.append(updated_at_or_before)

        if updated_after is not None:
            if after_id is not None:
                conditions.append(
                    "(COALESCE(updated_at, created_at) > ? OR "
                    "(COALESCE(updated_at, created_at) = ? AND id > ?::UUID))"
                )
                params.extend([updated_after, updated_after, after_id])
            else:
                conditions.append("COALESCE(updated_at, created_at) > ?")
                params.append(updated_after)

        where_clause = " AND ".join(conditions)
        query = (
            f"SELECT * FROM nodes WHERE {where_clause} "
            "ORDER BY COALESCE(updated_at, created_at) ASC, id ASC LIMIT ?"
        )
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_node(row) for row in rows]

    async def get_organizer_watermark(self, *, scope_key: str, job: str):
        """Read one scope and job's watermark, if it has ever completed."""
        from prme.organizer.watermarks import OrganizerWatermark

        def read() -> OrganizerWatermark | None:
            row = self._conn.execute(
                "SELECT completed_through, resume_updated_at, resume_id, "
                "stalled_id, stalled_attempts "
                "FROM organizer_watermarks WHERE scope_key = ? AND job = ?",
                [scope_key, job],
            ).fetchone()
            if row is None:
                return None

            def ensure_tz(value: datetime | None) -> datetime | None:
                if value is None:
                    return None
                return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

            resume_id = None if row[2] is None else str(row[2])
            return OrganizerWatermark(
                scope_key=scope_key,
                job=job,
                completed_through=ensure_tz(row[0]),
                resume_updated_at=ensure_tz(row[1]),
                resume_id=resume_id,
                stalled_id=None if row[3] is None else str(row[3]),
                stalled_attempts=int(row[4] or 0),
            )

        async with self._conn_lock:
            return await run_to_completion(read)

    async def set_organizer_watermark(
        self,
        *,
        scope_key: str,
        job: str,
        completed_through: datetime | None,
        resume_updated_at: datetime | None = None,
        resume_id: str | None = None,
        stalled_id: str | None = None,
        stalled_attempts: int = 0,
    ) -> None:
        """Upsert a job's progress for one scope, monotonically.

        Two runs over one scope may finish out of order. ``completed_through``
        keeps the furthest position either run reached, and the resume cursor
        keeps the later ``(resume_updated_at, resume_id)`` pair. A completed
        run clears a cursor only when that cursor does not point past the
        completed position, so it cannot discard a newer stop from a run that
        started first. The stall fields describe the latest writer's failure
        and are not positional: the last writer sets them.
        """
        def write() -> None:
            self._conn.execute(
                "INSERT INTO organizer_watermarks "
                "(scope_key, job, completed_through, resume_updated_at, resume_id, "
                "stalled_id, stalled_attempts, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, current_timestamp) "
                "ON CONFLICT (scope_key, job) DO UPDATE SET "
                "completed_through = CASE "
                "WHEN organizer_watermarks.completed_through IS NULL THEN excluded.completed_through "
                "WHEN excluded.completed_through IS NULL THEN organizer_watermarks.completed_through "
                "WHEN excluded.completed_through > organizer_watermarks.completed_through "
                "THEN excluded.completed_through "
                "ELSE organizer_watermarks.completed_through END, "
                "resume_updated_at = CASE "
                "WHEN excluded.resume_updated_at IS NULL THEN CASE "
                "WHEN organizer_watermarks.resume_updated_at IS NOT NULL "
                "AND (excluded.completed_through IS NULL "
                "OR organizer_watermarks.resume_updated_at > excluded.completed_through) "
                "THEN organizer_watermarks.resume_updated_at ELSE NULL END "
                "WHEN organizer_watermarks.resume_updated_at IS NULL THEN excluded.resume_updated_at "
                "WHEN excluded.resume_updated_at > organizer_watermarks.resume_updated_at "
                "OR (excluded.resume_updated_at = organizer_watermarks.resume_updated_at "
                "AND excluded.resume_id > organizer_watermarks.resume_id) "
                "THEN excluded.resume_updated_at "
                "ELSE organizer_watermarks.resume_updated_at END, "
                "resume_id = CASE "
                "WHEN excluded.resume_updated_at IS NULL THEN CASE "
                "WHEN organizer_watermarks.resume_updated_at IS NOT NULL "
                "AND (excluded.completed_through IS NULL "
                "OR organizer_watermarks.resume_updated_at > excluded.completed_through) "
                "THEN organizer_watermarks.resume_id ELSE NULL END "
                "WHEN organizer_watermarks.resume_updated_at IS NULL THEN excluded.resume_id "
                "WHEN excluded.resume_updated_at > organizer_watermarks.resume_updated_at "
                "OR (excluded.resume_updated_at = organizer_watermarks.resume_updated_at "
                "AND excluded.resume_id > organizer_watermarks.resume_id) "
                "THEN excluded.resume_id "
                "ELSE organizer_watermarks.resume_id END, "
                "stalled_id = excluded.stalled_id, "
                "stalled_attempts = excluded.stalled_attempts, "
                "updated_at = excluded.updated_at",
                [
                    scope_key, job, completed_through, resume_updated_at, resume_id,
                    stalled_id, stalled_attempts,
                ],
            )

        async with self._conn_lock:
            await run_to_completion(write)

    async def delete_organizer_watermark(self, *, scope_key: str, job: str) -> None:
        """Remove one scope and job's row.

        Used when an owner-wide erasure was recorded between a watermark
        write's guard read and its upsert: the row just written must not
        outlive the purge.
        """
        def write() -> None:
            self._conn.execute(
                "DELETE FROM organizer_watermarks WHERE scope_key = ? AND job = ?",
                [scope_key, job],
            )

        async with self._conn_lock:
            await run_to_completion(write)

    # --- Node Update ---

    async def update_node(self, node_id: str, **updates) -> None:
        """Update specific fields on an existing node.

        Args:
            node_id: UUID string of the node to update.
            **updates: Field names and values to update.

        Raises:
            ValueError: If node_id does not exist or no valid fields provided.
        """
        async with self._conn_lock:
            await run_to_completion(self._update_node_sync, node_id, updates)

    # --- Edge Operations ---

    async def merge_nodes(self, node_a_id: str, node_b_id: str, *, user_id: str, kind: str, score: float) -> MergeResult | None:
        """Atomically publish a compatible organizer merge and its journal."""
        from prme.storage.organizer_merge import merge_duckdb
        return await merge_duckdb(self, node_a_id, node_b_id, user_id=user_id, kind=kind, score=score)

    async def propose_alias(
        self, node_a_id: str, node_b_id: str, *, user_id: str,
        alias_type: str, score: float,
        evidence: AliasProposalEvidence | dict[str, Any] | None = None,
    ) -> AliasProposalResult | None:
        """Atomically publish an unverified alias link and its journal."""
        from prme.storage.alias_proposal import propose_duckdb
        return await propose_duckdb(
            self, node_a_id, node_b_id, user_id=user_id,
            alias_type=alias_type, score=score, evidence=evidence,
        )

    async def review_alias_proposal(
        self, proposal_operation_id: str, *, user_id: str, decision: str,
        reviewer_id: str, reason: str | None = None,
    ):
        """Atomically record a review and publish an accepted identity link."""
        from prme.storage.alias_review import review_duckdb
        return await review_duckdb(
            self, proposal_operation_id, user_id=user_id, decision=decision,
            reviewer_id=reviewer_id, reason=reason,
        )

    async def list_alias_proposals(
        self, *, user_id: str, scope=None, status=None, limit: int = 100,
    ):
        """List durable alias proposals with their current review status."""
        from prme.storage.alias_review import list_duckdb
        return await list_duckdb(
            self, user_id=user_id, scope=scope, status=status, limit=limit,
        )

    async def create_edge(self, edge: MemoryEdge) -> str:
        """Create a new edge between two nodes.

        Args:
            edge: The MemoryEdge to store.

        Returns:
            String UUID of the created edge.
        """
        async with self._conn_lock:
            await run_to_completion(self._create_edge_sync, edge)
        return str(edge.id)

    async def get_edges(
        self,
        *,
        source_id: str | None = None,
        target_id: str | None = None,
        node_ids: list[str] | None = None,
        edge_type: EdgeType | None = None,
        valid_at: datetime | None = None,
        min_confidence: float | None = None,
    ) -> list[MemoryEdge]:
        """Query edges with filters.

        Args:
            source_id: Filter by source node.
            target_id: Filter by target node.
            node_ids: Filter to edges touching ANY of these node IDs
                (source or target).
            edge_type: Filter by edge type.
            valid_at: Temporal validity filter.
            min_confidence: Minimum confidence threshold.

        Returns:
            List of matching MemoryEdges.
        """
        async with self._conn_lock:
            return await run_to_completion(
                self._get_edges_sync,
                source_id,
                target_id,
                node_ids,
                edge_type,
                valid_at,
                min_confidence,
            )

    # --- Lifecycle Transitions ---

    async def promote(
        self, node_id: str, *, request_id: str | UUID | None = None,
        actor_id: str = "system",
    ) -> None:
        """Promote a tentative node to stable.

        Args:
            node_id: String UUID of the node to promote.

        Raises:
            ValueError: If the node doesn't exist or the transition is invalid.
        """
        from prme.storage.lifecycle import transition_duckdb
        await transition_duckdb(
            self, node_id, "promote", request_id=request_id, actor_id=actor_id,
        )

    async def supersede(
        self,
        old_node_id: str,
        new_node_id: str,
        *,
        evidence_id: str | None = None,
        actor_id: str = "system",
    ) -> None:
        """Mark a node as superseded by another.

        Creates a SUPERSEDES edge from new_node to old_node with optional
        evidence_id for provenance tracking. Updates the old node's
        lifecycle_state to 'superseded' and sets superseded_by pointer.

        Args:
            old_node_id: Node being replaced.
            new_node_id: Replacement node.
            evidence_id: Optional event ID providing evidence for the
                supersedence.

        Raises:
            ValueError: If either node doesn't exist or the transition
                is invalid.
        """
        await self.supersede_many(
            [(old_node_id, new_node_id, evidence_id)], actor_id=actor_id,
        )

    async def scan_nodes(
        self, *, user_id: str | None, scope: Scope | None = None,
        node_type: NodeType | None = None,
        lifecycle_states: list[LifecycleState] | None = None,
        after_id: str | None = None, limit: int = 100,
        operator_unscoped: bool = False,
        content_contains_any: list[str] | None = None,
        speaker: list[str] | None = None,
        evidence_events: list[str] | None = None,
    ) -> list[MemoryNode]:
        """Read a stable-ID page within a tenant or explicit operator scope.

        The optional filters narrow the page before it is materialized, so a
        caller can page over the records relevant to one subject without
        reading the whole scope. ``content_contains_any`` is a case-insensitive
        substring match, like ``query_nodes()``. ``speaker`` matches a record
        attributed to one of these names: the reserved source speaker or a
        claim's ``speaker_reference``. ``evidence_events`` matches a record
        whose evidence cites one of these event ids.
        """
        if (
            limit < 1
            or user_id == ""
            or (user_id is None and not operator_unscoped)
        ):
            raise ValueError("scan_nodes requires user_id and a positive limit")
        states = list(ACTIVE_LIFECYCLE_STATES) if lifecycle_states is None else lifecycle_states
        if not states:
            return []
        conditions = ["lifecycle_state IN (" + ",".join("?" for _ in states) + ")"]
        params: list = [state.value for state in states]
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        for field, value in (("scope", scope), ("node_type", node_type)):
            if value is not None:
                conditions.append(f"{field} = ?")
                params.append(value.value)
        if content_contains_any:
            conditions.append(
                "(" + " OR ".join(["LOWER(content) LIKE ? ESCAPE '\\'" for _ in content_contains_any]) + ")"
            )
            params.extend(f"%{self._escape_like(value.lower())}%" for value in content_contains_any)
        if speaker:
            folded = [value.lower() for value in speaker]
            slots = ",".join("?" for _ in folded)
            conditions.append(
                "(LOWER(TRIM(json_extract_string(metadata, '$.prme_speaker_v1'))) IN (" + slots + ") "
                "OR LOWER(TRIM(json_extract_string(metadata, '$.speaker_reference.speaker'))) "
                "IN (" + slots + "))"
            )
            params.extend([*folded, *folded])
        if evidence_events:
            conditions.append(
                "EXISTS (SELECT 1 FROM json_each(evidence_refs) AS cited "
                "JOIN json_each(?::JSON) AS wanted ON cited.value::VARCHAR = wanted.value::VARCHAR)"
            )
            params.append(json.dumps([str(UUID(event_id)) for event_id in evidence_events]))
        if after_id is not None:
            conditions.append("id > ?::UUID")
            params.append(str(UUID(after_id)))
        params.append(limit)
        sql = "SELECT * FROM nodes WHERE " + " AND ".join(conditions) + " ORDER BY id ASC LIMIT ?"

        def read() -> list[MemoryNode]:
            nodes = [self._row_to_node(row) for row in self._conn.execute(sql, params).fetchall()]
            return self._owned_view_sync(nodes, user_id) if user_id else nodes

        async with self._conn_lock:
            return await run_to_completion(read)

    async def scan_assertions(
        self, *, user_id: str, scope: Scope, node_types: tuple[NodeType, ...],
        subject_key: str, predicate_key: str,
    ) -> tuple[list[MemoryNode], int, int] | None:
        """Read one owner's claims at one stored address, with the two scan counts.

        Returns the rows at that address, the number of queryable rows of those node types,
        and how many of them carry no address at all. A caller counts
        ``selector_mismatch`` as the total minus the unkeyed minus what it got back, so the
        two numbers reproduce the counts the full scan reports without reading it.

        Returns None when this store has no address columns, which is a pack that predates
        them and has not been opened since. The caller then keeps the full scan.
        """
        from prme.storage.assertion_keys import (
            PREDICATE_COLUMN, SUBJECT_COLUMN, assertion_columns,
        )
        if not assertion_columns(self._conn):
            return None
        states = [state.value for state in LifecycleState]
        types = [node_type.value for node_type in node_types]
        type_slots = ",".join("?" for _ in types)
        state_slots = ",".join("?" for _ in states)
        # No index serves this read: the supported DuckDB chose a sequential scan with or
        # without one, and an index on a column ``update_node`` rewrites would turn that
        # update into a delete and an insert that skips an in-flight ``updated_at`` claim
        # (the reason ``idx_nodes_lifecycle`` is absent from ``schema.py``). The saving is
        # comparing two short stored strings instead of decoding JSON for every row.
        page_sql = (
            f"SELECT * FROM nodes WHERE user_id = ? AND scope = ? "
            f"AND node_type IN ({type_slots}) "
            f"AND {SUBJECT_COLUMN} = ? AND {PREDICATE_COLUMN} = ? "
            f"AND lifecycle_state IN ({state_slots}) ORDER BY id ASC"
        )
        page_params = [user_id, scope.value, *types, subject_key, predicate_key, *states]
        count_sql = (
            f"SELECT count(*), count(*) FILTER "
            f"(WHERE {SUBJECT_COLUMN} IS NULL OR {PREDICATE_COLUMN} IS NULL) "
            f"FROM nodes WHERE user_id = ? AND scope = ? AND node_type IN ({type_slots}) "
            f"AND lifecycle_state IN ({state_slots})"
        )
        count_params = [user_id, scope.value, *types, *states]

        def read():
            nodes = [self._row_to_node(row) for row in self._conn.execute(page_sql, page_params).fetchall()]
            total, unkeyed = self._conn.execute(count_sql, count_params).fetchone()
            return self._owned_view_sync(nodes, user_id), int(total), int(unkeyed)

        async with self._conn_lock:
            return await run_to_completion(read)

    async def retire_consolidated(self, source_id: str, summary_id: str, **policy: Any) -> bool:
        """Retire only current, eligible sources covered by an active summary."""
        from prme.storage.consolidation_retirement import retire_duckdb
        return await retire_duckdb(self, source_id, summary_id, **policy)

    @protected_mutation
    async def supersede_many(
        self,
        replacements: list[tuple[str, str, str | None]],
        *,
        actor_id: str = "system",
    ) -> None:
        """Commit all replacement states, edges and journals together."""
        if not replacements:
            return
        async with self._conn_lock:
            await run_to_completion(self._supersede_many_sync, replacements, actor_id)

    def _supersede_many_sync(
        self,
        replacements: list[tuple[str, str, str | None]],
        actor_id: str = "system",
    ) -> None:
        from prme.storage.supersedence import _request, _supersede_many_duckdb

        requests = [
            _request(old, new, evidence, actor_id)
            for old, new, evidence in replacements
        ]
        _supersede_many_duckdb(self, requests)

    @protected_mutation
    async def contradict(
        self,
        node_a_id: str,
        node_b_id: str,
        *,
        evidence_id: str | None = None,
        actor_id: str = "system",
    ) -> None:
        """Mark two nodes as contradicting each other.

        Creates a CONTRADICTS edge (from node_b to node_a), transitions both
        nodes to CONTESTED lifecycle state, and logs a CONTRADICTION_NOTED
        operation to the operations table.

        Args:
            node_a_id: First conflicting node (typically the existing/older node).
            node_b_id: Second conflicting node (typically the new/incoming node).
            evidence_id: Optional event ID providing evidence.

        Raises:
            ValueError: If either node is not found or not in an active state.
        """
        node_a_id, node_b_id = str(UUID(node_a_id)), str(UUID(node_b_id))
        async with self._conn_lock:
            await run_to_completion(
                self._atomic_sync, self._contradict_sync, node_a_id, node_b_id,
                evidence_id, actor_id,
            )

    @protected_mutation
    async def resolve_contradiction(
        self,
        winner_id: str,
        loser_id: str,
        *,
        resolver_actor_id: str = "system",
        evidence_id: str | None = None,
    ) -> None:
        """Resolve a contradiction by declaring a winner and loser.

        Validates both nodes are CONTESTED and a CONTRADICTS edge exists
        between them. Transitions winner to STABLE and loser to DEPRECATED.
        Logs EPISTEMIC_TRANSITION operations for both and a
        CONTRADICTION_RESOLVED operation.

        Args:
            winner_id: Node determined to be correct (-> STABLE).
            loser_id: Node determined to be incorrect (-> DEPRECATED).
            resolver_actor_id: ID of the resolving actor.
            evidence_id: Optional supporting evidence event ID.

        Raises:
            ValueError: If nodes are not CONTESTED or no CONTRADICTS edge exists.
        """
        winner_id, loser_id = str(UUID(winner_id)), str(UUID(loser_id))
        async with self._conn_lock:
            await run_to_completion(
                self._atomic_sync,
                self._resolve_contradiction_sync,
                winner_id,
                loser_id,
                resolver_actor_id,
                evidence_id,
            )

    async def archive(
        self, node_id: str, *, request_id: str | UUID | None = None,
        actor_id: str = "system",
    ) -> None:
        """Archive a node (terminal state).

        Any non-archived node can be archived. Archived is terminal --
        no further transitions are allowed.

        Args:
            node_id: String UUID of the node to archive.

        Raises:
            ValueError: If the node doesn't exist or is already archived.
        """
        from prme.storage.lifecycle import transition_duckdb
        await transition_duckdb(
            self, node_id, "archive", request_id=request_id, actor_id=actor_id,
        )

    async def archive_expired(
        self, node_id: str, *, user_id: str, evaluated_at: datetime,
    ) -> bool:
        """Atomically archive an expired node with its retention tombstone."""
        from prme.storage.retention import expire_duckdb
        return await expire_duckdb(
            self, node_id, user_id=user_id, evaluated_at=evaluated_at
        )

    async def deprecate(
        self, node_id: str, *, request_id: str | UUID | None = None,
        actor_id: str = "system",
    ) -> None:
        """Deprecate a node (mark as confirmed incorrect).

        Valid transitions to DEPRECATED: CONTESTED -> DEPRECATED.
        Used by the organizer for threshold-based deprecation when
        confidence drops below the deprecate threshold.

        Args:
            node_id: String UUID of the node to deprecate.

        Raises:
            ValueError: If the node doesn't exist or the transition
                is invalid.
        """
        from prme.storage.lifecycle import transition_duckdb
        await transition_duckdb(
            self, node_id, "deprecate", request_id=request_id, actor_id=actor_id,
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
        include_unverified_aliases: bool = False,
        user_id: str | None = None,
    ) -> list[MemoryNode]:
        """Get nodes within N hops of a starting node.

        Uses a recursive CTE on the edges table to find all reachable
        nodes within max_hops. Traverses edges in both directions
        (source->target and target->source) to find the full neighborhood.

        Args:
            node_id: Starting node ID.
            max_hops: Maximum number of hops (default 2).
            edge_types: Only traverse edges of these types.
            valid_at: Temporal filter for nodes.
            min_confidence: Minimum node confidence.
            include_superseded: Include superseded/archived nodes.
            include_unverified_aliases: Traverse unaccepted alias proposals.
            user_id: When given, the walk never steps onto another user's
                node, only the user's nodes are returned, and a starting
                node another user owns has no neighborhood.

        Returns:
            List of reachable MemoryNodes (excluding the starting node).
        """
        async with self._conn_lock:
            return await run_to_completion(
                self._get_neighborhood_sync,
                node_id,
                max_hops,
                edge_types,
                valid_at,
                min_confidence,
                include_superseded,
                include_unverified_aliases,
                user_id,
            )

    async def get_neighborhood_with_depth(
        self,
        node_id: str,
        *,
        max_hops: int = 2,
        edge_types: list[EdgeType] | None = None,
        valid_at: datetime | None = None,
        min_confidence: float | None = None,
        include_superseded: bool = False,
        include_unverified_aliases: bool = False,
        user_id: str | None = None,
    ) -> list[tuple[MemoryNode, int]]:
        """Get nodes within N hops along with their minimum hop distance.

        Runs a single cycle-guarded recursive CTE and returns each
        reachable node once with MIN(depth), so callers don't need to
        re-traverse the neighborhood per hop level.

        Args:
            node_id: Starting node ID.
            max_hops: Maximum number of hops (default 2).
            edge_types: Only traverse edges of these types.
            valid_at: Temporal filter for nodes.
            min_confidence: Minimum node confidence.
            include_superseded: Include superseded/archived nodes.
            include_unverified_aliases: Traverse unaccepted alias proposals.
            user_id: When given, the walk never steps onto another user's
                node, so it does not reach the user's nodes through one;
                only the user's nodes are returned, and a starting node
                another user owns has no neighborhood.

        Returns:
            List of (MemoryNode, min_depth) tuples, excluding the
            starting node.
        """
        async with self._conn_lock:
            return await run_to_completion(
                self._get_neighborhood_with_depth_sync,
                node_id,
                max_hops,
                edge_types,
                valid_at,
                min_confidence,
                include_superseded,
                include_unverified_aliases,
                user_id,
            )

    async def walk_from_seeds(
        self,
        seed_ids: list[str],
        *,
        max_hops: int,
        valid_at: datetime | None = None,
        user_id: str,
    ) -> list[tuple[WalkedNode, int]]:
        """Every node within ``max_hops`` of any seed, once, with its fewest hops.

        The per-seed walks of ``get_neighborhood_with_depth`` in one query
        (``prme.storage.seed_walk``): a seed is returned when another seed
        reaches it, at its fewest hops from another seed. The rows are small
        ``WalkedNode`` records; read full records with ``get_nodes``.
        """
        if not seed_ids:
            return []
        query = seed_walk_sql("duckdb", with_valid_at=valid_at is not None)
        params: list = [user_id, max_hops, list(seed_ids)]
        if valid_at is not None:
            params.append(valid_at)

        def read() -> list[tuple[WalkedNode, int]]:
            return walked_nodes(self._conn.execute(query, params).fetchall(), epoch=True)

        async with self._conn_lock:
            return await run_to_completion(read)

    async def walk_links(self, node_ids: list[str]) -> list[tuple[str, str]]:
        """The links a graph walk can step along that touch ``node_ids``, as two ids each.

        The edges ``get_edges(node_ids=...)`` returns, without an edge from a
        node to itself or an alias proposal no one accepted, as (source,
        target) text instead of full records (``prme.storage.seed_walk``).
        """
        ids = list(node_ids)
        if not ids:
            return []
        literals = _uuid_literals(ids)
        if literals is not None:
            query, params = walk_links_sql("duckdb", literals), []
        else:
            query, params = walk_links_sql("duckdb", ", ".join("?" for _ in ids)), ids + ids

        def read() -> list[tuple[str, str]]:
            return [(source, target) for source, target in self._conn.execute(query, params).fetchall()]

        async with self._conn_lock:
            return await run_to_completion(read)

    async def find_shortest_path(
        self,
        source_id: str,
        target_id: str,
        *,
        edge_types: list[EdgeType] | None = None,
        include_unverified_aliases: bool = False,
    ) -> list[str] | None:
        """Find the shortest path between two nodes via BFS.

        Uses a recursive CTE to perform breadth-first search from
        source to target, tracking the path at each step.

        Args:
            source_id: Starting node ID.
            target_id: Target node ID.
            edge_types: Only traverse edges of these types.
            include_unverified_aliases: Traverse unaccepted alias proposals.

        Returns:
            List of node IDs forming the shortest path (including
            source and target), or None if no path exists.
        """
        async with self._conn_lock:
            return await run_to_completion(
                self._find_shortest_path_sync,
                source_id,
                target_id,
                edge_types,
                include_unverified_aliases,
            )

    async def get_supersedence_chain(
        self,
        node_id: str,
        *,
        direction: str = "forward",
        user_id: str | None = None,
    ) -> list[MemoryNode]:
        """Traverse the supersedence chain from a node.

        SUPERSEDES edge direction: new_node -> old_node.
        - "forward" = what replaced this node (follow edges where this
          node is the TARGET of SUPERSEDES).
        - "backward" = what this node replaced (follow edges where this
          node is the SOURCE of SUPERSEDES).

        Args:
            node_id: Starting node ID.
            direction: "forward" or "backward".
            user_id: When given, a starting node another user owns has no
                chain, each step follows the newest link whose node is not
                another user's (so a fork follows the user's branch), the
                chain ends where only another user's node is left, and
                a retired node's ``superseded_by`` never
                names another user's node.

        Returns:
            Ordered list of MemoryNodes in the chain.
        """
        async with self._conn_lock:
            return await run_to_completion(
                self._get_supersedence_chain_sync, node_id, direction, user_id
            )

    async def find_cross_user_links(self, *, limit: int = 100) -> CrossUserLinks:
        """Count and list the links that cross users (an operator read).

        An edge crosses users when its two nodes, or the edge itself, do not
        all belong to one user; a pointer crosses users when a node's
        ``superseded_by`` names another user's node. Both are counted in
        full and listed, in id order, up to ``limit`` each.
        """
        if limit < 0:
            raise ValueError("limit must not be negative")
        async with self._conn_lock:
            return await run_to_completion(self._find_cross_user_links_sync, limit)

    # --- Cleanup / Rollback ---

    async def delete_node(self, node_id: str) -> None:
        """Delete a node by ID for rollback cleanup.

        No-op if the node does not exist. Logs the deletion at debug
        level for traceability.

        Args:
            node_id: String UUID of the node to delete.
        """
        # Defense-in-depth: primary write serialization is via WriteQueue
        async with self._conn_lock:
            await run_to_completion(self._delete_node_sync, node_id)

    async def delete_edge(self, edge_id: str) -> None:
        """Delete an edge by ID for rollback cleanup.

        No-op if the edge does not exist. Logs the deletion at debug
        level for traceability.

        Args:
            edge_id: String UUID of the edge to delete.
        """
        # Defense-in-depth: primary write serialization is via WriteQueue
        async with self._conn_lock:
            await run_to_completion(self._delete_edge_sync, edge_id)

    # --- Internal sync methods ---

    def _delete_node_sync(self, node_id: str) -> None:
        """Delete a node by ID (sync). No-op if not found."""
        self._conn.execute("DELETE FROM nodes WHERE id = ?", [node_id])
        logger.debug("graph.delete_node", extra={"node_id": node_id})

    def _delete_edge_sync(self, edge_id: str) -> None:
        """Delete an edge by ID (sync). No-op if not found."""
        self._conn.execute("DELETE FROM edges WHERE id = ?", [edge_id])
        logger.debug("graph.delete_edge", extra={"edge_id": edge_id})

    def _create_node_sync(self, node: MemoryNode) -> None:
        """Insert a node into the nodes table (sync).

        The claim address and the privacy marker go in this statement, not a second one:
        DuckDB rewrites a row on UPDATE as a delete plus an insert, so a later write costs a
        row rewrite and two index updates, while the same statement costs one more triple of
        values.
        """
        from prme.storage.assertion_keys import (
            PREDICATE_COLUMN, PRIVACY_COLUMN, SUBJECT_COLUMN, key_values,
        )

        evidence_json = (
            json.dumps([str(ref) for ref in node.evidence_refs])
            if node.evidence_refs
            else None
        )
        metadata_json = (
            json.dumps(node.metadata) if node.metadata is not None else None
        )
        subject_key, predicate_key, marked = key_values(node)

        self._conn.execute(
            f"""
            INSERT INTO nodes (
                id, node_type, user_id, session_id, scope, content,
                metadata, confidence, salience, lifecycle_state,
                valid_from, valid_to, superseded_by, evidence_refs,
                created_at, updated_at, epistemic_type, source_type,
                decay_profile, last_reinforced_at, reinforcement_boost,
                salience_base, confidence_base, pinned, ttl_days, event_time,
                {SUBJECT_COLUMN}, {PREDICATE_COLUMN}, {PRIVACY_COLUMN}
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
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
                node.ttl_days,
                node.event_time,
                subject_key,
                predicate_key,
                marked,
            ],
        )

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
        "ttl_days",
        "event_time",
    }

    def _update_node_sync(self, node_id: str, updates: dict) -> None:
        """Build and execute a dynamic UPDATE statement for the given fields."""
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

        # Verify the node exists
        existing = self._conn.execute(
            "SELECT id FROM nodes WHERE id = ?", [node_id]
        ).fetchone()
        if existing is None:
            raise ValueError(f"Node {node_id} not found")

        # Always set updated_at to current timestamp
        now = datetime.now(timezone.utc)
        if "updated_at" not in valid_updates:
            valid_updates["updated_at"] = now

        # Build SET clause with parameterized placeholders
        set_parts: list[str] = []
        params: list = []

        for field, value in valid_updates.items():
            set_parts.append(f"{field} = ?")
            # Serialize special types
            if field == "evidence_refs" and value is not None:
                params.append(json.dumps([str(u) for u in value]))
            elif field == "metadata" and value is not None:
                params.append(json.dumps(value))
            elif field == "decay_profile" and isinstance(value, DecayProfile):
                params.append(value.value)
            elif field == "lifecycle_state" and isinstance(value, LifecycleState):
                params.append(value.value)
            elif field == "superseded_by" and value is not None:
                params.append(str(value))
            elif field == "last_reinforced_at" and isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                params.append(value)
            else:
                params.append(value)

        if "metadata" in valid_updates:
            # A rewritten subject or predicate moves a claim to another address, and a
            # rewritten metadata may gain or lose the privacy marker. Both stored columns
            # are derived from ``metadata``, so they are rewritten in the same statement
            # that changes it and can never describe an older value.
            from prme.retrieval.aggregation import _assertion_values, normalize_assertion_value
            from prme.storage.assertion_keys import (
                PREDICATE_COLUMN, PRIVACY_COLUMN, SUBJECT_COLUMN, privacy_marker,
            )
            metadata_value = valid_updates["metadata"]
            values = _assertion_values(MemoryNode.model_construct(metadata=metadata_value))
            keys = (None, None) if values is None else (
                normalize_assertion_value("subject", values["subject"]),
                normalize_assertion_value("predicate", values["predicate"]),
            )
            set_parts.extend([
                f"{SUBJECT_COLUMN} = ?",
                f"{PREDICATE_COLUMN} = ?",
                f"{PRIVACY_COLUMN} = ?",
            ])
            params.extend([keys[0], keys[1], privacy_marker(metadata_value)])

        set_clause = ", ".join(set_parts)
        params.append(node_id)

        query = f"UPDATE nodes SET {set_clause} WHERE id = ?"
        self._conn.execute(query, params)

    def _get_node_sync(
        self, node_id: str, include_superseded: bool
    ) -> MemoryNode | None:
        """Retrieve a single node by ID (sync)."""
        if include_superseded:
            result = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ?", [node_id]
            ).fetchone()
        else:
            result = self._conn.execute(
                """
                SELECT * FROM nodes
                WHERE id = ?
                AND lifecycle_state IN ('tentative', 'stable', 'contested')
                """,
                [node_id],
            ).fetchone()

        if result is None:
            return None
        return self._row_to_node(result)

    def _get_nodes_sync(
        self, node_ids: list[str], include_superseded: bool
    ) -> list[MemoryNode]:
        """Retrieve multiple nodes by ID with one IN query (sync).

        Canonical UUID strings go into the SQL as literals, as in
        ``_get_edges_sync``: binding each id separately made reading 900
        nodes take about 60 ms instead of 12 (BOUNDED-GRAPH-WALK.md).
        """
        literals = _uuid_literals(list(node_ids))
        placeholders = literals if literals is not None else ", ".join(["?" for _ in node_ids])
        params = [] if literals is not None else list(node_ids)
        if include_superseded:
            query = f"SELECT * FROM nodes WHERE id IN ({placeholders})"
        else:
            query = f"""
                SELECT * FROM nodes
                WHERE id IN ({placeholders})
                AND lifecycle_state IN ('tentative', 'stable', 'contested')
            """
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_node(row) for row in rows]

    @staticmethod
    def _escape_like(pattern: str) -> str:
        """Escape LIKE wildcards in a user-supplied substring."""
        return (
            pattern.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )

    def _query_nodes_sync(
        self,
        node_type: NodeType | None,
        user_id: str | None,
        scope: Scope | None,
        scopes: list[Scope] | None,
        session_ids: list[str] | None,
        lifecycle_states: list[LifecycleState] | None,
        valid_at: datetime | None,
        min_confidence: float | None,
        min_salience: float | None,
        content_contains_any: list[str] | None,
        content_equals_any: list[str] | None,
        created_before: datetime | None,
        oldest_first: bool,
        exclude_archived_profiles: bool,
        limit: int,
    ) -> list[MemoryNode]:
        """Query nodes with dynamic WHERE clause (sync)."""
        conditions: list[str] = []
        params: list = []

        # Default lifecycle filter: active states only (includes CONTESTED
        # per Research Pitfall 3 -- contested nodes are active with unresolved
        # conflicts but still valid candidates for retrieval).
        if lifecycle_states is None:
            lifecycle_states = list(ACTIVE_LIFECYCLE_STATES)

        # Build lifecycle IN clause
        placeholders = ", ".join(["?" for _ in lifecycle_states])
        conditions.append(f"lifecycle_state IN ({placeholders})")
        params.extend([s.value for s in lifecycle_states])

        if node_type is not None:
            conditions.append("node_type = ?")
            params.append(node_type.value)

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)

        if scope is not None:
            conditions.append("scope = ?")
            params.append(scope.value)

        if scopes is not None and scopes:
            scope_placeholders = ", ".join(["?" for _ in scopes])
            conditions.append(f"scope IN ({scope_placeholders})")
            params.extend([s.value for s in scopes])

        if session_ids is not None and session_ids:
            sid_placeholders = ", ".join(["?" for _ in session_ids])
            conditions.append(f"session_id IN ({sid_placeholders})")
            params.extend(session_ids)

        if valid_at is not None:
            conditions.append(
                "valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
            )
            params.extend([valid_at, valid_at])

        if min_confidence is not None:
            conditions.append("confidence >= ?")
            params.append(min_confidence)

        if min_salience is not None:
            conditions.append("salience >= ?")
            params.append(min_salience)

        if content_contains_any is not None and content_contains_any:
            like_clauses = " OR ".join(
                ["LOWER(content) LIKE ? ESCAPE '\\'" for _ in content_contains_any]
            )
            conditions.append(f"({like_clauses})")
            params.extend(
                f"%{self._escape_like(p.lower())}%" for p in content_contains_any
            )

        if content_equals_any is not None and content_equals_any:
            equals_placeholders = ", ".join(["?" for _ in content_equals_any])
            conditions.append(
                f"LOWER(TRIM(content)) IN ({equals_placeholders})"
            )
            params.extend(p.strip().lower() for p in content_equals_any)

        if created_before is not None:
            conditions.append("created_at <= ?")
            params.append(created_before)

        if exclude_archived_profiles:
            # A profile publication archives the previous SUMMARY node and
            # keeps its metadata, so a newest-first read across lifecycle
            # states turns into a run of retired profiles (issue #432).
            conditions.append(ARCHIVED_PROFILE_EXCLUSION_SQL)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        order_direction = "ASC" if oldest_first else "DESC"
        query = f"""
            SELECT * FROM nodes
            WHERE {where_clause}
            ORDER BY created_at {order_direction}
            LIMIT ?
        """
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_node(row) for row in rows]

    def _count_nodes_sync(
        self,
        user_id: str | None,
        lifecycle_states: list[LifecycleState] | None,
        exclude_archived_profiles: bool = False,
    ) -> int:
        """Count nodes with SELECT COUNT(*) (sync)."""
        conditions: list[str] = []
        params: list = []

        # Same default lifecycle filter as _query_nodes_sync.
        if lifecycle_states is None:
            lifecycle_states = list(ACTIVE_LIFECYCLE_STATES)

        placeholders = ", ".join(["?" for _ in lifecycle_states])
        conditions.append(f"lifecycle_state IN ({placeholders})")
        params.extend([s.value for s in lifecycle_states])

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)

        if exclude_archived_profiles:
            # Matches _query_nodes_sync so the count sees the same page.
            conditions.append(ARCHIVED_PROFILE_EXCLUSION_SQL)

        where_clause = " AND ".join(conditions)
        query = f"SELECT COUNT(*) FROM nodes WHERE {where_clause}"

        row = self._conn.execute(query, params).fetchone()
        return int(row[0]) if row else 0

    def _create_edge_sync(self, edge: MemoryEdge) -> None:
        """Insert an edge into the edges table (sync)."""
        metadata_json = (
            json.dumps(edge.metadata) if edge.metadata is not None else None
        )

        self._conn.execute(
            """
            INSERT INTO edges (
                id, source_id, target_id, edge_type, user_id,
                confidence, valid_from, valid_to, provenance_event_id,
                metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(edge.id),
                str(edge.source_id),
                str(edge.target_id),
                edge.edge_type.value,
                edge.user_id,
                edge.confidence,
                edge.valid_from,
                edge.valid_to,
                (
                    str(edge.provenance_event_id)
                    if edge.provenance_event_id
                    else None
                ),
                metadata_json,
                edge.created_at,
            ],
        )

    def _get_edges_sync(
        self,
        source_id: str | None,
        target_id: str | None,
        node_ids: list[str] | None,
        edge_type: EdgeType | None,
        valid_at: datetime | None,
        min_confidence: float | None,
    ) -> list[MemoryEdge]:
        """Query edges with dynamic WHERE clause (sync)."""
        conditions: list[str] = []
        params: list = []

        if source_id is not None:
            conditions.append("source_id = ?")
            params.append(source_id)

        if target_id is not None:
            conditions.append("target_id = ?")
            params.append(target_id)

        if node_ids is not None and node_ids:
            # Canonical UUID strings go into the SQL as literals: DuckDB binds
            # each parameter separately, and the seed reach read passes up to
            # 5,000 ids twice (BOUNDED-GRAPH-WALK.md, 2026-11-12). Any other
            # value keeps the bound parameters.
            ids = list(node_ids)
            literals = _uuid_literals(ids)
            if literals is not None:
                conditions.append(f"(source_id IN ({literals}) OR target_id IN ({literals}))")
            else:
                id_placeholders = ", ".join(["?" for _ in ids])
                conditions.append(
                    f"(source_id IN ({id_placeholders}) "
                    f"OR target_id IN ({id_placeholders}))"
                )
                params.extend(ids)
                params.extend(ids)

        if edge_type is not None:
            conditions.append("edge_type = ?")
            params.append(edge_type.value)

        if valid_at is not None:
            conditions.append(
                "valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
            )
            params.extend([valid_at, valid_at])

        if min_confidence is not None:
            conditions.append("confidence >= ?")
            params.append(min_confidence)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"""
            SELECT * FROM edges
            WHERE {where_clause}
            ORDER BY created_at DESC
        """

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_edge(row) for row in rows]

    # --- Lifecycle sync methods ---

    def _atomic_sync(self, operation: Callable[..., None], *args: Any) -> None:
        """Keep a multi-write lifecycle operation and its audit trail inseparable."""
        self._conn.execute("BEGIN TRANSACTION")
        try:
            operation(*args)
            journal_deadline()
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def _contradict_sync(
        self,
        node_a_id: str,
        node_b_id: str,
        evidence_id: str | None,
        actor_id: str,
    ) -> None:
        """Mark two nodes as contradicting each other (sync).

        Creates CONTRADICTS edge from node_b (newer) to node_a (older),
        transitions both nodes to CONTESTED, and logs CONTRADICTION_NOTED
        operation.
        """
        import uuid as _uuid

        # Validate both nodes exist and are in active state
        row_a = self._conn.execute(
            "SELECT lifecycle_state, user_id, scope FROM nodes WHERE id = ?",
            [node_a_id],
        ).fetchone()
        if row_a is None:
            raise ValueError(f"Node {node_a_id} not found")

        row_b = self._conn.execute(
            "SELECT lifecycle_state, user_id, scope FROM nodes WHERE id = ?",
            [node_b_id],
        ).fetchone()
        if row_b is None:
            raise ValueError(f"Node {node_b_id} not found")

        if UUID(node_a_id) == UUID(node_b_id):
            raise ValueError("A node cannot contradict itself")
        if row_a[1:] != row_b[1:]:
            raise ValueError("Contradiction nodes must have the same user and scope")

        state_a = LifecycleState(row_a[0])
        state_b = LifecycleState(row_b[0])

        from prme.storage.transition_evidence import validate_duckdb
        provenance_uuid = validate_duckdb(self._conn, evidence_id, row_a[1], row_a[2])
        evidence_id = str(provenance_uuid) if provenance_uuid is not None else None
        existing_edge = self._conn.execute(
            """SELECT provenance_event_id FROM edges WHERE edge_type='contradicts'
            AND ((source_id=CAST(? AS UUID) AND target_id=CAST(? AS UUID))
              OR (source_id=CAST(? AS UUID) AND target_id=CAST(? AS UUID)))
            LIMIT 1""",
            [node_a_id, node_b_id, node_b_id, node_a_id],
        ).fetchone()
        if (
            state_a == LifecycleState.CONTESTED
            and state_b == LifecycleState.CONTESTED
            and existing_edge is not None
        ):
            existing_evidence = (
                str(existing_edge[0]) if existing_edge[0] is not None else None
            )
            prior = self._conn.execute(
                """SELECT payload,actor_id FROM operations
                WHERE op_type='CONTRADICTION_NOTED' AND target_id=?
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                [node_a_id],
            ).fetchone()
            payload = json.loads(prior[0]) if prior and isinstance(prior[0], str) else (
                prior[0] if prior else None
            )
            if (
                existing_evidence == evidence_id
                and payload
                and payload.get("node_a_id") == node_a_id
                and payload.get("node_b_id") == node_b_id
                and prior[1] == actor_id
            ):
                return
            raise ValueError("Contradiction already exists with different inputs")
        if existing_edge is not None:
            raise ValueError("Contradiction edge exists without matching contested state")

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

        # Transition both nodes to CONTESTED
        self._conn.execute(
            """
            UPDATE nodes
            SET lifecycle_state = ?, updated_at = current_timestamp
            WHERE id = ?
            """,
            [LifecycleState.CONTESTED.value, node_a_id],
        )
        self._conn.execute(
            """
            UPDATE nodes
            SET lifecycle_state = ?, updated_at = current_timestamp
            WHERE id = ?
            """,
            [LifecycleState.CONTESTED.value, node_b_id],
        )

        edge = MemoryEdge(
            source_id=UUID(node_b_id),
            target_id=UUID(node_a_id),
            edge_type=EdgeType.CONTRADICTS,
            user_id=row_a[1],  # Use the older node's user_id
            confidence=1.0,
            provenance_event_id=provenance_uuid,
        )
        self._create_edge_sync(edge)

        # Log CONTRADICTION_NOTED operation
        op_id = str(_uuid.uuid4())
        payload = json.dumps({
            "node_a_id": node_a_id,
            "node_b_id": node_b_id,
            "evidence_event_id": evidence_id,
        })
        self._conn.execute(
            """
            INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at)
            VALUES (?, 'CONTRADICTION_NOTED', ?, ?, ?, now())
            """,
            [op_id, node_a_id, payload, actor_id],
        )

    def _resolve_contradiction_sync(
        self,
        winner_id: str,
        loser_id: str,
        resolver_actor_id: str,
        evidence_id: str | None,
    ) -> None:
        """Resolve a contradiction by declaring a winner and loser (sync).

        Validates both nodes are CONTESTED and a CONTRADICTS edge exists
        between them. Transitions winner to STABLE and loser to DEPRECATED.
        Logs EPISTEMIC_TRANSITION operations for both and a
        CONTRADICTION_RESOLVED operation.
        """
        import uuid as _uuid

        # Validate both nodes exist and are CONTESTED
        winner_row = self._conn.execute(
            "SELECT lifecycle_state, user_id, scope FROM nodes WHERE id = ?",
            [winner_id],
        ).fetchone()
        if winner_row is None:
            raise ValueError(f"Winner node {winner_id} not found")

        loser_row = self._conn.execute(
            "SELECT lifecycle_state, user_id, scope FROM nodes WHERE id = ?",
            [loser_id],
        ).fetchone()
        if loser_row is None:
            raise ValueError(f"Loser node {loser_id} not found")

        if UUID(winner_id) == UUID(loser_id):
            raise ValueError("A node cannot resolve a contradiction with itself")
        if winner_row[1:] != loser_row[1:]:
            raise ValueError("Contradiction nodes must have the same user and scope")

        winner_state = LifecycleState(winner_row[0])
        loser_state = LifecycleState(loser_row[0])

        edge_row = self._conn.execute(
            """
            SELECT id FROM edges
            WHERE edge_type = 'contradicts'
            AND (
                (source_id = CAST(? AS UUID) AND target_id = CAST(? AS UUID))
                OR
                (source_id = CAST(? AS UUID) AND target_id = CAST(? AS UUID))
            )
            LIMIT 1
            """,
            [winner_id, loser_id, loser_id, winner_id],
        ).fetchone()

        from prme.storage.transition_evidence import validate_duckdb
        evidence = validate_duckdb(self._conn, evidence_id, winner_row[1], winner_row[2])
        evidence_id = str(evidence) if evidence is not None else None
        if (
            winner_state == LifecycleState.STABLE
            and loser_state == LifecycleState.DEPRECATED
            and edge_row is not None
        ):
            prior = self._conn.execute(
                """SELECT payload,actor_id FROM operations
                WHERE op_type='CONTRADICTION_RESOLVED' AND target_id=?
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                [winner_id],
            ).fetchone()
            payload = json.loads(prior[0]) if prior and isinstance(prior[0], str) else (
                prior[0] if prior else None
            )
            if (
                payload
                and payload.get("winner_id") == winner_id
                and payload.get("loser_id") == loser_id
                and payload.get("evidence_event_id") == evidence_id
                and prior[1] == resolver_actor_id
            ):
                return
            raise ValueError("Contradiction was already resolved with different inputs")

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

        if edge_row is None:
            raise ValueError(
                f"No CONTRADICTS edge exists between {winner_id} and {loser_id}"
            )

        # Transition winner to STABLE
        self._conn.execute(
            """
            UPDATE nodes
            SET lifecycle_state = ?, updated_at = current_timestamp
            WHERE id = ?
            """,
            [LifecycleState.STABLE.value, winner_id],
        )

        # Transition loser to DEPRECATED
        self._conn.execute(
            """
            UPDATE nodes
            SET lifecycle_state = ?, updated_at = current_timestamp
            WHERE id = ?
            """,
            [LifecycleState.DEPRECATED.value, loser_id],
        )

        # Log EPISTEMIC_TRANSITION for winner (CONTESTED -> STABLE)
        self._conn.execute(
            """
            INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at)
            VALUES (?, 'EPISTEMIC_TRANSITION', ?, ?, ?, now())
            """,
            [
                str(_uuid.uuid4()),
                winner_id,
                json.dumps({
                    "from_state": LifecycleState.CONTESTED.value,
                    "to_state": LifecycleState.STABLE.value,
                    "reason": "contradiction_resolved_winner",
                    "evidence_event_id": evidence_id,
                }),
                resolver_actor_id,
            ],
        )

        # Log EPISTEMIC_TRANSITION for loser (CONTESTED -> DEPRECATED)
        self._conn.execute(
            """
            INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at)
            VALUES (?, 'EPISTEMIC_TRANSITION', ?, ?, ?, now())
            """,
            [
                str(_uuid.uuid4()),
                loser_id,
                json.dumps({
                    "from_state": LifecycleState.CONTESTED.value,
                    "to_state": LifecycleState.DEPRECATED.value,
                    "reason": "contradiction_resolved_loser",
                    "evidence_event_id": evidence_id,
                }),
                resolver_actor_id,
            ],
        )

        # Log CONTRADICTION_RESOLVED operation
        self._conn.execute(
            """
            INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at)
            VALUES (?, 'CONTRADICTION_RESOLVED', ?, ?, ?, now())
            """,
            [
                str(_uuid.uuid4()),
                winner_id,
                json.dumps({
                    "winner_id": winner_id,
                    "loser_id": loser_id,
                    "evidence_event_id": evidence_id,
                }),
                resolver_actor_id,
            ],
        )

    # --- Traversal sync methods ---

    def _get_neighborhood_sync(
        self,
        node_id: str,
        max_hops: int,
        edge_types: list[EdgeType] | None,
        valid_at: datetime | None,
        min_confidence: float | None,
        include_superseded: bool,
        include_unverified_aliases: bool,
        user_id: str | None = None,
    ) -> list[MemoryNode]:
        """Get neighborhood via recursive CTE (sync).

        Delegates to the depth-aware variant and discards depths -- the
        reachable node set is identical.
        """
        results = self._get_neighborhood_with_depth_sync(
            node_id,
            max_hops,
            edge_types,
            valid_at,
            min_confidence,
            include_superseded,
            include_unverified_aliases,
            user_id,
        )
        return [node for node, _depth in results]

    def _get_neighborhood_with_depth_sync(
        self,
        node_id: str,
        max_hops: int,
        edge_types: list[EdgeType] | None,
        valid_at: datetime | None,
        min_confidence: float | None,
        include_superseded: bool,
        include_unverified_aliases: bool,
        user_id: str | None = None,
    ) -> list[tuple[MemoryNode, int]]:
        """Get neighborhood with min hop depth via recursive CTE (sync).

        Traverses edges in both directions. The recursive CTE keeps one row
        per (node, depth) -- UNION drops a row it has already produced -- so
        a node reached by many paths is expanded once per depth instead of
        once per path, which would multiply with every hub entity on the
        way. Each reachable node is returned once with its minimum depth
        (GROUP BY id, MIN(depth)).

        With ``user_id`` every step joins the node it reaches and drops it
        when another user owns it, so the walk never passes through another
        user's node (#438). A node that no longer exists is passed as
        before; it belongs to nobody.
        """
        if user_id is not None:
            start = self._conn.execute(
                "SELECT user_id FROM nodes WHERE id = ?", [node_id]
            ).fetchone()
            if start is not None and start[0] != user_id:
                return []
        logger.debug(
            "get_neighborhood using recursive CTE (SQL fallback)",
            extra={"node_id": node_id, "max_hops": max_hops},
        )

        # Build edge filter clause
        edge_conditions = []
        edge_params: list = []
        if edge_types is not None:
            placeholders = ", ".join(["?" for _ in edge_types])
            edge_conditions.append(f"e.edge_type IN ({placeholders})")
            edge_params.extend([et.value for et in edge_types])
        if not include_unverified_aliases:
            edge_conditions.append(
                "NOT (e.edge_type = 'relates_to' "
                "AND COALESCE(json_extract_string(e.metadata, '$.relation'), '') = 'alias' "
                "AND COALESCE(json_extract_string(e.metadata, '$.identity_verified'), '') = 'false')"
            )

        edge_where = (
            "AND " + " AND ".join(edge_conditions) if edge_conditions else ""
        )

        # Build node filter clause
        node_conditions = []
        node_params: list = []

        if not include_superseded:
            node_conditions.append(
                "n.lifecycle_state IN ('tentative', 'stable', 'contested')"
            )

        if valid_at is not None:
            node_conditions.append(
                "n.valid_from <= ? AND (n.valid_to IS NULL OR n.valid_to > ?)"
            )
            node_params.extend([valid_at, valid_at])

        if min_confidence is not None:
            node_conditions.append("n.confidence >= ?")
            node_params.append(min_confidence)

        if user_id is not None:
            node_conditions.append("n.user_id = ?")
            node_params.append(user_id)

        node_where = (
            "AND " + " AND ".join(node_conditions) if node_conditions else ""
        )

        # The owner guard on each step: join the node the step reaches and
        # keep the step unless another user owns that node.
        if user_id is not None:
            base_join = (
                "LEFT JOIN nodes o ON o.id = CASE WHEN e.source_id = CAST(? AS UUID) "
                "THEN e.target_id ELSE e.source_id END"
            )
            step_join = (
                "LEFT JOIN nodes o ON o.id = CASE WHEN e.source_id = nb.id "
                "THEN e.target_id ELSE e.source_id END"
            )
            owner_where = "AND (o.id IS NULL OR o.user_id = ?)"
        else:
            base_join = step_join = owner_where = ""

        # Recursive CTE: base case finds direct neighbors (both directions),
        # recursive case expands from there. Uses CASE to handle bidirectional
        # traversal in a single UNION. A row is a node and the depth it was
        # reached at; UNION drops repeats, so every node is expanded once per
        # depth however many paths lead to it. The outer query keeps the
        # minimum depth of each node.
        query = f"""
            WITH RECURSIVE neighborhood AS (
                -- Base case: direct neighbors in both directions
                SELECT
                    CASE WHEN e.source_id = CAST(? AS UUID)
                         THEN e.target_id
                         ELSE e.source_id
                    END AS id,
                    1 AS depth
                FROM edges e
                {base_join}
                WHERE (e.source_id = CAST(? AS UUID) OR e.target_id = CAST(? AS UUID))
                    {edge_where}
                    {owner_where}

                UNION

                -- Recursive case: expand from discovered neighbors.
                SELECT
                    CASE WHEN e.source_id = nb.id
                         THEN e.target_id
                         ELSE e.source_id
                    END AS id,
                    nb.depth + 1 AS depth
                FROM neighborhood nb
                JOIN edges e ON (e.source_id = nb.id OR e.target_id = nb.id)
                    {edge_where}
                {step_join}
                WHERE nb.depth < ?
                    {owner_where}
            )
            SELECT n.*, nb.min_depth
            FROM (
                SELECT id, MIN(depth) AS min_depth
                FROM neighborhood
                GROUP BY id
            ) nb
            JOIN nodes n ON nb.id = n.id
            WHERE n.id != CAST(? AS UUID) {node_where}
        """

        scoped = user_id is not None
        params: list = []
        # Base case: 3 refs to node_id (4 with the owner join) + edge_params
        # (+ the owner)
        params.append(node_id)
        if scoped:
            params.append(node_id)
        params.append(node_id)
        params.append(node_id)
        params.extend(edge_params)
        if scoped:
            params.append(user_id)
        # Recursive case: edge_params + max_hops (+ the owner)
        params.extend(edge_params)
        params.append(max_hops)
        if scoped:
            params.append(user_id)
        # Final WHERE: node_id + node_params
        params.append(node_id)
        params.extend(node_params)

        rows = self._conn.execute(query, params).fetchall()
        # The depth column trails the full nodes row; _row_to_node reads
        # fixed positions and ignores trailing columns.
        results = [(self._row_to_node(row), int(row[-1])) for row in rows]
        if not scoped:
            return results
        nodes = self._owned_view_sync([node for node, _depth in results], user_id)
        return [(node, depth) for node, (_node, depth) in zip(nodes, results)]

    def _find_shortest_path_sync(
        self,
        source_id: str,
        target_id: str,
        edge_types: list[EdgeType] | None,
        include_unverified_aliases: bool,
    ) -> list[str] | None:
        """BFS shortest path via iterative query (sync).

        Uses Python-side BFS with SQL queries per level to avoid
        DuckDB recursive CTE limitations with complex path tracking.
        """
        logger.debug(
            "find_shortest_path using iterative BFS",
            extra={"source_id": source_id, "target_id": target_id},
        )

        if source_id == target_id:
            return [source_id]

        # Build edge filter clause
        edge_conditions = []
        edge_params: list = []
        if edge_types is not None:
            placeholders = ", ".join(["?" for _ in edge_types])
            edge_conditions.append(f"edge_type IN ({placeholders})")
            edge_params.extend([et.value for et in edge_types])
        if not include_unverified_aliases:
            edge_conditions.append(
                "NOT (edge_type = 'relates_to' "
                "AND COALESCE(json_extract_string(metadata, '$.relation'), '') = 'alias' "
                "AND COALESCE(json_extract_string(metadata, '$.identity_verified'), '') = 'false')"
            )

        edge_where = (
            "AND " + " AND ".join(edge_conditions) if edge_conditions else ""
        )

        # BFS: track visited nodes and parent pointers
        visited: set[str] = {source_id}
        parent: dict[str, str] = {}
        frontier = [source_id]
        max_depth = 10

        for _ in range(max_depth):
            if not frontier:
                return None

            # Find all neighbors of frontier nodes
            next_frontier: list[str] = []
            for current_id in frontier:
                # Get neighbors in both directions
                query = f"""
                    SELECT
                        CASE WHEN source_id = CAST(? AS UUID)
                             THEN CAST(target_id AS VARCHAR)
                             ELSE CAST(source_id AS VARCHAR)
                        END AS neighbor_id
                    FROM edges
                    WHERE (source_id = CAST(? AS UUID) OR target_id = CAST(? AS UUID))
                        {edge_where}
                """
                params = [current_id, current_id, current_id]
                params.extend(edge_params)

                rows = self._conn.execute(query, params).fetchall()
                for row in rows:
                    neighbor_id = str(row[0])
                    if neighbor_id not in visited:
                        visited.add(neighbor_id)
                        parent[neighbor_id] = current_id
                        next_frontier.append(neighbor_id)

                        if neighbor_id == target_id:
                            # Reconstruct path
                            path = [target_id]
                            node = target_id
                            while node != source_id:
                                node = parent[node]
                                path.append(node)
                            path.reverse()
                            return path

            frontier = next_frontier

        return None

    def _get_supersedence_chain_sync(
        self, node_id: str, direction: str, user_id: str | None = None
    ) -> list[MemoryNode]:
        """Traverse supersedence chain via iterative queries (sync).

        SUPERSEDES edge direction: new_node (source) -> old_node (target).
        - "forward" = what replaced this node. This node is the TARGET
          of a SUPERSEDES edge; follow source_id to find the replacer,
          then continue from there.
        - "backward" = what this node replaced. This node is the SOURCE
          of a SUPERSEDES edge; follow target_id to find the replaced,
          then continue from there.
        """
        logger.debug(
            "get_supersedence_chain using iterative SQL",
            extra={"node_id": node_id, "direction": direction},
        )

        if user_id is not None:
            return self._get_owned_supersedence_chain_sync(node_id, direction, user_id)

        chain: list[MemoryNode] = []
        visited: set[str] = {node_id}
        current_id = node_id

        # Max chain length to prevent infinite loops
        max_depth = 100

        for _ in range(max_depth):
            if direction == "forward":
                # What replaced this node?
                # Find SUPERSEDES edge where current is TARGET (old node)
                row = self._conn.execute(
                    """
                    SELECT source_id FROM edges
                    WHERE target_id = ? AND edge_type = 'supersedes'
                    LIMIT 1
                    """,
                    [current_id],
                ).fetchone()
            elif direction == "backward":
                # What did this node replace?
                # Find SUPERSEDES edge where current is SOURCE (new node)
                row = self._conn.execute(
                    """
                    SELECT target_id FROM edges
                    WHERE source_id = ? AND edge_type = 'supersedes'
                    LIMIT 1
                    """,
                    [current_id],
                ).fetchone()
            else:
                raise ValueError(
                    f"Invalid direction: {direction}. Use 'forward' or 'backward'."
                )

            if row is None:
                break

            next_id = str(row[0])
            if next_id in visited:
                break  # Cycle detection

            visited.add(next_id)

            # Fetch the node (include all states since chain may have archived nodes)
            node_row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ?", [next_id]
            ).fetchone()
            if node_row is None:
                break

            chain.append(self._row_to_node(node_row))
            current_id = next_id

        return chain

    def _get_owned_supersedence_chain_sync(
        self, node_id: str, direction: str, user_id: str
    ) -> list[MemoryNode]:
        """The supersedence chain one user reads (sync, #437, #444).

        Each step reads every SUPERSEDES link of the current node, newest
        first (``get_edges`` order, which the admitted view of a protected
        store follows), and follows the first one whose node another user
        does not own, so a fork resolves the same way on every read. A link
        to a deleted node ends the chain, as it does unscoped.
        """
        if direction == "forward":
            step = (
                "SELECT CAST(source_id AS VARCHAR) FROM edges "
                "WHERE target_id = ? AND edge_type = 'supersedes' "
                "ORDER BY created_at DESC, id"
            )
        elif direction == "backward":
            step = (
                "SELECT CAST(target_id AS VARCHAR) FROM edges "
                "WHERE source_id = ? AND edge_type = 'supersedes' "
                "ORDER BY created_at DESC, id"
            )
        else:
            raise ValueError(
                f"Invalid direction: {direction}. Use 'forward' or 'backward'."
            )
        start = self._conn.execute(
            "SELECT user_id FROM nodes WHERE id = ?", [node_id]
        ).fetchone()
        if start is not None and start[0] != user_id:
            return []

        chain: list[MemoryNode] = []
        visited: set[str] = {str(node_id)}
        current_id = node_id
        for _ in range(100):
            linked = [str(row[0]) for row in self._conn.execute(step, [current_id]).fetchall()]
            foreign = self._foreign_ids_sync(linked, user_id)
            next_id = next((item for item in linked if item not in foreign), None)
            if next_id is None or next_id in visited:
                break
            visited.add(next_id)
            node_row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ?", [next_id]
            ).fetchone()
            if node_row is None:
                break
            chain.append(self._row_to_node(node_row))
            current_id = next_id
        return self._owned_view_sync(chain, user_id)

    # --- Owner-scoped read helpers ---

    def _foreign_ids_sync(self, node_ids: list[str], user_id: str) -> set[str]:
        """The given node ids that another user owns (sync)."""
        ids = sorted(set(node_ids))
        if not ids:
            return set()
        placeholders = ", ".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT CAST(id AS VARCHAR) FROM nodes "
            f"WHERE id IN ({placeholders}) AND user_id <> ?",
            [*ids, user_id],
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _owned_view_sync(self, nodes: list[MemoryNode], user_id: str) -> list[MemoryNode]:
        """Clear a ``superseded_by`` that names another user's node (sync, #443)."""
        named = named_successors(nodes)
        if not named:
            return nodes
        return hide_foreign_successors(nodes, self._foreign_ids_sync(named, user_id))

    def _find_cross_user_links_sync(self, limit: int) -> CrossUserLinks:
        edge_from = (
            "FROM edges e JOIN nodes s ON s.id = e.source_id "
            "JOIN nodes t ON t.id = e.target_id "
            f"WHERE {CROSS_USER_EDGE_WHERE}"
        )
        pointer_from = (
            "FROM nodes n JOIN nodes s ON s.id = n.superseded_by "
            f"WHERE {CROSS_USER_POINTER_WHERE}"
        )
        edge_count = self._conn.execute(f"SELECT COUNT(*) {edge_from}").fetchone()[0]
        pointer_count = self._conn.execute(f"SELECT COUNT(*) {pointer_from}").fetchone()[0]
        edges = self._conn.execute(
            "SELECT CAST(e.id AS VARCHAR), e.edge_type, e.user_id, "
            "CAST(e.source_id AS VARCHAR), s.user_id, "
            f"CAST(e.target_id AS VARCHAR), t.user_id {edge_from} "
            "ORDER BY e.id LIMIT ?",
            [limit],
        ).fetchall()
        pointers = self._conn.execute(
            "SELECT CAST(n.id AS VARCHAR), n.user_id, "
            f"CAST(n.superseded_by AS VARCHAR), s.user_id {pointer_from} "
            "ORDER BY n.id LIMIT ?",
            [limit],
        ).fetchall()
        return CrossUserLinks(
            edge_count=int(edge_count),
            pointer_count=int(pointer_count),
            edges=[CrossUserEdge(*map(str, row)) for row in edges],
            pointers=[CrossUserPointer(*map(str, row)) for row in pointers],
        )

    # --- Row conversion helpers ---

    def _row_to_node(self, row: tuple) -> MemoryNode:
        """Convert a DuckDB row tuple to a MemoryNode.

        Column order matches the CREATE TABLE definition:
        id, node_type, user_id, session_id, scope, content,
        metadata, confidence, salience, lifecycle_state,
        valid_from, valid_to, superseded_by, evidence_refs,
        created_at, updated_at
        """
        raw_id = row[0]
        node_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))

        # Parse evidence_refs from JSON
        raw_evidence = row[13]
        evidence_refs: list[UUID] = []
        if raw_evidence is not None:
            if isinstance(raw_evidence, str):
                evidence_refs = [UUID(ref) for ref in json.loads(raw_evidence)]
            elif isinstance(raw_evidence, list):
                evidence_refs = [UUID(str(ref)) for ref in raw_evidence]

        # Parse metadata
        raw_metadata = row[6]
        if isinstance(raw_metadata, str):
            metadata = json.loads(raw_metadata)
        else:
            metadata = raw_metadata

        # Parse superseded_by
        raw_superseded = row[12]
        superseded_by = None
        if raw_superseded is not None:
            superseded_by = (
                raw_superseded
                if isinstance(raw_superseded, UUID)
                else UUID(str(raw_superseded))
            )

        # Handle timezone-naive datetimes from DuckDB
        def ensure_tz(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            if isinstance(dt, datetime) and dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        # Graceful fallback for pre-migration rows (columns may not exist)
        raw_epistemic = row[16] if len(row) > 16 else "asserted"
        raw_source = row[17] if len(row) > 17 else "user_stated"

        # Decay/reinforcement fields (RFC-0015) -- positions 18-23
        raw_decay_profile = row[18] if len(row) > 18 else "medium"
        raw_last_reinforced = row[19] if len(row) > 19 else None
        raw_reinforcement_boost = row[20] if len(row) > 20 else 0.0
        raw_salience_base = row[21] if len(row) > 21 else row[8]  # fallback to salience
        raw_confidence_base = row[22] if len(row) > 22 else row[7]  # fallback to confidence
        raw_pinned = row[23] if len(row) > 23 else False
        raw_ttl_days = row[24] if len(row) > 24 else None

        # Bi-temporal field (issue #21) -- position 25
        raw_event_time = row[25] if len(row) > 25 else None

        return MemoryNode(
            id=node_id,
            node_type=NodeType(row[1]),
            user_id=row[2],
            session_id=row[3],
            scope=Scope(row[4]),
            content=row[5],
            metadata=metadata,
            confidence=row[7],
            salience=row[8],
            lifecycle_state=LifecycleState(row[9]),
            valid_from=ensure_tz(row[10]),
            valid_to=ensure_tz(row[11]),
            superseded_by=superseded_by,
            evidence_refs=evidence_refs,
            created_at=ensure_tz(row[14]),
            updated_at=ensure_tz(row[15]),
            epistemic_type=raw_epistemic,
            source_type=raw_source,
            decay_profile=DecayProfile(raw_decay_profile),
            last_reinforced_at=ensure_tz(raw_last_reinforced) or datetime.now(timezone.utc),
            reinforcement_boost=raw_reinforcement_boost,
            salience_base=raw_salience_base,
            confidence_base=raw_confidence_base,
            pinned=bool(raw_pinned),
            ttl_days=int(raw_ttl_days) if raw_ttl_days is not None else None,
            event_time=ensure_tz(raw_event_time),
        )

    def _row_to_edge(self, row: tuple) -> MemoryEdge:
        """Convert a DuckDB row tuple to a MemoryEdge.

        Column order matches the CREATE TABLE definition:
        id, source_id, target_id, edge_type, user_id,
        confidence, valid_from, valid_to, provenance_event_id,
        metadata, created_at
        """

        def to_uuid(val: object) -> UUID:
            return val if isinstance(val, UUID) else UUID(str(val))

        def ensure_tz(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            if isinstance(dt, datetime) and dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        # Parse provenance_event_id
        raw_provenance = row[8]
        provenance_event_id = (
            to_uuid(raw_provenance) if raw_provenance is not None else None
        )

        # Parse metadata
        raw_metadata = row[9]
        if isinstance(raw_metadata, str):
            metadata = json.loads(raw_metadata)
        else:
            metadata = raw_metadata

        return MemoryEdge(
            id=to_uuid(row[0]),
            source_id=to_uuid(row[1]),
            target_id=to_uuid(row[2]),
            edge_type=EdgeType(row[3]),
            user_id=row[4],
            confidence=row[5],
            valid_from=ensure_tz(row[6]),
            valid_to=ensure_tz(row[7]),
            provenance_event_id=provenance_event_id,
            metadata=metadata,
            created_at=ensure_tz(row[10]),
        )
