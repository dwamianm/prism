from __future__ import annotations

from benchmarks.diagnostics import llm_aggrefact_atomic_partition as subject


def _item(claim: str = "Maya moved to Rome and works as a surgeon.") -> dict:
    return {"claim": claim, "evidence_segments": []}


def _complete_arguments() -> dict:
    return {
        "atoms": [
            {"token_ids": ["C0001", "C0002", "C0003", "C0004"]},
            {"token_ids": ["C0001", "C0006", "C0007", "C0009"]},
        ]
    }


def test_partition_accepts_complete_source_bound_atoms() -> None:
    valid, errors = subject._strict_validate(_item(), _complete_arguments())

    assert valid is True
    assert errors == ()
    serialized = subject.json.dumps(_complete_arguments())
    assert "subject" not in serialized
    assert "supported" not in serialized
    assert "start" not in serialized


def test_partition_rejects_missing_required_token() -> None:
    arguments = _complete_arguments()
    arguments["atoms"][0]["token_ids"].remove("C0004")

    valid, errors = subject._strict_validate(_item(), arguments)

    assert valid is False
    assert "missing_claim_tokens:C0004" in errors


def test_partition_rejects_unused_or_degenerate_atom() -> None:
    arguments = _complete_arguments()
    arguments["atoms"].append({"token_ids": ["C0005", "C0010"]})

    valid, errors = subject._strict_validate(_item(), arguments)

    assert valid is False
    assert "atom_3_has_fewer_than_two_substantive_tokens" in errors


def test_partition_allows_nested_nonduplicate_atom() -> None:
    arguments = _complete_arguments()
    arguments["atoms"].append({"token_ids": ["C0006", "C0009"]})

    valid, errors = subject._strict_validate(_item(), arguments)

    assert valid is True
    assert errors == ()


def test_partition_rejects_duplicate_and_out_of_range_membership() -> None:
    arguments = _complete_arguments()
    arguments["atoms"][0]["token_ids"] += ["C0001", "C9999"]

    valid, errors = subject._strict_validate(_item(), arguments)

    assert valid is False
    assert "atom_1_duplicate_token" in errors
    assert "atom_1_unknown_claim_token" in errors
    assert "unknown_claim_tokens:C9999" in errors


def test_partition_identifies_duplicate_atom_indexes() -> None:
    arguments = _complete_arguments()
    arguments["atoms"].append({"token_ids": list(arguments["atoms"][0]["token_ids"])})

    valid, errors = subject._strict_validate(_item(), arguments)

    assert valid is False
    assert "duplicate_atoms:1,3" in errors


def test_partition_allows_optional_displayed_structural_tokens() -> None:
    arguments = _complete_arguments()
    arguments["atoms"][0]["token_ids"].append("C0005")
    arguments["atoms"][1]["token_ids"] += ["C0008", "C0010"]

    valid, errors = subject._strict_validate(_item(), arguments)

    assert valid is True
    assert errors == ()


def test_atomic_text_reconstructs_disjoint_exact_source_runs() -> None:
    partition = subject._AtomicPartition.model_validate(_complete_arguments())

    text, identifiers = subject._atom_text(_item()["claim"], partition, 2)

    assert text == "Maya works as a surgeon"
    assert identifiers == ("C0001", "C0006", "C0007", "C0009")


def test_tool_schema_limits_atom_membership_to_displayed_tokens() -> None:
    schema = subject._tool_for_item(_item())["function"]["parameters"]
    token_ids = schema["$defs"]["_AtomicUnit"]["properties"]["token_ids"]

    assert token_ids["items"]["enum"] == [f"C{index:04d}" for index in range(1, 11)]
    assert token_ids["uniqueItems"] is True


def test_cli_deliberately_has_no_test_dataset_argument() -> None:
    actions = {action.dest for action in subject._parser()._actions}

    assert "dev" in actions
    assert "test" not in actions
