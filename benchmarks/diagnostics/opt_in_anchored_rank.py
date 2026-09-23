"""Single research follow-up: prioritize the original balanced anchor.

Uses the same score envelope and neural inference as the preceding repair.
The original strongest ordinary multi-path candidate takes the first envelope
slot if it is in the reranked prefix. Remaining slots follow neural order.
Equal assigned scores still use canonical UUID ties; this is an ordering
preference, not an unconditional source-retention guarantee.
"""
from benchmarks.diagnostics.opt_in_rank_envelope import TRACE, assign_envelope
from prme.retrieval.packing import _is_pinned_or_active_task
from prme.retrieval.reranker import CrossEncoderReranker
from prme.types import NodeType


def original_anchor(candidates):
    eligible = [c for c in candidates if c.path_count >= 2
                and c.node.node_type != NodeType.INSTRUCTION
                and not _is_pinned_or_active_task(c)]
    return min(eligible, key=lambda c: (-c.composite_score, str(c.node.id))).node.id if eligible else None


class AnchoredRankEnvelopeReranker(CrossEncoderReranker):
    """Explicit research injection, with no public flag or default change."""

    async def rerank(self, query, candidates, top_k=100, prior_weight=.3):
        legacy = await super().rerank(query, candidates, top_k=top_k, prior_weight=prior_weight)
        count = min(len(candidates), top_k)
        anchor = original_anchor(candidates)
        prefix = legacy[:count]
        prioritized = [c for c in prefix if c.node.id == anchor]
        prioritized += [c for c in prefix if c.node.id != anchor]
        mapped = assign_envelope(candidates, prioritized + legacy[count:], top_k)
        trace = TRACE.get()
        if trace is not None:
            trace.append({'policy': 'original_balanced_anchor_envelope_v1', 'query': query,
                          'top_k': top_k, 'prior_weight': prior_weight,
                          'anchor_id': str(anchor) if anchor is not None else None,
                          'anchor_in_prefix': any(c.node.id == anchor for c in prefix),
                          'original': [{'node_id': str(c.node.id), 'score': c.composite_score} for c in candidates],
                          'legacy': [{'node_id': str(c.node.id), 'score': c.composite_score,
                                      'raw_neural_score': c.reranker_score} for c in legacy],
                          'mapped': [{'node_id': str(c.node.id), 'score': c.composite_score} for c in mapped]})
        return mapped
