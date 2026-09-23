"""Research-only correction for mixing reranked and unreranked score scales.

The existing neural-blend order assigns the original prefix's sorted score
multiset to that prefix. No unjudged tail score is changed or called a neural
judgment. Session expansion and packing then compare scores on the original
scale. Equal score slots retain canonical UUID tie-breaking. This is a ranking
heuristic, not learned calibration, relevance probability or proven quality.
"""
from contextvars import ContextVar

from prme.retrieval.models import ScoreAdjustment
from prme.retrieval.reranker import CrossEncoderReranker


TRACE: ContextVar[list | None] = ContextVar('rank_envelope_trace', default=None)


def assign_envelope(original, reranked, top_k):
    count = min(len(original), top_k)
    if count == 0:
        return reranked
    prefix = {c.node.id for c in original[:count]}
    if ({c.node.id for c in reranked[:count]} != prefix
            or [c.node.id for c in reranked[count:]] != [c.node.id for c in original[count:]]):
        raise ValueError('Reranker prefix or tail identity changed')
    scores = sorted((c.composite_score for c in original[:count]), reverse=True)
    updated = []
    for candidate, assigned in zip(reranked[:count], scores):
        provenance = candidate.score_provenance
        if provenance is None or not provenance.adjustments or provenance.adjustments[-1].kind != 'neural_blend':
            raise ValueError('Neural score lineage is required before ordinal assignment')
        if provenance.replay_score() != candidate.composite_score:
            raise ValueError('Neural score lineage does not replay')
        copied = candidate.model_copy(update={'composite_score': assigned,
            'score_provenance': provenance.model_copy(update={'adjustments': (
                *provenance.adjustments, ScoreAdjustment(kind='neural_rank_assignment',
                    coefficient=assigned, source_node_id=candidate.node.id))})})
        if copied.score_provenance.replay_score() != assigned:
            raise ValueError('Assigned score lineage does not replay')
        updated.append(copied)
    updated.sort(key=lambda c: (-c.composite_score, str(c.node.id)))
    return updated + reranked[count:]


class RankEnvelopeReranker(CrossEncoderReranker):
    """Explicit research injection only; no PRMEConfig field activates it."""
    _score_policy = 'neural_rank_envelope_v1'

    async def rerank(self, query, candidates, top_k=100, prior_weight=.3):
        original = [{'node_id': str(c.node.id), 'score': c.composite_score} for c in candidates]
        legacy = await super().rerank(query, candidates, top_k=top_k, prior_weight=prior_weight)
        mapped = assign_envelope(candidates, legacy, top_k)
        trace = TRACE.get()
        if trace is not None:
            trace.append({'query': query, 'top_k': top_k, 'prior_weight': prior_weight,
                'original': original,
                'legacy': [{'node_id': str(c.node.id), 'score': c.composite_score,
                            'raw_neural_score': c.reranker_score} for c in legacy],
                'mapped': [{'node_id': str(c.node.id), 'score': c.composite_score} for c in mapped]})
        return mapped
