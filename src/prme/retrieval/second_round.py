"""A second search for what the best result points at (``packing.second_round``).

"Where is the thing Dana asked for?" finds "Dana asked the robot to bring the
red mug later." and stops: the record that answers it, "The red mug is on the
kitchen counter.", shares no word with the question and little meaning. One
round of search cannot reach a record that only the first round's result
names.

The second round asks the word index for what the best result says that its
look-alikes do not: the content words of the top result that are neither in
the question nor in half or more of the next results ("red", "mug"; not
"asked", "robot", "bring", which every request shares). The best matches of
those words, at most ``SECOND_ROUND_HITS``, are placed just below the third
result, with the top result's score provenance and a ``second_round``
adjustment that replays, and the ``SECOND_ROUND`` path, which packing
reserves. One extra word search; no model call; deterministic.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
import re

from prme.retrieval.models import RetrievalCandidate, ScoreAdjustment, inherited_score, rank_fusion_relevance

SECOND_ROUND_VERSION = 1
SECOND_ROUND_PATH = "SECOND_ROUND"
# Words asked for at most, and matches placed at most.
SECOND_ROUND_TERMS = 6
SECOND_ROUND_HITS = 3
# The top result is compared with this many results after it, and its matches go just below this rank.
SECOND_ROUND_COMPARE = 9
SECOND_ROUND_AFTER_RANK = 3

_WORD_RE = re.compile(r"\w+")


def second_round_terms(query: str, ranked: list[RetrievalCandidate], stopwords: frozenset[str]) -> list[str]:
    """The words of the top result that the question and its look-alikes do not have, in the result's order."""
    if not ranked:
        return []
    asked = {word.lower() for word in _WORD_RE.findall(query)}
    others = ranked[1:1 + SECOND_ROUND_COMPARE]
    shared = Counter(
        word for candidate in others for word in {found.lower() for found in _WORD_RE.findall(candidate.node.content or "")}
    )
    terms: dict[str, None] = {}
    for word in _WORD_RE.findall(ranked[0].node.content or ""):
        key = word.lower()
        if len(key) < 3 or key.isdigit() or key in stopwords or key in asked:
            continue
        if others and shared[key] * 2 >= len(others):
            continue
        terms.setdefault(key, None)
    return list(terms)[:SECOND_ROUND_TERMS]


async def second_round(
    scored: list[RetrievalCandidate],
    terms: list[str],
    *,
    search: Callable[[str], Awaitable[list[dict]]],
    load: Callable[[list[str]], Awaitable[list[RetrievalCandidate]]],
) -> tuple[list[RetrievalCandidate], list[str]]:
    """Place the best word matches of ``terms`` just below the third result.

    ``scored`` is in rank order. ``search`` is the word search (node ids in
    match order) and ``load`` returns candidates for the ids that are not yet
    among ``scored``, already through the request's filters. A match that
    ranks above the place it would take keeps its own. Returns the candidates
    sorted by score and node id, and the ids placed in order.
    """
    if not scored or not terms:
        return scored, []
    anchor = scored[0]
    anchor_id = str(anchor.node.id)
    hits = [str(hit["node_id"]) for hit in await search(" ".join(terms))]
    hits = [node_id for node_id in dict.fromkeys(hits) if node_id != anchor_id][:SECOND_ROUND_HITS]
    if not hits:
        return scored, []
    by_id = {str(candidate.node.id): candidate for candidate in scored}
    missing = [node_id for node_id in hits if node_id not in by_id]
    loaded = {str(candidate.node.id): candidate for candidate in (await load(missing) if missing else [])}
    inherited, provenance = inherited_score(anchor)
    if inherited <= 0:
        return scored, []
    floor = (scored[SECOND_ROUND_AFTER_RANK - 1].composite_score if len(scored) >= SECOND_ROUND_AFTER_RANK
             else scored[-1].composite_score)
    relevance = rank_fusion_relevance(anchor)
    placed: list[str] = []
    for place, node_id in enumerate(hits):
        current = by_id.get(node_id) or loaded.get(node_id)
        if current is None:
            continue
        coefficient = min(1.0, floor * (1.0 - (place + 1) * 1e-6) / inherited)
        if coefficient <= 0:
            continue
        adjusted = None
        score = inherited * coefficient
        if provenance is not None:
            adjusted = provenance.model_copy(update={
                "adjustments": provenance.adjustments + (ScoreAdjustment(
                    kind="second_round", coefficient=coefficient, source_node_id=anchor.node.id,
                ),),
            })
            score = adjusted.replay_score()
        paths = current.paths if SECOND_ROUND_PATH in current.paths else [*current.paths, SECOND_ROUND_PATH]
        if node_id in by_id and current.composite_score >= score:
            by_id[node_id] = current.model_copy(update={"paths": paths})
        else:
            by_id[node_id] = current.model_copy(update={
                "paths": paths, "path_count": max(current.path_count, 1), "composite_score": score,
                "reranker_score": None, "score_trace": None, "score_provenance": adjusted,
                "context_relevance": max(current.context_relevance, relevance),
            })
        placed.append(node_id)
    if not placed:
        return scored, []
    results = list(by_id.values())
    results.sort(key=lambda candidate: (-candidate.composite_score, str(candidate.node.id)))
    return results, placed
