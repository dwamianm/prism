"""Whole-source entity mention excerpts with explicit provenance and token limits."""
from datetime import datetime, timezone
import re

from prme.models import MemoryNode
from prme.retrieval.tokenization import count_tokens


def mentions_entity(text: str, name: str) -> bool:
    """Match a complete literal name, including possessives and Unicode boundaries."""
    return bool(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE))


def _date(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return (value.astimezone(timezone.utc) if value.tzinfo is not None else value).isoformat()


def build_profile(
    name: str, sources: list[MemoryNode], *, token_budget: int, tokenizer: str,
) -> tuple[str, list[MemoryNode], int] | None:
    """Select complete source records; distinct episodes never deduplicate by text."""
    text = f"[Knowledge Profile: {name}]\nSource mentions; entity association is inferred."
    selected = []
    # Stable identity breaks ties without relying on backend row order.
    ordered = sorted(sources, key=lambda node: (_date(node.event_time or node.created_at), str(node.id)))
    for node in ordered:
        source = (
            f"[source={node.id}; event_time={_date(node.event_time)}; "
            f"recorded_at={_date(node.created_at)}; valid_from={_date(node.valid_from)}; "
            f"valid_to={_date(node.valid_to)}; epistemic={node.epistemic_type.value}; "
            f"source_type={node.source_type.value}]\n{node.content}"
        )
        candidate = text + "\n\n" + source
        if count_tokens(candidate, tokenizer) > token_budget:
            # A long source must not prevent later, smaller complete sources.
            continue
        text = candidate
        selected.append(node)
    if not selected:
        return None
    return text, selected, count_tokens(text, tokenizer)
