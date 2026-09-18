"""Head-study evidence export and comparison direction contracts."""

import json

import pytest
import tiktoken

from benchmarks.diagnostics.packing_head_reader import prepare, COMPARISONS
from benchmarks.diagnostics.hindsight_capture import digest, write
from benchmarks.diagnostics.public_context_scores import summarize


def fixture_paths(tmp_path):
    def save(name, value):
        p = tmp_path / (name + ".json")
        write(p, value)
        return p

    inputs = save(
        "inputs",
        {
            "cases": [
                {
                    "case_id": "q",
                    "question": "When?",
                    "question_date": "2026-01-01T00:00:00+00:00",
                    "turns": [{"content": "HIDDEN_CANARY"}],
                }
            ]
        },
    )
    plan = save(
        "plan", {"identity": {"files": {"inputs": digest(inputs.read_bytes())}}}
    )
    contexts = {}
    for policy, text in [
        ("density", "Only in winter."),
        ("head1", "Only if it rains."),
    ]:
        contexts[policy + ":4096"] = {
            "context": text,
            "sha256": digest(text.encode()),
            "tokens": len(tiktoken.get_encoding("cl100k_base").encode(text)),
            "evidence_recall": 1,
            "gold": "GOLD_CANARY",
        }
    capture = save(
        "capture",
        {
            "complete": True,
            "controls_reproduced": 3,
            "plan_sha256": digest(plan.read_bytes()),
            "details": [
                {"case_id": "q", "category": "CATEGORY_CANARY", "arms": contexts}
            ],
        },
    )
    completion = save(
        "completion",
        {"native_exit_code": 0, "output_sha256": digest(capture.read_bytes())},
    )
    verification = save(
        "verification",
        {
            "complete": True,
            "contexts_verified": 6,
            "output_sha256": digest(capture.read_bytes()),
        },
    )
    return dict(
        inputs_path=inputs,
        capture_path=capture,
        completion_path=completion,
        verification_path=verification,
        capture_plan_path=plan,
    )


def test_head_export_keeps_only_exact_neutral_context(tmp_path):
    result = prepare(**fixture_paths(tmp_path))
    assert "CANARY" not in json.dumps(result)
    contexts = result["rows"][0]["contexts"]
    assert contexts["memory_a"]["context"] == "Only in winter."
    assert contexts["memory_b"]["context"] == "Only if it rains."
    assert contexts["empty"]["context"] == ""


@pytest.mark.parametrize(
    "mutation", ["native", "capture", "verification", "cohort", "source_plan"]
)
def test_head_export_rejects_changed_or_incomplete_evidence(tmp_path, mutation):
    paths = fixture_paths(tmp_path)
    key = {
        "native": "completion_path",
        "capture": "capture_path",
        "verification": "verification_path",
        "cohort": "inputs_path",
        "source_plan": "capture_plan_path",
    }[mutation]
    value = json.loads(paths[key].read_bytes())
    if mutation == "native":
        value["native_exit_code"] = 1
    elif mutation == "capture":
        value["details"][0]["arms"]["head1:4096"]["context"] = "Changed"
    elif mutation == "verification":
        value["contexts_verified"] = 5
    elif mutation == "cohort":
        value["cases"][0]["case_id"] = "other"
    else:
        value["identity"]["files"]["inputs"] = "changed"
    write(paths[key], value)
    with pytest.raises(ValueError):
        prepare(**paths)


def test_head_comparison_counts_gains_and_losses_in_registered_direction():
    rows = [
        {
            "case_id": "a",
            "category": "assistant",
            "answers": {"m": {"memory_a": False, "memory_b": True, "empty": False}},
        },
        {
            "case_id": "b",
            "category": "preference",
            "answers": {"m": {"memory_a": True, "memory_b": False, "empty": False}},
        },
    ]
    result = summarize(
        rows, {"a": "shared", "b": "shared"}, ["m"], comparisons=COMPARISONS
    )["m"]
    total = result["overall"]["head1_vs_density"]
    assert total["before_arm"] == "memory_a" and total["after_arm"] == "memory_b"
    assert total["before_correct"] == total["after_correct"] == 1
    assert total["group_bootstrap"]["wins"] == total["group_bootstrap"]["losses"] == 1
    assert (
        result["categories"]["assistant"]["head1_vs_density"]["group_bootstrap"][
            "delta"
        ]
        == 1
    )
    assert (
        result["categories"]["preference"]["head1_vs_density"]["group_bootstrap"][
            "delta"
        ]
        == -1
    )
    assert result["overall"]["head1_vs_empty"]["before_correct"] == 0
