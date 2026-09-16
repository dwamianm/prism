"""Deterministic two-stage retrieval for session-scoped episodes.

The primary retrievers score individual records. Long episodes can therefore
lose a supporting record even when several weak signals identify its episode.
This module first scores complete candidate episodes with BM25, then promotes a
bounded set of query-relevant records inside the best episodes. It uses only
already scoped and filtered candidates and performs no model calls.
"""

from __future__ import annotations

from collections import Counter
import math
import re

from prme.retrieval.config import PackingConfig
from prme.retrieval.models import (
    RetrievalCandidate,
    ScoreAdjustment,
)


_TERM = re.compile(r"[^\W_]+", re.UNICODE)
_BM25_K1 = 1.5
_BM25_B = 0.75


def _terms(text: str) -> tuple[str, ...]:
    return tuple(_TERM.findall(text.casefold()))


def _bm25_scores(
    query_terms: tuple[str, ...], documents: list[tuple[str, ...]]
) -> list[float]:
    """Return stable BM25 scores without requiring another search backend."""
    if not query_terms or not documents:
        return [0.0] * len(documents)
    lengths = [len(document) for document in documents]
    average_length = math.fsum(lengths) / len(lengths)
    if average_length == 0:
        return [0.0] * len(documents)
    document_frequency = Counter(
        term for document in documents for term in set(document)
    )
    unique_query_terms = tuple(dict.fromkeys(query_terms))
    count = len(documents)
    scores: list[float] = []
    for document, length in zip(documents, lengths):
        frequencies = Counter(document)
        score = 0.0
        for term in unique_query_terms:
            frequency = frequencies.get(term, 0)
            if frequency == 0:
                continue
            inverse_document_frequency = math.log(
                1
                + (count - document_frequency[term] + 0.5)
                / (document_frequency[term] + 0.5)
            )
            denominator = frequency + _BM25_K1 * (
                1 - _BM25_B + _BM25_B * length / average_length
            )
            score += inverse_document_frequency * (
                frequency * (_BM25_K1 + 1) / denominator
            )
        scores.append(score)
    return scores


def expand_episode_context(
    scored: list[RetrievalCandidate],
    query: str,
    config: PackingConfig,
) -> list[RetrievalCandidate]:
    """Promote local evidence from the best candidate-backed episodes.

    A session ID is PRME's existing episode boundary. Only sessions represented
    by at least two already authorized candidates participate. The full text of
    those candidates routes episodes; a second BM25 pass selects local records.
    Selected records receive an ``EPISODE_CONTEXT`` path and a replayable score
    inherited from the strongest record in their episode.
    """
    if config.episode_context_top_k <= 0 or not scored:
        return scored
    query_terms = _terms(query)
    if not query_terms:
        return scored

    episodes: dict[tuple[str, str], list[RetrievalCandidate]] = {}
    for candidate in scored:
        session_id = candidate.node.session_id
        if session_id is not None:
            episodes.setdefault(
                (candidate.node.scope.value, session_id), []
            ).append(candidate)
    episodes = {key: value for key, value in episodes.items() if len(value) >= 2}
    if not episodes:
        return scored

    episode_keys = sorted(episodes)
    episode_documents = [
        tuple(
            term
            for candidate in episodes[key]
            for term in _terms(candidate.node.content)
        )
        for key in episode_keys
    ]
    episode_scores = _bm25_scores(query_terms, episode_documents)
    ranked_episode_indices = sorted(
        range(len(episode_keys)),
        key=lambda index: (-episode_scores[index], episode_keys[index]),
    )
    selected_keys = [
        episode_keys[index]
        for index in ranked_episode_indices[: config.episode_context_top_k]
        if episode_scores[index] > 0
    ]
    if not selected_keys:
        return scored

    candidates_by_id = {
        str(candidate.node.id): candidate for candidate in scored
    }
    changed = False
    ranking_changed = False
    for key in selected_keys:
        members = episodes[key]
        local_scores = _bm25_scores(
            query_terms, [_terms(candidate.node.content) for candidate in members]
        )
        local_indices = sorted(
            range(len(members)),
            key=lambda index: (
                -local_scores[index],
                -members[index].composite_score,
                str(members[index].node.id),
            ),
        )[: config.episode_context_local_k]
        anchor = min(
            members,
            key=lambda candidate: (
                -candidate.composite_score,
                str(candidate.node.id),
            ),
        )
        inherited_score = anchor.composite_score * config.episode_context_score_decay
        inherited_provenance = anchor.score_provenance
        if inherited_provenance is not None:
            inherited_provenance = inherited_provenance.model_copy(
                update={
                    "adjustments": inherited_provenance.adjustments
                    + (
                        ScoreAdjustment(
                            kind="episode_decay",
                            coefficient=config.episode_context_score_decay,
                            source_node_id=anchor.node.id,
                        ),
                    )
                }
            )

        for index in local_indices:
            candidate = members[index]
            candidate_id = str(candidate.node.id)
            current = candidates_by_id[candidate_id]
            updates: dict[str, object] = {}
            if "EPISODE_CONTEXT" not in current.paths:
                updates["paths"] = [*current.paths, "EPISODE_CONTEXT"]
                updates["path_count"] = current.path_count + 1
            if inherited_score > current.composite_score:
                updates.update(
                    composite_score=inherited_score,
                    reranker_score=None,
                    score_trace=None,
                    score_provenance=inherited_provenance,
                )
                ranking_changed = True
            if updates:
                candidates_by_id[candidate_id] = current.model_copy(update=updates)
                changed = True

    if not changed:
        return scored
    expanded = list(candidates_by_id.values())
    if ranking_changed:
        expanded.sort(key=lambda candidate: (-candidate.composite_score, str(candidate.node.id)))
    return expanded
