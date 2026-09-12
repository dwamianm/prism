"""Raw competitor capture cannot infer labels or silently reconstruct source text."""

import copy
import json

import pytest
import tiktoken

from benchmarks.diagnostics.hindsight_capture import (
    context_from_units,
    returned_units,
    validate_case,
)


def case():
    return {
        "case_id": "case-0",
        "question": "Which path?",
        "question_date": "2025-06-01T12:00:00+00:00",
        "turns": [
            {
                "id": "s0:t0",
                "session_id": "session-0",
                "role": "user",
                "content": "Shaded paths only in summer.",
                "date": "2025-05-01T12:00:00+00:00",
            }
        ],
    }


def unit():
    turn = case()["turns"][0]
    return {
        "id": "unit-0",
        "document_id": turn["id"],
        "text": turn["content"],
        "metadata": {
            "source_turn": turn["id"],
            "source_session": turn["session_id"],
            "source_role": turn["role"],
            "source_date": turn["date"],
        },
    }


@pytest.mark.parametrize("location", ["case", "source"])
def test_source_contract_rejects_label_fields(location):
    value = case()
    (value if location == "case" else value["turns"][0])["has_answer"] = True
    with pytest.raises(ValueError, match="neutral"):
        validate_case(value)


@pytest.mark.parametrize(
    "mutation", ["unknown_id", "wrong_role", "duplicate", "altered_content"]
)
def test_invalid_public_unit_never_earns_source_identity(mutation):
    row = unit()
    if mutation == "unknown_id":
        row["id"] = "foreign"
    if mutation == "wrong_role":
        row["metadata"]["source_role"] = "assistant"
    if mutation == "altered_content":
        row["text"] = "Shaded paths always."
    rows = [row, row] if mutation == "duplicate" else [row]
    with pytest.raises(ValueError):
        returned_units({"results": rows}, case(), {"s0:t0": ["unit-0"]})


def test_partial_return_keeps_partial_text_in_adapter_context():
    row = unit()
    row["text"] = "Shaded paths"
    assert returned_units({"results": [row]}, case(), {"s0:t0": ["unit-0"]}) == [
        "s0:t0"
    ]
    result = context_from_units([row], 4096, tiktoken.get_encoding("cl100k_base"))
    assert json.loads(result["context"])["text"] == "Shaded paths"
    assert "only in summer" not in result["context"]


def test_full_serialized_budget_includes_metadata_and_never_truncates_text():
    encoding = tiktoken.get_encoding("cl100k_base")
    row = unit()
    second = copy.deepcopy(row)
    second["id"] = "unit-1"
    second["text"] = "Long complete condition. " * 200
    for budget in [0, 15, 100, 4096]:
        result = context_from_units([row, second], budget, encoding)
        assert (
            result["tokens"]
            == len(encoding.encode(result["context"], disallowed_special=()))
            <= budget
        )
        texts = [
            json.loads(line)["text"] for line in result["context"].split("\n") if line
        ]
        assert all(text in [row["text"], second["text"]] for text in texts)
