from benchmarks.integrations import run_summedits_segment_verification as segment


def test_segments_are_numbered_and_stable():
    assert segment._segments("First fact. Second fact!\nThird fact?", "S") == [
        ("S0001", "First fact."),
        ("S0002", "Second fact!"),
        ("S0003", "Third fact?"),
    ]


def test_decomposition_requires_exact_complete_segment_coverage():
    decomposition = segment._Decomposition(
        atomic_claims=[
            segment._DraftAtom(claim="Aster launched.", summary_segment_ids=["S0001"]),
            segment._DraftAtom(
                claim="Boreal landed.", summary_segment_ids=["S0002", "S0002"]
            ),
        ]
    )

    atoms, valid = segment._normalize_decomposition(
        decomposition,
        summary_segment_ids={"S0001", "S0002"},
    )

    assert valid is True
    assert [atom["atom_id"] for atom in atoms] == ["A0001", "A0002"]
    assert atoms[1]["summary_segment_ids"] == ("S0002",)


def test_decomposition_rejects_unknown_or_missing_segment_ids():
    decomposition = segment._Decomposition(
        atomic_claims=[
            segment._DraftAtom(claim="Aster launched.", summary_segment_ids=["S9999"])
        ]
    )

    _, valid = segment._normalize_decomposition(
        decomposition,
        summary_segment_ids={"S0001"},
    )

    assert valid is False


def test_verification_accepts_only_complete_supported_fixed_atoms():
    atoms = [
        {"atom_id": "A0001", "claim": "Aster launched."},
        {"atom_id": "A0002", "claim": "Boreal landed."},
    ]
    verification = segment._Verification(
        verdicts=[
            segment._Verdict(
                atom_id="A0001",
                status="supported",
                document_segment_ids=["D0001"],
                explanation="Explicitly stated.",
            ),
            segment._Verdict(
                atom_id="A0002",
                status="supported",
                document_segment_ids=["D0002"],
                explanation="Explicitly stated.",
            ),
        ]
    )

    result = segment._summarize_verification(
        verification,
        atoms=atoms,
        document_segment_ids={"D0001", "D0002"},
        decomposition_integrity=True,
    )

    assert result["predicted_supported"] is True
    assert result["verification_integrity"] is True


def test_verification_rejects_unknown_evidence_or_missing_atom():
    atoms = [
        {"atom_id": "A0001", "claim": "Aster launched."},
        {"atom_id": "A0002", "claim": "Boreal landed."},
    ]
    verification = segment._Verification(
        verdicts=[
            segment._Verdict(
                atom_id="A0001",
                status="supported",
                document_segment_ids=["D9999"],
                explanation="Invalid citation.",
            )
        ]
    )

    result = segment._summarize_verification(
        verification,
        atoms=atoms,
        document_segment_ids={"D0001"},
        decomposition_integrity=True,
    )

    assert result["predicted_supported"] is False
    assert result["verification_integrity"] is False
