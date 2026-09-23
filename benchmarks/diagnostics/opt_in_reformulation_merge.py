"""Research-only alternate-query signal merge; no production entry point."""
import asyncio
import math

from prme.retrieval.candidates import CandidateDiagnostics, generate_candidates
from prme.retrieval.query_analysis import analyze_query


SIGNALS = ('semantic_score', 'lexical_score', 'graph_proximity')


def merge_signals(candidates, alternatives):
    """Union backend paths and take maxima, without treating queries as backends."""
    merged = {c.node.id: c.model_copy(deep=True) for c in candidates}
    if len(merged) != len(candidates):
        raise ValueError('Duplicate original candidate identity')
    changed = set()
    added = set()
    for group in [candidates, *alternatives]:
        for candidate in group:
            if any(not math.isfinite(getattr(candidate, key)) for key in SIGNALS):
                raise ValueError('Non-finite retrieval signal')
            nid = candidate.node.id
            if nid not in merged:
                merged[nid] = candidate.model_copy(deep=True)
                added.add(nid)
            target = merged[nid]
            if target.node.model_dump(mode='json') != candidate.node.model_dump(mode='json'):
                raise ValueError('Same identity has different source snapshots')
            before = (tuple(target.paths), *(getattr(target, key) for key in SIGNALS))
            target.paths = sorted(set(target.paths) | set(candidate.paths))
            target.path_count = len(target.paths)
            for key in SIGNALS:
                setattr(target, key, max(getattr(target, key), getattr(candidate, key)))
            after = (tuple(target.paths), *(getattr(target, key) for key in SIGNALS))
            if before != after:
                changed.add(nid)
    return list(merged.values()), {'added_ids': sorted(map(str, added)),
                                  'changed_existing_ids': sorted(str(i) for i in changed - added)}


async def expand_with_merge(self, query, *, candidates, user_id, scope, time_from,
                            time_to, retrieval_mode, config, reference_time=None):
    """Use the usual query analyses and candidate passes, then merge all signals."""
    from prme.retrieval.reformulation import reformulate_query
    alternatives = await reformulate_query(query, provider=self._query_reformulation_provider,
        model=self._query_reformulation_model, count=self._query_reformulation_count)
    async def generate(alt):
        analysis = await analyze_query(alt, time_from=time_from, time_to=time_to,
            retrieval_mode=retrieval_mode, languages=self._temporal_languages,
            reference_time=reference_time)
        diagnostics = CandidateDiagnostics()
        found, _ = await generate_candidates(analysis, graph_store=self._graph_store,
            vector_index=self._vector_index, lexical_index=self._lexical_index,
            user_id=user_id, scope=scope, time_from=time_from, time_to=time_to,
            config=config, diagnostics=diagnostics)
        if diagnostics.backend_failures or diagnostics.embedding_mismatch:
            raise RuntimeError('Alternate-query backend failed')
        return found
    groups = await asyncio.gather(*(generate(alt) for alt in alternatives))
    merged, observation = merge_signals(candidates, groups)
    # Only commit the research candidate list after every alternate pass succeeds.
    candidates[:] = merged
    self._research_merge_trace.append({'query': query, 'alternatives': alternatives,
        'alternative_candidate_counts': [len(g) for g in groups], **observation})
    return len(observation['added_ids'])
