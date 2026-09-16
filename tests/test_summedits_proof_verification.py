from benchmarks.integrations import run_summedits_proof_verification as proof


def test_cohort_selection_is_balanced_unique_and_excludes_prototype():
    rows = []
    for label in (0, 1):
        for number in range(4):
            rows.append(
                {
                    "id": f"case-{label}-{number}",
                    "split": "evaluation",
                    "label": label,
                    "doc": f"document {label} {number}",
                    "summary": "summary",
                }
            )

    selected = proof._select_cohort(
        {"domain": rows},
        seed="seed",
        per_label_per_domain=2,
        excluded_ids={"case-0-0"},
        max_document_chars=100,
        max_summary_chars=100,
    )

    assert len(selected) == 4
    assert {row["label"] for row in selected} == {0, 1}
    assert sum(row["label"] == 0 for row in selected) == 2
    assert len({row["doc"] for row in selected}) == 4
    assert all(row["id"] != "case-0-0" for row in selected)


def test_proof_acceptance_requires_full_exact_citation_coverage():
    review = proof._ProofReview(
        atomic_claims=[
            proof._AtomicClaim(
                claim="Aster launched on Tuesday.",
                summary_quote="Aster launched on Tuesday.",
                status="supported",
                evidence_quotes=["Aster launched Tuesday"],
                explanation="The date and event match.",
            )
        ]
    )

    result = proof._summarize_review(
        review,
        document="Aster launched Tuesday after testing.",
        summary="Aster launched on Tuesday.",
    )

    assert result["predicted_supported"] is True
    assert result["summary_token_coverage"] == 1.0
    assert result["all_evidence_quotes_exact"] is True


def test_proof_rejects_partial_summary_coverage_or_missing_evidence():
    review = proof._ProofReview(
        atomic_claims=[
            proof._AtomicClaim(
                claim="Aster launched.",
                summary_quote="Aster launched",
                status="supported",
                evidence_quotes=[],
                explanation="Claimed support without a citation.",
            )
        ]
    )

    result = proof._summarize_review(
        review,
        document="Aster launched Tuesday.",
        summary="Aster launched Tuesday.",
    )

    assert result["predicted_supported"] is False
    assert result["summary_token_coverage"] < 1.0
    assert result["supported_atoms_have_evidence"] is False


def test_metrics_treat_only_label_one_as_supported():
    metrics = proof._metrics(
        [
            {"label": 1, "predicted_supported": True},
            {"label": 1, "predicted_supported": False},
            {"label": 0, "predicted_supported": True},
            {"label": 0, "predicted_supported": False},
        ]
    )

    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 1
    assert metrics["true_negative"] == 1
    assert metrics["false_negative"] == 1
    assert metrics["balanced_accuracy"] == 0.5
