"""Virtual decay computation for memory object salience and confidence.

Implements the RFC-0007/RFC-0015 decay formula for computing effective
(virtual) salience and confidence values from base values and time elapsed
since last reinforcement.

Decay is computed on-read (virtual), not persisted. Base values
(salience_base, confidence_base) remain unchanged in storage. The
effective values are used for retrieval scoring and threshold checks.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from prme.models.nodes import MemoryNode
from prme.types import DECAY_LAMBDAS, DecayProfile, EpistemicType, LifecycleState

# Reinforcement boost decay rate (constant, per RFC-0015 S3)
REINFORCEMENT_DECAY_RATE: float = 0.10


def compute_effective_salience(
    salience_base: float,
    reinforcement_boost: float,
    decay_profile: DecayProfile,
    last_reinforced_at: datetime,
    now: datetime,
    pinned: bool = False,
) -> float:
    """Compute effective salience using RFC-0007 decay formula.

    effective_salience = salience_base * exp(-lambda * t)
                       + reinforcement_boost * exp(-rho * t)

    where t = days since last_reinforced_at, rho = 0.10.

    Args:
        salience_base: Baseline salience before decay.
        reinforcement_boost: Cumulative reinforcement boost.
        decay_profile: Decay rate profile (determines lambda).
        last_reinforced_at: Timestamp of last reinforcement.
        now: Current timestamp for computing elapsed time.
        pinned: If True, return base + boost (no decay).

    Returns:
        Effective salience value, clamped to [0.0, 1.0].
    """
    if pinned:
        return min(salience_base + reinforcement_boost, 1.0)

    lam = DECAY_LAMBDAS[decay_profile]
    t = _days_elapsed(last_reinforced_at, now)

    effective = (
        salience_base * math.exp(-lam * t)
        + reinforcement_boost * math.exp(-REINFORCEMENT_DECAY_RATE * t)
    )
    return max(0.0, min(effective, 1.0))


def compute_effective_confidence(
    confidence_base: float,
    decay_profile: DecayProfile,
    last_reinforced_at: datetime,
    now: datetime,
    pinned: bool = False,
    epistemic_type: EpistemicType | None = None,
) -> float:
    """Compute effective confidence using RFC-0007 S4.

    effective_confidence = confidence_base * exp(-mu * t)
    where mu = lambda * 0.5.

    OBSERVED nodes: confidence decay only if t > 180 days.

    Args:
        confidence_base: Baseline confidence before decay.
        decay_profile: Decay rate profile (determines lambda).
        last_reinforced_at: Timestamp of last reinforcement.
        now: Current timestamp for computing elapsed time.
        pinned: If True, return base (no decay).
        epistemic_type: Epistemic classification. OBSERVED nodes have
            a 180-day grace period before confidence starts decaying.

    Returns:
        Effective confidence value, clamped to [0.0, 1.0].
    """
    if pinned:
        return confidence_base

    lam = DECAY_LAMBDAS[decay_profile]
    mu = lam * 0.5
    t = _days_elapsed(last_reinforced_at, now)

    # OBSERVED nodes: no confidence decay for first 180 days
    if epistemic_type == EpistemicType.OBSERVED and t <= 180.0:
        return confidence_base

    effective = confidence_base * math.exp(-mu * t)
    return max(0.0, min(effective, 1.0))


def apply_virtual_decay(node: MemoryNode, now: datetime) -> MemoryNode:
    """Return a copy of node with effective salience/confidence applied.

    Does NOT mutate the original node. Returns a new MemoryNode with
    the ``salience`` and ``confidence`` fields set to the virtual effective
    values. The base values remain unchanged.

    Exemptions (no decay applied):
    - pinned nodes
    - PERMANENT decay profile
    - ARCHIVED/DEPRECATED lifecycle state

    Args:
        node: The MemoryNode to compute virtual decay for.
        now: Current timestamp for computing elapsed time.

    Returns:
        A new MemoryNode with effective salience and confidence values.
    """
    # Exemptions: no decay
    if (
        node.pinned
        or node.decay_profile == DecayProfile.PERMANENT
        or node.lifecycle_state in (LifecycleState.ARCHIVED, LifecycleState.DEPRECATED)
    ):
        return node.model_copy()

    effective_salience = compute_effective_salience(
        salience_base=node.salience_base,
        reinforcement_boost=node.reinforcement_boost,
        decay_profile=node.decay_profile,
        last_reinforced_at=node.last_reinforced_at,
        now=now,
        pinned=node.pinned,
    )

    effective_confidence = compute_effective_confidence(
        confidence_base=node.confidence_base,
        decay_profile=node.decay_profile,
        last_reinforced_at=node.last_reinforced_at,
        now=now,
        pinned=node.pinned,
        epistemic_type=node.epistemic_type,
    )

    return node.model_copy(
        update={
            "salience": effective_salience,
            "confidence": effective_confidence,
        }
    )


def _days_elapsed(since: datetime, now: datetime) -> float:
    """Compute days elapsed between two datetimes.

    Handles timezone-naive datetimes by assuming UTC.

    Args:
        since: Start timestamp.
        now: End timestamp.

    Returns:
        Fractional days elapsed (non-negative, clamped to 0).
    """
    # Ensure both are timezone-aware (assume UTC if naive)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    delta = now - since
    days = delta.total_seconds() / 86400.0
    return max(0.0, days)
