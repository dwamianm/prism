"""Project ranked derived memories back to their direct evidence nodes.

Extraction improves routing, but many short derived nodes can hide the complete
source passage that an answer needs. This stage keeps the strongest score for an
exact evidence group while replacing that group's candidates with bounded,
active source nodes. The inherited score remains replayable and no model call is
required.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from prme.retrieval.config import PackingConfig
from prme.retrieval.filtering import filter_epistemic
from prme.retrieval.models import RetrievalCandidate, ScoreAdjustment
from prme.types import NodeType, RetrievalMode, Scope

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode
    from prme.storage.graph_store import GraphStore


def _evidence_key(candidate: RetrievalCandidate) -> tuple[str, ...] | None:
    refs = tuple(sorted(str(ref) for ref in candidate.node.evidence_refs))
    return refs or None


def _within_time_bounds(
    node: MemoryNode,
    *,
    knowledge_at: datetime | None,
    event_time_from: datetime | None,
    event_time_to: datetime | None,
    time_from: datetime | None,
    time_to: datetime | None,
) -> bool:
    if knowledge_at is not None and node.created_at > knowledge_at:
        return False
    observed = node.event_time or node.created_at
    if event_time_from is not None and observed < event_time_from:
        return False
    if event_time_to is not None and observed > event_time_to:
        return False
    if node.node_type not in {NodeType.ENTITY, NodeType.PREFERENCE}:
        if time_from is not None and node.valid_to is not None and node.valid_to <= time_from:
            return False
        if time_to is not None and node.valid_from is not None and node.valid_from > time_to:
            return False
    return True


async def project_evidence_context(
    scored: list[RetrievalCandidate],
    *,
    graph_store: GraphStore,
    user_id: str,
    config: PackingConfig,
    scopes: list[Scope] | None,
    retrieval_mode: RetrievalMode,
    unverified_confidence_threshold: float | None,
    knowledge_at: datetime | None = None,
    event_time_from: datetime | None = None,
    event_time_to: datetime | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
) -> list[RetrievalCandidate]:
    """Replace top exact evidence groups with bounded direct source nodes.

    A direct source is a visible node whose own ID occurs in the group's exact
    ``evidence_refs`` set. Sources must retain the anchor's owner and scope and
    pass the request's temporal and epistemic filters. If no eligible source is
    available, the original group is left unchanged.
    """
    top_k = config.evidence_projection_top_k
    if top_k <= 0 or not scored:
        return scored

    groups: dict[tuple[str, ...], list[RetrievalCandidate]] = {}
    for candidate in scored:
        key = _evidence_key(candidate)
        if key is not None:
            groups.setdefault(key, []).append(candidate)
    selected_keys = tuple(groups)[:top_k]
    if not selected_keys:
        return scored

    source_ids = sorted({source_id for key in selected_keys for source_id in key})
    sources = await graph_store.get_nodes(source_ids)
    sources_by_id = {str(node.id): node for node in sources}
    allowed_scopes = set(scopes) if scopes is not None else None

    replacements: list[RetrievalCandidate] = []
    replaced_ids: set[str] = set()
    for key in selected_keys:
        members = groups[key]
        anchor = members[0]
        eligible = [
            sources_by_id[source_id]
            for source_id in key
            if source_id in sources_by_id
            and sources_by_id[source_id].user_id == user_id
            and sources_by_id[source_id].scope == anchor.node.scope
            and (allowed_scopes is None or sources_by_id[source_id].scope in allowed_scopes)
            and _within_time_bounds(
                sources_by_id[source_id],
                knowledge_at=knowledge_at,
                event_time_from=event_time_from,
                event_time_to=event_time_to,
                time_from=time_from,
                time_to=time_to,
            )
        ]
        if not eligible:
            continue
        shells = [RetrievalCandidate(node=node) for node in eligible]
        shells, _ = filter_epistemic(
            shells,
            retrieval_mode,
            unverified_threshold=unverified_confidence_threshold,
        )
        allowed_ids = {str(candidate.node.id) for candidate in shells}
        eligible = [node for node in eligible if str(node.id) in allowed_ids]
        if not eligible:
            continue

        projection_score = (
            anchor.composite_score * config.evidence_projection_score_decay
        )
        provenance = anchor.score_provenance
        if provenance is not None:
            provenance = provenance.model_copy(
                update={
                    "adjustments": provenance.adjustments
                    + (
                        ScoreAdjustment(
                            kind="evidence_projection",
                            coefficient=config.evidence_projection_score_decay,
                            source_node_id=anchor.node.id,
                        ),
                    )
                }
            )
        for source in eligible[: config.evidence_projection_max_sources]:
            replacements.append(
                RetrievalCandidate(
                    node=source,
                    paths=["EVIDENCE_CONTEXT"],
                    path_count=1,
                    composite_score=projection_score,
                    score_provenance=provenance,
                )
            )
        replaced_ids.update(str(member.node.id) for member in members)

    if not replacements:
        return scored
    projected_by_id = {
        str(candidate.node.id): candidate
        for candidate in scored
        if str(candidate.node.id) not in replaced_ids
    }
    for replacement in replacements:
        source_id = str(replacement.node.id)
        current = projected_by_id.get(source_id)
        if current is None or replacement.composite_score > current.composite_score:
            projected_by_id[source_id] = replacement
        elif "EVIDENCE_CONTEXT" not in current.paths:
            projected_by_id[source_id] = current.model_copy(
                update={
                    "paths": [*current.paths, "EVIDENCE_CONTEXT"],
                    "path_count": current.path_count + 1,
                }
            )
    projected = list(projected_by_id.values())
    projected.sort(key=lambda candidate: (-candidate.composite_score, str(candidate.node.id)))
    return projected
