"""A packing configuration extension must not invalidate durable feedback links."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from prme.models.relevance import RetrievalReceipt


FIXTURES = Path(__file__).parent / "fixtures/relevance"


@pytest.mark.parametrize("version", [1, 2, 3])
def test_prior_receipt_canonical_bytes_and_checksum_survive(version):
    raw = (FIXTURES / f"receipt-v{version}.json").read_text()
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.model_dump_json() == raw
    assert restored.checksum == hashlib.sha256(raw.encode()).hexdigest()
    assert restored.packing.multipath_ordering == "density"
    assert "multipath_ordering" not in restored.model_dump()["packing"]
    assert restored.model_dump(include={"query"}) == {"query": restored.query}
    if version == 3:
        assert (
            restored.checksum
            == "df515beb704f88a65fcb3def812d9014a1ca75f0541856ff7a7520a4769609db"
        )


@pytest.mark.parametrize("version", [1, 2, 3])
def test_old_receipts_cannot_claim_the_new_score_ordering(version):
    payload = json.loads((FIXTURES / f"receipt-v{version}.json").read_text())
    payload["packing"]["multipath_ordering"] = "score"
    with pytest.raises(ValidationError):
        RetrievalReceipt.model_validate(payload)


@pytest.mark.parametrize("ordering", ["density", "score"])
def test_version_four_records_ordering_and_stable_roundtrip(ordering):
    payload = json.loads((FIXTURES / "receipt-v3.json").read_text())
    payload["schema_version"] = 4
    payload["packing"]["multipath_ordering"] = ordering
    receipt = RetrievalReceipt.model_validate(payload)
    assert receipt.model_dump()["packing"]["multipath_ordering"] == ordering
    assert (
        RetrievalReceipt.model_validate_json(receipt.model_dump_json()).checksum
        == receipt.checksum
    )
    assert receipt.replay_ranking() == tuple(
        item.node_id for item in receipt.candidates
    )


def test_version_four_cannot_infer_an_omitted_policy_or_execution():
    payload = json.loads((FIXTURES / "receipt-v3.json").read_text())
    payload["schema_version"] = 4
    with pytest.raises(ValidationError, match="explicit packing ordering"):
        RetrievalReceipt.model_validate(payload)
    payload["packing"]["multipath_ordering"] = "score"
    payload.pop("execution")
    with pytest.raises(ValidationError, match="execution descriptor"):
        RetrievalReceipt.model_validate(payload)


def test_version_eight_defaults_to_disabled_current_update_scoring():
    payload = json.loads((FIXTURES / "receipt-v4-score.json").read_text())
    payload["schema_version"] = 8
    payload["packing"].update(
        context_guidance_mode="off",
        context_format="auditable",
        episode_context_top_k=0,
        episode_context_local_k=8,
        episode_context_score_decay=0.95,
    )

    receipt = RetrievalReceipt.model_validate(payload)

    assert receipt.scoring.current_update_multiplier == 1.0
    assert all(
        item.weights.current_update_multiplier == 1.0
        for item in receipt.score_provenance.values()
    )
    serialized = receipt.model_dump()
    assert "current_update_multiplier" not in serialized["scoring"]
    assert all(
        "current_update_multiplier" not in item["weights"]
        for item in serialized["score_provenance"].values()
    )


def test_version_nine_requires_explicit_current_update_policy():
    payload = json.loads((FIXTURES / "receipt-v4-score.json").read_text())
    payload["schema_version"] = 9
    payload["packing"].update(
        context_guidance_mode="off",
        context_format="auditable",
        episode_context_top_k=0,
        episode_context_local_k=8,
        episode_context_score_decay=0.95,
    )

    with pytest.raises(ValidationError, match="explicit current-update multiplier"):
        RetrievalReceipt.model_validate(payload)


def test_version_ten_requires_explicit_evidence_projection_policy():
    payload = json.loads((FIXTURES / "receipt-v4-score.json").read_text())
    payload["schema_version"] = 10
    payload["packing"].update(
        context_guidance_mode="off",
        context_format="auditable",
        episode_context_top_k=0,
        episode_context_local_k=8,
        episode_context_score_decay=0.95,
    )
    payload["scoring"]["current_update_multiplier"] = 1.0
    for item in payload["score_provenance"].values():
        item["weights"]["current_update_multiplier"] = 1.0

    with pytest.raises(ValidationError, match="evidence projection settings"):
        RetrievalReceipt.model_validate(payload)
