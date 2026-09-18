"""Fail-closed natural-language planning for exact quantity aggregation."""

from __future__ import annotations

import re

from prme.models.aggregation import (
    QuantityAggregationPlan,
    QuantityAggregationPlanReason,
    QuantityAggregationQuery,
)


_AMOUNT_QUESTION_RE = re.compile(
    r"^\s*how\s+much(?:\s+money)?\s+did\s+(?P<subject>i|we)\s+"
    r"(?P<action>[a-z][a-z'-]*)\s*[?!.]?\s*$",
    re.IGNORECASE,
)
_TOTAL_QUESTION_RE = re.compile(
    r"^\s*what(?:'s|\s+is)\s+the\s+total(?:\s+amount)?\s+"
    r"(?P<subject>i|we)\s+(?P<action>[a-z][a-z'-]*)\s*[?!.]?\s*$",
    re.IGNORECASE,
)
_COUNT_QUESTION_RE = re.compile(
    r"^\s*how\s+many\s+(?P<unit>[a-z][a-z'-]*)\s+did\s+"
    r"(?P<subject>i|we)\s+(?P<action>[a-z][a-z'-]*)\s*[?!.]?\s*$",
    re.IGNORECASE,
)

_ACTION_FAMILIES: dict[str, tuple[str, ...]] = {
    "complete": ("complete", "completed", "completing"),
    "completed": ("complete", "completed", "completing"),
    "consume": ("consume", "consumed", "consuming"),
    "consumed": ("consume", "consumed", "consuming"),
    "donate": ("donate", "donated", "donating"),
    "donated": ("donate", "donated", "donating"),
    "drink": ("drink", "drank", "drinking"),
    "drank": ("drink", "drank", "drinking"),
    "earn": ("earn", "earned", "earning"),
    "earned": ("earn", "earned", "earning"),
    "lift": ("lift", "lifted", "lifting"),
    "lifted": ("lift", "lifted", "lifting"),
    "pay": ("pay", "paid", "paying"),
    "paid": ("pay", "paid", "paying"),
    "process": ("process", "processed", "processing"),
    "processed": ("process", "processed", "processing"),
    "raise": (
        "raise",
        "raised",
        "raising",
        "help_raise",
        "helped_raise",
        "helping_raise",
    ),
    "raised": (
        "raise",
        "raised",
        "raising",
        "help_raise",
        "helped_raise",
        "helping_raise",
    ),
    "run": ("run", "ran", "running"),
    "ran": ("run", "ran", "running"),
    "ship": ("ship", "shipped", "shipping"),
    "shipped": ("ship", "shipped", "shipping"),
    "spend": ("spend", "spent", "spending"),
    "spent": ("spend", "spent", "spending"),
}

_EXPLICIT_COUNT_UNITS = frozenset({
    "amps",
    "amperes",
    "bytes",
    "centimeters",
    "files",
    "gallons",
    "grams",
    "items",
    "kilograms",
    "kilometers",
    "liters",
    "meters",
    "miles",
    "milligrams",
    "milliliters",
    "millimeters",
    "packages",
    "people",
    "records",
    "requests",
    "tasks",
    "tickets",
    "units",
    "users",
    "volts",
    "watts",
})

_ASSUMPTIONS = (
    "owner_bound",
    "first_person_subject_exact",
    "positive_default_epistemic",
    "predicate_token_prefix_family",
    "unit_groups_are_never_converted",
)


def plan_quantity_aggregation(question: str) -> QuantityAggregationPlan:
    """Translate one narrow amount/count question or return an audit refusal.

    Accepted shapes contain no trailing qualifiers. This prevents a phrase such
    as ``for charity`` or ``last year`` from being silently discarded. Action
    variants come from a fixed inspectable table; the resulting query still
    uses the exact stored-set aggregation path.
    """
    if not question.strip():
        raise ValueError("quantity aggregation question must be nonempty")
    match = _AMOUNT_QUESTION_RE.fullmatch(question) or _TOTAL_QUESTION_RE.fullmatch(
        question
    )
    unit: str | None = None
    reason: QuantityAggregationPlanReason = "matched_amount_question"
    if match is None:
        match = _COUNT_QUESTION_RE.fullmatch(question)
        if match is None:
            return QuantityAggregationPlan(
                question=question,
                status="unsupported",
                reason="unsupported_shape",
            )
        unit = match.group("unit").casefold()
        reason = "matched_count_question"
        if unit not in _EXPLICIT_COUNT_UNITS:
            return QuantityAggregationPlan(
                question=question,
                status="unsupported",
                reason="unsupported_unit",
                action=match.group("action").casefold(),
            )

    action = match.group("action").casefold()
    subject = "I" if match.group("subject").casefold() == "i" else "we"
    prefixes = _ACTION_FAMILIES.get(action)
    if prefixes is None:
        return QuantityAggregationPlan(
            question=question,
            status="unsupported",
            reason="unsupported_action",
            action=action,
        )
    return QuantityAggregationPlan(
        question=question,
        status="ready",
        reason=reason,
        action=action,
        query=QuantityAggregationQuery(
            subjects=(subject,),
            predicate_prefixes=prefixes,
            units=(unit,) if unit is not None else (),
            group_by=("unit",),
            sample_limit=10,
        ),
        assumptions=_ASSUMPTIONS,
    )
