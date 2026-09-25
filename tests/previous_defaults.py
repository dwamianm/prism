"""The retrieval settings that were the defaults before 2026-09-25.

Rank fusion with a current-state recency boost of 0.25 and an event-time
tie-break, the reader context format, score ordering and a 0.6 rank fusion
session decay became the defaults then. Tests of the weighted formula, JSON
contexts, balanced ordering, or the receipt versions those settings write pin
these settings explicitly instead of relying on the defaults.
"""

from prme.retrieval.config import PackingConfig, ScoringWeights

PREVIOUS_PACKING_DEFAULTS = {
    "context_format": "auditable",
    "multipath_ordering": "balanced",
    "session_context_rank_fusion_score_decay": None,
}


def previous_packing(**values) -> PackingConfig:
    """A PackingConfig that starts from the previous packing defaults."""
    return PackingConfig(**{**PREVIOUS_PACKING_DEFAULTS, **values})


def previous_defaults(config):
    """``config`` with the weighted formula and the previous packing defaults.

    ``ScoringWeights()`` is the weighted formula without the rank fusion
    recency boost and tie-break.
    """
    return config.model_copy(update={
        "scoring": ScoringWeights(),
        "packing": config.packing.model_copy(update=PREVIOUS_PACKING_DEFAULTS),
    })
