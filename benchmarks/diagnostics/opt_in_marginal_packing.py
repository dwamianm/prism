"""Research-only marginal session packing; no public configuration or defaults.

Routing supplies a bounded bonus to a record's own relevance. It never reserves
an episode ahead of ordinary evidence and never inherits another record's score.
Repeated records in one exact scope/session receive diminishing marginal weight.
The product serializer and exact whole-output budget remain authoritative.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math

from prme.retrieval.episode_context import _bm25_scores, _terms
from prme.retrieval.packing import _is_pinned_or_active_task, _render_entry, pack_context
from prme.retrieval.tokenization import count_tokens
from prme.types import NodeType, RepresentationLevel


@dataclass(frozen=True)
class Policy:
    session_penalty: float
    episode_bonus: float


POLICIES = {
    'marginal_session_025': Policy(.25, 0.0),
    'marginal_episode_010': Policy(0.0, .10),
    'marginal_session_episode': Policy(.25, .10),
}


def session_key(candidate):
    node = candidate.node
    # Missing session is not a shared episode for unrelated records.
    return (node.scope.value, node.session_id or str(node.id))


def episode_support(candidates, query):
    groups = {}
    for candidate in candidates:
        if candidate.node.session_id is not None:
            groups.setdefault(session_key(candidate), []).append(candidate)
    groups = {key: rows for key, rows in groups.items() if len(rows) >= 2}
    if not groups:
        return {}
    keys = sorted(groups)
    query_terms = _terms(query)
    scores = _bm25_scores(query_terms, [tuple(term for c in groups[key] for term in _terms(c.node.content)) for key in keys])
    selected = sorted(range(len(keys)), key=lambda i: (-scores[i], keys[i]))[:2]
    maximum = max(scores, default=0)
    if maximum <= 0:
        return {}
    support = {}
    for index in selected:
        if scores[index] <= 0:
            continue
        members = groups[keys[index]]
        local = _bm25_scores(query_terms, [_terms(c.node.content) for c in members])
        peak = max(local, default=0)
        if peak > 0:
            for candidate, score in zip(members, local):
                support[candidate.node.id] = (scores[index] / maximum) * (score / peak)
    return support


def pack(candidates, query, config, policy, *, coverage_notice=None, context_guidance=None):
    if not math.isfinite(policy.session_penalty) or policy.session_penalty < 0 or not 0 <= policy.episode_bonus <= .10:
        raise ValueError('Marginal packing policy outside registered bounds')
    if config.context_format != 'auditable':
        raise ValueError('This research policy is registered only for auditable contexts')
    # Nodes and score provenance are read only; only candidate presentation fields
    # are changed by the ordinary packer. Avoid copying large immutable histories.
    candidates = [c.model_copy() for c in candidates]
    protected = [c for c in candidates if c.node.node_type == NodeType.INSTRUCTION or _is_pinned_or_active_task(c)]
    protected_ids = {c.node.id for c in protected}
    # Let the ordinary product rules determine whether mandatory records fit,
    # including their existing fidelity fallbacks. They always precede research
    # selections. Optional guidance retains the product non-displacement rule.
    initial = pack_context(protected, config, coverage_notice=coverage_notice)
    required = [(c.node.id, c.representation) for rows in initial.sections.values() for c in rows]
    selected_ids = {node_id for node_id, _ in required}
    selected = [c for c in candidates if c.node.id in selected_ids]
    counts = Counter(session_key(c) for c in selected)
    remaining = [c for c in candidates if c.node.id not in protected_ids]
    support = episode_support(remaining, query) if policy.episode_bonus else {}
    costs = {}
    for candidate in remaining:
        full = candidate.model_copy(update={'representation': RepresentationLevel.FULL,
                                             'rendered_text': candidate.node.content})
        costs[candidate.node.id] = count_tokens(_render_entry(full), config.tokenizer)
    # Preserve the baseline's one highest-score ordinary multipath anchor.
    head_candidates = [c for c in remaining if c.path_count >= 2]
    head = min(head_candidates, key=lambda c: (-c.composite_score, str(c.node.id))).node.id if head_candidates else None
    bundle = initial
    first = True
    while remaining:
        def order(candidate):
            if first and candidate.node.id == head:
                return -1, 0.0, str(candidate.node.id)
            tier = 0 if candidate.path_count >= 2 else 1
            value = (candidate.composite_score / max(costs[candidate.node.id], 1) ** .25
                     * (1 + policy.episode_bonus * support.get(candidate.node.id, 0.0))
                     / (1 + policy.session_penalty * counts[session_key(candidate)]))
            return tier, -value, str(candidate.node.id)
        current = min(remaining, key=order)
        remaining.remove(current)
        first = False
        # Cheap conservative rejection; the product count below decides fitting.
        if costs[current.node.id] > bundle.budget_remaining:
            continue
        trial_required = [*required, (current.node.id, RepresentationLevel.FULL)]
        trial = pack_context([*selected, current], config, coverage_notice=coverage_notice,
                             _required=trial_required)
        actual = {c.node.id: c.representation for rows in trial.sections.values() for c in rows}
        if any(actual.get(node_id) != level for node_id, level in trial_required):
            continue
        required = trial_required
        selected.append(current)
        selected_ids.add(current.node.id)
        counts[session_key(current)] += 1
        bundle = trial
    # Retain ordinary tiny-budget fallbacks for remaining records, without
    # allowing them to displace the selected whole-source records or user pins.
    return pack_context(candidates, config, coverage_notice=coverage_notice,
                        context_guidance=context_guidance, _required=required)
