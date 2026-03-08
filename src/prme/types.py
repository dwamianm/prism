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
    HAS_FACT = "has_fact"


class Scope(str, Enum):
    """Memory scoping levels (RFC-0004 S3).

    Six namespace types covering all isolation patterns:
    - PERSONAL: Single human user. Highest trust.
    - PROJECT: Shared across actors working on a common goal.
    - ORGANISATION: Shared across an organisation. Cross-project facts.
    - AGENT: Private to a specific AI agent's working memory.
    - SYSTEM: Reserved for system-generated content (summaries, organizer output).
    - SANDBOX: Temporary isolated scope for testing/simulation.
      Supports HARD_DELETE expiry action (RFC-0004 S7).
    """

    PERSONAL = "personal"
    PROJECT = "project"
    ORGANISATION = "organisation"
    AGENT = "agent"
    SYSTEM = "system"
    SANDBOX = "sandbox"


class LifecycleState(str, Enum):
    """Memory object lifecycle states.

    Objects progress: Tentative -> Stable -> Superseded -> Archived.
    Objects may also transition to Contested (unresolved contradiction)
    and Deprecated (confirmed incorrect). Forward-only transitions;
    Archived is terminal.
    """

    TENTATIVE = "tentative"
    STABLE = "stable"
    CONTESTED = "contested"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


# Valid lifecycle transitions: forward-only, Archived is terminal.
ALLOWED_TRANSITIONS: dict[LifecycleState, set[LifecycleState]] = {
    LifecycleState.TENTATIVE: {
        LifecycleState.STABLE,
        LifecycleState.SUPERSEDED,
        LifecycleState.CONTESTED,
        LifecycleState.ARCHIVED,
    },
    LifecycleState.STABLE: {
        LifecycleState.SUPERSEDED,
        LifecycleState.CONTESTED,
        LifecycleState.ARCHIVED,
    },
    LifecycleState.CONTESTED: {
        LifecycleState.STABLE,
        LifecycleState.DEPRECATED,
        LifecycleState.ARCHIVED,
    },
    LifecycleState.SUPERSEDED: {
        LifecycleState.ARCHIVED,
    },
    LifecycleState.DEPRECATED: {
        LifecycleState.ARCHIVED,
    },
    LifecycleState.ARCHIVED: set(),  # terminal state
}


class SourceType(str, Enum):
    """Source provenance type for confidence matrix lookup (RFC-0003 S4).

    Tracks how a memory object was originally produced. Persisted on
    MemoryNode for downstream provenance auditing and confidence scoring.
    """

    USER_STATED = "user_stated"
    USER_DEMONSTRATED = "user_demonstrated"
    SYSTEM_INFERRED = "system_inferred"
    EXTERNAL_DOCUMENT = "external_document"
    TOOL_OUTPUT = "tool_output"


class EpistemicType(str, Enum):
    """Epistemic status of memory assertions (RFC-0003 S3).

    Used for epistemic filtering (retrieval Stage 4) and epistemic
    weight lookup (scoring Stage 5).
    """

    OBSERVED = "observed"
    ASSERTED = "asserted"
    INFERRED = "inferred"
    HYPOTHETICAL = "hypothetical"
    CONDITIONAL = "conditional"
    DEPRECATED = "deprecated"
    UNVERIFIED = "unverified"


class DecayProfile(str, Enum):
    """Decay rate profiles for memory object salience/confidence decay (RFC-0015).

    Each profile maps to a lambda decay rate coefficient.
    Half-life = ln(2) / lambda days.
    """

    PERMANENT = "permanent"   # lambda = 0.000, no decay
    SLOW      = "slow"        # lambda = 0.005, half-life ~139 days
    MEDIUM    = "medium"      # lambda = 0.020, half-life ~35 days
    FAST      = "fast"        # lambda = 0.070, half-life ~10 days
    RAPID     = "rapid"       # lambda = 0.200, half-life ~3.5 days


DECAY_LAMBDAS: dict[DecayProfile, float] = {
    DecayProfile.PERMANENT: 0.000,
    DecayProfile.SLOW: 0.005,
    DecayProfile.MEDIUM: 0.020,
    DecayProfile.FAST: 0.070,
    DecayProfile.RAPID: 0.200,
}


DEFAULT_DECAY_PROFILE_MAPPING: dict[EpistemicType, DecayProfile] = {
    EpistemicType.OBSERVED: DecayProfile.SLOW,
    EpistemicType.ASSERTED: DecayProfile.MEDIUM,
    EpistemicType.INFERRED: DecayProfile.FAST,
    EpistemicType.HYPOTHETICAL: DecayProfile.RAPID,
    EpistemicType.CONDITIONAL: DecayProfile.MEDIUM,
    EpistemicType.DEPRECATED: DecayProfile.PERMANENT,
    EpistemicType.UNVERIFIED: DecayProfile.RAPID,
}


class QueryIntent(str, Enum):
    """Classification of retrieval query intent.

    Used by query analysis to classify intent and select retrieval backends.
    """

    SEMANTIC = "semantic"
    FACTUAL = "factual"
    ENTITY_LOOKUP = "entity_lookup"
    TEMPORAL = "temporal"
    RELATIONAL = "relational"


class RetrievalMode(str, Enum):
    """Retrieval mode controlling epistemic filtering (RFC-0003 S8).

    DEFAULT excludes HYPOTHETICAL and DEPRECATED; EXPLICIT includes everything.
    """

    DEFAULT = "default"
    EXPLICIT = "explicit"


class RepresentationLevel(str, Enum):
    """Context representation fidelity levels (RFC-0006 S4).

    Ordered by token cost, lowest to highest.
    """

    REFERENCE = "reference"
    KEY_VALUE = "key_value"
    STRUCTURED = "structured"
    PROSE = "prose"
    FULL = "full"


# Epistemic types valid at creation time. DEPRECATED is excluded --
# it is a lifecycle transition only, not assignable at creation
# (per CONTEXT.md locked decision).
CREATION_EPISTEMIC_TYPES: frozenset[EpistemicType] = frozenset({
    EpistemicType.OBSERVED,
    EpistemicType.ASSERTED,
    EpistemicType.INFERRED,
    EpistemicType.HYPOTHETICAL,
    EpistemicType.CONDITIONAL,
    EpistemicType.UNVERIFIED,
})

# [HYPOTHESIS] -- tunable per deployment
# Epistemic multiplier values for composite score formula (RFC-0005 S7).
EPISTEMIC_WEIGHTS: dict[EpistemicType, float] = {
    EpistemicType.OBSERVED: 1.0,
    EpistemicType.ASSERTED: 0.9,
    EpistemicType.INFERRED: 0.7,
    EpistemicType.HYPOTHETICAL: 0.3,
    EpistemicType.CONDITIONAL: 0.5,
    EpistemicType.DEPRECATED: 0.1,
    EpistemicType.UNVERIFIED: 0.5,
}

# Epistemic types excluded by DEFAULT retrieval mode.
DEFAULT_EXCLUDED_EPISTEMIC: set[EpistemicType] = {
    EpistemicType.HYPOTHETICAL,
    EpistemicType.DEPRECATED,
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
