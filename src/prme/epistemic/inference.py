"""Epistemic type and source type inference from node context.

These functions provide default epistemic_type and source_type assignments
for the store() code path, where no LLM classification is available. The
caller can always override the inferred values with explicit types.

Reference: RFC-0003 Section 3 (epistemic types), Section 4 (source types).
"""

from __future__ import annotations

from prme.types import EpistemicType, NodeType, SourceType

# Roles that speak for the memory's owner. Owner-only behavior, such as
# reinforcing a repeated instruction, applies only to these.
OWNER_ROLES = frozenset({"user", "human"})
# A human in the conversation other than the memory's owner, such as the
# second person in a two-person chat. Like the owner, a first-party source.
PARTICIPANT_ROLE = "participant"
FIRST_PARTY_ROLES = OWNER_ROLES | {PARTICIPANT_ROLE}


def infer_epistemic_type(
    node_type: NodeType,
    content: str | None = None,
    metadata: dict | None = None,
) -> EpistemicType:
    """Infer the best-guess epistemic type from node context.

    Heuristics based on node_type:
    - SUMMARY nodes -> INFERRED (system-generated)
    - EVENT nodes -> OBSERVED (direct user input)
    - All other nodes -> ASSERTED (extracted from text)

    This is for the store() path where no LLM classification is
    available. The caller can always override with an explicit type.

    Args:
        node_type: The type of the memory node.
        content: Optional node content (reserved for future heuristics).
        metadata: Optional metadata dict (reserved for future heuristics).

    Returns:
        The inferred EpistemicType.
    """
    if node_type == NodeType.SUMMARY:
        return EpistemicType.INFERRED
    if node_type == NodeType.EVENT:
        return EpistemicType.OBSERVED
    # INSTRUCTION nodes default to OBSERVED (user-stated behavioral rules)
    # but callers can override to INFERRED for system-learned rules.
    if node_type == NodeType.INSTRUCTION:
        return EpistemicType.OBSERVED
    # ENTITY, FACT, DECISION, PREFERENCE, TASK, NOTE
    return EpistemicType.ASSERTED


def infer_source_type(
    node_type: NodeType,
    role: str | None = None,
) -> SourceType:
    """Infer the source provenance type from node context.

    Heuristics based on role and node_type:
    - role == "user" or "human" -> USER_STATED
    - role == "participant" -> USER_STATED (a human in the conversation
      other than the memory's owner, such as the second person in a
      two-person chat)
    - role == "assistant" or "system" -> SYSTEM_INFERRED
    - role == "tool" -> TOOL_OUTPUT
    - EVENT nodes without role -> USER_STATED (events are user input)
    - All others without role -> USER_STATED (conservative default)

    The store() path, raw-source materialization and the ingestion
    pipeline's extracted claims all use this mapping.

    Args:
        node_type: The type of the memory node.
        role: Optional role string (e.g., 'user', 'participant', 'assistant').

    Returns:
        The inferred SourceType.
    """
    if role is not None:
        role_lower = role.lower()
        if role_lower in FIRST_PARTY_ROLES:
            return SourceType.USER_STATED
        if role_lower in ("assistant", "system"):
            return SourceType.SYSTEM_INFERRED
        if role_lower == "tool":
            return SourceType.TOOL_OUTPUT
    # Conservative default: USER_STATED
    return SourceType.USER_STATED
