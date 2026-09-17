from __future__ import annotations

from benchmarks.diagnostics import llm_aggrefact_token_roles as subject


def _item(claim: str = "Maya moved to Rome and works as a surgeon.") -> dict:
    return {
        "claim": claim,
        "evidence_segments": [
            ("E0001", "Maya moved to Rome. She works as an architect.")
        ],
    }


def _atom(atom_id: str, status: str = "supported") -> dict:
    return {
        "atom_id": atom_id,
        "status": status,
        "evidence": [
            {
                "evidence_id": "E0001",
                "subject": "aligned",
                "relation": "aligned",
                "object": "aligned" if status == "supported" else "conflict",
                "qualifiers": "aligned",
            }
        ],
    }


def _assignment(token: int, atom: int, role: str) -> dict:
    return {"token_id": f"C{token:04d}", "atom_id": f"A{atom:02d}", "role": role}


def _complete_arguments() -> dict:
    return {
        "atoms": [_atom("A01"), _atom("A02", "unsupported")],
        "assignments": [
            _assignment(1, 1, "subject"),
            _assignment(2, 1, "relation"),
            _assignment(3, 1, "relation"),
            _assignment(4, 1, "object"),
            _assignment(1, 2, "subject"),
            _assignment(6, 2, "relation"),
            _assignment(7, 2, "relation"),
            _assignment(9, 2, "object"),
        ],
    }


def test_token_roles_accept_complete_decomposition_without_ranges() -> None:
    valid, errors = subject._strict_validate(_item(), _complete_arguments())

    assert valid is True
    assert errors == ()
    assert "start" not in subject.json.dumps(_complete_arguments())
    assert "end" not in subject.json.dumps(_complete_arguments())


def test_token_roles_reject_missing_claim_token_and_missing_role() -> None:
    arguments = _complete_arguments()
    arguments["assignments"] = [
        value
        for value in arguments["assignments"]
        if value["token_id"] not in {"C0004", "C0009"}
    ]

    valid, errors = subject._strict_validate(_item(), arguments)

    assert valid is False
    assert "incomplete_claim_token_coverage" in errors
    assert "A01_object_missing_word" in errors
    assert "A02_object_missing_word" in errors


def test_token_roles_reject_duplicate_role_for_same_atom_token() -> None:
    arguments = _complete_arguments()
    arguments["assignments"].append(_assignment(1, 1, "qualifier"))

    valid, errors = subject._strict_validate(_item(), arguments)

    assert valid is False
    assert "duplicate_atom_token_assignment" in errors


def test_atomic_text_uses_only_ordered_authoritative_source_runs() -> None:
    verdict = subject._TokenRoleVerdict.model_validate(_complete_arguments())

    text, identifiers = subject._atom_text(_item()["claim"], verdict, "A02")

    assert text == "Maya works as a surgeon"
    assert identifiers == ("C0001", "C0006", "C0007", "C0009")


def test_strict_schema_rejects_unrecognized_fields() -> None:
    arguments = _complete_arguments()
    arguments["atoms"][0]["generated_claim"] = "Maya moved to Rome"

    valid, errors = subject._strict_validate(_item(), arguments)

    assert valid is False
    assert errors == ("tool_arguments_schema_invalid",)


def test_cli_deliberately_has_no_test_dataset_argument() -> None:
    actions = {action.dest for action in subject._parser()._actions}

    assert "dev" in actions
    assert "test" not in actions
