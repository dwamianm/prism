from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from prme import (
    ClaimEvidence,
    ClaimVerificationConfig,
    ClaimVerificationError,
    ClaimVerificationStatus,
    ClaimVerifier,
)
from prme.models import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context


def _evidence(text: str, reference: str) -> ClaimEvidence:
    return ClaimEvidence(reference=reference, memory_id=uuid4(), text=text)


async def test_prefers_single_minimal_support_over_larger_groups(monkeypatch):
    first = _evidence("Topically related only.", "m1")
    second = _evidence("The deployment date is May 4.", "m2")
    verifier = ClaimVerifier()
    verifier._resolved_revision = verifier.config.revision

    def predict(pairs):
        return [
            (0.01, 0.01, 0.98) if "Topically" in premise else (0.98, 0.01, 0.01)
            for premise, _claim in pairs
        ]

    monkeypatch.setattr(verifier, "_predict_sync", predict)

    result = await verifier.verify(
        "The deployment date is May 4.",
        [first, second],
    )

    assert result.status == ClaimVerificationStatus.SUPPORTED
    assert result.supporting_group == (second.memory_id,)
    assert result.refuting_group == ()
    assert len(result.group_scores) == 2
    assert result.model_called is True
    assert len(result.evaluation_id) == 64
    assert len(result.result_sha256) == 64


async def test_searches_bounded_minimal_pair_when_singles_are_insufficient(monkeypatch):
    first = _evidence("The trip starts in Austin.", "m1")
    second = _evidence("The next stop after Austin is Tulsa.", "m2")
    third = _evidence("Unrelated weather.", "m3")
    verifier = ClaimVerifier(
        ClaimVerificationConfig(max_group_size=2, group_candidate_limit=3)
    )

    def predict(pairs):
        scores = []
        for premise, _claim in pairs:
            if "trip starts" in premise and "next stop" in premise:
                scores.append((0.91, 0.02, 0.07))
            else:
                scores.append((0.20, 0.05, 0.75))
        return scores

    monkeypatch.setattr(verifier, "_predict_sync", predict)

    result = await verifier.verify(
        "Tulsa follows Austin on the trip.",
        [first, second, third],
    )

    assert result.status == ClaimVerificationStatus.SUPPORTED
    assert set(result.supporting_group) == {first.memory_id, second.memory_id}
    assert [len(item.memory_ids) for item in result.group_scores] == [1, 1, 1, 2, 2, 2]


async def test_entailing_and_refuting_groups_surface_contested(monkeypatch):
    support = _evidence("The launch is Tuesday.", "m1")
    refute = _evidence("The launch is not Tuesday; it is Wednesday.", "m2")
    verifier = ClaimVerifier()

    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.95, 0.01, 0.04), (0.01, 0.97, 0.02)],
    )

    result = await verifier.verify("The launch is Tuesday.", [support, refute])

    assert result.status == ClaimVerificationStatus.CONTESTED
    assert result.supporting_group == (support.memory_id,)
    assert result.refuting_group == (refute.memory_id,)


async def test_complete_set_claim_fails_closed_without_loading_model(monkeypatch):
    verifier = ClaimVerifier()
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: pytest.fail("completeness is not an NLI decision"),
    )

    result = await verifier.verify(
        "There are exactly four concerns across all sessions.",
        [_evidence("One concern is caching.", "m1")],
        requires_complete_set=True,
    )

    assert result.status == ClaimVerificationStatus.INCOMPLETE
    assert result.model_called is False
    assert result.limitations == ("complete_set_requires_structured_aggregation",)
    assert result.group_scores == ()


async def test_empty_evidence_is_explicit_insufficient_without_model(monkeypatch):
    verifier = ClaimVerifier()
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: pytest.fail("empty evidence must not load a model"),
    )

    result = await verifier.verify("The launch is Tuesday.", [])

    assert result.status == ClaimVerificationStatus.INSUFFICIENT
    assert result.model_called is False


async def test_bundle_verification_uses_exact_rendered_text_and_typed_provenance(
    monkeypatch,
):
    node = MemoryNode(
        user_id="owner",
        content="The API latency is 250ms.",
        node_type="fact",
        source_type="user_stated",
        epistemic_type="asserted",
        lifecycle_state="stable",
    )
    bundle = pack_context(
        [RetrievalCandidate(node=node, composite_score=1.0)],
        PackingConfig(
            token_budget=1000,
            overhead_tokens=0,
            min_fidelity="full",
            context_guidance_mode="off",
        ),
    )
    verifier = ClaimVerifier()
    captured = []

    def predict(pairs):
        captured.extend(pairs)
        return [(0.99, 0.005, 0.005)]

    monkeypatch.setattr(verifier, "_predict_sync", predict)

    result = await verifier.verify_bundle(
        "The API latency is 250ms.",
        bundle,
        evidence_refs=[str(node.id)],
    )
    evidence = verifier.bundle_evidence(bundle)

    assert result.status == ClaimVerificationStatus.SUPPORTED
    assert captured == [("The API latency is 250ms.", "The API latency is 250ms.")]
    assert len(evidence) == 1
    assert evidence[0].source_type.value == "user_stated"
    assert evidence[0].epistemic_type.value == "asserted"
    assert evidence[0].lifecycle_state.value == "stable"


async def test_bundle_rejects_unknown_reference():
    verifier = ClaimVerifier()

    with pytest.raises(ValueError, match="Unknown context reference"):
        await verifier.verify_bundle(
            "A claim.",
            pack_context([], PackingConfig(token_budget=100)),
            evidence_refs=["m999"],
        )


async def test_duplicate_or_excess_evidence_is_rejected_before_inference():
    verifier = ClaimVerifier(ClaimVerificationConfig(max_evidence=1))
    item = _evidence("Evidence.", "m1")
    duplicate_reference = item.model_copy(update={"memory_id": uuid4()})

    with pytest.raises(ValueError, match="configured maximum"):
        await verifier.verify("Claim.", [item, duplicate_reference])

    verifier = ClaimVerifier()
    with pytest.raises(ValueError, match="duplicate evidence reference"):
        await verifier.verify("Claim.", [item, duplicate_reference])
    with pytest.raises(ValueError, match="duplicate evidence memory"):
        await verifier.verify(
            "Claim.",
            [item, item.model_copy(update={"reference": "m2"})],
        )


def test_invalid_model_probabilities_fail_explicitly():
    verifier = ClaimVerifier()
    verifier._model = Mock()
    verifier._model.predict.return_value = [[0.2, 0.2, 0.2]]
    verifier._label_indexes = {"contradiction": 0, "entailment": 1, "neutral": 2}

    with pytest.raises(ClaimVerificationError, match="normalized"):
        verifier._predict_sync([("Evidence", "Claim")])


def test_claim_evidence_requires_nonempty_fields_and_uuid():
    with pytest.raises(ValueError):
        ClaimEvidence(reference=" ", memory_id=UUID(int=1), text="Evidence")
    with pytest.raises(ValueError):
        ClaimEvidence(reference="m1", memory_id=UUID(int=1), text=" ")
