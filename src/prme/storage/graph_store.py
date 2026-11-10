"""GraphStore Protocol defining full-spec graph operations for PRME.

The GraphStore is a Protocol (structural typing) so that alternative
implementations (e.g., RyuGraph, pure SQL) can satisfy it without
inheriting from a base class. This is the primary escape hatch for
DuckPGQ limitations.
"""

from datetime import datetime
from uuid import UUID
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from prme.organizer.watermarks import OrganizerWatermark

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.storage.organizer_merge import MergeResult
from prme.storage.owner_reads import CrossUserLinks
from prme.storage.alias_proposal import AliasProposalEvidence, AliasProposalResult
from prme.storage.alias_review import (
    AliasProposalInboxItem,
    AliasProposalReviewResult,
    AliasProposalStatus,
)
from prme.models.derivation import DerivationPlan, DerivationReceipt
from prme.models.extraction_work import ExtractionClaim
from prme.models.reingest import ReingestRecord
from prme.models.profile import ProfilePublication
from prme.types import ConditionState, EdgeType, LifecycleState, NodeType, Scope


@runtime_checkable
class GraphStore(Protocol):
    """Full-spec graph store interface for PRME.

    Covers: node/edge CRUD, graph traversal (neighborhood, shortest path),
    supersedence chain traversal, and lifecycle transitions.

    All methods are async. Implementations wrap synchronous database
    calls with asyncio.to_thread().

    All queries enforce user_id scoping to prevent cross-user data leakage.
    Query defaults filter to active lifecycle states (tentative + stable)
    unless explicitly overridden.
    """

    # --- Node Operations ---

    async def reinforce_node(self, node_id: str, *, user_id: str | None, evidence_id: str | None, request_id: str | UUID | None = None) -> None:
        """Atomically validate, reinforce and journal a node without lost updates."""
        ...

    async def apply_oscillation_penalty(
        self, node_id: str, chain_node_ids: list[str], *, user_id: str,
    ) -> bool:
        """Atomically validate and journal one detected oscillation penalty."""
        ...

    async def evaluate_condition(
        self, node_id: str, state: ConditionState | str, **kwargs: Any
    ) -> MemoryNode:
        """Atomically evaluate a conditional claim and journal the transition."""
        ...

    async def commit_derivation(self, plan: DerivationPlan, *, claim: ExtractionClaim | None = None) -> DerivationReceipt:
        """Atomically publish the exact journaled plan or return its receipt."""
        ...

    async def commit_reingest(self, plan: DerivationPlan, record: ReingestRecord) -> ReingestRecord:
        """Atomically publish a re-derived source's plan, retire its earlier claims and mark it current."""
        ...

    async def profile_generation(self, key: str) -> int:
        """Read the current generation for an owner/scope/entity profile key."""
        ...

    async def publish_profile(self, plan: ProfilePublication) -> str:
        """Publish a prepared profile and retire its predecessors atomically."""
        ...

    async def merge_nodes(self, node_a_id: str, node_b_id: str, *, user_id: str, kind: str, score: float) -> MergeResult | None:
        """Validate and journal an organizer merge in one backend transaction."""
        ...

    async def propose_alias(
        self, node_a_id: str, node_b_id: str, *, user_id: str,
        alias_type: str, score: float,
        evidence: AliasProposalEvidence | dict[str, Any] | None = None,
    ) -> AliasProposalResult | None:
        """Publish one durable, unverified alias relationship for a node pair."""
        ...

    async def review_alias_proposal(
        self, proposal_operation_id: str, *, user_id: str, decision: str,
        reviewer_id: str, reason: str | None = None,
    ) -> AliasProposalReviewResult:
        """Atomically accept or reject one durable alias proposal."""
        ...

    async def list_alias_proposals(
        self, *, user_id: str, scope: Scope | str | None = None,
        status: AliasProposalStatus | str | None = None, limit: int = 100,
    ) -> list[AliasProposalInboxItem]:
        """List decoded alias proposals and their owner-scoped review state."""
        ...

    async def create_node(self, node: MemoryNode) -> str:
        """Create a new node in the graph.

        Args:
            node: The MemoryNode to store.

        Returns:
            String UUID of the created node.
        """
        ...

    async def get_node(
        self,
        node_id: str,
        *,
        include_superseded: bool = False,
        user_id: str | None = None,
    ) -> MemoryNode | None:
        """Retrieve a node by ID.

        Every read given a ``user_id`` never steps onto, returns or names
        another user's node (#438, #443, #444); see
        ``prme.storage.owner_reads``.

        Args:
            node_id: String UUID of the node.
            include_superseded: If False (default), returns None for
                superseded/archived nodes.
            user_id: When given, another user's node reads as not found,
                and a retired node's ``superseded_by`` never
                names another user's node.

        Returns:
            The MemoryNode if found and visible, None otherwise.
        """
        ...

    async def get_nodes(
        self,
        node_ids: list[str],
        *,
        include_superseded: bool = False,
        user_id: str | None = None,
    ) -> list[MemoryNode]:
        """Retrieve multiple nodes by ID in a single round trip.

        Batch equivalent of get_node() — one ``WHERE id IN (...)`` query
        instead of N sequential lookups. Visibility semantics match
        get_node(): superseded/archived nodes are excluded unless
        include_superseded is True. Missing IDs are silently omitted.

        Args:
            node_ids: String UUIDs of the nodes to fetch.
            include_superseded: If False (default), superseded/archived
                nodes are omitted from the result.
            user_id: When given, another user's nodes are omitted, and
                a retired node's ``superseded_by`` never
                names another user's node.

        Returns:
            List of found, visible MemoryNodes (order not guaranteed).
        """
        ...

    async def get_event_nodes(self, event_id: str, *, user_id: str) -> list[MemoryNode]:
        """Return all scoped graph nodes citing the event, including retired nodes."""
        ...

    async def get_session_neighbors(
        self,
        trigger_ids: list[str],
        *,
        user_id: str,
        window: int,
        scopes: list[Scope] | None = None,
    ) -> dict[str, list[MemoryNode]]:
        """Return active nodes within ``window`` turns of owned trigger nodes.

        Ordering and adjacency are computed independently for each exact
        ``(session_id, scope)`` partition using ``(created_at, id)``. The
        Each trigger ID maps to its own ordered window, including the visible
        trigger node. There is no whole-session row cap.
        """
        ...

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
        """Query nodes with filters.

        Args:
            node_type: Filter by node type.
            user_id: Filter by owner user. A node's ``superseded_by`` is
                then never another user's node.
            scope: Filter by a single memory scope.
            scopes: Filter by multiple scopes (``scope IN (...)``).
                Mutually exclusive with ``scope``.
            session_ids: Filter by session IDs (``session_id IN (...)``).
            lifecycle_states: Filter by lifecycle states. Defaults to
                active states (TENTATIVE, STABLE, CONTESTED).
            valid_at: Temporal filter -- return nodes valid at this time.
            min_confidence: Minimum confidence threshold.
            min_salience: Minimum salience threshold.
            content_contains_any: Case-insensitive substring filters --
                matches nodes whose content contains ANY of the given
                strings (pushed into SQL as LIKE clauses).
            content_equals_any: Case-insensitive whole-content filters --
                matches nodes whose trimmed, lowercased content equals ANY
                of the given trimmed, lowercased strings. An empty list
                applies no filter.
            created_before: Only return nodes created at or before this time
                (``created_at <= created_before``). Pushes age-based cutoffs
                into SQL so the limit applies after filtering.
            oldest_first: Order by ``created_at`` ascending instead of the
                default descending. Combined with ``created_before`` this lets
                age-gated scans drain the oldest eligible nodes first instead
                of repeatedly re-examining the newest window.
            exclude_archived_profiles: Leave out archived entity profile
                SUMMARY nodes (``metadata.entity_profile``) so a newest-first
                page is not a run of retired profiles. Applied inside the
                query, before the limit. Defaults to ``False``.
            limit: Maximum results to return.

        Returns:
            List of matching MemoryNodes.
        """
        ...

    async def count_nodes(
        self,
        *,
        user_id: str | None = None,
        lifecycle_states: list[LifecycleState] | None = None,
        exclude_archived_profiles: bool = False,
    ) -> int:
        """Count nodes matching the filters without materializing them.

        Args:
            user_id: Filter by owner user.
            lifecycle_states: Filter by lifecycle states. Defaults to
                active states (TENTATIVE, STABLE, CONTESTED), matching
                query_nodes().
            exclude_archived_profiles: Leave out archived entity profile
                SUMMARY nodes (``metadata.entity_profile``), matching
                query_nodes(). Applied inside the count query. Defaults to
                ``False``.

        Returns:
            Number of matching nodes.
        """
        ...

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
        """Read the nodes an incremental organizer job may examine.

        Orders by ``(updated_at, id)`` so a caller can page and resume with
        the last processed node as a keyset cursor. ``updated_after`` and
        ``after_id`` are the exclusive lower bound; ``updated_at_or_before``
        is the inclusive run ceiling.
        """
        ...

    async def get_organizer_watermark(
        self, *, scope_key: str, job: str,
    ) -> "OrganizerWatermark | None":
        """Read one scope and job's organizer watermark, or None."""
        ...

    async def set_organizer_watermark(
        self,
        *,
        scope_key: str,
        job: str,
        completed_through: datetime | None,
        resume_updated_at: datetime | None = None,
        resume_id: str | None = None,
    ) -> None:
        """Record a job's progress for one scope, monotonically.

        The store keeps the furthest completed position and the newer resume
        cursor, so two concurrent runs converge instead of the last write
        winning. A production writer must call ``MemoryEngine``'s method
        instead of this one: the engine also refuses a scope whose owner has an
        owner-wide memory erasure, which this protocol cannot see.
        """
        ...

    async def delete_organizer_watermark(self, *, scope_key: str, job: str) -> None:
        """Remove one scope and job's row."""
        ...

    # --- Node Update ---

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
        """Read an owner page, or an explicit operator page, in immutable ID order.

        ``content_contains_any``, ``speaker`` and ``evidence_events`` narrow
        the page before it is materialized, so a caller can page over the
        records relevant to one subject instead of reading a whole scope.
        """
        ...

    async def update_node(self, node_id: str, **updates: Any) -> None:
        """Update specific fields on an existing node.

        Args:
            node_id: UUID string of the node to update.
            **updates: Field names and values to update. Valid fields include:
                reinforcement_boost, last_reinforced_at, confidence_base,
                salience_base, decay_profile, pinned, evidence_refs,
                metadata, lifecycle_state, superseded_by, confidence,
                salience, updated_at.

        Raises:
            ValueError: If node_id does not exist or no valid fields provided.
        """
        ...

    # --- Edge Operations ---

    async def retire_consolidated(self, source_id: str, summary_id: str, **policy: Any) -> bool:
        """Atomically retire a source only while its summary coverage is valid."""
        ...

    async def supersede_many(
        self,
        replacements: list[tuple[str, str, str | None]],
        *,
        actor_id: str = "system",
    ) -> None:
        """Atomically replace nodes, create edges and journal each correction.

        Every pair must belong to the same user/scope. Failure leaves all
        states, pointers, and edges unchanged.
        """
        ...

    async def create_edge(self, edge: MemoryEdge) -> str:
        """Create a new edge between two nodes.

        Args:
            edge: The MemoryEdge to store.

        Returns:
            String UUID of the created edge.
        """
        ...

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
            source_id: Filter by source node ID.
            target_id: Filter by target node ID.
            node_ids: Filter to edges touching ANY of these node IDs
                (``source_id IN (...) OR target_id IN (...)``). Batch
                equivalent of querying each node in both directions.
            edge_type: Filter by edge type.
            valid_at: Temporal filter.
            min_confidence: Minimum confidence threshold.

        Returns:
            List of matching MemoryEdges.
        """
        ...

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

        Args:
            node_id: Starting node ID.
            max_hops: Maximum number of hops (default 2).
            edge_types: Only traverse edges of these types.
            valid_at: Temporal filter for edges.
            min_confidence: Minimum edge confidence.
            include_superseded: Include superseded/archived nodes.
            include_unverified_aliases: Traverse alias proposals that have not
                been accepted as identity. Disabled by default so proposals do
                not affect ordinary retrieval.
            user_id: When given, the walk never steps onto another user's
                node, only the user's nodes are returned, and a starting
                node another user owns has no neighborhood.

        Returns:
            List of reachable MemoryNodes (excluding the starting node).
        """
        ...

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

        Single-query variant of get_neighborhood() that also returns the
        minimum depth at which each node was reached, so callers can
        derive hop-distance scores without re-traversing per hop level.

        Args:
            node_id: Starting node ID.
            max_hops: Maximum number of hops (default 2).
            edge_types: Only traverse edges of these types.
            valid_at: Temporal filter for edges.
            min_confidence: Minimum edge confidence.
            include_superseded: Include superseded/archived nodes.
            include_unverified_aliases: Traverse alias proposals that have not
                been accepted as identity. Disabled by default.
            user_id: As in get_neighborhood(): the walk never passes
                through another user's node.

        Returns:
            List of (MemoryNode, min_depth) tuples, excluding the
            starting node. min_depth is 1 for direct neighbors.
        """
        ...

    async def find_shortest_path(
        self,
        source_id: str,
        target_id: str,
        *,
        edge_types: list[EdgeType] | None = None,
        include_unverified_aliases: bool = False,
    ) -> list[str] | None:
        """Find the shortest path between two nodes.

        Args:
            source_id: Starting node ID.
            target_id: Target node ID.
            edge_types: Only traverse edges of these types.
            include_unverified_aliases: Traverse unaccepted alias proposals.

        Returns:
            List of node IDs forming the shortest path (including
            source and target), or None if no path exists.
        """
        ...

    # --- Supersedence ---

    async def get_supersedence_chain(
        self,
        node_id: str,
        *,
        direction: str = "forward",
        user_id: str | None = None,
    ) -> list[MemoryNode]:
        """Traverse the supersedence chain from a node.

        Args:
            node_id: Starting node ID.
            direction: "forward" = what replaced this node,
                       "backward" = what this node replaced.
            user_id: When given, a starting node another user owns has no
                chain, each step follows the newest link whose node is not
                another user's, and a retired node's ``superseded_by`` never
                names another user's node.

        Returns:
            Ordered list of MemoryNodes in the chain.
        """
        ...

    async def find_cross_user_links(self, *, limit: int = 100) -> CrossUserLinks:
        """Count and list the edges and ``superseded_by`` pointers that cross users.

        An unscoped operator read for repairing an old store (#438).
        """
        ...

    # --- Lifecycle Transitions ---

    async def promote(
        self, node_id: str, *, request_id: str | UUID | None = None,
        actor_id: str = "system",
    ) -> None:
        """Promote a tentative node to stable.

        Args:
            node_id: Node to promote.

        Raises:
            ValueError: If the transition is invalid.
        """
        ...

    async def supersede(
        self,
        old_node_id: str,
        new_node_id: str,
        *,
        evidence_id: str | None = None,
        actor_id: str = "system",
    ) -> None:
        """Mark a node as superseded by another.

        Args:
            old_node_id: Node being replaced.
            new_node_id: Replacement node.
            evidence_id: Optional event ID providing evidence for
                the supersedence. Required for automated transitions,
                optional for manual.
            actor_id: Actor recording the correction.

        Raises:
            ValueError: If the transition is invalid.
        """
        ...

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

        Both nodes must be in an active state (TENTATIVE or STABLE).

        Args:
            node_a_id: First conflicting node (typically the existing/older node).
            node_b_id: Second conflicting node (typically the new/incoming node).
            evidence_id: Optional event ID providing evidence.
            actor_id: Actor that identified the contradiction.

        Raises:
            ValueError: If either node is not found or not in an active state.
        """
        ...

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
        ...

    async def deprecate(
        self, node_id: str, *, request_id: str | UUID | None = None,
        actor_id: str = "system",
    ) -> None:
        """Atomically transition a contested node to deprecated and journal it."""
        ...

    async def archive(
        self, node_id: str, *, request_id: str | UUID | None = None,
        actor_id: str = "system",
    ) -> None:
        """Archive a node (terminal state).

        Args:
            node_id: Node to archive.

        Raises:
            ValueError: If the transition is invalid.
        """
        ...

    async def archive_expired(
        self, node_id: str, *, user_id: str, evaluated_at: datetime,
    ) -> bool:
        """Atomically archive an expired node with its retention tombstone."""
        ...

    # --- Cleanup / Rollback ---

    async def delete_node(self, node_id: str) -> None:
        """Delete a node by ID.

        Used for rollback cleanup when event materialization fails.
        Also needed for Phase 5 archival. No-op if the node does
        not exist.

        Args:
            node_id: String UUID of the node to delete.
        """
        ...

    async def delete_edge(self, edge_id: str) -> None:
        """Delete an edge by ID.

        Used for rollback cleanup when event materialization fails.
        Also needed for Phase 5 archival. No-op if the edge does
        not exist.

        Args:
            edge_id: String UUID of the edge to delete.
        """
        ...
