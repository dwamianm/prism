from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from prme import (
    ClaimEvidence,
    ClaimVerification,
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
    assert result.supporting_basis == "model_entailment"
    assert result.refuting_group == ()
    assert len(result.group_scores) == 2
    assert result.model_called is True
    assert len(result.evaluation_id) == 64
    assert len(result.result_sha256) == 64

    legacy = result.model_dump()
    legacy["schema_version"] = 1
    legacy.pop("supporting_basis")
    legacy.pop("refuting_basis")
    assert ClaimVerification.model_validate(legacy).schema_version == 1


async def test_searches_bounded_minimal_pair_when_singles_are_insufficient(monkeypatch):
    first = _evidence("The trip starts in Austin.", "m1")
    second = _evidence("The next stop after Austin is Tulsa.", "m2")
    third = _evidence("Unrelated weather.", "m3")
    verifier = ClaimVerifier(
        ClaimVerificationConfig(max_group_size=2, group_candidate_limit=3)
    )
    captured = []

    def predict(pairs):
        captured.extend(pairs)
        scores = []
        for premise, _claim in pairs:
            if "trip starts" in premise and "next stop" in premise:
                scores.append((0.91, 0.02, 0.07))
            elif "Unrelated weather" in premise:
                scores.append((0.01, 0.01, 0.98))
            elif "trip starts" in premise:
                scores.append((0.20, 0.05, 0.75))
            else:
                scores.append((0.25, 0.05, 0.70))
        return scores

    monkeypatch.setattr(verifier, "_predict_sync", predict)

    result = await verifier.verify(
        "Tulsa follows Austin on the trip.",
        [first, second, third],
    )

    assert result.status == ClaimVerificationStatus.SUPPORTED
    assert set(result.supporting_group) == {first.memory_id, second.memory_id}
    assert [len(item.memory_ids) for item in result.group_scores] == [1, 1, 1, 2, 2, 2]
    assert captured[3][0] == (
        "[E1] The trip starts in Austin.\n[E2] The next stop after Austin is Tulsa."
    )


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
    assert result.supporting_basis == "model_entailment"
    assert result.refuting_basis == "model_contradiction"


async def test_model_contradiction_without_explicit_corroboration_fails_closed(
    monkeypatch,
):
    evidence = _evidence("I want to deploy after the tests pass.", "m1")
    verifier = ClaimVerifier()
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.001, 0.97, 0.029)],
    )

    result = await verifier.verify("The user deployed the release.", [evidence])

    assert result.status == ClaimVerificationStatus.INSUFFICIENT
    assert result.refuting_group == ()
    assert result.group_scores[0].contradiction == 0.97
    assert result.limitations == ("uncorroborated_model_contradiction",)


@pytest.mark.parametrize(
    ("claim", "evidence_text"),
    [
        ("The launch is Tuesday.", "The launch is not Tuesday; it is Wednesday."),
        ("The launch is Tuesday.", "The launch is Wednesday."),
        ("The service listens on port 8443.", "The service listens on port 8080."),
        ("The launch is not Tuesday.", "The launch is Tuesday."),
    ],
)
async def test_explicit_cues_or_incompatible_values_corroborate_refutation(
    monkeypatch,
    claim,
    evidence_text,
):
    evidence = _evidence(evidence_text, "m1")
    verifier = ClaimVerifier()
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.001, 0.97, 0.029)],
    )

    result = await verifier.verify(claim, [evidence])

    assert result.status == ClaimVerificationStatus.REFUTED
    assert result.refuting_group == (evidence.memory_id,)
    assert result.refuting_basis == "model_contradiction"
    assert result.limitations == ()


async def test_model_only_policy_preserves_raw_model_refutation(monkeypatch):
    evidence = _evidence("I want to deploy after the tests pass.", "m1")
    verifier = ClaimVerifier(ClaimVerificationConfig(refutation_policy="model_only"))
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.001, 0.97, 0.029)],
    )

    result = await verifier.verify("The user deployed the release.", [evidence])

    assert result.status == ClaimVerificationStatus.REFUTED
    assert result.refuting_group == (evidence.memory_id,)
    assert result.limitations == ()


async def test_group_labels_do_not_count_as_concrete_evidence_values(monkeypatch):
    verifier = ClaimVerifier()
    evidence = [
        _evidence("The release plan is under review.", "m1"),
        _evidence("The team discussed deployment timing.", "m2"),
    ]

    def predict(pairs):
        if len(pairs) == 2:
            return [(0.01, 0.01, 0.98), (0.01, 0.01, 0.98)]
        return [(0.001, 0.97, 0.029)]

    monkeypatch.setattr(verifier, "_predict_sync", predict)

    result = await verifier.verify("Release 4 was deployed.", evidence)

    assert result.status == ClaimVerificationStatus.INSUFFICIENT
    assert result.refuting_group == ()
    assert result.limitations == ("uncorroborated_model_contradiction",)


async def test_nonactual_evidence_cannot_support_completed_claim(monkeypatch):
    evidence = _evidence(
        "I want to enable passkeys after the security review.",
        "m1",
    )
    verifier = ClaimVerifier()
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.98, 0.005, 0.015)],
    )

    result = await verifier.verify("The user enabled passkeys.", [evidence])

    assert result.status == ClaimVerificationStatus.INSUFFICIENT
    assert result.supporting_group == ()
    assert result.supporting_basis is None
    assert result.group_scores[0].entailment == 0.98
    assert result.limitations == ("uncorroborated_model_entailment",)


