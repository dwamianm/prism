"""The travel value-binding audit retains exact coverage and counts."""

import copy

import pytest

from benchmarks.diagnostics.memoryarena_value_binding_audit import (
    SLOTS,
    audit_value_bindings,
)


REGISTRATION = "a" * 64


def _cohort():
    base = [{"day": 1, **{slot: "Old" for slot in SLOTS}}]
    answer = [{"day": 1, **{slot: "Portland(Oregon)" for slot in SLOTS}}]
    return [{
        "id": 7,
        "base_person": {"daily_plans": base},
        "answers": [{"round_idx": 1, "daily_plans": answer}],
    }]


def _checkpoint(arm, *, value="Portland(Oregon)", qualified_call=False):
    plan = [{"day": 1, **{slot: value for slot in SLOTS}}]
    calls = [{"name": "RestaurantSearch", "args": {"city": value}}] if qualified_call else []
    guidance = {
        "schema_version": 1,
        "instruction": (
            "When presenting this tool result, preserve these exact source-backed "
            "values. This metadata is separate from the tool data."
        ),
        "values": [{"kind": "city", "presentation": value}],
    }
    payload = (
        '{"instruction":"When presenting this tool result, preserve these exact '
        'source-backed values. This metadata is separate from the tool data.",'
        f'"schema_version":1,"values":[{{"kind":"city","presentation":"{value}"}}]}}'
    )
    results = [{
        "name": "RestaurantSearch",
        "result": f"found\n\n<prme_value_presentations>{payload}</prme_value_presentations>",
    }] if qualified_call else []
    resolutions = []
    if arm == "prme" and qualified_call:
        resolutions.append({
            "tool_name": "RestaurantSearch",
            "original_arguments": {"city": value},
            "resolved_arguments": {"city": "Portland"},
            "replacements": [{
                "json_pointer": "/city",
                "presentation": value,
                "lookup": "Portland",
            }],
            "binding_uses": [{
                "json_pointer": "/city",
                "operation": "replaced",
                "kind": "city",
                "presentation": value,
                "lookup": "Portland",
            }],
            "result_presentation_guidance": guidance,
        })
    return {
        "registration_sha256": REGISTRATION,
        "arm": arm,
        "group_id": 7,
        "person_idx": 1,
        "person": {
            "plan": plan,
            "tool_argument_resolutions": resolutions if arm == "prme" else [],
        },
        "scratchpad": {"scratchpad": [{
            "tool_calls": calls,
            "tool_results": results,
        }]},
    }


def test_audit_counts_exact_values_and_qualified_tool_calls():
    result = audit_value_bindings(
        _cohort(),
        [
            _checkpoint("native_full_history"),
            _checkpoint("prme", qualified_call=True),
        ],
        registration_sha256=REGISTRATION,
    )

    assert result["coverage"] == {
        "complete": True,
        "checkpoint_count": 2,
        "group_count": 1,
        "person_count": 1,
    }
    assert result["qualified_changed_values"] == {
        "passed": 6,
        "total": 6,
        "rate": 1.0,
        "observed": 6,
    }
    assert result["qualified_tool_calls"]["count"] == 1
    assert result["qualified_tool_calls"]["traveler_count"] == 1
    assert result["tool_boundary_resolution"] == {
        "record_count": 1,
        "trace_coverage_complete": True,
        "replacement_count": 1,
        "replacement_traveler_count": 1,
        "replacements": [{
            "group_id": 7,
            "person_idx": 1,
            "resolution_index": 0,
            "tool": "RestaurantSearch",
            "json_pointer": "/city",
            "presentation": "Portland(Oregon)",
            "lookup": "Portland",
        }],
        "executed_qualified_argument_count": 0,
        "executed_qualified_traveler_count": 0,
        "executed_qualified_arguments": [],
        "binding_use_evidence_available": True,
        "binding_use_count": 1,
        "binding_use_operations": {"replaced": 1, "already_lookup": 0},
        "binding_uses": [{
            "group_id": 7,
            "person_idx": 1,
            "resolution_index": 0,
            "tool": "RestaurantSearch",
            "json_pointer": "/city",
            "operation": "replaced",
            "kind": "city",
            "presentation": "Portland(Oregon)",
            "lookup": "Portland",
        }],
        "guided_result_count": 1,
        "guided_result_traveler_count": 1,
    }


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "foreign", "registration"])
def test_audit_rejects_changed_or_ambiguous_coverage(mutation):
    rows = [_checkpoint("native_full_history"), _checkpoint("prme")]
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(copy.deepcopy(rows[-1]))
    elif mutation == "foreign":
        rows[-1]["person_idx"] = 2
    else:
        rows[-1]["registration_sha256"] = "b" * 64

    with pytest.raises(ValueError):
        audit_value_bindings(_cohort(), rows, registration_sha256=REGISTRATION)


def test_audit_requires_full_string_equality():
    result = audit_value_bindings(
        _cohort(),
        [_checkpoint("native_full_history"), _checkpoint("prme", value="Portland")],
        registration_sha256=REGISTRATION,
    )

    assert result["qualified_changed_values"]["passed"] == 0
    assert result["qualified_changed_values"]["observed"] == 6


@pytest.mark.parametrize(
    "mutation", ["missing_record", "trace", "undeclared", "guidance"]
)
def test_audit_rejects_incomplete_or_changed_resolution_evidence(mutation):
    native = _checkpoint("native_full_history")
    prme = _checkpoint("prme", qualified_call=True)
    if mutation == "missing_record":
        prme["person"]["tool_argument_resolutions"] = []
    elif mutation == "trace":
        prme["person"]["tool_argument_resolutions"][0]["tool_name"] = "Other"
    elif mutation == "undeclared":
        prme["person"]["tool_argument_resolutions"][0]["replacements"] = []
    else:
        prme["scratchpad"]["scratchpad"][0]["tool_results"][0]["result"] = "found"

    with pytest.raises(ValueError):
        audit_value_bindings(
            _cohort(), [native, prme], registration_sha256=REGISTRATION
        )


def test_audit_supports_matched_no_guidance_control_arm():
    native = _checkpoint("native_full_history")
    candidate = _checkpoint("prme", qualified_call=True)
    control = copy.deepcopy(candidate)
    control["arm"] = "prme_no_result_guidance"
    record = control["person"]["tool_argument_resolutions"][0]
    record["result_presentation_guidance_enabled"] = False
    record["result_presentation_guidance"] = None
    control["scratchpad"]["scratchpad"][0]["tool_results"][0]["result"] = "found"

    result = audit_value_bindings(
        _cohort(),
        [native, control, candidate],
        registration_sha256=REGISTRATION,
        arms=("native_full_history", "prme_no_result_guidance", "prme"),
        target_arm="prme_no_result_guidance",
        expect_result_guidance=False,
    )

    assert result["arm"] == "prme_no_result_guidance"
    assert result["result_guidance_expected"] is False
    assert result["tool_boundary_resolution"]["binding_use_count"] == 1
    assert result["tool_boundary_resolution"]["guided_result_count"] == 0
