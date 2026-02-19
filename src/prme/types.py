"""PRME type enums and lifecycle state machine.

Defines all domain enums (NodeType, EdgeType, Scope, LifecycleState)
and the lifecycle transition validation logic.
"""

from enum import Enum


class NodeType(str, Enum):
    """Types of memory graph nodes.

    Seven spec-defined types plus a generic Note catch-all.
    """

    ENTITY = "entity"
    EVENT = "event"
    FACT = "fact"
    DECISION = "decision"
    PREFERENCE = "preference"
    TASK = "task"
    SUMMARY = "summary"
    NOTE = "note"


class EdgeType(str, Enum):
    """Types of relationships between memory graph nodes."""

    RELATES_TO = "relates_to"
    SUPERSEDES = "supersedes"
    DERIVED_FROM = "derived_from"
    MENTIONS = "mentions"
    PART_OF = "part_of"
    CAUSED_BY = "caused_by"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"


class Scope(str, Enum):
    """Memory scoping levels.

    All three supported from day one per user decision.
    """

    PERSONAL = "personal"
    PROJECT = "project"
    ORG = "org"


class LifecycleState(str, Enum):
    """Memory object lifecycle states.

    Objects progress: Tentative -> Stable -> Superseded -> Archived.
    Forward-only transitions; Archived is terminal.
    """

    TENTATIVE = "tentative"
    STABLE = "stable"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


# Valid lifecycle transitions: forward-only, Archived is terminal.
ALLOWED_TRANSITIONS: dict[LifecycleState, set[LifecycleState]] = {
    LifecycleState.TENTATIVE: {
        LifecycleState.STABLE,
        LifecycleState.SUPERSEDED,
        LifecycleState.ARCHIVED,
    },
    LifecycleState.STABLE: {
        LifecycleState.SUPERSEDED,
        LifecycleState.ARCHIVED,
    },
    LifecycleState.SUPERSEDED: {
        LifecycleState.ARCHIVED,
    },
    LifecycleState.ARCHIVED: set(),  # terminal state
}


def validate_transition(current: LifecycleState, target: LifecycleState) -> bool:
    """Check whether a lifecycle state transition is valid.

    Args:
        current: The current lifecycle state.
        target: The desired target lifecycle state.

    Returns:
        True if the transition is allowed, False otherwise.
    """
    return target in ALLOWED_TRANSITIONS.get(current, set())
