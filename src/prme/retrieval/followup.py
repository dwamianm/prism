"""Bounded evidence-conditioned query construction; no labels or model calls."""
from __future__ import annotations

import re

from prme.retrieval.models import QueryAnalysis, RetrievalCandidate
from prme.retrieval.query_analysis import _extract_entities
from prme.types import NodeType

FOLLOWUP_K = 30
FOLLOWUP_ANCHORS = 5


def followup_analysis(analysis: QueryAnalysis, ranked: list[RetrievalCandidate]) -> tuple[QueryAnalysis | None, dict]:
    """Append at most 12 bounded signals from the first five ranked records.

    Reuse the original intent/time analysis so extracted dates cannot change
    request eligibility and the second round needs no dateparser pass.
    """
    signals: list[str] = []
    anchors = ranked[:FOLLOWUP_ANCHORS]
    for candidate in anchors:
        node = candidate.node
        values = [node.content] if node.node_type == NodeType.ENTITY else []
        values += _extract_entities(node.content[:1000])[:3]
        predicate = (node.metadata or {}).get("predicate")
        if node.node_type == NodeType.FACT and isinstance(predicate, str):
            values.append(predicate.replace("_", " "))
        for value in values:
            clean = " ".join(re.findall(r"[\w'-]+", value))[:80].strip()
            if clean and clean.casefold() not in analysis.query.casefold() and clean not in signals:
                signals.append(clean)
            if len(signals) == 12:
                break
        if len(signals) == 12:
            break
    observation = {"policy": "evidence_followup_v1", "anchor_ids": [str(c.node.id) for c in anchors],
                   "signals": signals, "per_path_limit": FOLLOWUP_K}
    if not signals:
        return None, {**observation, "status": "no_signals"}
    query = analysis.query + " " + " ".join(signals)
    entities = list(dict.fromkeys([*analysis.entities, *signals]))[:12]
    return analysis.model_copy(update={"query": query, "entities": entities}), {
        **observation, "query": query, "status": "prepared"}
