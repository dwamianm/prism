"""Fold claim statements into the claims they restate (Stage 5.5e).

With ``enable_claim_sentence_text`` and ``enable_ingest_claim_fixes``, a claim
whose sentences are already the text of an earlier claim of the same message
gets a one-sentence statement built from its own subject, predicate and object
(``grounding_method`` ``claim_statement_v1``), so that no two claims share a
text. The statement is short and repeats the names and verbs of the question,
so it outranks the claim that holds the full sentence, and the several
statements of one message take the places a reader needs for the answer.

This stage treats a statement as another way to find the claim it restates. The
claim of the same evidence whose text holds the statement's object takes the
better of the two scores, through a replayable ``claim_statement_fold``
adjustment that records the statement as its lineage, and the statement leaves
the results. A statement with no such claim among the candidates stays as it is.
The stage reads only stored metadata and makes no model call. It runs with
``packing.folded_records_keep_rank``, which also keeps a folded record's place in
the reader context (:func:`prme.retrieval.packing.pack_context`).
"""

from __future__ import annotations

import re

from prme.retrieval.models import (
    ExcludedCandidate,
    RetrievalCandidate,
    ScoreAdjustment,
    rank_fusion_relevance,
)
from prme.types import NodeType

STATEMENT_GROUNDING = "claim_statement_v1"
FOLD_REASON = "claim_statement_folded"
_CLAIM_TYPES = frozenset({NodeType.FACT, NodeType.DECISION, NodeType.PREFERENCE, NodeType.TASK})


def _is_statement(candidate: RetrievalCandidate) -> bool:
    node = candidate.node
    return node.node_type in _CLAIM_TYPES and (node.metadata or {}).get("grounding_method") == STATEMENT_GROUNDING


def _evidence_key(candidate: RetrievalCandidate) -> tuple[str, ...] | None:
    refs = tuple(sorted(str(ref) for ref in candidate.node.evidence_refs))
    return refs or None


def _holds(text: str, phrase: object) -> bool:
    """Whether ``text`` holds ``phrase`` as whole words, ignoring case and spacing."""
    if not isinstance(phrase, str) or not phrase.strip():
        return False
    words = " ".join(phrase.casefold().split())
    return re.search(rf"(?<!\w){re.escape(words)}(?!\w)", " ".join(text.casefold().split())) is not None


def _source(statement: RetrievalCandidate, siblings: list[RetrievalCandidate]) -> RetrievalCandidate | None:
    """The claim of the statement's evidence that holds its object, preferring one that holds its subject too."""
    metadata = statement.node.metadata or {}
    holders = [claim for claim in siblings if _holds(claim.node.content, metadata.get("object"))]
    named = [claim for claim in holders if _holds(claim.node.content, metadata.get("subject"))]
    return min(named or holders, key=lambda claim: (-claim.composite_score, str(claim.node.id)), default=None)


def fold_claim_statements(
    scored: list[RetrievalCandidate],
) -> tuple[list[RetrievalCandidate], list[ExcludedCandidate], bool]:
    """Fold each claim statement into the claim that holds its sentence.

    Returns the candidates, an exclusion for each folded statement, and
    whether any claim's score rose, which re-sorts the list by score and then
    node ID (the ``score_id`` ranking policy). A candidate list with no folded
    statement comes back unchanged.
    """
    statements = [candidate for candidate in scored if _is_statement(candidate)]
    if not statements:
        return scored, [], False
    claims: dict[tuple[str, ...], list[RetrievalCandidate]] = {}
    for candidate in scored:
        key = _evidence_key(candidate)
        if key is not None and candidate.node.node_type in _CLAIM_TYPES and not _is_statement(candidate):
            claims.setdefault(key, []).append(candidate)

    folded: dict[str, list[RetrievalCandidate]] = {}
    for statement in statements:
        key = _evidence_key(statement)
        source = _source(statement, claims.get(key, [])) if key is not None else None
        if source is not None:
            folded.setdefault(str(source.node.id), []).append(statement)
    if not folded:
        return scored, [], False

    gone = {str(statement.node.id) for group in folded.values() for statement in group}
    excluded = [
        ExcludedCandidate(node_id=statement.node.id, reason=FOLD_REASON, composite_score=statement.composite_score)
        for statement in statements if str(statement.node.id) in gone
    ]
    result, ranking_changed = [], False
    for candidate in scored:
        candidate_id = str(candidate.node.id)
        if candidate_id in gone:
            continue
        group = folded.get(candidate_id)
        if group:
            best = min(group, key=lambda statement: (-statement.composite_score, str(statement.node.id)))
            updates: dict[str, object] = {
                # The claim keeps the relevance of every statement it absorbs; min_score gates it under rank fusion.
                "context_relevance": max(rank_fusion_relevance(member) for member in [candidate, *group]),
            }
            if best.composite_score > candidate.composite_score:
                provenance = best.score_provenance
                if provenance is not None:
                    provenance = provenance.model_copy(update={
                        "adjustments": provenance.adjustments + (ScoreAdjustment(
                            kind="claim_statement_fold", coefficient=1.0, source_node_id=best.node.id,
                        ),),
                    })
                updates.update(
                    composite_score=best.composite_score, reranker_score=None, score_trace=None,
                    score_provenance=provenance,
                )
                ranking_changed = True
            candidate = candidate.model_copy(update=updates)
        result.append(candidate)
    if ranking_changed:
        result.sort(key=lambda candidate: (-candidate.composite_score, str(candidate.node.id)))
    return result, excluded, ranking_changed
