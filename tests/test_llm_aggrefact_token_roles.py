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


def _role(atom: int, role: str) -> dict:
    return {"atom_id": f"A{atom:02d}", "role": role}


def _complete_arguments() -> dict:
    return {
        "atoms": [_atom("A01"), _atom("A02", "unsupported")],
        "token_roles": {
            "C0001": [_role(1, "subject"), _role(2, "subject")],
            "C0002": [_role(1, "relation")],
            "C0003": [_role(1, "relation")],
            "C0004": [_role(1, "object")],
            "C0006": [_role(2, "relation")],
            "C0007": [_role(2, "relation")],
            "C0009": [_role(2, "object")],
        },
    }


def test_token_roles_accept_complete_decomposition_without_ranges() -> None:
    valid, errors = subject._strict_validate(_item(), _complete_arguments())

    assert valid is True
    assert errors == ()
    assert "start" not in subject.json.dumps(_complete_arguments())
    assert "end" not in subject.json.dumps(_complete_arguments())


def test_token_roles_reject_missing_claim_token_and_missing_role() -> None:
    arguments = _complete_arguments()
    del arguments["token_roles"]["C0004"]
    del arguments["token_roles"]["C0009"]

    valid, errors = subject._strict_validate(_item(), arguments)

    assert valid is False
    assert "token_role_key_set_invalid" in errors
    assert "A01_object_missing_word" in errors
    assert "A02_object_missing_word" in errors


def test_token_roles_reject_duplicate_role_for_same_atom_token() -> None:
    arguments = _complete_arguments()
    arguments["token_roles"]["C0001"].append(_role(1, "qualifier"))

    valid, errors = subject._strict_validate(_item(), arguments)

    assert valid is False
    assert "duplicate_atom_token_assignment" in errors


def test_atomic_text_uses_only_ordered_authoritative_source_runs() -> None:
    verdict = subject._TokenRoleVerdict.model_validate(_complete_arguments())

    text, identifiers = subject._atom_text(_item()["claim"], verdict, "A02")

    assert text == "Maya works as a surgeon"
    assert identifiers == ("C0001", "C0006", "C0007", "C0009")


def test_tool_schema_requires_every_substantive_token_property() -> None:
    schema = subject._tool_for_item(_item())["function"]["parameters"]
    token_roles = schema["properties"]["token_roles"]

    assert token_roles["required"] == [
        "C0001",
        "C0002",
        "C0003",
        "C0004",
        "C0006",
        "C0007",
        "C0009",
    ]
    assert set(token_roles["properties"]) == set(token_roles["required"])
    assert token_roles["additionalProperties"] is False


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
