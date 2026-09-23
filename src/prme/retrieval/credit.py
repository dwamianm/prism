"""Controlled context ablation and citation-checked presence credit."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from uuid import UUID

from prme.models.credit import CreditTier, ContextAblation, ContextPresenceCredit
from prme.models.relevance import AnswerCitationRecord
from prme.retrieval.models import MemoryBundle
from prme.retrieval.packing import _render_sections
from prme.retrieval.tokenization import count_tokens


def _context_sha256(context: str) -> str:
    return hashlib.sha256(context.encode()).hexdigest()


def ablate_context(
    bundle: MemoryBundle,
    node_ids: Sequence[str | UUID],
) -> ContextAblation:
    """Remove included entries without repacking or mutating ``bundle``.

    This holds every retained entry, representation, section, and coverage
    notice fixed. It is intended for controlled re-answer tests. Missing IDs,
    duplicates, and an empty removal set fail instead of producing ambiguous
    evidence.
    """
    removed = tuple(UUID(str(node_id)) for node_id in node_ids)
    if not removed:
        raise ValueError("Context ablation requires at least one node ID")
    if len(removed) != len(set(removed)):
        raise ValueError("Context ablation node IDs must be unique")

    snapshot = MemoryBundle.model_validate_json(bundle.model_dump_json())
    occurrences = [
        candidate.node.id
        for candidates in snapshot.sections.values()
        for candidate in candidates
    ]
    if len(occurrences) != len(set(occurrences)):
        raise ValueError("Context ablation requires unique included entries")
    if set(removed) - set(occurrences):
        raise ValueError("Context ablation IDs must identify included entries")

    remove_set = set(removed)
    sections = {
        section: [
            candidate
            for candidate in candidates
            if candidate.node.id not in remove_set
        ]
        for section, candidates in snapshot.sections.items()
    }
    sections = {section: candidates for section, candidates in sections.items() if candidates}
    # Re-render in the bundle's own format and references, so the counterfactual
    # differs from the baseline only by the removed entries.
    context_refs = {
        node_id: reference for reference, node_id in snapshot.context_references.items()
    }
    rendered = _render_sections(
        sections,
        coverage_notice=snapshot.coverage_notice,
        context_guidance=snapshot.context_guidance,
        context_format=snapshot.context_format,
        context_refs=context_refs or None,
    )
    if snapshot.tokenizer is None:
        raise ValueError("Context ablation requires the bundle tokenizer")
    tokens_used = count_tokens(rendered, snapshot.tokenizer)
    available = snapshot.tokens_used + snapshot.budget_remaining
    excluded_ids = list(snapshot.excluded_ids)
    for node_id in removed:
        if node_id not in excluded_ids:
            excluded_ids.append(node_id)
    counterfactual = MemoryBundle(
        sections=sections,
        included_count=sum(len(candidates) for candidates in sections.values()),
        excluded_ids=excluded_ids,
        tokens_used=tokens_used,
        token_budget=snapshot.token_budget,
        budget_remaining=max(0, available - tokens_used),
        min_fidelity=snapshot.min_fidelity,
        rendered_context=rendered,
        tokenizer=snapshot.tokenizer,
        coverage_notice=snapshot.coverage_notice,
        context_guidance=snapshot.context_guidance,
        context_format=snapshot.context_format,
        context_references={
            reference: node_id
            for reference, node_id in snapshot.context_references.items()
            if node_id not in remove_set
        },
    )
    return ContextAblation(
        removed_node_ids=removed,
        baseline_context_sha256=_context_sha256(snapshot.render()),
        counterfactual_context_sha256=_context_sha256(rendered),
        counterfactual=counterfactual,
    )


def assess_context_presence(
    ablation: ContextAblation,
    citation: AnswerCitationRecord,
    *,
    node_id: str | UUID,
    baseline_correct: bool,
    counterfactual_correct: bool,
    evaluation_id: str,
    counterfactual_answer_sha256: str | None = None,
) -> ContextPresenceCredit:
    """Assign signed credit after a fixed reader is run on an ablation.

    The removed entry must be the sole ablation target and must appear in the
    saved answer citation set. ``evaluation_id`` should identify the fixed
    reader, prompt, decoding, and judging protocol used for both answers.
    """
    identity = UUID(str(node_id))
    if ablation.removed_node_ids != (identity,):
        raise ValueError("Presence credit requires one matching ablation target")
    if identity not in citation.cited_node_ids:
        raise ValueError("Presence credit requires a cited memory")
    if citation.context_sha256 != ablation.baseline_context_sha256:
        raise ValueError("Citation and ablation baseline contexts do not match")
    outcomes = (baseline_correct, counterfactual_correct)
    tiers: dict[tuple[bool, bool], tuple[CreditTier, float]] = {
        (True, False): ("load_bearing", 1.0),
        (True, True): ("cited_non_flipping", 0.6),
        (False, True): ("misleading", -1.0),
        (False, False): ("cited_wrong_noncuring", 0.0),
    }
    tier, value = tiers[outcomes]
    return ContextPresenceCredit(
        request_id=citation.request_id,
        citation_id=citation.citation_id,
        node_id=identity,
        answer_id=citation.answer_id,
        citation_method=citation.method,
        evaluation_id=evaluation_id,
        baseline_context_sha256=ablation.baseline_context_sha256,
        counterfactual_context_sha256=ablation.counterfactual_context_sha256,
        counterfactual_answer_sha256=counterfactual_answer_sha256,
        baseline_correct=baseline_correct,
        counterfactual_correct=counterfactual_correct,
        tier=tier,
        value=value,
    )
