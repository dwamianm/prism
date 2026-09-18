"""Evidence-only evaluation primitives, independent of answer generation.

Ground-truth labels remain in the evaluator. Source IDs are neutral positions
in the conversation, never dataset session IDs that may encode answer status.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from collections.abc import Callable, Sequence


@dataclass(frozen=True)
class SourceTurn:
    id: str
    session_id: str
    role: str
    content: str
    date: str

    def render(self) -> str:
        return f"[{self.id}; {self.date}; {self.role}]\n{self.content}"


def longmemeval_sources(question: dict) -> tuple[list[SourceTurn], set[str]]:
    turns = []
    evidence = set()
    dates = question.get("haystack_dates", [])
    for i, session in enumerate(question["haystack_sessions"]):
        for j, turn in enumerate(session):
            source_id = f"s{i}:t{j}"
            turns.append(SourceTurn(
                id=source_id, session_id=f"session-{i}", role=turn["role"],
                content=turn["content"], date=dates[i] if i < len(dates) else "",
            ))
            if turn.get("has_answer", False):
                evidence.add(source_id)
    return turns, evidence


def select_questions(
    questions: list[dict], *, split: str, seed: str = "prme-evidence-v1", limit: int = 0,
) -> list[dict]:
    """Stable 20% development / 80% test assignment, keeping abstention pairs together.

    These are PRME's evaluation splits, not official dataset splits. Selection
    depends only on question identity, never answers or measured outcomes.
    """
    if split not in {"dev", "test", "all"} or limit < 0:
        raise ValueError("Choose dev, test, or all and a nonnegative limit")
    ids = [q["question_id"] for q in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate question IDs cannot define a reproducible split")

    def key(q):
        group = q["question_id"].removesuffix("_abs")
        return hashlib.sha256(f"{seed}:{group}".encode()).hexdigest()

    selected = [q for q in questions if (
        split == "all" or (int(key(q), 16) % 5 == 0) == (split == "dev")
    )]
    selected.sort(key=lambda q: (key(q), q["question_id"]))
    return selected[:limit] if limit else selected


def evidence_metrics(
    ranked_ids: Sequence[str], relevant: set[str], *, ks: Sequence[int] = (5, 10, 20, 50),
) -> dict[str, float | None]:
    """Source-turn recall/MRR/binary nDCG; unlabeled queries have null metrics.

    Deduplicate sources before assigning ranks so repeated references cannot
    inflate scores. No relevant labels means 'not measured', never perfect recall.
    """
    ranked = list(dict.fromkeys(ranked_ids))
    reciprocal_rank = next((1 / i for i, sid in enumerate(ranked, 1) if sid in relevant), 0.0)
    result = {"mrr": reciprocal_rank if relevant else None}
    for k in ks:
        if k < 1:
            raise ValueError("Evidence cutoffs must be positive")
        if not relevant:
            result[f"recall@{k}"] = result[f"ndcg@{k}"] = None
            continue
        hits = [i for i, sid in enumerate(ranked[:k], 1) if sid in relevant]
        result[f"recall@{k}"] = len(hits) / len(relevant)
        ideal = sum(1 / math.log2(i + 1) for i in range(1, min(k, len(relevant)) + 1))
        result[f"ndcg@{k}"] = sum(1 / math.log2(i + 1) for i in hits) / ideal
    return result


def pack_sources(
    turns: Sequence[SourceTurn], *, token_budget: int, count_tokens: Callable[[str], int],
) -> tuple[str, list[str], int]:
    """Greedily fit whole source turns, counting the entire rendered context.

    This shared evaluator packer isolates ranking quality. It does not claim
    to measure PRME's product packer, nor does it truncate qualifiers to fit.
    """
    if token_budget < 0:
        raise ValueError("Token budget must be nonnegative")
    parts: list[str] = []
    included: list[str] = []
    seen = set()
    for turn in turns:
        if turn.id in seen:
            continue
        seen.add(turn.id)
        proposed = "\n\n".join([*parts, turn.render()])
        if count_tokens(proposed) <= token_budget:
            parts.append(turn.render())
            included.append(turn.id)
    context = "\n\n".join(parts)
    return context, included, count_tokens(context)


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], *, constant: int = 60) -> list[str]:
    """A score-scale-independent baseline; constant is recorded in the report."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, source in enumerate(dict.fromkeys(ranking), 1):
            scores[source] = scores.get(source, 0.0) + 1 / (constant + rank)
    return sorted(scores, key=lambda source: (-scores[source], source))
