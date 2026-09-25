"""Inspectable configuration evidence for provisional PRME policies.

The RFC suite marks unvalidated parameters with ``[HYPOTHESIS]``.  This module
turns those field annotations into a stable, JSON-safe report so applications
can audit the policies they are actually running without copying a second list
of values out of the documentation.
"""

from __future__ import annotations

from enum import Enum
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class HypothesisSetting(BaseModel):
    """One provisional configuration value and its activation state."""

    model_config = ConfigDict(frozen=True)

    path: str
    environment_variable: str
    value: Any
    default: Any
    customized: bool
    effective: bool
    activation_condition: str | None = None
    description: str


class HypothesisAudit(BaseModel):
    """Versioned snapshot of all hypothesis-tagged configuration fields."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "hypothesis-audit-v1"
    hypothesis_count: int = Field(ge=0)
    effective_count: int = Field(ge=0)
    customized_count: int = Field(ge=0)
    settings: tuple[HypothesisSetting, ...]


# A field remains discoverable without an entry here.  This map only describes
# feature gates that make a discovered parameter dormant.  Keeping discovery
# separate from activation means a newly tagged field cannot disappear merely
# because this map was not updated.
_ACTIVATION_GATES: dict[str, tuple[str, str, Callable[[Any], bool]]] = {
    "scoring.current_update_multiplier": (
        "scoring.current_update_multiplier",
        "scoring.current_update_multiplier > 1.0",
        lambda value: value > 1.0,
    ),
    "scoring.rrf_k": (
        "scoring.fusion",
        "scoring.fusion == 'rrf'",
        lambda value: value == "rrf",
    ),
    # ScoringWeights drops it unless fusion is 'rrf', so set means applied,
    # to current-state questions.
    "scoring.rrf_recency_boost": (
        "scoring.rrf_recency_boost",
        "scoring.rrf_recency_boost is set (current-state questions only)",
        lambda value: value is not None,
    ),
    # Applied only to triggers scored by rank fusion, which a request's own
    # weights can select on a weighted engine.
    "packing.session_context_rank_fusion_score_decay": (
        "packing.session_context_rank_fusion_score_decay",
        "packing.session_context_rank_fusion_score_decay is set",
        lambda value: value is not None,
    ),
    "packing.cross_scope_top_n": (
        "packing.cross_scope_top_n",
        "packing.cross_scope_top_n > 0",
        lambda value: value > 0,
    ),
    "packing.episode_context_top_k": (
        "packing.episode_context_top_k",
        "packing.episode_context_top_k > 0",
        lambda value: value > 0,
    ),
    "packing.episode_context_local_k": (
        "packing.episode_context_top_k",
        "packing.episode_context_top_k > 0",
        lambda value: value > 0,
    ),
    "packing.episode_context_score_decay": (
        "packing.episode_context_top_k",
        "packing.episode_context_top_k > 0",
        lambda value: value > 0,
    ),
    "packing.evidence_projection_top_k": (
        "packing.evidence_projection_top_k",
        "packing.evidence_projection_top_k > 0",
        lambda value: value > 0,
    ),
    "packing.evidence_projection_max_sources": (
        "packing.evidence_projection_top_k",
        "packing.evidence_projection_top_k > 0",
        lambda value: value > 0,
    ),
    "packing.evidence_projection_score_decay": (
        "packing.evidence_projection_top_k",
        "packing.evidence_projection_top_k > 0",
        lambda value: value > 0,
    ),
    "packing.evidence_augmentation_top_k": (
        "packing.evidence_augmentation_top_k",
        "packing.evidence_augmentation_top_k > 0",
        lambda value: value > 0,
    ),
    "packing.evidence_augmentation_max_sources": (
        "packing.evidence_augmentation_top_k",
        "packing.evidence_augmentation_top_k > 0",
        lambda value: value > 0,
    ),
    "packing.evidence_augmentation_score_decay": (
        "packing.evidence_augmentation_top_k",
        "packing.evidence_augmentation_top_k > 0",
        lambda value: value > 0,
    ),
    "packing.evidence_augmentation_anchor_policy": (
        "packing.evidence_augmentation_top_k",
        "packing.evidence_augmentation_top_k > 0",
        lambda value: value > 0,
    ),
    "enable_qa_pairing": (
        "enable_qa_pairing",
        "enable_qa_pairing is true",
        bool,
    ),
    "novelty_high_threshold": (
        "enable_surprise_gating",
        "enable_surprise_gating is true",
        bool,
    ),
    "novelty_low_threshold": (
        "enable_surprise_gating",
        "enable_surprise_gating is true",
        bool,
    ),
    "novelty_salience_boost": (
        "enable_surprise_gating",
        "enable_surprise_gating is true",
        bool,
    ),
    "novelty_salience_penalty": (
        "enable_surprise_gating",
        "enable_surprise_gating is true",
        bool,
    ),
    "query_reformulation_count": (
        "enable_query_reformulation",
        "enable_query_reformulation is true",
        bool,
    ),
}


def _path_value(model: BaseModel, path: str) -> Any:
    value: Any = model
    for part in path.split("."):
        value = getattr(value, part)
    return value


def _public_value(value: Any) -> Any:
    """Return a JSON-safe value while refusing to expose secret fields."""
    if isinstance(value, SecretStr):
        return "<redacted>"
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return {
            name: _public_value(getattr(value, name))
            for name in type(value).model_fields
        }
    if isinstance(value, dict):
        return {str(key): _public_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_public_value(item) for item in value]
    return value


def _environment_variable(path: str) -> str:
    return "PRME_" + "__".join(part.upper() for part in path.split("."))


def audit_hypotheses(config: BaseModel) -> HypothesisAudit:
    """Discover and report every ``[HYPOTHESIS]`` field in *config*.

    Discovery follows nested Pydantic models and therefore automatically
    includes future hypothesis-tagged fields.  Activation gates only determine
    whether a discovered value currently affects behavior.
    """
    settings: list[HypothesisSetting] = []

    def visit(model: BaseModel, prefix: str = "") -> None:
        for name, field in type(model).model_fields.items():
            value = getattr(model, name)
            path = f"{prefix}.{name}" if prefix else name
            description = field.description or ""
            if "[HYPOTHESIS" in description:
                default = field.get_default(call_default_factory=True)
                gate = _ACTIVATION_GATES.get(path)
                effective = True
                condition = None
                if gate is not None:
                    gate_path, condition, predicate = gate
                    effective = bool(predicate(_path_value(config, gate_path)))
                public_value = _public_value(value)
                public_default = _public_value(default)
                settings.append(
                    HypothesisSetting(
                        path=path,
                        environment_variable=_environment_variable(path),
                        value=public_value,
                        default=public_default,
                        customized=value != default,
                        effective=effective,
                        activation_condition=condition,
                        description=description,
                    )
                )
            if isinstance(value, BaseModel):
                visit(value, path)

    visit(config)
    return HypothesisAudit(
        hypothesis_count=len(settings),
        effective_count=sum(item.effective for item in settings),
        customized_count=sum(item.customized for item in settings),
        settings=tuple(settings),
    )
