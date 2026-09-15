"""The public option must retain the exact tested policy and legacy receipt bytes."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from benchmarks.diagnostics.packing_composition import pack
from prme.models.relevance import RetrievalReceipt
from prme.retrieval.config import PackingConfig
from prme.retrieval.packing import pack_context
from tests.test_packing_order_option import candidate


@pytest.mark.parametrize("budget", [0, 20, 300, 900, 2000, 8000])
@pytest.mark.parametrize("fidelity", ["reference", "full"])
def test_public_balanced_matches_fixed_experiment_without_mutating_inputs(budget, fidelity):
    values = [candidate(1, "Only if the pilot succeeds. " * 100, .99),
              candidate(2, "Pinned source", .01, pinned=True),
              *[candidate(i + 3, f"Short source {i}", .4) for i in range(8)]]
    before = [c.model_dump(mode="json") for c in values]
    config = PackingConfig(token_budget=budget, min_fidelity=fidelity)
    expected = pack(values, config, reserve_head=True, alpha=.25)
    actual = pack_context(values, config.model_copy(update={"multipath_ordering": "balanced"}))
    assert actual.render() == expected.render()
    assert actual.tokens_used == expected.tokens_used
    assert actual.excluded_ids == expected.excluded_ids
    assert [c.model_dump(mode="json") for c in values] == before


@pytest.mark.parametrize("name,checksum", [
    ("receipt-v4.json", "2e3bbf779423fd0aaec89d4d75b25677316ca130ebc568f24f5ace35955679f6"),
    ("receipt-v4-score.json", "dad272c4448c0c7e0a4d543591be6cebeb3141e2582d90e1df3b4359a9605e05"),
])
def test_prechange_v4_wire_bytes_remain_identical(name, checksum):
    raw = (Path(__file__).parent / "fixtures/relevance" / name).read_text()
    assert hashlib.sha256(raw.encode()).hexdigest() == checksum
    receipt = RetrievalReceipt.model_validate_json(raw)
    assert receipt.model_dump_json() == raw and receipt.checksum == checksum


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_older_versions_cannot_claim_balanced_packing(version):
    raw = json.loads((Path(__file__).parent / f"fixtures/relevance/receipt-v{version}.json").read_text())
    raw["packing"]["multipath_ordering"] = "balanced"
    with pytest.raises(ValidationError):
        RetrievalReceipt.model_validate(raw)


def test_version_five_requires_policy_and_execution_and_replays_scores():
    raw = json.loads((Path(__file__).parent / "fixtures/relevance/receipt-v4.json").read_text())
    raw["schema_version"] = 5
    raw["packing"]["multipath_ordering"] = "balanced"
    receipt = RetrievalReceipt.model_validate(raw)
    assert RetrievalReceipt.model_validate_json(receipt.model_dump_json()).checksum == receipt.checksum
    assert receipt.replay_ranking() == tuple(c.node_id for c in receipt.candidates)
    without_policy = json.loads(receipt.model_dump_json())
    without_policy["packing"].pop("multipath_ordering")
    with pytest.raises(ValidationError, match="explicit packing ordering"):
        RetrievalReceipt.model_validate(without_policy)
    raw.pop("execution")
    with pytest.raises(ValidationError, match="execution descriptor"):
        RetrievalReceipt.model_validate(raw)


def test_version_six_records_guidance_and_requires_an_explicit_mode():
    raw = json.loads((Path(__file__).parent / "fixtures/relevance/receipt-v4.json").read_text())
    raw["schema_version"] = 6
    raw["packing"]["multipath_ordering"] = "balanced"
    raw["packing"]["context_guidance_mode"] = "temporal"
    receipt = RetrievalReceipt.model_validate(raw)
    assert receipt.packing.context_guidance_mode == "temporal"
    assert "context_guidance_mode" in receipt.model_dump()["packing"]
    assert RetrievalReceipt.model_validate_json(receipt.model_dump_json()).checksum == receipt.checksum
    raw["packing"].pop("context_guidance_mode")
    with pytest.raises(ValidationError, match="explicit context guidance mode"):
        RetrievalReceipt.model_validate(raw)


def test_versions_before_six_mean_guidance_was_off():
    raw = json.loads((Path(__file__).parent / "fixtures/relevance/receipt-v4.json").read_text())
    for version in (4, 5):
        raw["schema_version"] = version
        raw["packing"]["multipath_ordering"] = "balanced" if version == 5 else "density"
        receipt = RetrievalReceipt.model_validate(raw)
        assert receipt.packing.context_guidance_mode == "off"
        assert "context_guidance_mode" not in receipt.model_dump()["packing"]
        claimed = json.loads(receipt.model_dump_json())
        claimed["packing"]["context_guidance_mode"] = "temporal"
        with pytest.raises(ValidationError, match="version 6"):
            RetrievalReceipt.model_validate(claimed)


def test_version_seven_records_context_format_and_requires_it_explicitly():
    raw = json.loads((Path(__file__).parent / "fixtures/relevance/receipt-v4.json").read_text())
    raw["schema_version"] = 7
    raw["packing"].update({
        "multipath_ordering": "balanced",
        "context_guidance_mode": "temporal",
        "context_format": "compact",
    })
    receipt = RetrievalReceipt.model_validate(raw)
    assert receipt.packing.context_format == "compact"
    assert receipt.packing.episode_context_top_k == 0
    assert "episode_context_top_k" not in receipt.model_dump()["packing"]
    assert RetrievalReceipt.model_validate_json(receipt.model_dump_json()).checksum == receipt.checksum
    complete = json.loads(receipt.model_dump_json())
    raw["packing"].pop("context_format")
    with pytest.raises(ValidationError, match="explicit context format"):
        RetrievalReceipt.model_validate(raw)
    for field, message in (
        ("multipath_ordering", "explicit packing ordering"),
        ("context_guidance_mode", "explicit context guidance mode"),
    ):
        missing = json.loads(json.dumps(complete))
        missing["packing"].pop(field)
        with pytest.raises(ValidationError, match=message):
            RetrievalReceipt.model_validate(missing)


def test_version_eight_records_episode_context_and_requires_it_explicitly():
    raw = json.loads(
        (Path(__file__).parent / "fixtures/relevance/receipt-v4.json").read_text()
    )
    raw["schema_version"] = 8
    raw["packing"].update(
        {
            "multipath_ordering": "balanced",
            "context_guidance_mode": "temporal",
            "context_format": "compact",
            "episode_context_top_k": 2,
            "episode_context_local_k": 8,
            "episode_context_score_decay": 0.95,
        }
    )
    receipt = RetrievalReceipt.model_validate(raw)
    assert receipt.packing.episode_context_top_k == 2
    assert (
        RetrievalReceipt.model_validate_json(receipt.model_dump_json()).checksum
        == receipt.checksum
    )
    for field in (
        "episode_context_top_k",
        "episode_context_local_k",
        "episode_context_score_decay",
    ):
        missing = json.loads(receipt.model_dump_json())
        missing["packing"].pop(field)
        with pytest.raises(ValidationError, match="episode context settings"):
            RetrievalReceipt.model_validate(missing)


def test_versions_before_seven_mean_context_was_auditable():
    raw = json.loads((Path(__file__).parent / "fixtures/relevance/receipt-v4.json").read_text())
    for version in (4, 5, 6):
        raw["schema_version"] = version
        raw["packing"]["multipath_ordering"] = "balanced" if version >= 5 else "density"
        if version == 6:
            raw["packing"]["context_guidance_mode"] = "temporal"
        else:
            raw["packing"].pop("context_guidance_mode", None)
        receipt = RetrievalReceipt.model_validate(raw)
        assert receipt.packing.context_format == "auditable"
        assert "context_format" not in receipt.model_dump()["packing"]
        claimed = json.loads(receipt.model_dump_json())
        claimed["packing"]["context_format"] = "compact"
        with pytest.raises(ValidationError, match="version 7"):
            RetrievalReceipt.model_validate(claimed)
