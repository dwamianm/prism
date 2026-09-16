"""Auditable results for controlled context-entry interventions."""

import hashlib
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from prme.retrieval.models import MemoryBundle


CreditTier = Literal[
    "load_bearing",
    "cited_non_flipping",
    "misleading",
    "cited_wrong_noncuring",
]


class ContextAblation(BaseModel):
    """One exact removal of entries from an already packed context.

    The remaining entries are byte-for-byte unchanged and no replacement
    candidates are introduced. This isolates context use; it is deliberately
    distinct from deleting a memory and running retrieval again.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    semantics: Literal["exact_context_entry_removal"] = (
        "exact_context_entry_removal"
    )
    removed_node_ids: tuple[UUID, ...] = Field(min_length=1)
    baseline_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    counterfactual_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    counterfactual: MemoryBundle

    @model_validator(mode="after")
    def valid_counterfactual(self):
        if len(self.removed_node_ids) != len(set(self.removed_node_ids)):
            raise ValueError("removed_node_ids must be unique")
        remaining = [
            candidate.node.id
            for candidates in self.counterfactual.sections.values()
            for candidate in candidates
        ]
        if set(self.removed_node_ids) & set(remaining):
            raise ValueError("Removed entries remain in the counterfactual context")
        if len(remaining) != len(set(remaining)):
            raise ValueError("Counterfactual context contains duplicate entries")
        if self.counterfactual.included_count != len(remaining):
            raise ValueError("Counterfactual included_count does not match its entries")
        actual = hashlib.sha256(self.counterfactual.render().encode()).hexdigest()
        if actual != self.counterfactual_context_sha256:
            raise ValueError("Counterfactual context checksum mismatch")
        return self


class ContextPresenceCredit(BaseModel):
    """Signed evidence from re-answering one exact context ablation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    semantics: Literal["context_presence_credit_v1"] = "context_presence_credit_v1"
    request_id: UUID
    citation_id: UUID
    node_id: UUID
    answer_id: str = Field(min_length=1, max_length=512)
    citation_method: Literal[
        "model_reported", "application_verified", "human_verified"
    ]
    evaluation_id: str = Field(min_length=1, max_length=512)
    baseline_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    counterfactual_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    counterfactual_answer_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    baseline_correct: StrictBool
    counterfactual_correct: StrictBool
    tier: CreditTier
    value: float = Field(ge=-1, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_tier_value(self):
        expected = {
            (True, False): ("load_bearing", 1.0),
            (True, True): ("cited_non_flipping", 0.6),
            (False, True): ("misleading", -1.0),
            (False, False): ("cited_wrong_noncuring", 0.0),
        }[(self.baseline_correct, self.counterfactual_correct)]
        if (self.tier, self.value) != expected:
            raise ValueError("Credit tier does not match the recorded outcomes")
        if not self.evaluation_id.strip():
            raise ValueError("evaluation_id cannot be blank")
        return self
