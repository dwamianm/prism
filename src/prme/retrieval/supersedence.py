"""Read-only, direct replacement suppression with owner/scope validation."""
from __future__ import annotations

from datetime import datetime

from prme.retrieval.models import ExcludedCandidate, RetrievalCandidate
from prme.retrieval.filtering import filter_epistemic
from prme.types import EdgeType, LifecycleState, NodeType

CLAIM_TYPES = frozenset({NodeType.FACT, NodeType.PREFERENCE, NodeType.DECISION, NodeType.TASK})


async def suppress_replaced_claims(candidates: list[RetrievalCandidate], *, graph_store,
                                   user_id: str, reference_time: datetime,
                                   unverified_threshold: float | None = None) -> tuple[list[RetrievalCandidate], list[ExcludedCandidate]]:
    """Suppress only a direct, current, non-contested replacement.

    SUPERSEDES points from replacement to prior claim. Neither a newer date
    nor a CONTRADICTS edge establishes replacement. Raw/derived source turns
    are not claims, and suppression never mutates storage or cascades.
    """
    claims = {c.node.id: c for c in candidates if c.node.node_type in CLAIM_TYPES
              and c.node.user_id == user_id}
    if not claims:
        return candidates, []
    edges = await graph_store.get_edges(node_ids=sorted(map(str, claims)), edge_type=EdgeType.SUPERSEDES,
                                       valid_at=reference_time)
    replacements: dict = {nid: {c.node.superseded_by} if c.node.superseded_by else set()
                          for nid, c in claims.items()}
    for edge in edges:
        if edge.target_id in claims and edge.user_id == user_id:
            replacements[edge.target_id].add(edge.source_id)
    edge_pairs = {(e.source_id, e.target_id) for e in edges if e.user_id == user_id}
    ids = sorted({str(nid) for values in replacements.values() for nid in values})
    # Lifecycle and pointers alone cannot establish current state in a legacy
    # edge-only graph. Check direct successors of the proposed replacements as
    # well, without walking a chain or inferring validity for dependent nodes.
    successor_edges = await graph_store.get_edges(node_ids=ids, edge_type=EdgeType.SUPERSEDES,
        valid_at=reference_time) if ids else []
    replaced_ids = {e.target_id for e in [*edges, *successor_edges] if e.user_id == user_id}
    nodes = {n.id: n for n in await graph_store.get_nodes(ids)} if ids else {}
    eligible, _ = filter_epistemic([RetrievalCandidate(node=n) for n in nodes.values()],
                                 unverified_threshold=unverified_threshold)
    nodes = {c.node.id: c.node for c in eligible}
    suppressed = set()
    for nid, values in replacements.items():
        old = claims[nid].node
        for replacement_id in values:
            new = nodes.get(replacement_id)
            if (new is not None and new.id != old.id and new.user_id == user_id and new.scope == old.scope
                    and new.node_type == old.node_type
                    and new.lifecycle_state in {LifecycleState.TENTATIVE, LifecycleState.STABLE}
                    and new.superseded_by is None
                    and new.id not in replaced_ids
                    and old.valid_from <= new.valid_from <= reference_time
                    and (new.valid_to is None or new.valid_to > reference_time)):
                # A raw cyclic pair is ambiguous and must not suppress either.
                if old.id not in replacements.get(new.id, set()) and (old.id, new.id) not in edge_pairs:
                    suppressed.add(nid)
                    break
    return [c for c in candidates if c.node.id not in suppressed], [
        ExcludedCandidate(node_id=nid, reason="supersedence_filtered") for nid in sorted(suppressed, key=str)]
