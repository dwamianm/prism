"""Resolve source turns for packing without promoting them in the ranking."""
from __future__ import annotations

from prme.retrieval.evidence_context import _within_time_bounds
from prme.retrieval.filtering import filter_epistemic
from prme.retrieval.models import RetrievalCandidate, ScoreAdjustment, rank_fusion_relevance
from prme.retrieval.supersedence import CLAIM_TYPES
from prme.types import NodeType


async def prepare_sources(scored, *, graph_store, user_id, scopes, retrieval_mode,
                          unverified_confidence_threshold, **time_bounds):
    """Fetch each direct source once, constrained to the claim's owner/scope.

    New sources have a replayable zero score and a separate packing-only path.
    They get no independent packing priority. Selection can still exclude them.
    """
    claims = [c for c in scored if c.node.node_type in CLAIM_TYPES | {NodeType.SUMMARY}]
    ids = sorted({str(ref) for c in claims for ref in c.node.evidence_refs if ref != c.node.id})
    nodes = {n.id: n for n in await graph_store.get_nodes(ids)} if ids else {}
    existing = {c.node.id: c for c in scored}
    links = {}
    for claim in claims:
        eligible = []
        for ref in sorted(set(claim.node.evidence_refs), key=str):
            source = nodes.get(ref)
            if (source is None or source.id == claim.node.id
                    or source.node_type not in {NodeType.NOTE, NodeType.EVENT}
                    or source.user_id != user_id or source.scope != claim.node.scope
                    or (scopes is not None and source.scope not in scopes)
                    or not _within_time_bounds(source, **time_bounds)):
                continue
            allowed, _ = filter_epistemic([RetrievalCandidate(node=source)], retrieval_mode,
                                         unverified_threshold=unverified_confidence_threshold)
            if not allowed:
                continue
            eligible.append(ref)
            if ref in existing and existing[ref].node.model_dump(mode="json") != source.model_dump(mode="json"):
                raise ValueError("Source co-packing observed conflicting snapshots of one node")
            if ref not in existing:
                provenance = claim.score_provenance
                if provenance is not None:
                    provenance = provenance.model_copy(update={"adjustments": provenance.adjustments + (
                        ScoreAdjustment(kind="source_context", coefficient=0, source_node_id=claim.node.id),)})
                existing[ref] = RetrievalCandidate(node=source, paths=["SOURCE_CONTEXT"], path_count=0,
                    score_provenance=provenance, context_relevance=rank_fusion_relevance(claim))
        if eligible:
            links[claim.node.id] = tuple(eligible)
    result = sorted(existing.values(), key=lambda c: (-c.composite_score, str(c.node.id)))
    return result, links
