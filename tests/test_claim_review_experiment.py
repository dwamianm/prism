"""Experimental review cannot silently lose coverage, evidence or qualifications."""

import pytest
from pydantic import ValidationError

from benchmarks.diagnostics.claim_review import Review, reviewed_result
from prme.ingestion.schema import ExtractionResult
from prme.models import MemoryNode
from prme.types import NodeType

SOURCE = "Maya might use Redis if latency improves."


def payload(ids):
    return {"assessments": [{"claim_id": i, "supported": True, "fact_type": "fact",
                             "epistemic_type": "conditional", "evidence_quote": SOURCE} for i in ids]}


@pytest.mark.parametrize("ids", [[], [0], [0, 0], [0, 2], [0, 1, 2]])
def test_review_rejects_missing_duplicate_and_unknown_claim_ids(ids):
    with pytest.raises(ValidationError):
        Review.model_validate(payload(ids), context={"claim_ids": [0, 1], "source": SOURCE})


def test_review_rejects_fabricated_citation():
    data = payload([0])
    data["assessments"][0]["evidence_quote"] = "Maya prefers Redis."
    with pytest.raises(ValidationError):
        Review.model_validate(data, context={"claim_ids": [0], "source": SOURCE})


def test_review_reorders_by_identity_and_keeps_full_source_without_mutating_input():
    original = ExtractionResult.model_validate({"entities": [{"name": "Maya", "entity_type": "person"}],
        "facts": [{"subject": "Maya", "predicate": "uses", "object": "Redis", "evidence_quote": SOURCE,
                   "fact_type": "preference", "epistemic_type": "asserted"}]})
    before = original.model_dump()
    node = MemoryNode(user_id="probe", content=SOURCE, node_type=NodeType.PREFERENCE,
                      metadata=original.facts[0].model_dump())
    unsupported = node.model_copy(update={"metadata": {**node.metadata, "predicate": "prefers"}})
    data = payload([1, 0])
    data["assessments"][0]["supported"] = False
    # A short genuine review quote must not truncate stored source conditions.
    data["assessments"][1]["evidence_quote"] = "Maya might use Redis"
    review = Review.model_validate(data, context={"claim_ids": [0, 1], "source": SOURCE})
    result = reviewed_result(original, [node, unsupported], review)
    assert len(result.facts) == 1
    assert result.facts[0].fact_type == "fact" and result.facts[0].epistemic_type == "conditional"
    assert result.facts[0].evidence_quote == SOURCE
    assert original.model_dump() == before