@pytest.mark.parametrize(
    ("claim", "evidence_text"),
    [
        ("The user wants offline sync.", "I would like offline sync."),
        (
            "The user is trying to reduce latency.",
            "I am working on reducing latency.",
        ),
        ("The recommendation is Redis.", "You should use Redis."),
    ],
)
async def test_nonactual_support_is_allowed_when_claim_preserves_speech_act(
    monkeypatch,
    claim,
    evidence_text,
):
    evidence = _evidence(evidence_text, "m1")
    verifier = ClaimVerifier()
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.98, 0.005, 0.015)],
    )

    result = await verifier.verify(claim, [evidence])

    assert result.status == ClaimVerificationStatus.SUPPORTED
    assert result.supporting_group == (evidence.memory_id,)
    assert result.supporting_basis == "model_entailment"
    assert result.limitations == ()


async def test_model_only_entailment_policy_preserves_raw_support(monkeypatch):
    evidence = _evidence("I want to enable passkeys.", "m1")
    verifier = ClaimVerifier(ClaimVerificationConfig(entailment_policy="model_only"))
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.98, 0.005, 0.015)],
    )

    result = await verifier.verify("The user enabled passkeys.", [evidence])

    assert result.status == ClaimVerificationStatus.SUPPORTED
    assert result.supporting_basis == "model_entailment"
    assert result.limitations == ()


async def test_typed_hypothetical_evidence_cannot_support_unqualified_fact(
    monkeypatch,
):
    evidence = ClaimEvidence(
        reference="m1",
        memory_id=uuid4(),
        text="The deployment happens after approval.",
        epistemic_type="hypothetical",
    )
    verifier = ClaimVerifier()
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.98, 0.005, 0.015)],
    )

    result = await verifier.verify("The deployment happens after approval.", [evidence])

    assert result.status == ClaimVerificationStatus.INSUFFICIENT
    assert result.limitations == ("uncorroborated_model_entailment",)


async def test_typed_hypothetical_evidence_can_support_qualified_claim(monkeypatch):
    evidence = ClaimEvidence(
        reference="m1",
        memory_id=uuid4(),
        text="The deployment could happen after approval.",
        epistemic_type="hypothetical",
    )
    verifier = ClaimVerifier()
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.98, 0.005, 0.015)],
    )

    result = await verifier.verify(
        "The deployment might happen after approval.",
        [evidence],
    )

    assert result.status == ClaimVerificationStatus.SUPPORTED
    assert result.supporting_basis == "model_entailment"


async def test_hypothetical_negation_cannot_trigger_deterministic_refutation(
    monkeypatch,
):
    evidence = ClaimEvidence(
        reference="m1",
        memory_id=uuid4(),
        text="Ravi might not own the ingestion pipeline.",
        epistemic_type="hypothetical",
    )
    verifier = ClaimVerifier()
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.002, 0.032, 0.966)],
    )

    result = await verifier.verify("Ravi owns the ingestion pipeline.", [evidence])

    assert result.status == ClaimVerificationStatus.INSUFFICIENT
    assert result.refuting_basis is None


async def test_explicit_near_exact_negation_surfaces_conflict_when_model_is_neutral(
    monkeypatch,
):
    support = _evidence("Ravi owns the ingestion pipeline.", "m1")
    correction = _evidence(
        "Ravi no longer owns ingestion; Tessa owns it.",
        "m2",
    )
    verifier = ClaimVerifier()
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.997, 0.001, 0.002), (0.002, 0.032, 0.966)],
    )

    result = await verifier.verify(
        "Ravi owns the ingestion pipeline.",
        [support, correction],
    )

    assert result.status == ClaimVerificationStatus.CONTESTED
    assert result.supporting_group == (support.memory_id,)
    assert result.refuting_group == (correction.memory_id,)
    assert result.refuting_basis == "explicit_negation_overlap"
    assert result.group_scores[1].contradiction == 0.032


async def test_lexically_related_negation_does_not_refute_another_relation(monkeypatch):
    evidence = _evidence("Nadia did not attend the Atlas project review.", "m1")
    verifier = ClaimVerifier()
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.002, 0.032, 0.966)],
    )

    result = await verifier.verify("Nadia leads the Atlas project.", [evidence])

    assert result.status == ClaimVerificationStatus.INSUFFICIENT
    assert result.refuting_group == ()
    assert result.refuting_basis is None


async def test_negated_intention_does_not_refute_completed_action(monkeypatch):
    evidence = _evidence("Alice wants to not deploy the release.", "m1")
    verifier = ClaimVerifier()
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.002, 0.95, 0.048)],
    )

    result = await verifier.verify("Alice deployed the release.", [evidence])

    assert result.status == ClaimVerificationStatus.INSUFFICIENT
    assert result.refuting_group == ()
    assert result.refuting_basis is None
    assert result.limitations == ("uncorroborated_model_contradiction",)


async def test_matching_negative_claim_is_supported_without_self_refutation(
    monkeypatch,
):
    evidence = _evidence("Alice does not use Jira.", "m1")
    verifier = ClaimVerifier()
    monkeypatch.setattr(
        verifier,
        "_predict_sync",
        lambda _pairs: [(0.99, 0.005, 0.005)],
    )

    result = await verifier.verify("Alice does not use Jira.", [evidence])

    assert result.status == ClaimVerificationStatus.SUPPORTED
    assert result.supporting_group == (evidence.memory_id,)
    assert result.refuting_group == ()


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
