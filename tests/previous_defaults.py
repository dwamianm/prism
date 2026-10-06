"""The retrieval settings that were the defaults before 2026-09-25.

Rank fusion with a current-state recency boost of 0.25 and an event-time
tie-break, the reader context format, score ordering and a 0.6 rank fusion
session decay became the defaults then, and balanced ordering replaced score
ordering again later that day. Tests of the weighted formula, JSON contexts,
or the receipt versions those settings write pin these settings explicitly
instead of relying on the defaults. Balanced ordering is named too, so the set
stays complete whatever the ordering default is. Folding each source text
once (packing.fold_repeated_text) became a default later, so the previous
defaults leave it off, and ``without_folding`` turns off only that.
"""

from prme.retrieval.config import PackingConfig, ScoringWeights

PREVIOUS_PACKING_DEFAULTS = {
    "context_format": "auditable",
    "multipath_ordering": "balanced",
    "session_context_rank_fusion_score_decay": None,
    "fold_repeated_text": False,
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


def without_folding(config):
    """``config`` without the repeated-text folding that PRMEConfig turned on by default.

    A folded retrieval writes a version 22 receipt, so tests of what earlier
    receipt versions record (the rank fusion session decay, a skipped floor,
    the recency settings, event-time recency, session context packing) pin it
    off and keep the other defaults.
    """
    return config.model_copy(update={"packing": config.packing.model_copy(update={"fold_repeated_text": False})})
